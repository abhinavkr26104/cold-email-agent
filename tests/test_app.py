import json

from fastapi.testclient import TestClient

from api import create_app
from automation import prepare_drafts, send_drafts
from storage import Database


def seed_matches(tmp_path, count=6, *, contact=False):
    db = Database(tmp_path / "api.db")
    db.save_profile(
        "Ada Lovelace", "Python developer who built a public API.",
        {"minimum_score": 70, "desired_titles": ["Engineer"]},
    )
    db.add_source("Acme", "lever", "acme", "https://jobs.lever.co/acme")
    source = db.list_sources()[0]
    db.upsert_jobs(source["id"], [
        {
            "external_id": str(index), "title": f"Engineer {index}", "location": "Remote",
            "description": "Build Python APIs", "job_url": f"https://jobs.lever.co/acme/{index}",
            "apply_url": f"https://jobs.lever.co/acme/{index}/apply",
            "contact_email": "jobs@acme.test" if contact else None,
            "contact_source_url": "https://jobs.lever.co/acme" if contact else None,
        }
        for index in range(count)
    ])
    for index, job in enumerate(db.open_jobs()):
        db.save_match(job["id"], {
            "score": 95 - index, "decision": "qualified", "evidence": ["Python"],
            "missing_requirements": [], "rejection_reason": "",
            "job_fingerprint": job["fingerprint"],
        }, "test-model")
    return db


def fake_email(request):
    assert not request.applied_at
    greeting = request.recipient_name or "hiring team"
    return f"Subject: {request.role_title}\n\nHello {greeting},\n\nGrounded candidate note.\n\nAda Lovelace"


def test_top_five_matches_receive_pre_application_drafts(tmp_path):
    db = seed_matches(tmp_path)

    assert prepare_drafts(db, generator=fake_email) == 5
    items = db.approval_items()

    assert len(items) == 5
    assert all(item["application_status"] == "discovered" for item in items)
    assert all("applied" not in item["body"].lower() for item in items)


def test_approval_contract_blocks_send_until_application_and_contact(tmp_path):
    db = seed_matches(tmp_path, count=1)
    prepare_drafts(db, generator=fake_email)
    client = TestClient(create_app(db))

    item = client.get("/api/approval-items").json()[0]

    assert item["display_state"] == "Contact pending"
    assert item["can_send"] is False
    assert {blocker["code"] for blocker in item["blockers"]} >= {
        "application_required", "contact_required"
    }
    assert client.post("/api/outreach/send", json={"draft_ids": [item["id"]], "confirmed": False}).status_code == 400


def test_later_contact_personalizes_untouched_generic_draft(tmp_path):
    db = seed_matches(tmp_path, count=1)
    prepare_drafts(db, generator=fake_email)
    item = db.approval_items()[0]
    assert "hiring team" in item["body"]
    db.save_contact(item["job_id"], {
        "email": "rina@acme.test", "name": "Rina", "position": "Recruiter",
        "source_kind": "hunter", "confidence": 95, "verification_status": "valid",
        "sources": ["https://acme.test/rina"],
    })

    assert prepare_drafts(db, generator=fake_email) == 1
    assert "Hello Rina" in db.get_draft(item["id"])["body"]


def test_later_contact_preserves_edited_draft_and_marks_it_stale(tmp_path):
    db = seed_matches(tmp_path, count=1)
    prepare_drafts(db, generator=fake_email)
    item = db.approval_items()[0]
    db.edit_draft(item["id"], "My subject", "My carefully edited body")
    db.save_contact(item["job_id"], {
        "email": "rina@acme.test", "name": "Rina", "position": "Recruiter",
        "source_kind": "hunter", "confidence": 95, "verification_status": "valid",
        "sources": ["https://acme.test/rina"],
    })

    assert prepare_drafts(db, generator=fake_email) == 0
    draft = db.get_draft(item["id"])
    assert draft["body"] == "My carefully edited body"
    assert draft["stale"] == 1


def test_mark_applied_unlocks_sourced_draft_and_send_creates_snapshot(tmp_path):
    db = seed_matches(tmp_path, count=1, contact=True)
    prepare_drafts(db, generator=fake_email)
    item = db.approval_items()[0]
    db.mark_applied(item["job_id"])

    class Mailer:
        def send(self, recipient, subject, body):
            return {"message_id": "m1", "thread_id": "t1"}

    result = send_drafts([item["id"]], db, Mailer())

    assert result["sent"]
    assert db.get_draft(item["id"])["status"] == "sent"
    outreach = db.tracked_outreach()[0]
    assert outreach["gmail_message_id"] == "m1"
    assert json.loads(outreach["contact_sources_json"]) == ["https://jobs.lever.co/acme"]


def test_discovery_run_conflict_and_persistent_contract(tmp_path):
    db = seed_matches(tmp_path, count=1)
    db.create_task_run("existing")
    client = TestClient(create_app(db))

    response = client.post("/api/discovery-runs")

    assert response.status_code == 409
    run = client.get("/api/discovery-runs/existing").json()
    assert run["status"] == "queued"
    assert run["progress"] == 0


def test_profile_validation_and_match_pagination(tmp_path):
    db = seed_matches(tmp_path, count=3)
    client = TestClient(create_app(db))

    assert client.put("/api/profile", json={"candidate_name": "", "candidate_profile": "x", "preferences": {}}).status_code == 422
    result = client.get("/api/matches?page=1&page_size=2&minimum_score=80").json()

    assert result["total"] == 3
    assert len(result["items"]) == 2
    assert result["items"][0]["evidence"] == ["Python"]
