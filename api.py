"""Local FastAPI service for the Scoutly React application."""

from __future__ import annotations

import json
import os
import uuid
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from automation import (
    DAILY_ENRICHMENT_LIMIT,
    DAILY_SEND_LIMIT,
    MONTHLY_HUNTER_LIMIT,
    discover,
    enrich_applied_jobs,
    evaluate_matches,
    find_contact_for_job,
    personalize_draft,
    prepare_drafts,
    send_drafts,
    sync_replies,
)
from discovery import infer_source
from document_input import DocumentInputError, extract_pdf_text
from gmail_provider import CLIENT_SECRET, TOKEN_FILE, GmailProvider
from storage import Database
from workflow import ColdEmailInput, generate_cold_email


ROOT = Path(__file__).resolve().parent
FRONTEND_DIST = ROOT / "frontend" / "dist"


class ProfilePayload(BaseModel):
    candidate_name: str = Field(min_length=1)
    candidate_profile: str = Field(min_length=1)
    preferences: dict[str, Any] = Field(default_factory=dict)


class SourcePayload(BaseModel):
    company_name: str = Field(min_length=1)
    board_url: str = Field(min_length=1)


class SourcePatch(BaseModel):
    company_name: str | None = None
    enabled: bool | None = None


class DraftPatch(BaseModel):
    subject: str
    body: str


class SendPayload(BaseModel):
    draft_ids: list[int] = Field(min_length=1)
    confirmed: bool


class ManualDraftPayload(BaseModel):
    candidate_name: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    candidate_profile: str = Field(min_length=1)
    job_description: str = Field(min_length=1)
    recipient_name: str = ""
    recipient_position: str = ""
    role_title: str = ""
    applied_at: str = ""


class EmptyPayload(BaseModel):
    pass


class DocumentPayload(BaseModel):
    filename: str
    content_base64: str


def _decode(row: dict[str, Any], *fields: str) -> dict[str, Any]:
    result = dict(row)
    for field in fields:
        raw = result.pop(field, None)
        result[field.removesuffix("_json")] = json.loads(raw or "[]")
    return result


class DiscoveryQueue:
    """A durable single-worker queue backed by task_runs."""

    def __init__(self, db: Database):
        self.db = db
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discovery")

    def submit(self) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        run = self.db.create_task_run(run_id)
        self.executor.submit(self._execute, run_id)
        return run

    def _execute(self, run_id: str) -> None:
        result: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []
        self.db.update_task_run(run_id, status="running", stage="discovering", progress=5)
        stages = [
            ("discovering", 15, lambda: discover(self.db)),
            ("matching", 45, lambda: evaluate_matches(self.db)),
            ("contacts", 70, lambda: {"enrichment": enrich_applied_jobs(self.db)}),
            ("drafting", 88, lambda: {"drafts_prepared": prepare_drafts(self.db, limit=5)}),
        ]
        for stage_name, progress, operation in stages:
            self.db.update_task_run(run_id, stage=stage_name, progress=progress, result=result, errors=errors)
            try:
                result.update(operation())
            except Exception as error:  # keep useful earlier results and report the failed stage
                errors.append({"stage": stage_name, "message": str(error)})
                if stage_name in {"discovering", "matching"}:
                    break
        final_status = "completed" if not errors else ("partial" if result else "failed")
        self.db.update_task_run(
            run_id, status=final_status, stage=final_status, progress=100,
            result=result, errors=errors,
        )


