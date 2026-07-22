"""Tests for document parsing (PDF / DOCX / TXT) with positions.

Fixtures are built in-memory (python-docx for DOCX, fpdf2 for PDF) so the suite
stays deterministic and carries no committed binaries. PDF text extraction is
lossy, so PDF assertions check substrings and page numbers rather than exact text.
"""

from io import BytesIO

import pytest
from docx import Document
from fpdf import FPDF
from PIL import Image

from contextvault.ingestion import (
    DocumentParseError,
    ParsedDocument,
    UnsupportedDocumentError,
    parse_document,
)


def _docx_bytes(paragraphs: list[str]) -> bytes:
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _pdf_bytes(pages: list[str]) -> bytes:
    pdf = FPDF()
    pdf.set_font("helvetica", size=12)
    for text in pages:
        pdf.add_page()
        pdf.multi_cell(0, 10, text=text)
    return bytes(pdf.output())


def _assert_contiguous(parsed: ParsedDocument) -> None:
    """Blocks tile the full text with no gaps or overlaps."""
    assert "".join(b.text for b in parsed.blocks) == parsed.text
    cursor = 0
    for block in parsed.blocks:
        assert block.start == cursor
        assert block.end == cursor + len(block.text)
        cursor = block.end
    assert cursor == len(parsed.text)


def test_parse_txt_preserves_text_and_positions() -> None:
    raw = "Line one.\nРядок два.\n"
    parsed = parse_document("notes.txt", raw.encode("utf-8"))

    assert parsed.text == raw
    assert len(parsed.blocks) == 1
    only = parsed.blocks[0]
    assert only.page is None
    assert only.start == 0
    assert only.end == len(raw)
    _assert_contiguous(parsed)


def test_parse_txt_rejects_invalid_encoding() -> None:
    with pytest.raises(DocumentParseError):
        parse_document("bad.txt", b"\xff\xfe\x00 not utf-8")


def test_parse_docx_extracts_paragraphs_with_offsets() -> None:
    parsed = parse_document("doc.docx", _docx_bytes(["First paragraph.", "Второй абзац."]))

    assert "First paragraph." in parsed.text
    assert "Второй абзац." in parsed.text
    assert all(b.page is None for b in parsed.blocks)
    _assert_contiguous(parsed)


def test_parse_pdf_tracks_page_numbers() -> None:
    parsed = parse_document("report.pdf", _pdf_bytes(["Hello from page one.", "Second page here."]))

    assert len(parsed.blocks) == 2
    assert parsed.blocks[0].page == 1
    assert parsed.blocks[1].page == 2
    assert "Hello" in parsed.blocks[0].text
    assert "Second" in parsed.blocks[1].text
    _assert_contiguous(parsed)

    # A char offset in the full text maps back to the correct page.
    idx = parsed.text.index("Second")
    page = next(b.page for b in parsed.blocks if b.start <= idx < b.end)
    assert page == 2


def test_extension_is_case_insensitive() -> None:
    parsed = parse_document("NOTES.TXT", b"hello")
    assert parsed.text == "hello"


def test_unsupported_extension_raises() -> None:
    with pytest.raises(UnsupportedDocumentError):
        parse_document("archive.zip", b"anything")


def test_corrupt_pdf_raises_parse_error() -> None:
    with pytest.raises(DocumentParseError):
        parse_document("broken.pdf", b"%PDF-1.4 not really a pdf")


def test_corrupt_docx_raises_parse_error() -> None:
    with pytest.raises(DocumentParseError):
        parse_document("broken.docx", b"PK not really a docx")


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 16), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_parse_image_returns_ocr_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.ingestion.ocr.ocr_image", lambda image: "Hello world")
    parsed = parse_document("scan.png", _png_bytes())
    assert parsed.text == "Hello world"
    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].page is None
    _assert_contiguous(parsed)


def test_parse_image_without_text_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.ingestion.ocr.ocr_image", lambda image: "   ")
    with pytest.raises(DocumentParseError, match="No text found in image"):
        parse_document("blank.png", _png_bytes())


def test_parse_corrupt_image_fails() -> None:
    with pytest.raises(DocumentParseError, match="Could not read image file"):
        parse_document("broken.png", b"this is not an image")


def _heic_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 16), "white").save(buf, format="HEIF")
    return buf.getvalue()


def test_parse_heic_returns_ocr_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.ingestion.ocr.ocr_image", lambda image: "Hello HEIC")
    parsed = parse_document("photo.heic", _heic_bytes())
    assert parsed.text == "Hello HEIC"
    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].page is None
    _assert_contiguous(parsed)


def test_parse_heif_extension_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.ingestion.ocr.ocr_image", lambda image: "text")
    parsed = parse_document("photo.heif", _heic_bytes())
    assert parsed.text == "text"


def test_parsed_from_text_single_block() -> None:
    from contextvault.ingestion.parsing import parsed_from_text

    parsed = parsed_from_text("some page text")
    assert parsed.text == "some page text"
    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].page is None
