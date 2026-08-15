import pytest

import discovery
from discovery import greenhouse_jobs, infer_source, jooble_jobs, lever_jobs


def test_infer_supported_sources():
    assert infer_source("https://boards.greenhouse.io/acme/jobs/1") == ("greenhouse", "acme")
    assert infer_source("https://jobs.lever.co/acme") == ("lever", "acme")


@pytest.mark.parametrize("url", ["http://jobs.lever.co/acme", "https://example.com/jobs"])
def test_infer_source_rejects_unsafe_or_unknown_urls(url):
    with pytest.raises(ValueError):
        infer_source(url)


def test_greenhouse_normalization_and_published_contact(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "fetch_json",
        lambda _: {
            "jobs": [
                {
                    "id": 7,
                    "title": "Python Intern",
                    "location": {"name": "Remote"},
                    "content": "<p>Email careers@acme.test</p>",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/7",
                }
            ]
        },
    )
    monkeypatch.setattr(
        discovery, "fetch_text", lambda _: (_ for _ in ()).throw(AssertionError("redundant page fetch"))
    )

    result = greenhouse_jobs("acme")[0]

    assert result["external_id"] == "7"
    assert result["description"] == "Email careers@acme.test"
    assert result["contact_email"] == "careers@acme.test"


def test_lever_normalization_without_contact(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "fetch_json",
        lambda _: [
            {
                "id": "abc",
                "text": "Backend Engineer",
                "categories": {"location": "Delhi", "commitment": "Full-time"},
                "descriptionPlain": "Build Python services.",
                "additionalPlain": "",
                "lists": [],
                "hostedUrl": "https://jobs.lever.co/acme/abc",
                "applyUrl": "https://jobs.lever.co/acme/abc/apply",
            }
        ],
    )
    monkeypatch.setattr(
        discovery, "fetch_text", lambda _: (_ for _ in ()).throw(AssertionError("redundant page fetch"))
    )

    result = lever_jobs("acme")[0]

    assert result["employment_type"] == "Full-time"
    assert result["contact_email"] is None


def test_jooble_normalizes_snippet_and_marks_unclear_remote_eligibility(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "post_json",
        lambda *_args, **_kwargs: {
            "jobs": [
                {
                    "id": 42,
                    "title": "Python Engineer",
                    "company": "Acme",
                    "location": "Remote",
                    "snippet": "<p>Build APIs. Contact talent@acme.test</p>",
                    "type": "Full-time",
                    "link": "https://in.jooble.org/jdp/42",
                    "updated": "2026-08-15T10:00:00Z",
                }
            ]
        },
    )

    result = jooble_jobs("key", "Python Engineer", "Remote")[0]

    assert result["origin_provider"] == "jooble"
    assert result["description_quality"] == "snippet"
    assert result["contact_email"] == "talent@acme.test"
    assert result["eligibility_warning"]
