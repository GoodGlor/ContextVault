"""Enumerated column values shared across models."""

import enum


class Role(enum.StrEnum):
    """Account role. v1 has a single admin, built role-based for later growth."""

    ADMIN = "admin"
    USER = "user"


class LLMProviderName(enum.StrEnum):
    """LLM provider a repository generates answers with (design spec §3).

    Values match the ``get_llm_provider`` factory keys (see ``llm/__init__.py``),
    so per-repo routing (card #25) resolves a stored provider directly. Every
    repository must pick one before it can answer — there is no system default.
    """

    GEMINI = "gemini"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    ANTHROPIC = "anthropic"


class SourceKind(enum.StrEnum):
    """Kind of ingested source: a document, an admin note, an image, or a web page."""

    DOCUMENT = "document"
    ADMIN_NOTE = "admin_note"
    IMAGE = "image"
    WEB = "web"


class SourceStatus(enum.StrEnum):
    """Ingestion state of a source (design spec §7: parse→chunk→embed→store).

    A source is ``PENDING`` on creation, flips to ``PROCESSING`` while the
    pipeline runs, and ends at ``DONE`` or ``FAILED``. ``FAILED`` always pairs
    with a captured error so a failure is recorded, never silent.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class DatabaseType(enum.StrEnum):
    """SQL engine of an admin-connected reporting database (DB-reports spec §2)."""

    POSTGRES = "postgres"
    MYSQL = "mysql"


class ReportStatus(enum.StrEnum):
    """Generation state of a report; mirrors SourceStatus's lifecycle."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
