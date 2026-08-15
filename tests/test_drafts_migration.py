from storage import Database


def test_queued_outreach_is_migrated_to_edited_draft(tmp_path):
    db = Database(tmp_path / "migration.db")
    db.save_profile("Ada", "Python developer", {})
    db.add_source("Acme", "lever", "acme", "https://jobs.lever.co/acme")
    source = db.list_sources()[0]
    db.upsert_jobs(source["id"], [{
        "external_id": "1", "title": "Engineer", "description": "Python",
        "job_url": "https://jobs.lever.co/acme/1", "contact_email": "jobs@acme.test",
        "contact_source_url": "https://jobs.lever.co/acme/1",
    }])
    job = db.open_jobs()[0]
    db.mark_applied(job["id"])
    db.queue_outreach(job["id"], "jobs@acme.test", "https://jobs.lever.co/acme/1", "Subject", "Body")
    with db.connect() as connection:
        connection.execute("DELETE FROM drafts")

    migrated = Database(db.path)
    draft = migrated.approval_items()

    # A match did not exist, so inspect the migrated row directly.
    with migrated.connect() as connection:
        row = connection.execute("SELECT * FROM drafts").fetchone()
    assert row["subject"] == "Subject"
    assert row["body"] == "Body"
    assert row["edited"] == 1


def test_active_run_is_marked_interrupted_when_database_reopens(tmp_path):
    db = Database(tmp_path / "restart.db")
    db.create_task_run("run-before-restart")

    reopened = Database(db.path)
    run = reopened.get_task_run("run-before-restart")

    assert run["status"] == "interrupted"
    assert run["stage"] == "interrupted"
    assert run["finished_at"]
