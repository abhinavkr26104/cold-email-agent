from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from automation import discover, enrich_applied_jobs, send_approved, sync_replies
from matching import hard_filter
from storage import Database


def seeded_database(tmp_path: Path) -> tuple[Database, int]:
    db = Database(tmp_path / "agent.db")
    db.save_profile(
        "Ada Lovelace",
        "Python developer who built an API.",
        {
            "locations": ["remote"],
            "employment_types": ["full-time"],
            "remote_policy": "any",
            "minimum_score": 70,
        },
    )
    db.add_source("Acme", "lever", "acme", "https://jobs.lever.co/acme")
    source = db.list_sources()[0]
    db.upsert_jobs(
        source["id"],
        [
            {
                "external_id": "role-1",
                "title": "Python Engineer",
                "location": "Remote",
                "employment_type": "Full-time",
                "description": "Build Python APIs.",
                "job_url": "https://jobs.lever.co/acme/role-1",
                "apply_url": "https://jobs.lever.co/acme/role-1/apply",
                "contact_email": "careers@acme.test",
                "contact_source_url": "https://jobs.lever.co/acme/role-1",
            }
        ],
    )
    job = db.open_jobs()[0]
    db.save_match(
        job["id"],
        {
            "score": 90,
            "decision": "qualified",
            "evidence": ["Python"],
            "missing_requirements": [],
            "rejection_reason": "",
            "job_fingerprint": job["fingerprint"],
        },
        "fake-model",
    )
    db.mark_applied(job["id"])
    db.queue_outreach(
        job["id"], "careers@acme.test", "https://jobs.lever.co/acme/role-1", "Python role", "Hello"
    )
    return db, db.queued_outreach()[0]["id"]


def test_hard_filter_rejects_exclusion_before_model():
    job = {"title": "Senior Python Engineer", "location": "Remote", "employment_type": "Full-time", "description": ""}
    assert hard_filter(job, {"excluded_keywords": ["senior"]}) == "Excluded keyword: senior"


def test_hard_filter_requires_configured_role_keyword():
    job = {"title": "Python Engineer", "location": "Remote", "employment_type": "Full-time", "description": "APIs"}
    assert hard_filter(job, {"required_keywords": ["django"]}) == "Required keyword is missing: django"


def test_send_approved_persists_gmail_ids(tmp_path):
    db, outreach_id = seeded_database(tmp_path)

    class Mailer:
        def send(self, recipient, subject, body):
            assert recipient == "careers@acme.test"
            return {"message_id": "message-1", "thread_id": "thread-1"}

    result = send_approved([outreach_id], db, Mailer())

    assert result["sent"] == [outreach_id]
    assert db.get_outreach(outreach_id)["status"] == "sent"


def test_send_blocks_contact_without_current_provenance(tmp_path):
    db, outreach_id = seeded_database(tmp_path)
    with db.connect() as connection:
        connection.execute("UPDATE jobs SET contact_email=NULL WHERE id=1")

    result = send_approved([outreach_id], db, mailer=object())

    assert result["sent"] == []
    assert result["skipped"][0]["reason"] == "Contact provenance changed"


def test_reply_sync_marks_and_labels_human_reply(tmp_path):
    db, outreach_id = seeded_database(tmp_path)

    class Mailer:
        def send(self, recipient, subject, body):
            return {"message_id": "message-1", "thread_id": "thread-1"}

        def find_reply(self, thread_id, sent_at):
            return {"automated": False, "received_at": "2026-08-15T10:00:00+00:00"}

        def label_reply(self, thread_id):
            assert thread_id == "thread-1"

    mailer = Mailer()
    send_approved([outreach_id], db, mailer)
    result = sync_replies(db, mailer)

    assert result["human_replies"] == 1
    assert db.get_outreach(outreach_id)["status"] == "replied"


def test_daily_limit_is_enforced_before_mailer_creation(tmp_path, monkeypatch):
    db, outreach_id = seeded_database(tmp_path)
    monkeypatch.setattr(db, "sent_today", lambda: 10)

    with pytest.raises(ValueError, match="Daily limit"):
        send_approved([outreach_id], db, mailer=object())


