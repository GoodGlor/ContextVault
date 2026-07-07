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
