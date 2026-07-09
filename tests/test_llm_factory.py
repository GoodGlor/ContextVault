"""Tests for the ``get_llm_provider`` factory / default-provider selection (card #21).

The factory reads ``llm_provider`` and returns the matching provider, defaulting
to Gemini. ``genai.Client`` is stubbed so no API key is needed to construct one.
"""

import types
from typing import Any

import pytest

from contextvault.llm import LLMProvider, get_llm_provider
from contextvault.llm.gemini import GeminiLLMProvider
from contextvault.llm.openai import OpenAILLMProvider
from contextvault.llm.openrouter import OpenRouterLLMProvider


def _stub_gemini_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.llm.gemini.genai.Client", lambda **kwargs: object())


def _stub_openai_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.llm.openai.AsyncOpenAI", lambda **kwargs: object())


def _stub_openrouter_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("contextvault.llm.openrouter.AsyncOpenAI", lambda **kwargs: object())


def test_default_provider_is_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_gemini_client(monkeypatch)
    monkeypatch.setattr(
        "contextvault.llm.get_settings",
        lambda: types.SimpleNamespace(llm_provider="gemini"),
    )
    provider = get_llm_provider()
    assert isinstance(provider, GeminiLLMProvider)
    assert isinstance(provider, LLMProvider)


def test_explicit_name_overrides_configured_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_gemini_client(monkeypatch)
    # Case-insensitive; explicit name wins regardless of the setting.
    provider: Any = get_llm_provider("GEMINI")
    assert isinstance(provider, GeminiLLMProvider)


def test_openai_provider_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_openai_client(monkeypatch)
    # Case-insensitive selection, matching the other providers.
    provider: Any = get_llm_provider("OpenAI")
    assert isinstance(provider, OpenAILLMProvider)
    assert isinstance(provider, LLMProvider)


def test_openrouter_provider_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_openrouter_client(monkeypatch)
    # Case-insensitive selection, matching the other providers.
    provider: Any = get_llm_provider("OpenRouter")
    assert isinstance(provider, OpenRouterLLMProvider)
    assert isinstance(provider, LLMProvider)


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="not-yet-wired"):
        get_llm_provider("does-not-exist")
