"""Pluggable embedding layer (design spec §7).

Import ``get_embedding_provider`` to obtain the process-wide provider, or the
``EmbeddingProvider`` protocol to type against the abstraction.
"""

from functools import lru_cache

from contextvault.core.config import get_settings
from contextvault.embeddings.base import EmbeddingProvider
from contextvault.embeddings.local import LocalEmbeddingProvider

__all__ = ["EmbeddingProvider", "LocalEmbeddingProvider", "get_embedding_provider"]


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """Return the cached, config-driven embedding provider.

    v1 always returns the local provider; swapping to a paid backend later is a
    change here, not at every call site.
    """
    settings = get_settings()
    return LocalEmbeddingProvider(
        model_name=settings.embedding_model,
        dimension=settings.embedding_dim,
    )
