"""Text and PDF input helpers shared by the user interface and tests."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


MAX_PDF_BYTES = 10 * 1024 * 1024


class DocumentInputError(ValueError):
    """Raised when a user-provided document cannot supply usable text."""


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract selectable text from an unencrypted PDF."""

    if not pdf_bytes:
        raise DocumentInputError("The uploaded PDF is empty.")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise DocumentInputError("PDF files must be 10 MB or smaller.")

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception as error:
        raise DocumentInputError("The uploaded file is not a readable PDF.") from error

    if reader.is_encrypted:
        raise DocumentInputError("Password-protected PDFs are not supported.")

    try:
        pages = [text.strip() for page in reader.pages if (text := page.extract_text())]
    except Exception as error:
        raise DocumentInputError("Text could not be extracted from this PDF.") from error

    extracted = "\n\n".join(page for page in pages if page).strip()
    if not extracted:
        raise DocumentInputError(
            "No selectable text was found. Use a text-based PDF or paste the text instead."
        )
    return extracted


def resolve_document_text(
    method: str,
    pasted_text: str,
    uploaded_pdf: bytes | None,
    label: str,
) -> str:
    """Resolve one UI input mode to plain text for the graph."""

    if method == "Paste text":
        return pasted_text.strip()
    if method == "Upload PDF":
        if uploaded_pdf is None:
            raise DocumentInputError(f"Upload a PDF for {label.lower()}.")
        return extract_pdf_text(uploaded_pdf)
    raise DocumentInputError(f"Unsupported input method for {label.lower()}.")
