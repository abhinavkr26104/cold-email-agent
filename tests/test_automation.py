from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from automation import discover, enrich_applied_jobs
from matching import hard_filter
from storage import Database


def test_hard_filter_rejects_exclusion_before_model():
    job = {"title": "Senior Python Engineer", "location": "Remote", "employment_type": "Full-time", "description": ""}
    assert hard_filter(job, {"excluded_keywords": ["senior"]}) == "Excluded keyword: senior"


def test_hard_filter_requires_configured_role_keyword():
    job = {"title": "Python Engineer", "location": "Remote", "employment_type": "Full-time", "description": "APIs"}
    assert hard_filter(job, {"required_keywords": ["django"]}) == "Required keyword is missing: django"


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
