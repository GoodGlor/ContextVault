"""LLM generation layer: the provider-agnostic ``answer`` contract.

``LLMProvider`` is the interface every vendor implementation satisfies;
``Answer``/``Citation`` are the shared result schema (answer text + numbered
citations mapping ``[n]`` back to a source span). ``get_llm_provider`` returns the
system-default provider selected by the ``llm_provider`` setting — the seam the
RAG loop generates through, so call sites depend only on the contract.
"""

from contextvault.core.config import get_settings
from contextvault.llm.base import Answer, Citation, LLMProvider

__all__ = ["Answer", "Citation", "LLMProvider", "get_llm_provider"]


def get_llm_provider(name: str | None = None) -> LLMProvider:
    """Return the configured LLM provider (defaults to the ``llm_provider`` setting).

    Providers are imported lazily so importing this package never drags in a
    vendor SDK — only the selected provider's SDK loads. The Google (Gemini),
    OpenAI (ChatGPT), and OpenRouter providers are wired; the Anthropic provider
    joins this factory when per-provider routing lands (design spec §4/§7).
    """
    provider = (name or get_settings().llm_provider).lower()
    if provider == "gemini":
        from contextvault.llm.gemini import GeminiLLMProvider

        return GeminiLLMProvider()
    if provider == "openai":
        from contextvault.llm.openai import OpenAILLMProvider

        return OpenAILLMProvider()
    if provider == "openrouter":
        from contextvault.llm.openrouter import OpenRouterLLMProvider

        return OpenRouterLLMProvider()
    raise ValueError(f"unsupported or not-yet-wired LLM provider: {provider!r}")
