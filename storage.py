"""SQLite persistence for discovery, matching, outreach, and mailbox state."""

from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = Path(os.getenv("COLD_EMAIL_DB", str(PROJECT_ROOT / "data" / "cold_email_agent.db")))


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    candidate_name TEXT NOT NULL,
    candidate_profile TEXT NOT NULL,
    preferences_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    provider TEXT NOT NULL CHECK(provider IN ('greenhouse', 'lever', 'jooble')),
    board_token TEXT NOT NULL,
    board_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(provider, board_token)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    company_name TEXT NOT NULL DEFAULT '',
    origin_provider TEXT NOT NULL DEFAULT '',
    canonical_key TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    employment_type TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    job_url TEXT NOT NULL,
    apply_url TEXT NOT NULL DEFAULT '',
    contact_email TEXT,
    contact_source_url TEXT,
    fingerprint TEXT NOT NULL,
    posted_at TEXT,
    description_quality TEXT NOT NULL DEFAULT 'full',
    application_status TEXT NOT NULL DEFAULT 'discovered',
    applied_at TEXT,
    eligibility_warning TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(source_id, external_id)
);
CREATE TABLE IF NOT EXISTS matches (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    score INTEGER NOT NULL,
    decision TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    missing_json TEXT NOT NULL,
    rejection_reason TEXT,
    model_name TEXT NOT NULL,
    job_fingerprint TEXT NOT NULL,
    evaluated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outreach (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    recipient_source_url TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    contact_name TEXT NOT NULL DEFAULT '',
    contact_position TEXT NOT NULL DEFAULT '',
    contact_confidence INTEGER,
    contact_source_kind TEXT NOT NULL DEFAULT '',
    contact_sources_json TEXT NOT NULL DEFAULT '[]',
    applied_at_snapshot TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    gmail_message_id TEXT,
    gmail_thread_id TEXT,
    sent_at TEXT,
    replied_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, recipient)
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
    job_snapshot_json TEXT NOT NULL DEFAULT '{}',
    contact_snapshot_json TEXT NOT NULL DEFAULT '{}',
    edited INTEGER NOT NULL DEFAULT 0,
    stale INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    model_name TEXT NOT NULL DEFAULT '',
    generated_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_name TEXT NOT NULL,
    company_name TEXT NOT NULL,
    role_title TEXT NOT NULL DEFAULT '',
    recipient_name TEXT NOT NULL DEFAULT '',
    recipient_position TEXT NOT NULL DEFAULT '',
    candidate_profile TEXT NOT NULL,
    job_description TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    result_json TEXT NOT NULL DEFAULT '{}',
    errors_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL,
    confidence INTEGER,
    verification_status TEXT,
    sources_json TEXT NOT NULL DEFAULT '[]',
    selected INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT NOT NULL,
    UNIQUE(job_id, email)
);
CREATE TABLE IF NOT EXISTS api_usage (
    provider TEXT NOT NULL,
    period TEXT NOT NULL,
    units INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(provider, period)
);
CREATE TABLE IF NOT EXISTS rate_limit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    units INTEGER NOT NULL DEFAULT 1,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_events_scope_time
ON rate_limit_events(scope, occurred_at);
CREATE TABLE IF NOT EXISTS enrichment_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    credits INTEGER NOT NULL DEFAULT 0,
    attempted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_job_key(company: str, title: str, location: str) -> str:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return "|".join((normalize(company), normalize(title), normalize(location)))


