"""Application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables or a local .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ContextVault"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://contextvault:contextvault@localhost:5432/contextvault"
    secret_key: str = "change-me-in-production"

    # Local embedding model (sentence-transformers). bge-m3 is multilingual
    # (handles Russian/Ukrainian + English) and needs no query/passage prefixes,
    # so it fits the generic ``embed(texts)`` interface directly. Swapping to the
    # multilingual-e5 family is a config change — see the README embeddings note.
    embedding_model: str = "BAAI/bge-m3"

    # Dimension of the pgvector embedding column. Must match ``embedding_model``'s
    # output width (bge-m3 and multilingual-e5-large are both 1024-dim); changing
    # it requires a re-embed and a schema migration.
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
    # embedding model; higher is stricter. bge-m3 relevant matches sit well above
    # this conservative default.
    retrieval_min_score: float = 0.3

    # Generation (RAG loop). ``llm_provider`` selects the system-default LLM
    # provider the RAG loop generates with (design spec §4/§7); full per-repo
    # routing across providers is a later card. The Google (Gemini) provider
    # authenticates with ``gemini_api_key`` (falling back to the SDK's own
    # GEMINI_API_KEY / GOOGLE_API_KEY resolution when unset) and ``gemini_model``
    # is configurable. ``llm_max_tokens`` caps the generated answer length.
    llm_provider: str = "gemini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_max_tokens: int = 2048

    # JWT session tokens.
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # First-admin bootstrap (see `python -m contextvault.cli create-admin`).
    initial_admin_username: str | None = None
    initial_admin_password: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
