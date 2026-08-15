import sqlite3

from storage import Database


def test_legacy_database_is_migrated_without_losing_jobs(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company_name TEXT NOT NULL,
            provider TEXT NOT NULL CHECK(provider IN ('greenhouse','lever')),
            board_token TEXT NOT NULL, board_url TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, UNIQUE(provider,board_token));
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL REFERENCES sources(id),
            external_id TEXT NOT NULL,title TEXT NOT NULL,location TEXT NOT NULL DEFAULT '',
            employment_type TEXT NOT NULL DEFAULT '',description TEXT NOT NULL,job_url TEXT NOT NULL,
            apply_url TEXT NOT NULL DEFAULT '',contact_email TEXT,contact_source_url TEXT,
            fingerprint TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,UNIQUE(source_id,external_id));
        INSERT INTO sources VALUES(1,'Acme','lever','acme','https://jobs.lever.co/acme',1,'2026-01-01');
        INSERT INTO jobs VALUES(
            1,1,'role','Python Engineer','Remote','Full-time','Build APIs',
            'https://jobs.lever.co/acme/role','', 'jobs@acme.test','https://acme.test/jobs',
            'fingerprint','open','2026-01-01','2026-01-01');
        """
    )
    connection.commit()
    connection.close()

    db = Database(path)
    jooble_source_id = db.ensure_jooble_source()
    job = db.open_jobs()[0]

    assert jooble_source_id > 1
    assert job["company_name"] == "Acme"
    assert job["origin_provider"] == "lever"
    assert job["canonical_key"]
    assert db.ranked_matches() == []
