"""Schema-level tests for the core ORM models.

These assert the table/column/constraint shape on ``Base.metadata`` without a
live database, so they run fast and deterministically. Migration application
against a real pgvector database is verified separately (see the PR checks).
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# Importing the package registers every model on Base.metadata.
import contextvault.models  # noqa: F401
from contextvault.core.config import get_settings
from contextvault.db.base import Base
from contextvault.models import LLMProviderName, Repository, SourceKind


def _table(name: str) -> sa.Table:
    assert name in Base.metadata.tables, f"missing table: {name}"
    return Base.metadata.tables[name]


def test_all_core_tables_registered() -> None:
    expected = {"users", "repositories", "sources", "chunks", "grants"}
    assert expected <= set(Base.metadata.tables)


def test_users_columns_and_constraints() -> None:
    users = _table("users")
    cols = users.columns
    assert {"id", "username", "password_hash", "role", "must_change_password"} <= set(cols.keys())
    assert users.c.username.unique is True
    assert users.c.password_hash.nullable is False
    assert users.c.must_change_password.nullable is False
    # role is a native enum with the two v1 values
    role_type = users.c.role.type
    assert isinstance(role_type, sa.Enum)
    assert set(role_type.enums) == {"admin", "user"}


def test_grants_link_users_and_repositories_with_expiry() -> None:
    grants = _table("grants")
    referenced = {fk.column.table.name for fk in grants.foreign_keys}
    assert {"users", "repositories"} <= referenced
    assert "expires_at" in grants.columns
    assert grants.c.expires_at.nullable is True
    # a user may be granted a given repo at most once
    uniques = {
        tuple(sorted(col.name for col in c.columns))
        for c in grants.constraints
        if isinstance(c, sa.UniqueConstraint)
    }
    assert ("repository_id", "user_id") in uniques


def test_repositories_carry_llm_config() -> None:
    """Per-repo LLM config (card #24): provider (native enum), model, encrypted
    key — all nullable, since a repository starts unconfigured (design spec §3)."""
    repos = _table("repositories")
    cols = repos.columns
    assert {"llm_provider", "llm_model", "api_key_encrypted"} <= set(cols.keys())

    provider_type = repos.c.llm_provider.type
    assert isinstance(provider_type, sa.Enum)
    assert set(provider_type.enums) == {"gemini", "openai", "openrouter", "anthropic"}

    # Unconfigured by default: every config column is nullable.
    assert repos.c.llm_provider.nullable is True
    assert repos.c.llm_model.nullable is True
    assert repos.c.api_key_encrypted.nullable is True


def test_repository_llm_configured_requires_all_three_fields() -> None:
    """A repo is answerable only once provider, model, and key are all set — the
    predicate the query endpoint gates on (design spec §3: no system default)."""
    repo = Repository(name="Vault")
    assert repo.llm_configured is False

    repo.llm_provider = LLMProviderName.OPENAI
    assert repo.llm_configured is False
    repo.llm_model = "gpt-4o"
    assert repo.llm_configured is False
    repo.api_key_encrypted = "cipher"
    assert repo.llm_configured is True


def test_sources_belong_to_repository_and_have_kind() -> None:
    sources = _table("sources")
    kind_type = sources.c.kind.type
    assert isinstance(kind_type, sa.Enum)
    assert set(kind_type.enums) == {"document", "admin_note", "image", "web"}
    repo_fk = [fk for fk in sources.c.repository_id.foreign_keys]
    assert repo_fk and repo_fk[0].column.table.name == "repositories"
    assert repo_fk[0].ondelete == "CASCADE"


def test_source_kinds_include_image_and_web() -> None:
    assert SourceKind.IMAGE == "image"
    assert SourceKind.WEB == "web"


def test_source_has_optional_source_url() -> None:
    from contextvault.models import Source

    src = Source(repository_id=None, kind=SourceKind.WEB, title="t", source_url="https://x.test")
    assert src.source_url == "https://x.test"


def test_chunks_carry_vector_and_denormalized_repository() -> None:
    """The repository_id on chunks lets the access filter + vector search run
    as a single SQL query (design spec §4/§6)."""
    chunks = _table("chunks")
    assert isinstance(chunks.c.embedding.type, Vector)
    assert chunks.c.embedding.type.dim == get_settings().embedding_dim
    # denormalized repository_id present alongside source_id
    assert "repository_id" in chunks.columns
    assert "source_id" in chunks.columns
    src_fk = [fk for fk in chunks.c.source_id.foreign_keys]
    assert src_fk and src_fk[0].ondelete == "CASCADE"