def create_app(database: Database | None = None) -> FastAPI:
    db = database or Database()
    queue = DiscoveryQueue(db)
    app = FastAPI(title="Scoutly API", version="2.0.0")
    app.state.db = db
    app.state.discovery_queue = queue
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/profile")
    def get_profile() -> dict[str, Any] | None:
        return db.get_profile()

    @app.put("/api/profile")
    def put_profile(payload: ProfilePayload) -> dict[str, Any]:
        db.save_profile(payload.candidate_name, payload.candidate_profile, payload.preferences)
        return db.get_profile() or {}

    @app.get("/api/sources")
    def get_sources() -> list[dict[str, Any]]:
        return db.list_sources()

    @app.post("/api/sources", status_code=status.HTTP_201_CREATED)
    def post_source(payload: SourcePayload) -> dict[str, Any]:
        try:
            provider, token = infer_source(payload.board_url)
            db.add_source(payload.company_name, provider, token, payload.board_url)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return next(
            source for source in db.list_sources()
            if source["provider"] == provider and source["board_token"] == token
        )

    @app.patch("/api/sources/{source_id}")
    def patch_source(source_id: int, payload: SourcePatch) -> dict[str, Any]:
        try:
            return db.update_source(source_id, payload.model_dump(exclude_none=True))
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.post("/api/discovery-runs", status_code=status.HTTP_202_ACCEPTED)
    def start_discovery() -> dict[str, Any]:
        try:
            return queue.submit()
        except RuntimeError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error

    @app.get("/api/discovery-runs/{run_id}")
    def get_discovery_run(run_id: str) -> dict[str, Any]:
        run = db.get_task_run(run_id)
        if not run:
            raise HTTPException(404, "Discovery run not found.")
        return run

    @app.get("/api/matches")
    def get_matches(
        match_status: str | None = Query(None, alias="status"),
        minimum_score: int | None = Query(None, ge=0, le=100), company: str | None = None,
        page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    ) -> dict[str, Any]:
        rows, total = db.list_matches(
            status=match_status, minimum_score=minimum_score, company=company,
            limit=page_size, offset=(page - 1) * page_size,
        )
        items = [_decode(row, "evidence_json", "missing_json") for row in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def approval_item(row: dict[str, Any]) -> dict[str, Any]:
        blockers: list[dict[str, str]] = []
        if row["job_status"] != "open":
            blockers.append({"code": "role_closed", "message": "The role is no longer open."})
        if row["application_status"] != "applied":
            blockers.append({"code": "application_required", "message": "Mark this role as applied."})
        if not row["selected_contact_email"]:
            blockers.append({"code": "contact_required", "message": "A verified contact is still needed."})
        sources = json.loads(row.get("contact_sources_json") or "[]")
        if row["selected_contact_email"] and (not row["contact_source_kind"] or not sources):
            blockers.append({"code": "contact_provenance_missing", "message": "Contact source evidence is missing."})
        if not row["subject"].strip() or not row["body"].strip():
            blockers.append({"code": "draft_incomplete", "message": "Subject and message are required."})
        if row["sent_count"]:
            blockers.append({"code": "duplicate_send", "message": "Outreach was already sent for this role."})
        if db.sent_today() >= DAILY_SEND_LIMIT:
            blockers.append({"code": "gmail_daily_limit", "message": "The daily Gmail limit is full."})
        can_send = not blockers
        if row["status"] == "sent" or row["sent_count"]:
            state = "Sent"
        elif not row["selected_contact_email"]:
            state = "Contact pending"
        elif row["application_status"] != "applied":
            state = "Ready after application"
        elif can_send:
            state = "Ready to send"
        else:
            state = "Draft ready"
        item = _decode(row, "evidence_json", "missing_json")
        item["contact_sources"] = sources
        item["can_send"] = can_send
        item["blockers"] = blockers
        item["display_state"] = state
        return item

    @app.get("/api/approval-items")
    def get_approval_items() -> list[dict[str, Any]]:
        return [approval_item(row) for row in db.approval_items()]

    @app.patch("/api/drafts/{draft_id}")
    def patch_draft(draft_id: int, payload: DraftPatch) -> dict[str, Any]:
        try:
            db.edit_draft(draft_id, payload.subject, payload.body)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        row = next(item for item in db.approval_items() if item["id"] == draft_id)
        return approval_item(row)

    @app.post("/api/drafts/{draft_id}/regenerate")
    def regenerate_draft(draft_id: int) -> dict[str, Any]:
        try:
            draft = db.get_draft(draft_id)
            if not draft:
                raise ValueError("Unknown draft.")
            prepare_drafts(db, limit=1, force_job_id=draft["job_id"])
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        row = next(item for item in db.approval_items() if item["id"] == draft_id)
        return approval_item(row)

    @app.post("/api/jobs/{job_id}/mark-applied")
    def mark_applied(job_id: int) -> dict[str, Any]:
        try:
            db.mark_applied(job_id)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"job_id": job_id, "application_status": "applied"}

    @app.post("/api/jobs/{job_id}/find-contact")
    def find_contact(job_id: int) -> dict[str, Any]:
        try:
            contact = find_contact_for_job(db, job_id)
            draft = next((item for item in db.approval_items() if item["job_id"] == job_id), None)
            if contact and draft:
                if draft["edited"]:
                    db.mark_draft_stale(job_id)
                else:
                    personalize_draft(db, draft["id"])
            return {"contact": contact, "draft_preserved": bool(draft and draft["edited"])}
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/outreach/send")
    def send_outreach(payload: SendPayload) -> dict[str, Any]:
        if not payload.confirmed:
            raise HTTPException(400, "Explicit batch confirmation is required.")
        try:
            result = send_drafts(payload.draft_ids, db)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        if result["skipped"] and not result["sent"] and not result["failed"]:
            raise HTTPException(409, detail={"message": "No drafts were sendable.", "result": result})
        return result

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, Any]:
        stats = db.dashboard_stats()
        latest = db.last_run("discover")
        return {**stats, "latest_discovery": latest}

    @app.get("/api/conversations")
    def conversations() -> list[dict[str, Any]]:
        return db.tracked_outreach()

    @app.post("/api/replies/sync")
    def reply_sync() -> dict[str, int]:
        try:
            return sync_replies(db, interactive=False)
        except Exception as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/gmail/connect")
    def gmail_connect() -> dict[str, Any]:
        try:
            gmail = GmailProvider(interactive=True)
            return {"connected": True, "email": gmail.account_email}
        except Exception as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/settings/status")
    def settings_status() -> dict[str, Any]:
        return {
            "gmail": {"connected": TOKEN_FILE.exists(), "client_configured": CLIENT_SECRET.exists()},
            "providers": {
                "groq": bool(os.getenv("GROQ_API_KEY", "").strip()),
                "jooble": bool(os.getenv("JOOBLE_API_KEY", "").strip()),
                "hunter": bool(os.getenv("HUNTER_API_KEY", "").strip()),
            },
            "quotas": {
                "gmail": {"used": db.sent_today(), "limit": DAILY_SEND_LIMIT},
                "hunter_daily": {"used": db.enrichment_attempts_today(), "limit": DAILY_ENRICHMENT_LIMIT},
                "hunter_monthly": {"used": db.hunter_usage(), "limit": MONTHLY_HUNTER_LIMIT},
            },
        }

    @app.post("/api/manual-draft")
    def manual_draft(payload: ManualDraftPayload) -> dict[str, str]:
        email = generate_cold_email(ColdEmailInput(**payload.model_dump()))
        return {"body": email}

    @app.post("/api/documents/extract")
    def extract_document(payload: DocumentPayload) -> dict[str, str]:
        if not payload.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Only PDF documents are supported.")
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
            return {"text": extract_pdf_text(content)}
        except (ValueError, DocumentInputError) as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if FRONTEND_DIST.exists():
        assets = FRONTEND_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def react_app(full_path: str) -> Response:
            target = FRONTEND_DIST / full_path
            if full_path and target.is_file() and FRONTEND_DIST in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
