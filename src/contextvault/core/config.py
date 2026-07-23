"""Application settings, loaded from environment / .env."""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Which .env file to load, resolved at import time. Defaults to ``.env``; set
# ``CONTEXTVAULT_ENV_FILE`` to point elsewhere, or to an empty string to disable
# .env loading entirely. Tests set it empty so a developer's local .env never bleeds
# into the suite (settings then come only from real env vars + the defaults below).
_ENV_FILE = os.getenv("CONTEXTVAULT_ENV_FILE", ".env") or None


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables or a local .env."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ContextVault"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://contextvault:contextvault@localhost:5432/contextvault"
    secret_key: str = "change-me-in-production"

    # Embedding model, served by Google's Gemini embedding API (no local model
    # runs on the host). Handles Russian/Ukrainian + English via the asymmetric
    # ``task_type`` (document vs. query) the provider passes per call.
    embedding_model: str = "gemini-embedding-001"

    # Dimension of the pgvector embedding column. Gemini is asked for this width
    # via ``output_dimensionality`` on every call, so it need not match the
    # model's native output size; changing it still requires a re-embed and a
    # schema migration.
    embedding_dim: int = 1024

    # Chunking (ingestion `chunk` stage). Character-based windows sized for
    # retrieval; ``chunk_overlap`` chars are shared between neighbours so a
    # passage split across a boundary still lands whole in some chunk. Must
    # satisfy ``chunk_overlap < chunk_size``.
    chunk_size: int = 1000
    chunk_overlap: int = 150

    # Retrieval (RAG loop). Number of most-similar chunks the vector search
    # returns for a query, before generation. Higher gives the model more
    # context at the cost of prompt size / latency (design spec §4).
    retrieval_top_k: int = 5

    # Minimum cosine similarity (in ``[-1, 1]``) a retrieved chunk must reach to
    # count as relevant. Hits below this are dropped, so a query that finds only
    # weak matches yields no chunks — the signal behind the honest "not in this
    # vault" answer and the knowledge-gap dashboard (design spec §4/§5). Tune per
    # embedding model; higher is stricter. This is a conservative default carried
    # over from the previous local model; Gemini's embedding distribution may
    # warrant re-tuning.
    retrieval_min_score: float = 0.3

    # Generation (RAG loop). ``llm_provider`` selects the system-default LLM
    # provider the RAG loop generates with (design spec §4/§7); full per-repo
    # routing across providers is a later card. Each repository supplies its own
    # provider API key (encrypted at rest — see ``models/repository.py``); there
    # is no process-wide provider-key fallback, so no ``*_api_key`` settings. The
    # ``*_model`` values are only default model ids used when a repo omits one.
    # ``llm_max_tokens`` caps the generated answer length.
    llm_provider: str = "gemini"
    gemini_model: str = "gemini-2.5-flash"
    anthropic_model: str = "claude-opus-4-8"
    openai_model: str = "gpt-4o"
    # OpenRouter is OpenAI-compatible: the same wire format reached through its
    # gateway. Model ids are vendor-namespaced (e.g. ``openai/gpt-4o``,
    # ``anthropic/claude-3.5-sonnet``); ``openrouter_base_url`` is the OpenAI
    # SDK's ``base_url`` override that points the client at the gateway.
    openrouter_model: str = "openai/gpt-4o"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_max_tokens: int = 2048

    # Master key for encrypting provider API keys at rest (see core/crypto.py).
    # A Fernet key sourced from env/secrets, never committed. Unset by default:
    # encryption fails loudly rather than storing plaintext, so it must be set
    # before any provider key is persisted. Generate one with
    # ``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``.
    encryption_key: str | None = None

    # JWT session tokens.
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Onboarding invitations (card #26). How long an issued invite link stays
    # valid before it expires; the admin may override per-invite at issue time.
    invite_expiry_hours: int = 72

    # First-admin bootstrap (see `python -m contextvault.cli create-admin`).
    initial_admin_username: str | None = None
    initial_admin_password: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
