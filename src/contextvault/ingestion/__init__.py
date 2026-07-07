"""Ingestion pipeline: parse → chunk → embed → store (design spec §7).

v1 implements the parse and chunk stages; embedding/storage land in later cards.
"""

from contextvault.ingestion.chunking import TextChunk, chunk_document
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
    "TextChunk",
    "UnsupportedDocumentError",
    "chunk_document",
    "parse_document",
]
