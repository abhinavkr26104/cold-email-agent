"""Public Greenhouse and Lever job discovery with published-contact extraction."""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


USER_AGENT = "ColdEmailAgent/1.0 (personal job search)"
EMAIL_PATTERN = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)


class DiscoveryError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(html.unescape(value or ""))
    return " ".join(" ".join(parser.parts).split())


def fetch_json(url: str, timeout: int = 20) -> Any:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except Exception as error:
        raise DiscoveryError(f"Could not fetch {url}: {error}") from error


def post_json(url: str, payload: dict[str, Any], timeout: int = 20) -> Any:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except Exception as error:
        raise DiscoveryError(f"Could not fetch Jooble jobs: {error}") from error


def fetch_text(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(2_000_000).decode(response.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def infer_source(board_url: str) -> tuple[str, str]:
    parsed = urlparse(board_url.strip())
    if parsed.scheme != "https":
        raise ValueError("Career board URL must use HTTPS.")
    parts = [part for part in parsed.path.split("/") if part]
    host = parsed.hostname or ""
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and parts:
        return "greenhouse", parts[0]
    if host in {"jobs.lever.co", "jobs.eu.lever.co"} and parts:
        return "lever", parts[0]
    raise ValueError("Only public Greenhouse and Lever board URLs are supported.")


def published_contact(description: str, job_url: str) -> tuple[str | None, str | None]:
    for content, source in ((description, job_url), (plain_text(fetch_text(job_url)), job_url)):
        for candidate in EMAIL_PATTERN.findall(content):
            lowered = candidate.lower()
            if not lowered.endswith(("@example.com", "@example.org", "@test.com")):
                return candidate, source
    return None, None


def published_contact_from_text(description: str, source_url: str) -> tuple[str | None, str | None]:
    for candidate in EMAIL_PATTERN.findall(description):
        lowered = candidate.lower()
        if not lowered.endswith(("@example.com", "@example.org", "@test.com")):
            return candidate, source_url
    return None, None


def greenhouse_jobs(token: str) -> list[dict[str, Any]]:
    payload = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    jobs = []
    for item in payload.get("jobs", []):
        description = plain_text(item.get("content", ""))
        job_url = item.get("absolute_url", "")
        # Greenhouse already includes the full posting content. Avoid opening
        # every job page again, which is slow and defeats provider rate limits.
        email, source = published_contact_from_text(description, job_url)
        jobs.append(
            {
                "external_id": str(item["id"]),
                "title": item.get("title", "Untitled role"),
                "location": (item.get("location") or {}).get("name", ""),
                "employment_type": "",
                "description": description,
                "job_url": job_url,
                "apply_url": job_url,
                "contact_email": email,
                "contact_source_url": source,
            }
        )
    return jobs


def lever_jobs(token: str) -> list[dict[str, Any]]:
    payload = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    jobs = []
    for item in payload:
        categories = item.get("categories") or {}
        description = plain_text(
            " ".join(
                [item.get("descriptionPlain", ""), item.get("additionalPlain", "")]
                + [section.get("content", "") for section in item.get("lists", [])]
            )
        )
        job_url = item.get("hostedUrl", "")
        # Lever's API payload contains the complete description and sections.
        email, source = published_contact_from_text(description, job_url)
        jobs.append(
            {
                "external_id": str(item["id"]),
                "title": item.get("text", "Untitled role"),
                "location": categories.get("location", ""),
                "employment_type": categories.get("commitment", ""),
                "description": description,
                "job_url": job_url,
                "apply_url": item.get("applyUrl", job_url),
                "contact_email": email,
                "contact_source_url": source,
            }
        )
    return jobs


def jooble_jobs(api_key: str, keywords: str, location: str, *, results_per_page: int = 20) -> list[dict[str, Any]]:
    payload = post_json(
        f"https://jooble.org/api/{api_key}",
        {
            "keywords": keywords,
            "location": location,
            "page": "1",
            "ResultOnPage": str(results_per_page),
            "companysearch": "false",
        },
    )
    jobs: list[dict[str, Any]] = []
    for item in payload.get("jobs", []):
        description = plain_text(item.get("snippet", ""))
        job_url = item.get("link", "")
        email, source = published_contact_from_text(description, job_url)
        location_text = item.get("location", "")
        remote_query = location.lower() == "remote"
        eligibility_warning = None
        if remote_query:
            eligibility_text = f"{location_text} {description}".lower()
            confirmed = any(term in eligibility_text for term in ("india", "worldwide", "anywhere", "global", "asia"))
            if not confirmed:
                eligibility_warning = "Remote eligibility for candidates in India is not confirmed."
        jobs.append(
            {
                "external_id": str(item.get("id") or job_url),
                "company_name": item.get("company") or "Company not specified",
                "origin_provider": "jooble",
                "title": item.get("title", "Untitled role"),
                "location": location_text,
                "employment_type": item.get("type", ""),
                "description": description,
                "job_url": job_url,
                "apply_url": job_url,
                "contact_email": email,
                "contact_source_url": source,
                "posted_at": item.get("updated"),
                "description_quality": "snippet",
                "eligibility_warning": eligibility_warning,
            }
        )
    return jobs


def fetch_source(provider: str, token: str) -> list[dict[str, Any]]:
    if provider == "greenhouse":
        return greenhouse_jobs(token)
    if provider == "lever":
        return lever_jobs(token)
    raise ValueError(f"Unsupported provider: {provider}")