class Database:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    def _migrate(self, db: sqlite3.Connection) -> None:
        """Upgrade databases created by the original two-provider release."""

        source_sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
        ).fetchone()["sql"]
        if "'jooble'" not in source_sql:
            db.commit()
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("PRAGMA legacy_alter_table=ON")
            db.execute("ALTER TABLE sources RENAME TO sources_legacy")
            db.execute(
                """CREATE TABLE sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                provider TEXT NOT NULL CHECK(provider IN ('greenhouse','lever','jooble')),
                board_token TEXT NOT NULL, board_url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                UNIQUE(provider, board_token))"""
            )
            db.execute("INSERT INTO sources SELECT * FROM sources_legacy")
            db.execute("DROP TABLE sources_legacy")
            db.execute("PRAGMA legacy_alter_table=OFF")
            db.execute("PRAGMA foreign_keys=ON")

        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
        additions = {
            "company_name": "TEXT NOT NULL DEFAULT ''",
            "origin_provider": "TEXT NOT NULL DEFAULT ''",
            "canonical_key": "TEXT NOT NULL DEFAULT ''",
            "posted_at": "TEXT",
            "description_quality": "TEXT NOT NULL DEFAULT 'full'",
            "application_status": "TEXT NOT NULL DEFAULT 'discovered'",
            "applied_at": "TEXT",
            "eligibility_warning": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")

        outreach_columns = {row["name"] for row in db.execute("PRAGMA table_info(outreach)")}
        outreach_additions = {
            "contact_name": "TEXT NOT NULL DEFAULT ''",
            "contact_position": "TEXT NOT NULL DEFAULT ''",
            "contact_confidence": "INTEGER",
            "contact_source_kind": "TEXT NOT NULL DEFAULT ''",
            "contact_sources_json": "TEXT NOT NULL DEFAULT '[]'",
            "applied_at_snapshot": "TEXT",
        }
        for name, declaration in outreach_additions.items():
            if name not in outreach_columns:
                db.execute(f"ALTER TABLE outreach ADD COLUMN {name} {declaration}")

        rows = db.execute(
            """SELECT jobs.id,jobs.title,jobs.location,sources.company_name,sources.provider
            FROM jobs JOIN sources ON sources.id=jobs.source_id
            WHERE jobs.canonical_key='' OR jobs.company_name='' OR jobs.origin_provider=''"""
        ).fetchall()
        for row in rows:
            db.execute(
                "UPDATE jobs SET company_name=?,origin_provider=?,canonical_key=? WHERE id=?",
                (
                    row["company_name"], row["provider"],
                    canonical_job_key(row["company_name"], row["title"], row["location"]), row["id"],
                ),
            )
        db.execute(
            """INSERT OR IGNORE INTO contacts(
            job_id,email,source_kind,sources_json,selected,discovered_at)
            SELECT id,contact_email,'published',
            CASE WHEN contact_source_url IS NULL THEN '[]' ELSE json_array(contact_source_url) END,
            1,first_seen_at FROM jobs WHERE contact_email IS NOT NULL AND contact_email!=''"""
        )
        # Queued outreach in pre-React databases was both an editable draft and
        # a delivery record. Preserve its content in the new draft store while
        # keeping Gmail history immutable in outreach.
        db.execute(
            """INSERT OR IGNORE INTO drafts(
            job_id,subject,body,contact_snapshot_json,edited,stale,status,model_name,
            generated_at,updated_at)
            SELECT job_id,subject,body,
            json_object('email',recipient,'source_url',recipient_source_url,
                        'name',contact_name,'position',contact_position,
                        'confidence',contact_confidence,'source_kind',contact_source_kind,
                        'sources',json(contact_sources_json)),
            1,0,CASE WHEN status='queued' THEN 'draft' ELSE status END,'legacy',created_at,created_at
            FROM outreach"""
        )
        # Runs left active by a terminated process are explicit and recoverable.
        db.execute(
            """UPDATE task_runs SET status='interrupted',stage='interrupted',finished_at=?,updated_at=?
            WHERE status IN ('queued','running')""",
            (utcnow(), utcnow()),
        )
        db.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','4')"
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def save_profile(self, name: str, profile: str, preferences: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO profile VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET candidate_name=excluded.candidate_name,
                candidate_profile=excluded.candidate_profile,
                preferences_json=excluded.preferences_json, updated_at=excluded.updated_at""",
                (name.strip(), profile.strip(), json.dumps(preferences), utcnow()),
            )

    def get_profile(self) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM profile WHERE id=1").fetchone()
        if not row:
            return None
        result = dict(row)
        result["preferences"] = json.loads(result.pop("preferences_json"))
        return result

    def create_manual_draft(self, values: dict[str, str]) -> dict[str, Any]:
        now = utcnow()
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO manual_drafts(
                candidate_name,company_name,role_title,recipient_name,recipient_position,
                candidate_profile,job_description,body,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    values["candidate_name"].strip(), values["company_name"].strip(),
                    values.get("role_title", "").strip(), values.get("recipient_name", "").strip(),
                    values.get("recipient_position", "").strip(), values["candidate_profile"].strip(),
                    values["job_description"].strip(), values["body"].strip(), now, now,
                ),
            )
            row = db.execute("SELECT * FROM manual_drafts WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def list_manual_drafts(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM manual_drafts ORDER BY updated_at DESC, id DESC").fetchall()
        return [dict(row) for row in rows]

    def update_manual_draft(self, draft_id: int, body: str) -> dict[str, Any]:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE manual_drafts SET body=?,updated_at=? WHERE id=?",
                (body.strip(), utcnow(), draft_id),
            ).rowcount
            if not changed:
                raise ValueError("Saved draft not found.")
            row = db.execute("SELECT * FROM manual_drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(row)

    def delete_manual_draft(self, draft_id: int) -> None:
        with self.connect() as db:
            if not db.execute("DELETE FROM manual_drafts WHERE id=?", (draft_id,)).rowcount:
                raise ValueError("Saved draft not found.")

    def add_source(self, company: str, provider: str, token: str, url: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO sources(company_name,provider,board_token,board_url,created_at) VALUES(?,?,?,?,?)",
                (company.strip(), provider, token, url, utcnow()),
            )

    def ensure_jooble_source(self) -> int:
        self.add_source("Jooble", "jooble", "automatic", "https://jooble.org")
        with self.connect() as db:
            return int(
                db.execute(
                    "SELECT id FROM sources WHERE provider='jooble' AND board_token='automatic'"
                ).fetchone()["id"]
            )

    def list_sources(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM sources WHERE provider!='jooble' ORDER BY company_name"
                )
            ]

    def set_source_enabled(self, source_id: int, enabled: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE sources SET enabled=? WHERE id=?", (int(enabled), source_id))

    def update_source(self, source_id: int, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"company_name", "enabled"}
        changes = {key: value for key, value in values.items() if key in allowed}
        if not changes:
            raise ValueError("No supported source fields were supplied.")
        assignments = ",".join(f"{key}=?" for key in changes)
        with self.connect() as db:
            changed = db.execute(
                f"UPDATE sources SET {assignments} WHERE id=?",
                (*[int(value) if key == "enabled" else str(value).strip() for key, value in changes.items()], source_id),
            ).rowcount
            if not changed:
                raise ValueError("Unknown source.")
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(row)

    def upsert_jobs(self, source_id: int, jobs: list[dict[str, Any]]) -> int:
        now = utcnow()
        seen: list[str] = []
        with self.connect() as db:
            source = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if not source:
                raise ValueError("Unknown job source.")
            for job in jobs:
                seen.append(str(job["external_id"]))
                company_name = job.get("company_name") or source["company_name"]
                provider = job.get("origin_provider") or source["provider"]
                canonical_key = canonical_job_key(
                    company_name, job["title"], job.get("location", "")
                )
                fingerprint = hashlib.sha256(
                    "\0".join(
                        [job["title"], job.get("location", ""), job.get("employment_type", ""), job["description"]]
                    ).encode("utf-8")
                ).hexdigest()
                db.execute(
                    """INSERT INTO jobs(source_id,external_id,company_name,origin_provider,canonical_key,
                    title,location,employment_type,description,job_url,apply_url,contact_email,
                    contact_source_url,fingerprint,posted_at,description_quality,eligibility_warning,
                    status,first_seen_at,last_seen_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)
                    ON CONFLICT(source_id,external_id) DO UPDATE SET title=excluded.title,
                    company_name=excluded.company_name,origin_provider=excluded.origin_provider,
                    canonical_key=excluded.canonical_key,
                    location=excluded.location,employment_type=excluded.employment_type,
                    description=excluded.description,job_url=excluded.job_url,apply_url=excluded.apply_url,
                    contact_email=excluded.contact_email,contact_source_url=excluded.contact_source_url,
                    fingerprint=excluded.fingerprint,posted_at=excluded.posted_at,
                    description_quality=excluded.description_quality,
                    eligibility_warning=excluded.eligibility_warning,
                    status='open',last_seen_at=excluded.last_seen_at""",
                    (
                        source_id, str(job["external_id"]), company_name, provider, canonical_key,
                        job["title"], job.get("location", ""),
                        job.get("employment_type", ""), job["description"], job["job_url"],
                        job.get("apply_url", ""), job.get("contact_email"),
                        job.get("contact_source_url"), fingerprint, job.get("posted_at"),
                        job.get("description_quality", "full"), job.get("eligibility_warning"), now, now,
                    ),
                )
                saved = db.execute(
                    "SELECT id FROM jobs WHERE source_id=? AND external_id=?",
                    (source_id, str(job["external_id"])),
                ).fetchone()
                if job.get("contact_email"):
                    db.execute("UPDATE contacts SET selected=0 WHERE job_id=?", (saved["id"],))
                    db.execute(
                        """INSERT INTO contacts(job_id,email,source_kind,sources_json,selected,discovered_at)
                        VALUES(?,?, 'published', ?,1,?) ON CONFLICT(job_id,email) DO UPDATE SET
                        source_kind='published',sources_json=excluded.sources_json,selected=1""",
                        (
                            saved["id"], job["contact_email"],
                            json.dumps([job["contact_source_url"]] if job.get("contact_source_url") else []), now,
                        ),
                    )
            if source["provider"] != "jooble" and seen:
                placeholders = ",".join("?" for _ in seen)
                db.execute(
                    f"UPDATE jobs SET status='closed' WHERE source_id=? AND external_id NOT IN ({placeholders})",
                    (source_id, *seen),
                )
            elif source["provider"] != "jooble":
                db.execute("UPDATE jobs SET status='closed' WHERE source_id=?", (source_id,))
            if source["provider"] == "jooble":
                db.execute(
                    """UPDATE jobs SET status='stale' WHERE source_id=? AND status='open'
                    AND datetime(last_seen_at) < datetime('now','-7 days')""",
                    (source_id,),
                )
            db.execute(
                """UPDATE jobs SET status='duplicate' WHERE origin_provider='jooble'
                AND status='open' AND EXISTS(
                    SELECT 1 FROM jobs direct WHERE direct.canonical_key=jobs.canonical_key
                    AND direct.origin_provider IN ('greenhouse','lever') AND direct.status='open')"""
            )
        return len(jobs)

    def open_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT jobs.*, jobs.company_name, jobs.origin_provider AS provider FROM jobs
                JOIN sources ON sources.id=jobs.source_id WHERE jobs.status='open'"""
            ).fetchall()
        return [dict(row) for row in rows]

    def jobs_for_evaluation(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT jobs.*,jobs.company_name,jobs.origin_provider AS provider FROM jobs
                JOIN sources ON sources.id=jobs.source_id
                LEFT JOIN matches ON matches.job_id=jobs.id
                WHERE jobs.status='open' AND (matches.job_id IS NULL OR matches.job_fingerprint != jobs.fingerprint)
                ORDER BY COALESCE(jobs.posted_at,jobs.first_seen_at) DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def save_match(self, job_id: int, result: dict[str, Any], model_name: str) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO matches(job_id,score,decision,evidence_json,missing_json,rejection_reason,
                model_name,job_fingerprint,evaluated_at) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET score=excluded.score,decision=excluded.decision,
                evidence_json=excluded.evidence_json,missing_json=excluded.missing_json,
                rejection_reason=excluded.rejection_reason,model_name=excluded.model_name,
                job_fingerprint=excluded.job_fingerprint,
                evaluated_at=excluded.evaluated_at""",
                (
                    job_id, result["score"], result["decision"], json.dumps(result.get("evidence", [])),
                    json.dumps(result.get("missing_requirements", [])), result.get("rejection_reason"),
                    model_name, result["job_fingerprint"], utcnow(),
                ),
            )

    def ranked_matches(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT jobs.*,jobs.company_name,matches.score,matches.decision,
                matches.evidence_json,matches.missing_json,matches.rejection_reason,
                outreach.id AS outreach_id,outreach.status AS outreach_status,
                contacts.id AS contact_id,contacts.email AS selected_contact_email,
                contacts.name AS contact_name,contacts.position AS contact_position,
                contacts.confidence AS contact_confidence,contacts.source_kind AS contact_source_kind,
                contacts.sources_json AS contact_sources_json
                FROM matches JOIN jobs ON jobs.id=matches.job_id
                JOIN sources ON sources.id=jobs.source_id
                LEFT JOIN outreach ON outreach.job_id=jobs.id
                LEFT JOIN contacts ON contacts.job_id=jobs.id AND contacts.selected=1
                WHERE jobs.status='open' ORDER BY matches.score DESC, jobs.first_seen_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def list_matches(
        self, *, status: str | None = None, minimum_score: int | None = None,
        company: str | None = None, limit: int = 25, offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["1=1"]
        values: list[Any] = []
        if status:
            clauses.append("jobs.status=?")
            values.append(status)
        if minimum_score is not None:
            clauses.append("matches.score>=?")
            values.append(minimum_score)
        if company:
            clauses.append("lower(jobs.company_name) LIKE lower(?)")
            values.append(f"%{company.strip()}%")
        where = " AND ".join(clauses)
        with self.connect() as db:
            total = int(db.execute(
                f"SELECT COUNT(*) count FROM matches JOIN jobs ON jobs.id=matches.job_id WHERE {where}",
                values,
            ).fetchone()["count"])
            rows = db.execute(
                f"""SELECT jobs.*,matches.score,matches.decision,matches.evidence_json,
                matches.missing_json,matches.rejection_reason,drafts.id AS draft_id,
                drafts.status AS draft_status,contacts.email AS selected_contact_email
                FROM matches JOIN jobs ON jobs.id=matches.job_id
                LEFT JOIN drafts ON drafts.job_id=jobs.id
                LEFT JOIN contacts ON contacts.job_id=jobs.id AND contacts.selected=1
                WHERE {where} ORDER BY matches.score DESC,jobs.first_seen_at DESC LIMIT ? OFFSET ?""",
                (*values, max(1, min(limit, 100)), max(0, offset)),
            ).fetchall()
        return [dict(row) for row in rows], total

    def top_qualified_jobs(self, limit: int = 5) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT jobs.*,matches.score,matches.model_name,
                contacts.email AS selected_contact_email,contacts.name AS contact_name,
                contacts.position AS contact_position,contacts.confidence AS contact_confidence,
                contacts.source_kind AS contact_source_kind,contacts.sources_json AS contact_sources_json,
                drafts.id AS draft_id,drafts.edited AS draft_edited,
                drafts.contact_snapshot_json AS draft_contact_snapshot_json
                FROM matches JOIN jobs ON jobs.id=matches.job_id
                LEFT JOIN contacts ON contacts.job_id=jobs.id AND contacts.selected=1
                LEFT JOIN drafts ON drafts.job_id=jobs.id
                WHERE jobs.status='open' AND matches.decision='qualified'
                ORDER BY matches.score DESC,jobs.first_seen_at DESC LIMIT ?""",
                (max(1, min(limit, 50)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_draft(
        self, job_id: int, subject: str, body: str, *, profile_snapshot: dict[str, Any],
        job_snapshot: dict[str, Any], contact_snapshot: dict[str, Any] | None,
        model_name: str, force: bool = False,
    ) -> int:
        now = utcnow()
        with self.connect() as db:
            current = db.execute("SELECT * FROM drafts WHERE job_id=?", (job_id,)).fetchone()
            if current and current["edited"] and not force:
                db.execute("UPDATE drafts SET stale=1,updated_at=? WHERE id=?", (now, current["id"]))
                return int(current["id"])
            db.execute(
                """INSERT INTO drafts(job_id,subject,body,profile_snapshot_json,job_snapshot_json,
                contact_snapshot_json,edited,stale,status,model_name,generated_at,updated_at)
                VALUES(?,?,?,?,?,?,0,0,'draft',?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET subject=excluded.subject,body=excluded.body,
                profile_snapshot_json=excluded.profile_snapshot_json,
                job_snapshot_json=excluded.job_snapshot_json,
                contact_snapshot_json=excluded.contact_snapshot_json,edited=0,stale=0,
                status=CASE WHEN drafts.status='sent' THEN drafts.status ELSE 'draft' END,
                model_name=excluded.model_name,generated_at=excluded.generated_at,
                updated_at=excluded.updated_at""",
                (job_id, subject.strip(), body.strip(), json.dumps(profile_snapshot),
                 json.dumps(job_snapshot), json.dumps(contact_snapshot or {}), model_name, now, now),
            )
            return int(db.execute("SELECT id FROM drafts WHERE job_id=?", (job_id,)).fetchone()["id"])

    def get_draft(self, draft_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT drafts.*,jobs.title,jobs.description,jobs.company_name,jobs.status AS job_status,
                jobs.application_status,jobs.applied_at,jobs.apply_url,jobs.job_url,
                contacts.email AS selected_contact_email,contacts.name AS contact_name,
                contacts.position AS contact_position,contacts.confidence AS contact_confidence,
                contacts.source_kind AS contact_source_kind,contacts.sources_json AS contact_sources_json
                FROM drafts JOIN jobs ON jobs.id=drafts.job_id
                LEFT JOIN contacts ON contacts.job_id=jobs.id AND contacts.selected=1
                WHERE drafts.id=?""",
                (draft_id,),
            ).fetchone()
        return dict(row) if row else None

    def approval_items(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT drafts.*,jobs.title,jobs.description,jobs.company_name,jobs.location,
                jobs.status AS job_status,jobs.application_status,jobs.applied_at,jobs.apply_url,
                jobs.job_url,matches.score,matches.evidence_json,matches.missing_json,
                contacts.email AS selected_contact_email,contacts.name AS contact_name,
                contacts.position AS contact_position,contacts.confidence AS contact_confidence,
                contacts.verification_status AS contact_verification_status,
                contacts.source_kind AS contact_source_kind,contacts.sources_json AS contact_sources_json,
                (SELECT COUNT(*) FROM outreach o WHERE o.job_id=jobs.id AND o.status IN ('sent','replied','auto_reply')) AS sent_count
                FROM drafts JOIN jobs ON jobs.id=drafts.job_id
                JOIN matches ON matches.job_id=jobs.id
                LEFT JOIN contacts ON contacts.job_id=jobs.id AND contacts.selected=1
                ORDER BY matches.score DESC,drafts.updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def edit_draft(self, draft_id: int, subject: str, body: str) -> dict[str, Any]:
        with self.connect() as db:
            changed = db.execute(
                """UPDATE drafts SET subject=?,body=?,edited=1,stale=0,updated_at=?
                WHERE id=? AND status!='sent'""",
                (subject.strip(), body.strip(), utcnow(), draft_id),
            ).rowcount
            if not changed:
                raise ValueError("Draft was not found or has already been sent.")
        return self.get_draft(draft_id) or {}

    def mark_draft_stale(self, job_id: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE drafts SET stale=1,updated_at=? WHERE job_id=? AND edited=1 AND status!='sent'",
                (utcnow(), job_id),
            )

    def selected_contact(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM contacts WHERE job_id=? AND selected=1", (job_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["sources"] = json.loads(result.pop("sources_json") or "[]")
        return result

    def create_outreach_from_draft(self, draft_id: int) -> int:
        draft = self.get_draft(draft_id)
        if not draft:
            raise ValueError("Unknown draft.")
        contact = self.selected_contact(draft["job_id"])
        if not contact or not contact.get("sources"):
            raise ValueError("A verified contact with source evidence is required.")
        if draft["application_status"] != "applied":
            raise ValueError("Mark the job as applied before approving outreach.")
        self.queue_outreach(
            draft["job_id"], contact["email"], contact["sources"][0],
            draft["subject"], draft["body"],
        )
        with self.connect() as db:
            return int(db.execute(
                "SELECT id FROM outreach WHERE job_id=? AND recipient=?",
                (draft["job_id"], contact["email"]),
            ).fetchone()["id"])

    def mark_draft_sent(self, job_id: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE drafts SET status='sent',stale=0,updated_at=? WHERE job_id=?",
                (utcnow(), job_id),
            )

    def create_task_run(self, run_id: str, kind: str = "discovery") -> dict[str, Any]:
        now = utcnow()
        with self.connect() as db:
            active = db.execute(
                "SELECT id FROM task_runs WHERE kind=? AND status IN ('queued','running') LIMIT 1",
                (kind,),
            ).fetchone()
            if active:
                raise RuntimeError(f"A {kind} run is already active: {active['id']}")
            db.execute(
                """INSERT INTO task_runs(id,kind,status,stage,progress,created_at,updated_at)
                VALUES(?,?,'queued','queued',0,?,?)""",
                (run_id, kind, now, now),
            )
        return self.get_task_run(run_id) or {}

    def update_task_run(
        self, run_id: str, *, status: str | None = None, stage: str | None = None,
        progress: int | None = None, result: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        fields = ["updated_at=?"]
        values: list[Any] = [utcnow()]
        for name, value in (("status", status), ("stage", stage), ("progress", progress)):
            if value is not None:
                fields.append(f"{name}=?")
                values.append(value)
        if result is not None:
            fields.append("result_json=?")
            values.append(json.dumps(result))
        if errors is not None:
            fields.append("errors_json=?")
            values.append(json.dumps(errors))
        if status == "running":
            fields.append("started_at=COALESCE(started_at,?)")
            values.append(utcnow())
        if status in {"completed", "failed", "partial", "interrupted"}:
            fields.append("finished_at=?")
            values.append(utcnow())
        values.append(run_id)
        with self.connect() as db:
            db.execute(f"UPDATE task_runs SET {','.join(fields)} WHERE id=?", values)

    def get_task_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json") or "{}")
        result["errors"] = json.loads(result.pop("errors_json") or "[]")
        return result

    def queue_outreach(self, job_id: int, recipient: str, source_url: str, subject: str, body: str) -> None:
        with self.connect() as db:
            job = db.execute(
                "SELECT application_status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not job or job["application_status"] != "applied":
                raise ValueError("Mark the job as applied before queuing recruiter outreach.")
            snapshot = db.execute(
                """SELECT jobs.applied_at,contacts.name,contacts.position,contacts.confidence,
                contacts.source_kind,contacts.sources_json FROM jobs
                LEFT JOIN contacts ON contacts.job_id=jobs.id AND contacts.email=?
                WHERE jobs.id=?""",
                (recipient, job_id),
            ).fetchone()
            if not snapshot or not snapshot["source_kind"]:
                raise ValueError("Recipient must be the selected verified contact for this job.")
            db.execute(
                """INSERT INTO outreach(job_id,recipient,recipient_source_url,subject,body,
                contact_name,contact_position,contact_confidence,contact_source_kind,
                contact_sources_json,applied_at_snapshot,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,recipient) DO UPDATE SET
                subject=CASE WHEN outreach.status='queued' THEN excluded.subject ELSE outreach.subject END,
                body=CASE WHEN outreach.status='queued' THEN excluded.body ELSE outreach.body END""",
                (
                    job_id, recipient, source_url, subject, body,
                    snapshot["name"] or "", snapshot["position"] or "", snapshot["confidence"],
                    snapshot["source_kind"] or "", snapshot["sources_json"] or "[]",
                    snapshot["applied_at"], utcnow(),
                ),
            )
            db.execute(
                "UPDATE jobs SET application_status='outreach_queued' WHERE id=?",
                (job_id,),
            )

    def queued_outreach(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT outreach.*,jobs.title,jobs.status AS job_status,jobs.company_name,
                contacts.name AS contact_name,contacts.position AS contact_position,
                contacts.confidence AS contact_confidence,contacts.source_kind AS contact_source_kind,
                contacts.sources_json AS contact_sources_json
                FROM outreach JOIN jobs ON jobs.id=outreach.job_id
                JOIN sources ON sources.id=jobs.source_id
                LEFT JOIN contacts ON contacts.job_id=jobs.id AND contacts.email=outreach.recipient
                WHERE outreach.status='queued'
                ORDER BY outreach.created_at"""
            ).fetchall()
        return [dict(row) for row in rows]

    def get_outreach(self, outreach_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT outreach.*,jobs.title,jobs.description,jobs.status AS job_status,
                jobs.contact_email,jobs.contact_source_url,jobs.application_status,jobs.applied_at,
                jobs.company_name
                FROM outreach JOIN jobs ON jobs.id=outreach.job_id
                JOIN sources ON sources.id=jobs.source_id WHERE outreach.id=?""",
                (outreach_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_draft(self, outreach_id: int, subject: str, body: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE outreach SET subject=?,body=? WHERE id=? AND status='queued'",
                (subject.strip(), body.strip(), outreach_id),
            )

    def mark_sent(self, outreach_id: int, message_id: str, thread_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE outreach SET status='sent',gmail_message_id=?,gmail_thread_id=?,sent_at=?,last_error=NULL WHERE id=?",
                (message_id, thread_id, utcnow(), outreach_id),
            )
            db.execute(
                """UPDATE jobs SET application_status='contacted' WHERE id=(
                SELECT job_id FROM outreach WHERE id=?)""",
                (outreach_id,),
            )
            db.execute(
                """UPDATE drafts SET status='sent',updated_at=? WHERE job_id=(
                SELECT job_id FROM outreach WHERE id=?)""",
                (utcnow(), outreach_id),
            )

    def mark_send_error(self, outreach_id: int, error: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE outreach SET last_error=? WHERE id=?", (error[:1000], outreach_id))

    def sent_today(self) -> int:
        local_date = datetime.now().astimezone().date().isoformat()
        with self.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM outreach WHERE status='sent' AND date(sent_at,'localtime')=?",
                (local_date,),
            ).fetchone()
        return int(row["count"])

    def tracked_outreach(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT outreach.*,jobs.title,jobs.company_name FROM outreach
                JOIN jobs ON jobs.id=outreach.job_id JOIN sources ON sources.id=jobs.source_id
                WHERE outreach.status IN ('sent','replied','auto_reply') ORDER BY sent_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_stats(self) -> dict[str, int]:
        with self.connect() as db:
            row = db.execute(
                """SELECT
                (SELECT COUNT(*) FROM jobs WHERE status='open') AS open_roles,
                (SELECT COUNT(*) FROM matches JOIN jobs ON jobs.id=matches.job_id
                    WHERE matches.decision='qualified' AND jobs.status='open') AS qualified,
                (SELECT COUNT(*) FROM drafts WHERE status='draft') AS queued,
                (SELECT COUNT(*) FROM manual_drafts) AS saved_drafts,
                (SELECT COUNT(*) FROM jobs WHERE application_status='applied') AS applied"""
            ).fetchone()
        return {key: int(row[key]) for key in row.keys()}

    def last_run(self, command: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM run_history WHERE command=? ORDER BY finished_at DESC LIMIT 1",
                (command,),
            ).fetchone()
        return dict(row) if row else None

    def mark_applied(self, job_id: int) -> None:
        with self.connect() as db:
            changed = db.execute(
                """UPDATE jobs SET application_status='applied',applied_at=COALESCE(applied_at,?)
                WHERE id=? AND status='open'""",
                (utcnow(), job_id),
            ).rowcount
            if not changed:
                raise ValueError("Only an open job can be marked as applied.")

    def save_contact(self, job_id: int, contact: dict[str, Any], *, selected: bool = True) -> None:
        with self.connect() as db:
            if selected:
                db.execute("UPDATE contacts SET selected=0 WHERE job_id=?", (job_id,))
            db.execute(
                """INSERT INTO contacts(job_id,email,name,position,source_kind,confidence,
                verification_status,sources_json,selected,discovered_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,email) DO UPDATE SET
                name=excluded.name,position=excluded.position,source_kind=excluded.source_kind,
                confidence=excluded.confidence,verification_status=excluded.verification_status,
                sources_json=excluded.sources_json,selected=excluded.selected""",
                (
                    job_id, contact["email"], contact.get("name", ""), contact.get("position", ""),
                    contact["source_kind"], contact.get("confidence"),
                    contact.get("verification_status"), json.dumps(contact.get("sources", [])),
                    int(selected), utcnow(),
                ),
            )
            if selected:
                sources = contact.get("sources", [])
                db.execute(
                    "UPDATE jobs SET contact_email=?,contact_source_url=? WHERE id=?",
                    (contact["email"], sources[0] if sources else "hunter", job_id),
                )

    def jobs_for_enrichment(self, minimum_score: int = 80, limit: int = 2) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT jobs.*,matches.score FROM jobs JOIN matches ON matches.job_id=jobs.id
                LEFT JOIN contacts ON contacts.job_id=jobs.id AND contacts.selected=1
                WHERE jobs.status='open'
                AND matches.decision='qualified' AND matches.score>=? AND contacts.id IS NULL
                AND NOT EXISTS(SELECT 1 FROM enrichment_attempts ea WHERE ea.job_id=jobs.id
                    AND ea.status IN ('contact_found','no_contact','cached'))
                ORDER BY matches.score DESC,jobs.applied_at LIMIT ?""",
                (minimum_score, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def cached_company_contact(self, company_name: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT contacts.* FROM contacts JOIN jobs ON jobs.id=contacts.job_id
                WHERE lower(jobs.company_name)=lower(?) AND contacts.selected=1
                AND contacts.source_kind='hunter' ORDER BY contacts.discovered_at DESC LIMIT 1""",
                (company_name,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["sources"] = json.loads(result.pop("sources_json"))
        return result

    def record_enrichment_attempt(self, job_id: int, status: str, credits: int) -> None:
        now = datetime.now().astimezone()
        period = now.strftime("%Y-%m")
        with self.connect() as db:
            db.execute(
                "INSERT INTO enrichment_attempts(job_id,status,credits,attempted_at) VALUES(?,?,?,?)",
                (job_id, status, credits, utcnow()),
            )
            if credits:
                db.execute(
                    """INSERT INTO api_usage(provider,period,units) VALUES('hunter',?,?)
                    ON CONFLICT(provider,period) DO UPDATE SET units=units+excluded.units""",
                    (period, credits),
                )

    def hunter_usage(self) -> int:
        period = datetime.now().astimezone().strftime("%Y-%m")
        with self.connect() as db:
            row = db.execute(
                "SELECT units FROM api_usage WHERE provider='hunter' AND period=?", (period,)
            ).fetchone()
        return int(row["units"]) if row else 0

    def enrichment_attempts_today(self) -> int:
        today = datetime.now().astimezone().date().isoformat()
        with self.connect() as db:
            row = db.execute(
                """SELECT COUNT(*) AS count FROM enrichment_attempts
                WHERE date(attempted_at,'localtime')=?""",
                (today,),
            ).fetchone()
        return int(row["count"])

    def acquire_rate_limit(
        self,
        scope: str,
        limit: int,
        window_seconds: int,
        *,
        units: int = 1,
        now: datetime | None = None,
    ) -> tuple[bool, int]:
        """Atomically reserve capacity and return (allowed, retry_after_seconds)."""

        if not scope.strip() or limit < 1 or window_seconds < 1 or units < 1:
            raise ValueError("Rate-limit scope, limit, window, and units must be positive.")
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = (moment - timedelta(seconds=window_seconds)).isoformat()
        timestamp = moment.isoformat()
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM rate_limit_events WHERE scope=? AND occurred_at<=?",
                (scope, cutoff),
            )
            rows = connection.execute(
                "SELECT units,occurred_at FROM rate_limit_events WHERE scope=? ORDER BY occurred_at",
                (scope,),
            ).fetchall()
            used = sum(int(row["units"]) for row in rows)
            if used + units > limit:
                oldest = datetime.fromisoformat(rows[0]["occurred_at"])
                retry_after = max(1, int((oldest + timedelta(seconds=window_seconds) - moment).total_seconds()) + 1)
                connection.commit()
                return False, retry_after
            connection.execute(
                "INSERT INTO rate_limit_events(scope,units,occurred_at) VALUES(?,?,?)",
                (scope.strip(), units, timestamp),
            )
            connection.commit()
            return True, 0
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_reply(self, outreach_id: int, automated: bool, replied_at: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE outreach SET status=?,replied_at=? WHERE id=? AND status IN ('sent','auto_reply')",
                ("auto_reply" if automated else "replied", replied_at, outreach_id),
            )
            if not automated:
                db.execute(
                    """UPDATE jobs SET application_status='replied' WHERE id=(
                    SELECT job_id FROM outreach WHERE id=?)""",
                    (outreach_id,),
                )

    def record_run(self, command: str, status: str, detail: str, started_at: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO run_history(command,status,detail,started_at,finished_at) VALUES(?,?,?,?,?)",
                (command, status, detail[:4000], started_at, utcnow()),
            )
