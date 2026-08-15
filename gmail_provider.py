"""Gmail OAuth, approved-message sending, labels, and tracked-thread reply sync."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Any


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
SECRETS_DIR = Path(__file__).resolve().parent / ".secrets"
CLIENT_SECRET = SECRETS_DIR / "gmail-client-secret.json"
TOKEN_FILE = SECRETS_DIR / "gmail-token.json"
OUTREACH_LABEL = "ColdEmailAgent/Outreach"
REPLY_LABEL = "ColdEmailAgent/Reply"


class GmailConfigurationError(RuntimeError):
    pass


class GmailProvider:
    def __init__(self, service: Any | None = None, *, interactive: bool = True):
        self.service = service or self._authorize(interactive)
        self.profile = self.service.users().getProfile(userId="me").execute()
        self.account_email = self.profile["emailAddress"].lower()

    @staticmethod
    def _authorize(interactive: bool):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        credentials = None
        if TOKEN_FILE.exists():
            credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            if not interactive:
                raise GmailConfigurationError("Gmail is not connected. Open the dashboard to connect it first.")
            if not CLIENT_SECRET.exists():
                raise GmailConfigurationError(f"Place Google OAuth desktop credentials at {CLIENT_SECRET}.")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            credentials = flow.run_local_server(port=0)
            SECRETS_DIR.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def _label_id(self, name: str) -> str:
        labels = self.service.users().labels().list(userId="me").execute().get("labels", [])
        existing = next((label for label in labels if label["name"] == name), None)
        if existing:
            return existing["id"]
        created = self.service.users().labels().create(
            userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        ).execute()
        return created["id"]

    def send(self, recipient: str, subject: str, body: str) -> dict[str, str]:
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = self.profile["emailAddress"]
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        sent = self.service.users().messages().send(userId="me", body={"raw": raw}).execute()
        self.service.users().threads().modify(
            userId="me", id=sent["threadId"], body={"addLabelIds": [self._label_id(OUTREACH_LABEL)]}
        ).execute()
        return {"message_id": sent["id"], "thread_id": sent["threadId"]}

    @staticmethod
    def _headers(message: dict[str, Any]) -> dict[str, str]:
        return {
            header["name"].lower(): header["value"]
            for header in message.get("payload", {}).get("headers", [])
        }

    def find_reply(self, thread_id: str, sent_at: str) -> dict[str, Any] | None:
        thread = self.service.users().threads().get(userId="me", id=thread_id, format="metadata").execute()
        sent_ms = int(datetime.fromisoformat(sent_at).timestamp() * 1000)
        for message in sorted(thread.get("messages", []), key=lambda item: int(item.get("internalDate", 0))):
            if int(message.get("internalDate", 0)) <= sent_ms:
                continue
            headers = self._headers(message)
            sender = parseaddr(headers.get("from", ""))[1].lower()
            if not sender or sender == self.account_email:
                continue
            automated = (
                headers.get("auto-submitted", "no").lower() != "no"
                or headers.get("precedence", "").lower() in {"bulk", "junk", "list"}
                or "no-reply" in sender
                or "noreply" in sender
            )
            return {
                "automated": automated,
                "received_at": datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc).isoformat(),
            }
        return None

    def label_reply(self, thread_id: str) -> None:
        self.service.users().threads().modify(
            userId="me", id=thread_id, body={"addLabelIds": [self._label_id(REPLY_LABEL)]}
        ).execute()
