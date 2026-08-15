"""Recruiting-contact discovery through Hunter's free API."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RECRUITING_TERMS = (
    "recruiter",
    "recruiting",
    "talent acquisition",
    "talent partner",
    "hiring",
    "human resources",
    "people operations",
    "people partner",
)


class ContactDiscoveryError(RuntimeError):
    pass


class HunterClient:
    def __init__(self, api_key: str):
        if not api_key.strip():
            raise ValueError("Hunter API key is required.")
        self.api_key = api_key.strip()

    def find_recruiting_contacts(self, company: str) -> list[dict[str, Any]]:
        query = urlencode(
            {"company": company, "limit": 10, "type": "personal", "api_key": self.api_key}
        )
        request = Request(
            f"https://api.hunter.io/v2/domain-search?{query}",
            headers={"User-Agent": "ColdEmailAgent/1.0", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except Exception as error:
            raise ContactDiscoveryError(f"Hunter contact search failed: {error}") from error

        candidates: list[dict[str, Any]] = []
        for item in (payload.get("data") or {}).get("emails", []):
            position = (item.get("position") or "").strip()
            department = (item.get("department") or "").lower()
            if department not in {"hr", "human_resources"} and not any(
                term in position.lower() for term in RECRUITING_TERMS
            ):
                continue
            confidence = int(item.get("confidence") or 0)
            sources = [source.get("uri") for source in item.get("sources", []) if source.get("uri")]
            if confidence < 80 or not sources:
                continue
            name = " ".join(
                part for part in (item.get("first_name"), item.get("last_name")) if part
            )
            verification = (item.get("verification") or {}).get("status")
            candidates.append(
                {
                    "email": item["value"],
                    "name": name,
                    "position": position,
                    "source_kind": "hunter",
                    "confidence": confidence,
                    "verification_status": verification,
                    "sources": sources,
                    "selection_score": confidence
                    + (15 if "recruit" in position.lower() else 0)
                    + (10 if "talent" in position.lower() else 0)
                    + (5 if verification == "valid" else 0),
                }
            )
        return sorted(candidates, key=lambda item: item["selection_score"], reverse=True)
