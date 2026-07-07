"""Ingestion pipeline: parse → chunk → embed → store (design spec §7).

v1 implements the parsing stage; chunking/embedding/storage land in later cards.
"""

from contextvault.ingestion.parsing import (
    DocumentError,
    DocumentParseError,
    ParsedDocument,
    TextBlock,
    UnsupportedDocumentError,
    parse_document,
)

__all__ = [
    "DocumentError",
    "DocumentParseError",
    "ParsedDocument",
    "TextBlock",
    "UnsupportedDocumentError",
    "parse_document",
]
