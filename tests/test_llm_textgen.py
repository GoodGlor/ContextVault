"""Dispatch + error-wrapping contract of the plain-text generation helper."""

import pytest

import contextvault.llm.textgen as textgen
from contextvault.llm.textgen import TextGenError, generate_text


async def test_unknown_provider_raises() -> None:
    with pytest.raises(TextGenError, match="Unsupported provider"):
        await generate_text("mystery", "k", "m", prompt="hi")


async def test_dispatches_to_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(api_key: str, model: str, prompt: str) -> str:
        assert (api_key, model, prompt) == ("k", "m", "hi")
        return "generated"

    monkeypatch.setattr(textgen, "_generate_gemini", fake)
    assert await generate_text("gemini", "k", "m", prompt="hi") == "generated"


async def test_provider_failure_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(api_key: str, model: str, prompt: str) -> str:
        raise RuntimeError("quota")

    monkeypatch.setattr(textgen, "_generate_gemini", boom)
    with pytest.raises(TextGenError, match="quota"):
        await generate_text("gemini", "k", "m", prompt="hi")