def test_persistent_rate_limit_resets_after_window(tmp_path):
    db = Database(tmp_path / "agent.db")
    start = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)

    assert db.acquire_rate_limit("provider:test", 2, 60, now=start) == (True, 0)
    assert db.acquire_rate_limit("provider:test", 2, 60, now=start) == (True, 0)
    allowed, retry_after = db.acquire_rate_limit("provider:test", 2, 60, now=start)
    assert allowed is False
    assert retry_after == 61
    assert db.acquire_rate_limit(
        "provider:test", 2, 60, now=start + timedelta(seconds=61)
    ) == (True, 0)


def test_send_rate_limit_cannot_be_bypassed_by_stale_sent_count(tmp_path, monkeypatch):
    db, outreach_id = seeded_database(tmp_path)
    monkeypatch.setattr(db, "sent_today", lambda: 0)
    db.acquire_rate_limit("gmail:send", 10, 86_400, units=10)

    result = send_approved([outreach_id], db, mailer=object())

    assert result["sent"] == []
    assert "Send rate limit reached" in result["skipped"][0]["reason"]


def test_queue_requires_application_to_be_marked(tmp_path):
    db = Database(tmp_path / "agent.db")
    db.add_source("Acme", "lever", "acme", "https://jobs.lever.co/acme")
    source = db.list_sources()[0]
    db.upsert_jobs(
        source["id"],
        [{
            "external_id": "role", "title": "Engineer", "location": "Remote",
            "description": "Build APIs", "job_url": "https://jobs.lever.co/acme/role",
            "contact_email": "jobs@acme.test", "contact_source_url": "https://acme.test/jobs",
        }],
    )
    job = db.open_jobs()[0]

    with pytest.raises(ValueError, match="Mark the job as applied"):
        db.queue_outreach(job["id"], "jobs@acme.test", "https://acme.test/jobs", "Role", "Hello")


def test_jooble_discovery_uses_profile_titles(tmp_path, monkeypatch):
    db = Database(tmp_path / "agent.db")
    db.save_profile(
        "Ada", "Python developer", {"desired_titles": ["Python Engineer"], "minimum_score": 70}
    )
    monkeypatch.setenv("JOOBLE_API_KEY", "key")
    calls = []

    def fake_jobs(_key, title, location):
        calls.append((title, location))
        return [{
            "external_id": f"{location}-1", "company_name": "Acme", "origin_provider": "jooble",
            "title": title, "location": location, "description": "Python APIs",
            "job_url": f"https://jooble.test/{location}", "description_quality": "snippet",
        }]

    monkeypatch.setattr("automation.jooble_jobs", fake_jobs)

    result = discover(db)

    assert calls == [("Python Engineer", "India"), ("Python Engineer", "Remote")]
    assert result["jooble_jobs"] == 2
    assert {job["origin_provider"] for job in db.open_jobs()} == {"jooble"}


def test_hunter_contact_is_cached_for_other_job_at_same_company(tmp_path):
    db = Database(tmp_path / "agent.db")
    db.add_source("Acme", "lever", "acme", "https://jobs.lever.co/acme")
    source = db.list_sources()[0]
    db.upsert_jobs(
        source["id"],
        [
            {"external_id": "1", "title": "Python Engineer", "location": "Remote", "description": "Python", "job_url": "https://jobs.lever.co/acme/1"},
            {"external_id": "2", "title": "API Engineer", "location": "Remote", "description": "APIs", "job_url": "https://jobs.lever.co/acme/2"},
        ],
    )
    for job in db.open_jobs():
        db.save_match(
            job["id"],
            {"score": 90, "decision": "qualified", "evidence": [], "missing_requirements": [],
             "rejection_reason": "", "job_fingerprint": job["fingerprint"]},
            "model",
        )
        db.mark_applied(job["id"])

    class Hunter:
        def __init__(self):
            self.calls = 0

        def find_recruiting_contacts(self, company):
            self.calls += 1
            return [{
                "email": "recruiter@acme.test", "name": "Rina", "position": "Recruiter",
                "source_kind": "hunter", "confidence": 95, "verification_status": "valid",
                "sources": ["https://acme.test/team"], "selection_score": 110,
            }]

    hunter = Hunter()
    result = enrich_applied_jobs(db, hunter)

    assert result == {"attempted": 2, "contacts_found": 2, "skipped_quota": 0, "skipped_config": 0}
    assert hunter.calls == 1
    assert db.hunter_usage() == 1
