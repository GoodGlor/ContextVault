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

    # Dimension of the pgvector embedding column. Tied to the active embedding
    # model (multilingual-e5 / bge-m3 family are 1024-dim); changing it requires
    # a re-embed and a schema migration.
    embedding_dim: int = 1024


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
