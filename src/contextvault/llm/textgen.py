"""Plain-text generation via a provider's global key — non-RAG LLM calls.

The RAG loop speaks through ``LLMProvider.answer`` (grounded, cited); some
features — report SQL generation — need a raw completion instead. This module
mirrors :mod:`contextvault.llm.ocr`: standalone per-provider async functions
dispatched by name, every failure wrapped in :class:`TextGenError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anthropic import AsyncAnthropic
from anthropic.types import TextBlock
from openai import AsyncOpenAI

from contextvault.core.config import get_settings

if TYPE_CHECKING:
    from google import genai

__all__ = ["TextGenError", "generate_text"]

_MAX_TOKENS = 2048


class TextGenError(Exception):
    """The provider could not generate text (bad key, network, quota, …)."""


def _genai_client(api_key: str) -> genai.Client:
    from google import genai

    return genai.Client(api_key=api_key)


async def _generate_gemini(api_key: str, model: str, prompt: str) -> str:
    from google.genai import types

    client = _genai_client(api_key)
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=_MAX_TOKENS),
    )
    return (response.text or "").strip()


async def _generate_openai_compatible(
    api_key: str, model: str, prompt: str, base_url: str | None
) -> str:
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    completion = await client.chat.completions.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return (completion.choices[0].message.content or "").strip()


async def _generate_anthropic(api_key: str, model: str, prompt: str) -> str:
    client = AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in message.content if isinstance(b, TextBlock)).strip()


async def generate_text(
    provider: str, api_key: str, model: str, *, prompt: str, base_url: str | None = None
) -> str:
    """Generate a completion for ``prompt`` with ``provider``'s model ``model``."""
    name = provider.lower()
    try:
        if name == "gemini":
            return await _generate_gemini(api_key, model, prompt)
        if name == "openai":
            return await _generate_openai_compatible(api_key, model, prompt, None)
        if name == "openrouter":
            base = base_url or get_settings().openrouter_base_url
            return await _generate_openai_compatible(api_key, model, prompt, base)
        if name == "anthropic":
            return await _generate_anthropic(api_key, model, prompt)
    except TextGenError:
        raise
    except Exception as exc:  # noqa: BLE001 — any SDK/network failure becomes a clean error
        raise TextGenError(f"Could not generate text: {exc}") from exc
    raise TextGenError(f"Unsupported provider: {provider!r}")
