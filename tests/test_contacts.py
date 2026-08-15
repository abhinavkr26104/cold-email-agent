import io
import json

import contacts
from contacts import HunterClient


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def test_hunter_selects_only_sourced_high_confidence_recruiting_contacts(monkeypatch):
    payload = {
        "data": {
            "emails": [
                {
                    "value": "recruiter@acme.test",
                    "first_name": "Rina",
                    "last_name": "Shah",
                    "position": "Senior Talent Acquisition Partner",
                    "department": "hr",
                    "confidence": 93,
                    "sources": [{"uri": "https://acme.test/team"}],
                    "verification": {"status": "valid"},
                },
                {
                    "value": "engineer@acme.test",
                    "position": "Software Engineer",
                    "department": "it",
                    "confidence": 99,
                    "sources": [{"uri": "https://acme.test/team"}],
                },
                {
                    "value": "hr@acme.test",
                    "position": "Recruiter",
                    "department": "hr",
                    "confidence": 60,
                    "sources": [{"uri": "https://acme.test/team"}],
                },
            ]
        }
    }
    monkeypatch.setattr(
        contacts, "urlopen", lambda *_args, **_kwargs: Response(json.dumps(payload).encode())
    )

    found = HunterClient("key").find_recruiting_contacts("Acme")

    assert len(found) == 1
    assert found[0]["email"] == "recruiter@acme.test"
    assert found[0]["name"] == "Rina Shah"
