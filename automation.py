"""Orchestration and command-line entry points for scheduled local automation."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from contacts import HunterClient
from discovery import EMAIL_PATTERN, fetch_source, jooble_jobs
from gmail_provider import GmailProvider
from matching import FitAssessment, RoleMatcher, hard_filter
from storage import Database, utcnow
from workflow import ColdEmailInput, generate_cold_email


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


DAILY_SEND_LIMIT = _positive_env_int("DAILY_SEND_LIMIT", 10)
DAILY_ENRICHMENT_LIMIT = _positive_env_int("DAILY_ENRICHMENT_LIMIT", 2)
MONTHLY_HUNTER_LIMIT = _positive_env_int("MONTHLY_HUNTER_LIMIT", 40)
DAILY_MODEL_SCORING_LIMIT = _positive_env_int("DAILY_MODEL_SCORING_LIMIT", 50)
DAILY_DRAFT_LIMIT = _positive_env_int("DAILY_DRAFT_LIMIT", 10)
JOOBLE_REQUESTS_PER_MINUTE = _positive_env_int("JOOBLE_REQUESTS_PER_MINUTE", 10)
JOOBLE_REQUESTS_PER_DAY = _positive_env_int("JOOBLE_REQUESTS_PER_DAY", 100)
ATS_REQUESTS_PER_MINUTE = _positive_env_int("ATS_REQUESTS_PER_MINUTE", 30)
HUNTER_REQUESTS_PER_MINUTE = _positive_env_int("HUNTER_REQUESTS_PER_MINUTE", 5)
INBOX_SYNCS_PER_HOUR = _positive_env_int("INBOX_SYNCS_PER_HOUR", 12)


def _reserve(db: Database, scope: str, limit: int, seconds: int) -> tuple[bool, int]:
    return db.acquire_rate_limit(scope, limit, seconds)


def discover(db: Database) -> dict[str, int]:
    totals = {"sources": 0, "jobs": 0, "failed_sources": 0, "jooble_jobs": 0, "jooble_skipped": 0}
    for source in db.list_sources():
        if not source["enabled"]:
            continue
        totals["sources"] += 1
        allowed, _ = _reserve(db, f"ats:{source['provider']}", ATS_REQUESTS_PER_MINUTE, 60)
        if not allowed:
            totals["failed_sources"] += 1
            continue
        try:
            jobs = fetch_source(source["provider"], source["board_token"])
            totals["jobs"] += db.upsert_jobs(source["id"], jobs)
        except Exception:
            totals["failed_sources"] += 1
    profile = db.get_profile()
    jooble_key = os.getenv("JOOBLE_API_KEY", "").strip()
    titles = (profile or {}).get("preferences", {}).get("desired_titles", [])
    if jooble_key and titles:
        source_id = db.ensure_jooble_source()
        aggregated: dict[str, dict[str, Any]] = {}
        for title in [str(value).strip() for value in titles[:5] if str(value).strip()]:
            for location in ("India", "Remote"):
                minute_ok, _ = _reserve(db, "jooble:minute", JOOBLE_REQUESTS_PER_MINUTE, 60)
                if not minute_ok:
                    totals["jooble_skipped"] += 1
                    continue
                daily_ok, _ = _reserve(db, "jooble:day", JOOBLE_REQUESTS_PER_DAY, 86_400)
                if not daily_ok:
                    totals["jooble_skipped"] += 1
                    continue
                try:
                    for job in jooble_jobs(jooble_key, title, location):
                        aggregated[str(job["external_id"])] = job
                except Exception:
                    totals["failed_sources"] += 1
        totals["jooble_jobs"] = db.upsert_jobs(source_id, list(aggregated.values()))
        totals["jobs"] += totals["jooble_jobs"]
    else:
        totals["jooble_skipped"] = 1
    return totals


def evaluate_matches(db: Database, matcher: RoleMatcher | None = None) -> dict[str, int]:
    profile = db.get_profile()
    if not profile:
        raise RuntimeError("Save a candidate profile before evaluating roles.")
    evaluator = matcher or RoleMatcher()
    totals = {"evaluated": 0, "qualified": 0}
    for job in db.jobs_for_evaluation():
        rejection = hard_filter(job, profile["preferences"])
        if rejection:
            result = FitAssessment(
                score=0, decision="rejected", evidence=[], missing_requirements=[],
                rejection_reason=rejection,
            )
        else:
            allowed, _ = _reserve(db, "model:scoring", DAILY_MODEL_SCORING_LIMIT, 86_400)
            if not allowed:
                continue
            result = evaluator.evaluate(job, profile)
        payload = result.model_dump()
        payload["job_fingerprint"] = job["fingerprint"]
        db.save_match(job["id"], payload, evaluator.model_name)
        totals["evaluated"] += 1
        totals["qualified"] += result.decision == "qualified"
    return totals


def enrich_applied_jobs(db: Database, hunter: HunterClient | None = None) -> dict[str, int]:
    """Enrich the highest-ranked matches (legacy name retained for CLI callers)."""
    totals = {"attempted": 0, "contacts_found": 0, "skipped_quota": 0, "skipped_config": 0}
    remaining_month = MONTHLY_HUNTER_LIMIT - db.hunter_usage()
    remaining_today = DAILY_ENRICHMENT_LIMIT - db.enrichment_attempts_today()
    allowance = max(0, min(remaining_month, remaining_today))
    if allowance == 0:
        totals["skipped_quota"] = 1
        return totals
    api_key = os.getenv("HUNTER_API_KEY", "").strip()
    if hunter is None and not api_key:
        totals["skipped_config"] = 1
        return totals
    client = hunter or HunterClient(api_key)
    for job in db.jobs_for_enrichment(minimum_score=0, limit=allowance):
        totals["attempted"] += 1
        cached = db.cached_company_contact(job["company_name"])
        if cached:
            db.save_contact(job["id"], cached)
            db.record_enrichment_attempt(job["id"], "cached", 0)
            totals["contacts_found"] += 1
            continue
        try:
            allowed, _ = _reserve(db, "hunter:minute", HUNTER_REQUESTS_PER_MINUTE, 60)
            if not allowed:
                totals["skipped_quota"] += 1
                continue
            candidates = client.find_recruiting_contacts(job["company_name"])
            if candidates:
                contact = dict(candidates[0])
                contact.pop("selection_score", None)
                db.save_contact(job["id"], contact)
                totals["contacts_found"] += 1
                db.record_enrichment_attempt(job["id"], "contact_found", 1)
            else:
                db.record_enrichment_attempt(job["id"], "no_contact", 1)
        except Exception:
            db.record_enrichment_attempt(job["id"], "failed", 0)
    return totals


def find_contact_for_job(
    db: Database, job_id: int, hunter: HunterClient | None = None,
) -> dict[str, Any] | None:
    """Find one sourced contact while enforcing all Hunter limits."""

    existing = db.selected_contact(job_id)
    if existing:
        return existing
    job = next((item for item in db.open_jobs() if item["id"] == job_id), None)
    if not job:
        raise ValueError("Only an open role can be enriched.")
    if db.hunter_usage() >= MONTHLY_HUNTER_LIMIT:
        raise ValueError("Hunter monthly quota is exhausted.")
    if db.enrichment_attempts_today() >= DAILY_ENRICHMENT_LIMIT:
        raise ValueError("Hunter daily quota is exhausted.")
    cached = db.cached_company_contact(job["company_name"])
    if cached:
        db.save_contact(job_id, cached)
        db.record_enrichment_attempt(job_id, "cached", 0)
        return db.selected_contact(job_id)
    api_key = os.getenv("HUNTER_API_KEY", "").strip()
    if hunter is None and not api_key:
        raise ValueError("Hunter is not configured.")
    allowed, retry_after = _reserve(db, "hunter:minute", HUNTER_REQUESTS_PER_MINUTE, 60)
    if not allowed:
        raise ValueError(f"Hunter minute limit reached; retry in {retry_after} seconds.")
    client = hunter or HunterClient(api_key)
    try:
        candidates = client.find_recruiting_contacts(job["company_name"])
    except Exception:
        db.record_enrichment_attempt(job_id, "failed", 0)
        raise
    if not candidates:
        db.record_enrichment_attempt(job_id, "no_contact", 1)
        return None
    contact = dict(candidates[0])
    contact.pop("selection_score", None)
    db.save_contact(job_id, contact)
    db.record_enrichment_attempt(job_id, "contact_found", 1)
    return db.selected_contact(job_id)


def _split_generated_email(email: str, title: str, company: str) -> tuple[str, str]:
    lines = email.strip().splitlines()
    if lines and re.match(r"^subject\s*:", lines[0], re.I):
        subject = lines.pop(0).split(":", 1)[1].strip()
        while lines and not lines[0].strip():
            lines.pop(0)
        return subject, "\n".join(lines).strip()
    return f"Interest in {title} at {company}", email.strip()


def prepare_drafts(
    db: Database, limit: int = 5, *, generator=generate_cold_email,
    force_job_id: int | None = None,
) -> int:
    """Create drafts for the top qualified roles, with or without a contact."""

    profile = db.get_profile()
    if not profile:
        raise RuntimeError("Save a candidate profile before preparing drafts.")
    prepared = 0
    matches = db.top_qualified_jobs(limit=50 if force_job_id is not None else max(limit, 5))
    if force_job_id is not None:
        matches = [match for match in matches if match["id"] == force_job_id]
        if not matches:
            raise ValueError("A draft can only be generated for a top qualified open role.")
    for match in matches[:limit]:
        if match.get("draft_id") and force_job_id is None:
            previous_contact = json.loads(match.get("draft_contact_snapshot_json") or "{}")
            contact_arrived = bool(match.get("selected_contact_email") and not previous_contact.get("email"))
            if not contact_arrived:
                continue
            if match.get("draft_edited"):
                db.mark_draft_stale(match["id"])
                continue
        allowed, _ = _reserve(db, "model:drafting", DAILY_DRAFT_LIMIT, 86_400)
        if not allowed:
            break
        email = generator(
            ColdEmailInput(
                candidate_name=profile["candidate_name"],
                company_name=match["company_name"],
                candidate_profile=profile["candidate_profile"],
                job_description=match["description"],
                recipient_name=match["contact_name"] or "",
                recipient_position=match["contact_position"] or "",
                role_title=match["title"],
                # The prompt may claim an application only after this is set.
                applied_at=match["applied_at"] or "",
            )
        )
        subject, body = _split_generated_email(email, match["title"], match["company_name"])
        contact = db.selected_contact(match["id"])
        db.save_draft(
            match["id"], subject, body,
            profile_snapshot={
                "candidate_name": profile["candidate_name"],
                "candidate_profile": profile["candidate_profile"],
                "preferences": profile["preferences"],
            },
            job_snapshot={
                "title": match["title"], "company_name": match["company_name"],
                "description": match["description"], "fingerprint": match["fingerprint"],
                "applied_at": match["applied_at"],
            },
            contact_snapshot=contact,
            model_name=match.get("model_name") or os.getenv("GROQ_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2")),
            force=force_job_id is not None,
        )
        prepared += 1
    return prepared


def personalize_draft(
    db: Database, draft_id: int, *, generator=generate_cold_email,
) -> dict[str, Any]:
    draft = db.get_draft(draft_id)
    if not draft:
        raise ValueError("Unknown draft.")
    if not draft.get("selected_contact_email"):
        raise ValueError("No selected contact is available for personalization.")
    prepare_drafts(db, limit=1, generator=generator, force_job_id=draft["job_id"])
    return db.get_draft(draft_id) or {}


def prepare_queue(db: Database, limit: int = DAILY_SEND_LIMIT) -> int:
    profile = db.get_profile()
    if not profile:
        raise RuntimeError("Save a candidate profile before preparing outreach.")
    prepared = 0
    for match in db.ranked_matches():
        if prepared >= limit:
            break
        if (
            match["decision"] != "qualified"
            or match["application_status"] != "applied"
            or not match["selected_contact_email"]
            or match["outreach_id"]
        ):
            continue
        allowed, _ = _reserve(db, "model:drafting", DAILY_DRAFT_LIMIT, 86_400)
        if not allowed:
            break
        email = generate_cold_email(
            ColdEmailInput(
                candidate_name=profile["candidate_name"],
                company_name=match["company_name"],
                candidate_profile=profile["candidate_profile"],
                job_description=match["description"],
                recipient_name=match["contact_name"] or "",
                recipient_position=match["contact_position"] or "",
                role_title=match["title"],
                applied_at=match["applied_at"] or "",
            )
        )
        subject, body = _split_generated_email(email, match["title"], match["company_name"])
        db.queue_outreach(
            match["id"], match["selected_contact_email"],
            (json.loads(match["contact_sources_json"] or "[]") or [match["contact_source_url"]])[0],
            subject, body,
        )
        prepared += 1
    return prepared


def run_discovery(db: Database | None = None) -> dict[str, Any]:
    database = db or Database()
    started = utcnow()
    try:
        result: dict[str, Any] = discover(database)
        result.update(evaluate_matches(database))
        # Published contacts already selected during upsert win; Hunter fills
        # only the remaining top matches and is still limited to two per day.
        result["enrichment"] = enrich_applied_jobs(database)
        result["drafts_prepared"] = prepare_drafts(database, limit=5)
        database.record_run("discover", "success", str(result), started)
        return result
    except Exception as error:
        database.record_run("discover", "failed", str(error), started)
        raise


def send_approved(
    outreach_ids: list[int], db: Database | None = None, mailer: GmailProvider | None = None
) -> dict[str, Any]:
    database = db or Database()
    unique_ids = list(dict.fromkeys(outreach_ids))
    remaining = DAILY_SEND_LIMIT - database.sent_today()
    if len(unique_ids) > remaining:
        raise ValueError(f"Daily limit allows only {remaining} more message(s).")
    gmail = mailer or GmailProvider()
    result: dict[str, Any] = {"sent": [], "skipped": [], "failed": []}
    for outreach_id in unique_ids:
        row = database.get_outreach(outreach_id)
        if not row or row["status"] != "queued":
            result["skipped"].append({"id": outreach_id, "reason": "Not queued"})
            continue
        if row["job_status"] != "open":
            result["skipped"].append({"id": outreach_id, "reason": "Role is closed"})
            continue
        if row["application_status"] not in {"applied", "outreach_queued"}:
            result["skipped"].append({"id": outreach_id, "reason": "Job is not marked applied"})
            continue
        if not EMAIL_PATTERN.fullmatch(row["recipient"]):
            result["skipped"].append({"id": outreach_id, "reason": "Invalid recipient address"})
            continue
        if not row["subject"].strip() or not row["body"].strip():
            result["skipped"].append({"id": outreach_id, "reason": "Subject and message are required"})
            continue
        if row["recipient"].lower() != (row["contact_email"] or "").lower() or not row["recipient_source_url"]:
            result["skipped"].append({"id": outreach_id, "reason": "Contact provenance changed"})
            continue
        allowed, retry_after = _reserve(database, "gmail:send", DAILY_SEND_LIMIT, 86_400)
        if not allowed:
            result["skipped"].append(
                {"id": outreach_id, "reason": f"Send rate limit reached; retry in {retry_after} seconds"}
            )
            continue
        try:
            sent = gmail.send(row["recipient"], row["subject"], row["body"])
            database.mark_sent(outreach_id, sent["message_id"], sent["thread_id"])
            result["sent"].append(outreach_id)
        except Exception as error:
            database.mark_send_error(outreach_id, str(error))
            result["failed"].append({"id": outreach_id, "error": str(error)})
    return result


def send_drafts(
    draft_ids: list[int], db: Database | None = None, mailer: GmailProvider | None = None,
) -> dict[str, Any]:
    """Freeze approved drafts into delivery snapshots and send them."""

    database = db or Database()
    outreach_ids: list[int] = []
    rejected: list[dict[str, Any]] = []
    for draft_id in list(dict.fromkeys(draft_ids)):
        draft = database.get_draft(draft_id)
        if not draft:
            rejected.append({"id": draft_id, "reason": "Unknown draft"})
            continue
        if draft["job_status"] != "open":
            rejected.append({"id": draft_id, "reason": "Role is closed"})
            continue
        if draft["application_status"] != "applied":
            rejected.append({"id": draft_id, "reason": "Job is not marked applied"})
            continue
        if not draft["subject"].strip() or not draft["body"].strip():
            rejected.append({"id": draft_id, "reason": "Subject and message are required"})
            continue
        try:
            outreach_ids.append(database.create_outreach_from_draft(draft_id))
        except ValueError as error:
            rejected.append({"id": draft_id, "reason": str(error)})
    if not outreach_ids:
        return {"sent": [], "skipped": rejected, "failed": []}
    result = send_approved(outreach_ids, database, mailer)
    result["skipped"] = rejected + result["skipped"]
    return result


def sync_replies(
    db: Database | None = None, mailer: GmailProvider | None = None, *, interactive: bool = False
) -> dict[str, int]:
    database = db or Database()
    started = utcnow()
    totals = {"checked": 0, "human_replies": 0, "automated_replies": 0}
    try:
        allowed, retry_after = _reserve(database, "gmail:sync", INBOX_SYNCS_PER_HOUR, 3_600)
        if not allowed:
            totals["rate_limited"] = retry_after
            database.record_run("sync-replies", "rate_limited", str(totals), started)
            return totals
        gmail = mailer or GmailProvider(interactive=interactive)
        for outreach in database.tracked_outreach():
            if outreach["status"] not in {"sent", "auto_reply"}:
                continue
            totals["checked"] += 1
            reply = gmail.find_reply(outreach["gmail_thread_id"], outreach["sent_at"])
            if not reply:
                continue
            database.mark_reply(outreach["id"], reply["automated"], reply["received_at"])
            gmail.label_reply(outreach["gmail_thread_id"])
            totals["automated_replies" if reply["automated"] else "human_replies"] += 1
        database.record_run("sync-replies", "success", str(totals), started)
        return totals
    except Exception as error:
        database.record_run("sync-replies", "failed", str(error), started)
        raise


def install_scheduler() -> None:
    script = str(Path(__file__).resolve())
    python = sys.executable
    tasks = [
        ("ColdEmailAgent-Discover", "DAILY", "1", "08:00", "discover"),
        ("ColdEmailAgent-SyncReplies", "MINUTE", "15", None, "sync-replies"),
    ]
    for name, schedule, modifier, start, command in tasks:
        task_command = f'"{python}" "{script}" {command}'
        args = ["schtasks", "/Create", "/F", "/TN", name, "/TR", task_command, "/SC", schedule, "/MO", modifier]
        if start:
            args.extend(["/ST", start])
        subprocess.run(args, check=True)
        settings_command = (
            f"$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable; "
            f"Set-ScheduledTask -TaskName '{name}' -Settings $settings | Out-Null"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", settings_command], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["discover", "sync-replies", "install-scheduler"])
    args = parser.parse_args()
    if args.command == "discover":
        print(run_discovery())
    elif args.command == "sync-replies":
        print(sync_replies())
    else:
        install_scheduler()
        print("Windows scheduled tasks installed.")


if __name__ == "__main__":
    main()
