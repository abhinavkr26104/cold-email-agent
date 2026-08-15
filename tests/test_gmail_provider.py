import base64
from email import message_from_bytes

from gmail_provider import GmailProvider


class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeLabels:
    def __init__(self):
        self.created = []

    def list(self, **_):
        return Call({"labels": [{"id": "outreach-label", "name": "ColdEmailAgent/Outreach"}]})

    def create(self, **kwargs):
        self.created.append(kwargs["body"]["name"])
        return Call({"id": "created-label"})


class FakeMessages:
    def __init__(self):
        self.raw = None

    def send(self, **kwargs):
        self.raw = kwargs["body"]["raw"]
        return Call({"id": "message-1", "threadId": "thread-1"})


class FakeThreads:
    def __init__(self, thread=None):
        self.thread = thread or {"messages": []}
        self.modified = []

    def get(self, **_):
        return Call(self.thread)

    def modify(self, **kwargs):
        self.modified.append(kwargs)
        return Call({})


class FakeUsers:
    def __init__(self, thread=None):
        self.messages_api = FakeMessages()
        self.labels_api = FakeLabels()
        self.threads_api = FakeThreads(thread)

    def getProfile(self, **_):
        return Call({"emailAddress": "candidate@gmail.com"})

    def messages(self):
        return self.messages_api

    def labels(self):
        return self.labels_api

    def threads(self):
        return self.threads_api


class FakeService:
    def __init__(self, thread=None):
        self.users_api = FakeUsers(thread)

    def users(self):
        return self.users_api


def test_send_builds_message_and_labels_thread():
    service = FakeService()
    gmail = GmailProvider(service=service)

    result = gmail.send("careers@acme.test", "Python role", "Hello team")

    raw = base64.urlsafe_b64decode(service.users_api.messages_api.raw)
    message = message_from_bytes(raw)
    assert message["To"] == "careers@acme.test"
    assert message["Subject"] == "Python role"
    assert result == {"message_id": "message-1", "thread_id": "thread-1"}
    assert service.users_api.threads_api.modified[0]["id"] == "thread-1"


def test_find_reply_distinguishes_automated_sender():
    thread = {
        "messages": [
            {
                "internalDate": "2000",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Robot <no-reply@acme.test>"},
                        {"name": "Auto-Submitted", "value": "auto-replied"},
                    ]
                },
            }
        ]
    }
    gmail = GmailProvider(service=FakeService(thread))

    reply = gmail.find_reply("thread-1", "1970-01-01T00:00:01+00:00")

    assert reply["automated"] is True
