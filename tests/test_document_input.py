from types import SimpleNamespace

import pytest

import document_input
from document_input import DocumentInputError, extract_pdf_text, resolve_document_text


class FakePage:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


def fake_reader(monkeypatch, pages, *, encrypted=False):
    monkeypatch.setattr(
        document_input,
        "PdfReader",
        lambda _: SimpleNamespace(pages=pages, is_encrypted=encrypted),
    )


def test_extract_pdf_text_combines_nonempty_pages(monkeypatch):
    fake_reader(monkeypatch, [FakePage(" Resume page one "), FakePage(None), FakePage("Page two")])

    assert extract_pdf_text(b"fake-pdf") == "Resume page one\n\nPage two"


def test_extract_pdf_text_rejects_image_only_pdf(monkeypatch):
    fake_reader(monkeypatch, [FakePage("")])

    with pytest.raises(DocumentInputError, match="No selectable text"):
        extract_pdf_text(b"fake-pdf")


def test_extract_pdf_text_rejects_encrypted_pdf(monkeypatch):
    fake_reader(monkeypatch, [], encrypted=True)

    with pytest.raises(DocumentInputError, match="Password-protected"):
        extract_pdf_text(b"fake-pdf")


def test_extract_pdf_text_rejects_oversized_pdf():
    oversized = b"x" * (document_input.MAX_PDF_BYTES + 1)

    with pytest.raises(DocumentInputError, match="10 MB"):
        extract_pdf_text(oversized)


def test_resolve_document_text_supports_both_modes(monkeypatch):
    fake_reader(monkeypatch, [FakePage("PDF content")])

    assert resolve_document_text("Paste text", "  Pasted content  ", None, "Profile") == "Pasted content"
    assert resolve_document_text("Upload PDF", "", b"fake-pdf", "Profile") == "PDF content"


def test_resolve_document_text_requires_selected_pdf():
    with pytest.raises(DocumentInputError, match="Upload a PDF"):
        resolve_document_text("Upload PDF", "", None, "Candidate profile")
