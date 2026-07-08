"""Enumerated column values shared across models."""

import enum


class Role(enum.StrEnum):
    """Account role. v1 has a single admin, built role-based for later growth."""

    ADMIN = "admin"
    USER = "user"


class SourceKind(enum.StrEnum):
    """Kind of ingested source: an uploaded document or an admin-authored note."""

    DOCUMENT = "document"
    ADMIN_NOTE = "admin_note"


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
