from fastapi.testclient import TestClient

import api
from api import create_app
from storage import Database


def seeded_database(tmp_path, count=3):
    db = Database(tmp_path / "api.db")
    db.save_profile("Ada Lovelace", "Python developer who built a public API.", {"minimum_score": 70, "desired_titles": ["Engineer"]})
    db.add_source("Acme", "lever", "acme", "https://jobs.lever.co/acme")
    source = db.list_sources()[0]
    db.upsert_jobs(source["id"], [{"external_id": str(index), "title": f"Engineer {index}", "location": "Remote", "description": "Build Python APIs", "job_url": f"https://jobs.lever.co/acme/{index}", "apply_url": f"https://jobs.lever.co/acme/{index}/apply"} for index in range(count)])
    for index, job in enumerate(db.open_jobs()):
        db.save_match(job["id"], {"score": 95 - index, "decision": "qualified", "evidence": ["Python"], "missing_requirements": [], "rejection_reason": "", "job_fingerprint": job["fingerprint"]}, "test-model")
    return db


def test_profile_validation_and_match_pagination(tmp_path):
    client = TestClient(create_app(seeded_database(tmp_path)))
    assert client.put("/api/profile", json={"candidate_name": "", "candidate_profile": "x", "preferences": {}}).status_code == 422
    result = client.get("/api/matches?page=1&page_size=2&minimum_score=80").json()
    assert result["total"] == 3
    assert len(result["items"]) == 2


def test_manual_drafts_can_be_created_listed_updated_and_deleted(tmp_path, monkeypatch):
    db = seeded_database(tmp_path, count=1)
    monkeypatch.setattr(api, "generate_cold_email", lambda _input: "Subject: Engineer\n\nHello from Ada")
    client = TestClient(create_app(db))
    payload = {"candidate_name": "Ada", "company_name": "Acme", "candidate_profile": "Python", "job_description": "Build APIs", "role_title": "Engineer", "recipient_name": "Rina", "recipient_position": "Recruiter", "body": "Subject: Engineer\n\nHello from Ada"}
    created = client.post("/api/manual-drafts", json=payload)
    assert created.status_code == 201
    draft_id = created.json()["id"]
    assert client.get("/api/manual-drafts").json()[0]["body"].startswith("Subject:")
    updated = client.patch(f"/api/manual-drafts/{draft_id}", json={"body": "Updated email"})
    assert updated.status_code == 200
    assert updated.json()["body"] == "Updated email"
    assert client.delete(f"/api/manual-drafts/{draft_id}").status_code == 204
    assert client.get("/api/manual-drafts").json() == []


def test_removed_outreach_and_conversation_routes_are_unavailable(tmp_path):
    client = TestClient(create_app(seeded_database(tmp_path, count=1)))
    assert client.get("/api/approval-items").status_code == 404
    assert client.get("/api/conversations").status_code == 404
    assert client.post("/api/outreach/send", json={"draft_ids": [1], "confirmed": True}).status_code == 404
