# Custom (OpenAI-compatible) LLM Provider — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single **`custom`** ("Custom, OpenAI-compatible") LLM provider with one **global** endpoint (base URL + *optional* key) so chat, report generation, and image OCR can run against a customer's self-hosted model (Ollama, vLLM, LM Studio, TGI, LocalAI). Embeddings stay on Gemini this phase.

**Architecture:** `custom` reuses the existing OpenAI Chat Completions wire path (exactly as OpenRouter does today). A new nullable `base_url` column on `provider_settings` stores the endpoint; the key column becomes nullable for keyless local servers. One service seam — `get_call_credentials()` — resolves `(api_key, base_url)` for every call site, substituting a harmless placeholder key when the local server is keyless. Five name-dispatch sites (`llm/__init__`, `llm/models`, `llm/ocr`, `llm/textgen`, plus the answer client `llm/openai`) gain a `custom` branch or a `base_url` param.

**Tech Stack:** FastAPI, async SQLAlchemy 2.0, Alembic, Postgres (native `llm_provider` ENUM), `openai` AsyncOpenAI SDK, React/Vite + react-i18next, pytest (async, `db_session` fixture), vitest.

## Global Constraints

- **Provider enum value is exactly `custom`** — must match across `models/enums.py`, the `get_llm_provider` factory key, the five dispatch sites, and the frontend `LLMProvider` union. Case-insensitive dispatch (`name.lower()`), same as siblings.
- **Keyless is first-class.** A `custom` row may have `base_url` set and **no** key. Never store the placeholder. The placeholder key string is `"sk-noauth"`, defined once as `providers.NOAUTH_PLACEHOLDER`.
- **Not air-gapped this phase.** Ingestion still requires a verified **Gemini** key to embed (`deps.get_embedder` unchanged). Do **not** describe `custom` as "nothing leaves your network." The Providers UI must show the embeddings note.
- **`base_url` is not a secret** — stored plaintext, never Fernet-encrypted, and it *is* returned in the provider status (unlike the key, which stays masked).
- **Cloud providers are untouched.** Every `custom` branch is additive; `gemini`/`openai`/`openrouter`/`anthropic` behavior and their key-required gating are unchanged.
- **i18n:** every new user-facing string in **both** `en.json` and `uk.json`. (Provider *option* labels stay English proper nouns, consistent with the existing hardcoded `LLM_PROVIDERS` labels; only descriptive copy — base-URL field, hints, notes, fallbacks — is translated.)
- **TDD throughout:** failing test first, watch it fail, minimal code, watch it pass, commit.

---

## File Structure

**Backend — modify**
- `src/contextvault/models/enums.py` — add `CUSTOM = "custom"`.
- `src/contextvault/models/provider_setting.py` — add `base_url`; make `api_key_encrypted` nullable.
- `migrations/versions/<rev>_custom_provider.py` — **create** (enum value + column + null-relax).
- `src/contextvault/services/providers.py` — base-URL resolution, keyless verify/store, `get_call_credentials`, `NOAUTH_PLACEHOLDER`.
- `src/contextvault/llm/openai.py` — `base_url` param on the answer client.
- `src/contextvault/llm/__init__.py` — `base_url` param + `custom` branch in the factory.
- `src/contextvault/llm/models.py` — `custom` branch (rename `_list_openrouter` → `_list_openai_compatible`).
- `src/contextvault/llm/ocr.py` — `custom` branch.
- `src/contextvault/llm/textgen.py` — `custom` branch.
- `src/contextvault/api/deps.py` — `build_repo_llm` threads `base_url`; drop the key `assert`.
- `src/contextvault/services/reports.py` — resolve via `get_call_credentials`.
- `src/contextvault/services/ingestion.py` — `_ocr_image` via `get_call_credentials` + `base_url`.
- `src/contextvault/api/providers.py` — request/response gain `base_url`; keyless-custom route validation.
- `src/contextvault/api/repositories.py` — `list_llm_models` resolves via credentials, gates on *verified* not *key present*.

**Frontend — modify**
- `frontend/src/api/repositories.ts` — `LLMProvider` union + `LLM_PROVIDERS` entry.
- `frontend/src/api/providers.ts` — `ProviderStatus.base_url`; `setProviderKey` payload.
- `frontend/src/pages/AdminProvidersPage.tsx` — custom row: base-URL field, optional key, embeddings note.
- `frontend/src/pages/AdminRepositoriesPage.tsx` — custom model: free-text input + datalist fallback.
- `frontend/src/i18n/locales/en.json` + `uk.json` — new strings.

**Docs — modify**
- `docs/architecture.md` — providers section: the `custom` provider + the Gemini-embeddings caveat.

**Tests — create/extend**
- `tests/test_models.py` (or new `tests/test_provider_setting_model.py`) — model persistence.
- `tests/test_providers_service.py` — **create** — base-url resolution, keyless verify/store, `get_call_credentials`.
- `tests/test_llm_factory.py`, `tests/test_llm_models.py`, `tests/test_llm_ocr.py`, `tests/test_llm_textgen.py`, `tests/test_llm_openai.py` — `custom` branches.
- `tests/test_providers_api.py` — custom endpoint (keyless, base-url required).
- `tests/test_reports_service.py`, `tests/test_ingestion_pipeline.py` — credential resolution for custom.
- `frontend/src/pages/AdminProvidersPage.test.tsx`, `AdminRepositoriesPage.test.tsx` — custom UI.

---

### Task 1: Data layer — enum value, `base_url` column, nullable key, migration

**Files:**
- Modify: `src/contextvault/models/enums.py:13-24`
- Modify: `src/contextvault/models/provider_setting.py:35`
- Create: `migrations/versions/<rev>_custom_provider.py`
- Test: `tests/test_providers_service.py` (new file; DB-backed, uses `db_session`)

**Interfaces:**
- Produces: `LLMProviderName.CUSTOM == "custom"`; `ProviderSetting.base_url: str | None`; `ProviderSetting.api_key_encrypted: str | None`.

- [ ] **Step 1: Write the failing test** — `tests/test_providers_service.py`:

```python
"""Tests for the provider-settings service: custom (OpenAI-compatible) support.

Covers the data-layer additions (a nullable base_url and a now-optional key) plus
the service seams that resolve a base URL and call credentials, including the
keyless-local-server path. DB-backed tests use the rolled-back ``db_session``.
"""

import uuid
from datetime import UTC, datetime

import pytest

from contextvault.models import LLMProviderName, ProviderSetting
from sqlalchemy.ext.asyncio import AsyncSession


def test_custom_is_a_provider_value() -> None:
    assert LLMProviderName.CUSTOM.value == "custom"


async def test_custom_row_persists_with_base_url_and_no_key(db_session: AsyncSession) -> None:
    row = ProviderSetting(
        provider=LLMProviderName.CUSTOM,
        api_key_encrypted=None,
        base_url="http://localhost:11434/v1",
        verified_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.api_key_encrypted is None
    assert row.base_url == "http://localhost:11434/v1"
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/test_providers_service.py -v`
Expected: FAIL — `AttributeError: CUSTOM` and/or the `base_url`/nullable-key column doesn't exist (or a NOT NULL violation) until the model + migration land.

- [ ] **Step 3: Add the enum value** — `src/contextvault/models/enums.py`, inside `LLMProviderName`, after `ANTHROPIC`:

```python
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"
```

- [ ] **Step 4: Update the model** — `src/contextvault/models/provider_setting.py`. Change the key column to nullable and add `base_url`:

```python
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Custom (OpenAI-compatible) endpoints store their address here; it is not a
    # secret (never encrypted) and, unlike the key, is returned in status responses.
    # NULL for the cloud providers, which use fixed/hardcoded endpoints.
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Update the class docstring's "one API key per vendor" line to note that a custom endpoint may be keyless (a base URL with no key).

- [ ] **Step 5: Create the migration.** Confirm the head first: `uv run alembic heads` (expect `b8c2d5e7f901`). Create `migrations/versions/<rev>_custom_provider.py` (keep the generated revision id; set `down_revision` to the real head):

```python
"""custom openai-compatible provider: enum value, base_url, nullable key

Adds the ``custom`` value to the ``llm_provider`` enum, a nullable ``base_url``
column on ``provider_settings`` (the endpoint address for a self-hosted server),
and relaxes ``api_key_encrypted`` to nullable so a keyless local server can be
stored (base URL only).

Revision ID: c1d2e3f40506
Revises: b8c2d5e7f901
Create Date: 2026-07-24 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f40506"
down_revision: str | None = "b8c2d5e7f901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ permits ADD VALUE inside a transaction as long as the new value
    # is not *used* in the same transaction (it is not — no data references it here).
    op.execute("ALTER TYPE llm_provider ADD VALUE IF NOT EXISTS 'custom'")
    op.add_column("provider_settings", sa.Column("base_url", sa.Text(), nullable=True))
    op.alter_column(
        "provider_settings", "api_key_encrypted", existing_type=sa.Text(), nullable=True
    )


def downgrade() -> None:
    # Postgres cannot drop a single enum value, so 'custom' is left in the type
    # (harmless, unused). Re-tightening the key column will fail if a keyless custom
    # row exists; that is acceptable for a downgrade and documented here.
    op.alter_column(
        "provider_settings", "api_key_encrypted", existing_type=sa.Text(), nullable=False
    )
    op.drop_column("provider_settings", "base_url")
```

- [ ] **Step 6: Apply the migration**

Run: `uv run alembic upgrade head`
Expected: succeeds; `\d provider_settings` shows `base_url text` and `api_key_encrypted` nullable.

- [ ] **Step 7: Run the test — verify it passes**

Run: `uv run pytest tests/test_providers_service.py -v`
Expected: PASS (both tests).

- [ ] **Step 8: Commit**

```bash
git add src/contextvault/models/enums.py src/contextvault/models/provider_setting.py migrations/versions/*_custom_provider.py tests/test_providers_service.py
git commit -m "feat(providers): custom provider enum value + base_url column + nullable key"
```

---

### Task 2: Providers service — base-URL resolution, keyless verify/store, `get_call_credentials`

**Files:**
- Modify: `src/contextvault/services/providers.py`
- Test: `tests/test_providers_service.py` (extend)

**Interfaces:**
- Consumes: `LLMProviderName.CUSTOM`, `ProviderSetting.base_url`, nullable `api_key_encrypted` (Task 1).
- Produces:
  - `NOAUTH_PLACEHOLDER: str = "sk-noauth"`
  - `async get_provider_base_url(session, provider) -> str | None`
  - `async get_call_credentials(session, provider) -> tuple[str, str | None]` — returns `(api_key_or_placeholder, base_url)`.
  - `async verify_key(provider, api_key, *, base_url=None) -> None` (signature extended).
  - `async set_provider_key(session, provider, api_key, *, now, base_url=None) -> ProviderSetting` (signature extended; `api_key` may be `None`).
  - `async get_provider_key(...)` now tolerates a NULL stored key (returns `None`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_providers_service.py`:

```python
from contextvault.services import providers as provider_service


async def test_get_provider_base_url_reads_custom_row(db_session: AsyncSession) -> None:
    db_session.add(
        ProviderSetting(
            provider=LLMProviderName.CUSTOM,
            api_key_encrypted=None,
            base_url="http://gpu-box:8000/v1",
            verified_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    assert (
        await provider_service.get_provider_base_url(db_session, LLMProviderName.CUSTOM)
        == "http://gpu-box:8000/v1"
    )


async def test_call_credentials_uses_placeholder_when_keyless(db_session: AsyncSession) -> None:
    db_session.add(
        ProviderSetting(
            provider=LLMProviderName.CUSTOM,
            api_key_encrypted=None,
            base_url="http://gpu-box:8000/v1",
            verified_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    key, base_url = await provider_service.get_call_credentials(
        db_session, LLMProviderName.CUSTOM
    )
    assert key == provider_service.NOAUTH_PLACEHOLDER
    assert base_url == "http://gpu-box:8000/v1"


async def test_set_custom_stores_base_url_and_optional_key(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok_list_models(provider: str, api_key: str, *, base_url: str | None = None):
        return ["llama3.1:8b"]

    monkeypatch.setattr("contextvault.services.providers.list_models", ok_list_models)
    setting = await provider_service.set_provider_key(
        db_session,
        LLMProviderName.CUSTOM,
        None,  # keyless
        now=datetime.now(UTC),
        base_url="http://localhost:11434/v1",
    )
    assert setting.base_url == "http://localhost:11434/v1"
    assert setting.api_key_encrypted is None
    assert setting.verified_at is not None
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/test_providers_service.py -v`
Expected: FAIL — `AttributeError: get_provider_base_url` / `get_call_credentials` / `NOAUTH_PLACEHOLDER`, and `set_provider_key` rejects the extra `base_url` kwarg.

- [ ] **Step 3: Edit `src/contextvault/services/providers.py`.** Add the constant near the top (after the imports/`ProviderKeyInvalid`):

```python
# OpenAI-compatible servers require *some* Authorization header even when they
# ignore it; a keyless local endpoint gets this harmless placeholder at call time.
# It is never persisted.
NOAUTH_PLACEHOLDER = "sk-noauth"
```

Replace `_base_url_for` with a static helper + the async DB resolver:

```python
def _static_base_url(provider: LLMProviderName) -> str | None:
    """The fixed OpenAI-compatible base URL a provider needs from settings (OpenRouter
    only). Custom endpoints are per-row and resolved via ``get_provider_base_url``."""
    return get_settings().openrouter_base_url if provider == LLMProviderName.OPENROUTER else None


async def get_provider_base_url(session: AsyncSession, provider: LLMProviderName) -> str | None:
    """The OpenAI-compatible base URL for ``provider`` at call time.

    Custom endpoints store their address on the row; OpenRouter uses the settings
    default; every other provider talks to its SDK's own endpoint (``None``)."""
    if provider == LLMProviderName.CUSTOM:
        setting = await get_setting(session, provider)
        return setting.base_url if setting else None
    return _static_base_url(provider)
```

Update `get_provider_key` to tolerate a NULL stored key:

```python
async def get_provider_key(session: AsyncSession, provider: LLMProviderName) -> str | None:
    """The decrypted key for ``provider``, or ``None`` when none is stored (a keyless
    custom endpoint stores no key). Decryption happens only here."""
    setting = await get_setting(session, provider)
    if setting is None or setting.api_key_encrypted is None:
        return None
    return decrypt(setting.api_key_encrypted)
```

Add the credential resolver (place after `get_provider_key`):

```python
async def get_call_credentials(
    session: AsyncSession, provider: LLMProviderName
) -> tuple[str, str | None]:
    """The ``(api_key, base_url)`` to construct a client for ``provider``.

    A keyless custom endpoint yields the placeholder key so the client still sends
    an Authorization header. Cloud providers are gated on a real key upstream, so
    the placeholder is never reached for them."""
    key = await get_provider_key(session, provider)
    base_url = await get_provider_base_url(session, provider)
    return key or NOAUTH_PLACEHOLDER, base_url
```

Extend `verify_key` and `set_provider_key`:

```python
async def verify_key(
    provider: LLMProviderName, api_key: str | None, *, base_url: str | None = None
) -> None:
    """Check the endpoint answers, raising :class:`ProviderKeyInvalid` if not.

    ``base_url`` is the endpoint being saved (custom) — it isn't in the DB yet, so
    it is passed in. A keyless custom endpoint is verified with the placeholder key."""
    try:
        await list_models(
            provider.value,
            api_key or NOAUTH_PLACEHOLDER,
            base_url=base_url or _static_base_url(provider),
        )
    except ModelListError as exc:
        raise ProviderKeyInvalid(str(exc)) from exc


async def set_provider_key(
    session: AsyncSession,
    provider: LLMProviderName,
    api_key: str | None,
    *,
    now: datetime,
    base_url: str | None = None,
) -> ProviderSetting:
    """Verify then store ``provider``'s config (upsert), stamping ``verified_at``.

    ``api_key`` may be ``None`` for a keyless custom endpoint; ``base_url`` is stored
    for custom (``None`` for cloud providers). Stores nothing if verification fails."""
    await verify_key(provider, api_key, base_url=base_url)

    setting = await get_setting(session, provider)
    if setting is None:
        setting = ProviderSetting(provider=provider)
        session.add(setting)
    setting.api_key_encrypted = encrypt(api_key) if api_key else None
    setting.base_url = base_url
    setting.verified_at = now
    await session.commit()
    await session.refresh(setting)
    return setting
```

- [ ] **Step 4: Run the tests — verify they pass**

Run: `uv run pytest tests/test_providers_service.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm no regression in the existing providers API tests**

Run: `uv run pytest tests/test_providers_api.py -v`
Expected: PASS (the extended signatures are backward-compatible — `base_url` defaults to `None`).

- [ ] **Step 6: Commit**

```bash
git add src/contextvault/services/providers.py tests/test_providers_service.py
git commit -m "feat(providers): base-url resolution + keyless verify/store + call credentials"
```

---

### Task 3: Answer path — `base_url` on the OpenAI client + `custom` factory branch

**Files:**
- Modify: `src/contextvault/llm/openai.py:40-58`
- Modify: `src/contextvault/llm/__init__.py:16-50`
- Test: `tests/test_llm_openai.py`, `tests/test_llm_factory.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `OpenAILLMProvider(..., base_url: str | None = None)`; `get_llm_provider(..., base_url: str | None = None)` with a `custom` branch → `OpenAILLMProvider`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_llm_factory.py`:

```python
def test_custom_provider_selectable_with_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "contextvault.llm.openai.AsyncOpenAI",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    provider: Any = get_llm_provider(
        "custom", api_key="sk-noauth", model="llama3.1:8b", base_url="http://localhost:11434/v1"
    )
    assert isinstance(provider, OpenAILLMProvider)
    assert provider._model == "llama3.1:8b"
    assert captured["base_url"] == "http://localhost:11434/v1"
```

Append to `tests/test_llm_openai.py` (mirror `test_default_client_points_at_openrouter` from the OpenRouter suite):

```python
def test_client_receives_base_url(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_async_openai(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("contextvault.llm.openai.AsyncOpenAI", _fake_async_openai)
    OpenAILLMProvider(api_key="sk-noauth", base_url="http://localhost:11434/v1")

    assert captured["base_url"] == "http://localhost:11434/v1"
    assert captured["api_key"] == "sk-noauth"
```

(Match the `Any`/import style already used in `tests/test_llm_openai.py`.)

- [ ] **Step 2: Run — verify they fail**

Run: `uv run pytest tests/test_llm_openai.py tests/test_llm_factory.py -v -k "base_url or custom"`
Expected: FAIL — `OpenAILLMProvider` has no `base_url` kwarg / factory raises `not-yet-wired` for `custom`.

- [ ] **Step 3: Add `base_url` to `OpenAILLMProvider`** — `src/contextvault/llm/openai.py`, in `__init__` add the param and thread it:

```python
    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        base_url: str | None = None,
    ) -> None:
```
and:
```python
        self._client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)
```
Extend the docstring to note `base_url` aims the client at a custom OpenAI-compatible endpoint (defaults to the SDK's OpenAI endpoint when `None`).

- [ ] **Step 4: Add the factory param + `custom` branch** — `src/contextvault/llm/__init__.py`:

```python
def get_llm_provider(
    name: str | None = None,
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
```
Pass `base_url` to the OpenRouter branch and add the `custom` branch before the final `raise`:
```python
    if provider == "openrouter":
        from contextvault.llm.openrouter import OpenRouterLLMProvider

        return OpenRouterLLMProvider(api_key=api_key, model=model, base_url=base_url)
    if provider == "anthropic":
        from contextvault.llm.anthropic import AnthropicLLMProvider

        return AnthropicLLMProvider(api_key=api_key, model=model)
    if provider == "custom":
        # A self-hosted OpenAI-compatible server: reuse the OpenAI answer path aimed
        # at the stored base URL. The repo always supplies a model for custom.
        from contextvault.llm.openai import OpenAILLMProvider

        return OpenAILLMProvider(api_key=api_key, model=model, base_url=base_url)
    raise ValueError(f"unsupported or not-yet-wired LLM provider: {provider!r}")
```
Update the factory docstring to mention the `custom` OpenAI-compatible option and `base_url`.

- [ ] **Step 5: Run — verify pass**

Run: `uv run pytest tests/test_llm_openai.py tests/test_llm_factory.py tests/test_llm_openrouter.py -v`
Expected: PASS (OpenRouter still passes — its `base_url` defaults unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/contextvault/llm/openai.py src/contextvault/llm/__init__.py tests/test_llm_openai.py tests/test_llm_factory.py
git commit -m "feat(llm): thread base_url through the answer client + custom factory branch"
```

---

### Task 4: `custom` branches in model-listing, OCR, and text generation

**Files:**
- Modify: `src/contextvault/llm/models.py:58-97`
- Modify: `src/contextvault/llm/ocr.py:154-168`
- Modify: `src/contextvault/llm/textgen.py:76-90`
- Test: `tests/test_llm_models.py`, `tests/test_llm_ocr.py`, `tests/test_llm_textgen.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `list_models("custom", key, base_url=...)`, `transcribe_image("custom", ..., base_url=...)`, `generate_text("custom", ..., base_url=...)` — each hits the OpenAI-compatible client at `base_url`, returning **all** model ids (no chat-family filter, since local ids are arbitrary).

- [ ] **Step 1: Write the failing tests.** In `tests/test_llm_models.py` add (match the file's existing stub style for `AsyncOpenAI.models.list`):

```python
async def test_custom_lists_all_models_via_base_url(monkeypatch):
    captured = {}

    class _Model:
        def __init__(self, mid): self.id = mid

    class _FakeModels:
        def __aiter__(self):
            async def gen():
                for m in (_Model("llama3.1:8b"), _Model("nomic-embed-text")):
                    yield m
            return gen()

    class _FakeClient:
        def __init__(self, **kwargs): captured.update(kwargs); self.models = _FakeModels()

    monkeypatch.setattr("contextvault.llm.models.AsyncOpenAI", _FakeClient)
    from contextvault.llm.models import list_models
    result = await list_models("custom", "sk-noauth", base_url="http://localhost:11434/v1")
    # No chat-family filter for custom: every id is returned (local names are arbitrary).
    assert result == ["llama3.1:8b", "nomic-embed-text"]
    assert captured["base_url"] == "http://localhost:11434/v1"
```

In `tests/test_llm_ocr.py` and `tests/test_llm_textgen.py`, add a test asserting the `custom` branch constructs `AsyncOpenAI` with the passed `base_url` and returns the transcription/completion (mirror each file's existing OpenRouter/openai-compatible test, changing the provider to `"custom"` and passing `base_url="http://x/v1"`; assert the mock client's `base_url` kwarg equals `"http://x/v1"`).

- [ ] **Step 2: Run — verify they fail**

Run: `uv run pytest tests/test_llm_models.py tests/test_llm_ocr.py tests/test_llm_textgen.py -v -k custom`
Expected: FAIL — `Unsupported provider: 'custom'`.

- [ ] **Step 3: `models.py` — rename the OpenRouter helper and add the branch.** Rename `_list_openrouter` → `_list_openai_compatible` (update the OpenRouter call), then add a `custom` branch:

```python
async def _list_openai_compatible(api_key: str, base_url: str | None) -> list[str]:
    # OpenAI-compatible endpoints (OpenRouter, self-hosted) already expose chat model
    # ids directly; return them all rather than applying the OpenAI-family filter.
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return sorted({m.id async for m in client.models.list()})
```
In `list_models` dispatch:
```python
        if name == "openrouter":
            return await _list_openai_compatible(api_key, base_url)
        if name == "gemini":
            return await _list_gemini(api_key)
        if name == "custom":
            return await _list_openai_compatible(api_key, base_url)
```
Update the `base_url` docstring line to "used for OpenRouter and custom OpenAI-compatible endpoints."

- [ ] **Step 4: `ocr.py` — add the branch** (after the `openrouter` branch, before `anthropic`):

```python
        if name == "custom":
            return await _ocr_openai_compatible(api_key, model, jpeg, base_url)
```
Update the `transcribe_image` docstring: "`base_url` is used for OpenRouter and custom endpoints."

- [ ] **Step 5: `textgen.py` — add the branch** (after `openrouter`, before `anthropic`):

```python
        if name == "custom":
            return await _generate_openai_compatible(api_key, model, prompt, base_url)
```

- [ ] **Step 6: Run — verify pass**

Run: `uv run pytest tests/test_llm_models.py tests/test_llm_ocr.py tests/test_llm_textgen.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/contextvault/llm/models.py src/contextvault/llm/ocr.py src/contextvault/llm/textgen.py tests/test_llm_models.py tests/test_llm_ocr.py tests/test_llm_textgen.py
git commit -m "feat(llm): custom branch in model-listing, OCR, and text generation"
```

---

### Task 5: Call-site plumbing — deps, reports, ingestion, model-listing endpoint

**Files:**
- Modify: `src/contextvault/api/deps.py:122-135`
- Modify: `src/contextvault/services/reports.py:77-83`
- Modify: `src/contextvault/services/ingestion.py:86-96`
- Modify: `src/contextvault/api/repositories.py:281-297`
- Test: `tests/test_reports_service.py`, `tests/test_ingestion_pipeline.py`, `tests/test_reports_api.py` (as applicable)

**Interfaces:**
- Consumes: `provider_service.get_call_credentials` (Task 2), `get_llm_provider(..., base_url=)` (Task 3).

- [ ] **Step 1: Write/extend the failing test.** In `tests/test_ingestion_pipeline.py`, add a test that an image source on a **keyless custom** repo transcribes successfully — i.e. `_ocr_image` no longer asserts a non-null key. Set up a repo with `llm_provider=CUSTOM`, `llm_model="llava"`, a verified keyless custom `ProviderSetting` (base_url set), monkeypatch `contextvault.services.ingestion.transcribe_image` to capture its `base_url` kwarg and return text, and assert ingestion reaches DONE and the captured `base_url` matches. (Mirror the existing image-ingestion test in that file; reuse its repo/source factories.)

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/test_ingestion_pipeline.py -v -k custom`
Expected: FAIL — `AssertionError` on `assert key is not None`, or `transcribe_image` called without `base_url`.

- [ ] **Step 3: `deps.py` `build_repo_llm`** — replace the key lookup + assert + factory call:

```python
    assert repo.llm_provider is not None
    api_key, base_url = await provider_service.get_call_credentials(session, repo.llm_provider)
    return get_llm_provider(
        repo.llm_provider.value, api_key=api_key, model=repo.llm_model, base_url=base_url
    )
```
(Drop the `assert api_key is not None` — a keyless custom endpoint legitimately has no key; `get_call_credentials` yields the placeholder.)

- [ ] **Step 4: `services/ingestion.py` `_ocr_image`** — replace lines around 86-96:

```python
    assert repo.llm_provider is not None and repo.llm_model is not None
    provider, model = repo.llm_provider.value, repo.llm_model
    api_key, base_url = await provider_service.get_call_credentials(session, repo.llm_provider)
    # Release the pooled DB connection before the slow vision call (see note below).
    await session.commit()
    text = await transcribe_image(provider, api_key, model, image=data, base_url=base_url)
```
(Remove the `key = await ...get_provider_key(...)` line and its `assert key is not None`. Keep the surrounding pool-release comment.)

- [ ] **Step 5: `services/reports.py`** — replace the key/base_url block (around 77-83):

```python
        if not await provider_service.repo_is_answerable(session, repo):
            raise ReportGenerationError("The repository's LLM provider has no verified key.")
        api_key, base_url = await provider_service.get_call_credentials(session, repo.llm_provider)
        # Release the pooled app-DB connection before slow LLM/reporting-DB work.
        await session.commit()
```
(Removes the `openrouter`-only inline `base_url` line and the separate `get_provider_key` + `if not api_key` guard, folding the answerability check into one place. `repo.llm_provider` is non-None inside this branch.)

- [ ] **Step 6: `api/repositories.py` `list_llm_models`** — gate on *verified*, resolve via credentials:

```python
    await _get_repo(session, repository_id)  # 404 if the repo is unknown
    if payload.provider not in await provider_service.verified_provider_names(session):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This provider has no verified API key; add one in Providers settings first.",
        )
    key, base_url = await provider_service.get_call_credentials(session, payload.provider)
    try:
        models = await list_models(payload.provider.value, key, base_url=base_url)
    except ModelListError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ListModelsResponse(models=models)
```
Then remove the now-unused `get_settings` import **iff** it is unused elsewhere in the file (run `uv run ruff check src/contextvault/api/repositories.py` — fix any F401 it reports; leave the import if still used).

- [ ] **Step 7: Run the affected suites — verify pass**

Run: `uv run pytest tests/test_ingestion_pipeline.py tests/test_reports_service.py tests/test_reports_api.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/contextvault/api/deps.py src/contextvault/services/reports.py src/contextvault/services/ingestion.py src/contextvault/api/repositories.py tests/
git commit -m "feat(providers): resolve custom credentials at all call sites (chat, reports, OCR, model list)"
```

---

### Task 6: Providers API — optional key + base_url, keyless-custom validation

**Files:**
- Modify: `src/contextvault/api/providers.py`
- Test: `tests/test_providers_api.py`

**Interfaces:**
- Consumes: `set_provider_key(..., base_url=)` (Task 2).
- Produces: `PUT /admin/providers/{provider}` accepts `{api_key?, base_url?}`; `ProviderStatusResponse.base_url: str | None`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_providers_api.py` (reuse its `client`, `_token`, `_auth`, `_stub_verify` helpers):

```python
async def test_custom_requires_base_url(client, db_session, monkeypatch) -> None:
    _stub_verify(monkeypatch, ok=True)
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.put(
        "/admin/providers/custom", json={"api_key": None}, headers=_auth(token)
    )
    assert resp.status_code == 400
    assert "base url" in resp.json()["detail"].lower()


async def test_custom_saves_keyless_with_base_url(client, db_session, monkeypatch) -> None:
    _stub_verify(monkeypatch, ok=True)
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.put(
        "/admin/providers/custom",
        json={"base_url": "http://localhost:11434/v1"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "custom"
    assert body["verified"] is True
    assert body["base_url"] == "http://localhost:11434/v1"
    assert body["api_key_masked"] is None


async def test_cloud_provider_still_requires_key(client, db_session, monkeypatch) -> None:
    _stub_verify(monkeypatch, ok=True)
    token = await _token(client, db_session, Role.ADMIN)
    resp = await client.put("/admin/providers/openai", json={"base_url": None}, headers=_auth(token))
    assert resp.status_code == 400
```

- [ ] **Step 2: Run — verify they fail**

Run: `uv run pytest tests/test_providers_api.py -v -k "custom or cloud_provider_still"`
Expected: FAIL — request model rejects missing `api_key` (currently required) / no `base_url` field / response has no `base_url`.

- [ ] **Step 3: Edit `src/contextvault/api/providers.py`.** Request + response models:

```python
class ProviderKeyRequest(BaseModel):
    """The config to store for a provider (verified before it is saved).

    ``api_key`` is optional so a keyless custom (OpenAI-compatible) endpoint can be
    saved with only a ``base_url``; cloud providers still require a key (enforced in
    the route, which knows the provider from the path)."""

    api_key: str | None = None
    base_url: str | None = None


class ProviderStatusResponse(BaseModel):
    provider: LLMProviderName
    configured: bool
    verified: bool
    api_key_masked: str | None
    base_url: str | None
```

`_status` — include `base_url`:
```python
def _status(provider: LLMProviderName, setting: ProviderSetting | None) -> ProviderStatusResponse:
    masked = (
        mask_key(decrypt(setting.api_key_encrypted))
        if setting and setting.api_key_encrypted is not None
        else None
    )
    return ProviderStatusResponse(
        provider=provider,
        configured=setting is not None,
        verified=bool(setting and setting.verified_at is not None),
        api_key_masked=masked,
        base_url=setting.base_url if setting else None,
    )
```

`set_provider` route body — provider-aware validation:
```python
    key = (payload.api_key or "").strip() or None
    base_url = (payload.base_url or "").strip() or None
    if provider == LLMProviderName.CUSTOM:
        if not base_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A base URL is required for a custom OpenAI-compatible endpoint.",
            )
    elif not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="API key is required."
        )
    try:
        setting = await provider_service.set_provider_key(
            session, provider, key, now=datetime.now(UTC), base_url=base_url
        )
    except provider_service.ProviderKeyInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _status(provider, setting)
```

- [ ] **Step 4: Run — verify pass (incl. the existing suite)**

Run: `uv run pytest tests/test_providers_api.py -v`
Expected: PASS (existing cloud-key tests still green — they send `api_key` and get a masked key + `base_url: null`).

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/api/providers.py tests/test_providers_api.py
git commit -m "feat(providers-api): optional key + base_url, keyless-custom validation"
```

---

### Task 7: Frontend API layer — provider union, labels, provider-key payload

**Files:**
- Modify: `frontend/src/api/repositories.ts:18-26`
- Modify: `frontend/src/api/providers.ts`
- Test: covered indirectly by Tasks 8–9 component tests (no standalone test file for these thin clients).

**Interfaces:**
- Produces: `LLMProvider` includes `"custom"`; `LLM_PROVIDERS` has a custom entry; `ProviderStatus.base_url`; `setProviderKey(provider, { apiKey?, baseUrl? })`.

- [ ] **Step 1: `repositories.ts`** — extend the union and the labels array:

```ts
export type LLMProvider = "gemini" | "openai" | "openrouter" | "anthropic" | "custom";

export const LLM_PROVIDERS: { value: LLMProvider; label: string }[] = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Google (Gemini)" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "custom", label: "Custom (local / self-hosted)" },
];
```

- [ ] **Step 2: `providers.ts`** — add `base_url` to the status and change the setter signature:

```ts
export interface ProviderStatus {
  provider: LLMProvider;
  configured: boolean;
  verified: boolean;
  api_key_masked: string | null;
  base_url: string | null;
}

/** Every provider with its key status (admin-only). Custom endpoints appear here too. */
export function listProviders(): Promise<ProviderStatus[]> {
  return api.get<ProviderStatus[]>("/admin/providers");
}

/** Store (and first verify) a provider's config. Cloud providers need `apiKey`; a
 *  custom OpenAI-compatible endpoint needs `baseUrl` and may omit the key. */
export function setProviderKey(
  provider: LLMProvider,
  input: { apiKey?: string; baseUrl?: string },
): Promise<ProviderStatus> {
  return api.put<ProviderStatus>(`/admin/providers/${provider}`, {
    api_key: input.apiKey ?? null,
    base_url: input.baseUrl ?? null,
  });
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: FAILS in `AdminProvidersPage.tsx` (old `setProviderKey(provider, apiKey)` call site) — that's expected; Task 8 fixes it. Do not fix here beyond the API files.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/repositories.ts frontend/src/api/providers.ts
git commit -m "feat(fe-api): custom provider in union/labels + base_url in provider payload"
```

*(This task intentionally leaves the tree typecheck-red until Task 8; note it in the ledger. Verify Task 8 in the same session immediately after.)*

---

### Task 8: Providers page — custom row (base URL, optional key, embeddings note)

**Files:**
- Modify: `frontend/src/pages/AdminProvidersPage.tsx`
- Test: `frontend/src/pages/AdminProvidersPage.test.tsx`

**Interfaces:**
- Consumes: `setProviderKey(provider, { apiKey?, baseUrl? })`, `ProviderStatus.base_url` (Task 7); i18n keys (Task 10).

- [ ] **Step 1: Write the failing test.** In `AdminProvidersPage.test.tsx` add a case: given `listProviders` resolves a `custom` row (`configured: false, verified: false, api_key_masked: null, base_url: null`), the custom row renders a **Base URL** field and an embeddings note; saving with only a base URL calls `setProviderKey("custom", { baseUrl: "http://localhost:11434/v1", apiKey: "" })` (assert the mocked `setProviderKey` received a `baseUrl`). Follow the file's existing mocking of `../api/providers`.

- [ ] **Step 2: Run — verify it fails**

Run: `cd frontend && npm run test -- src/pages/AdminProvidersPage.test.tsx`
Expected: FAIL — no base-URL field; and/or typecheck error at the old call site.

- [ ] **Step 3: Update `ProviderRow`.** Add `baseUrl` state, branch on `isCustom`, fix the `setProviderKey` call, render the extra fields for custom. Replace the `ProviderRow` body:

```tsx
function ProviderRow({
  status,
  onChanged,
}: {
  status: ProviderStatus;
  onChanged: (updated: ProviderStatus) => void;
}): ReactNode {
  const { t } = useTranslation();
  const isCustom = status.provider === "custom";
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(status.base_url ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSave = isCustom ? baseUrl.trim() !== "" : apiKey.trim() !== "";

  const onSave = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await setProviderKey(status.provider, {
        apiKey: apiKey.trim() || undefined,
        baseUrl: isCustom ? baseUrl.trim() : undefined,
      });
      onChanged(updated);
      setApiKey("");
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err, t("providers.couldNotSave")));
    } finally {
      setSaving(false);
    }
  };

  const onRemove = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await deleteProviderKey(status.provider);
      setBaseUrl("");
      onChanged({ ...status, configured: false, verified: false, api_key_masked: null, base_url: null });
    } catch (err) {
      setError(errorMessage(err, t("providers.couldNotRemove")));
    } finally {
      setSaving(false);
    }
  };

  const keyId = `provider-key-${status.provider}`;
  const urlId = `provider-url-${status.provider}`;

  return (
    <li className="provider-item">
      <form className="provider-form" onSubmit={onSave}>
        <div className="provider-head">
          <span className="provider-name">{PROVIDER_LABEL[status.provider]}</span>
          <span className={status.verified ? "badge configured" : "badge unconfigured"}>
            {status.verified ? t("providers.verified") : t("providers.notSet")}
          </span>
          {status.api_key_masked !== null && (
            <span className="current-key">
              {t("providers.currentKey", { value: status.api_key_masked })}
            </span>
          )}
        </div>

        {isCustom && (
          <>
            <label htmlFor={urlId}>{t("providers.baseUrl")}</label>
            <input
              id={urlId}
              type="url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={t("providers.baseUrlPlaceholder")}
            />
            <p className="notice">{t("providers.customEmbeddingsNote")}</p>
          </>
        )}

        <label htmlFor={keyId}>
          {isCustom ? t("providers.apiKeyOptional") : t("providers.apiKey")}
        </label>
        <input
          id={keyId}
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={status.configured ? t("providers.replacePlaceholder") : ""}
        />
        <button type="submit" disabled={saving || !canSave}>
          {saving ? t("providers.saving") : t("providers.saveKey")}
        </button>
        {status.configured && (
          <button type="button" onClick={onRemove} disabled={saving}>
            {t("providers.removeKey")}
          </button>
        )}
        {saved && <p className="success">{t("providers.saved")}</p>}
        {error !== null && <p className="error">{error}</p>}
      </form>
    </li>
  );
}
```

- [ ] **Step 4: Run — verify pass + typecheck**

Run: `cd frontend && npm run test -- src/pages/AdminProvidersPage.test.tsx && npm run typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/AdminProvidersPage.tsx frontend/src/pages/AdminProvidersPage.test.tsx
git commit -m "feat(fe): custom provider row — base URL, optional key, embeddings note"
```

---

### Task 9: Repository config — free-text model with datalist fallback for custom

**Files:**
- Modify: `frontend/src/pages/AdminRepositoriesPage.tsx:388-409`
- Test: `frontend/src/pages/AdminRepositoriesPage.test.tsx`

**Interfaces:**
- Consumes: `LLM_PROVIDERS` incl. custom (Task 7); i18n keys (Task 10).
- Behavior: when `provider === "custom"`, the model field is a free-text `<input>` (arbitrary local ids like `llama3.1:8b`) with a `<datalist>` of any models `Load models` fetched from the endpoint. Other providers keep the `<select>`.

- [ ] **Step 1: Write the failing test.** In `AdminRepositoriesPage.test.tsx` add a case: with a verified `custom` provider selected and `listModels` returning `[]` (server without `/v1/models`), a **text input** for the model is present and typing `llama3.1:8b` + save calls `setLlmConfig(id, { provider: "custom", model: "llama3.1:8b" })`. Follow the file's existing mock setup for `../api/providers` / `../api/repositories`.

- [ ] **Step 2: Run — verify it fails**

Run: `cd frontend && npm run test -- src/pages/AdminRepositoriesPage.test.tsx`
Expected: FAIL — for custom with no models, today only the (empty) `<select>` path exists, so no editable model field appears.

- [ ] **Step 3: Branch the model field on custom.** Replace the model-field block (the `{modelOptions.length > 0 && ( ... <select> ... )}` region, ~388-409) with:

```tsx
      {/* Cloud providers pick from the fetched catalogue; a custom OpenAI-compatible
          endpoint takes a free-typed model id (local names are arbitrary), with any
          fetched ids offered as datalist suggestions. */}
      {provider === "custom" ? (
        <>
          <label htmlFor={modelSelectId}>{t("repositories.model")}</label>
          <input
            id={modelSelectId}
            list={`${modelSelectId}-list`}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={t("repositories.modelManualPlaceholder")}
            required
          />
          <datalist id={`${modelSelectId}-list`}>
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </>
      ) : (
        modelOptions.length > 0 && (
          <>
            <label htmlFor={modelSelectId}>{t("repositories.model")}</label>
            <select
              id={modelSelectId}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              required
            >
              {!modelOptions.includes(model) && (
                <option value="">{t("repositories.chooseModelPlaceholder")}</option>
              )}
              {modelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </>
        )
      )}
```

(For custom, `Load models` still works — it fills `models`, which become datalist suggestions; an empty list is no longer a dead end because the input is free-text. Leave the existing `noModels` messaging as-is for cloud providers.)

- [ ] **Step 4: Run — verify pass + typecheck**

Run: `cd frontend && npm run test -- src/pages/AdminRepositoriesPage.test.tsx && npm run typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/AdminRepositoriesPage.tsx frontend/src/pages/AdminRepositoriesPage.test.tsx
git commit -m "feat(fe): free-text model with datalist fallback for custom provider"
```

---

### Task 10: i18n — EN + UK strings

**Files:**
- Modify: `frontend/src/i18n/locales/en.json`
- Modify: `frontend/src/i18n/locales/uk.json`
- Test: `cd frontend && npm run test` (existing i18n key-parity test if present) + `npm run build`

**Interfaces:**
- Produces: the keys used in Tasks 8–9.

- [ ] **Step 1:** Add to `en.json` under `providers`:

```json
    "apiKeyOptional": "API key (optional)",
    "baseUrl": "Base URL",
    "baseUrlPlaceholder": "http://localhost:11434/v1",
    "customEmbeddingsNote": "Chat, reports, and image reading run on your server. Document embedding still uses Gemini in this version."
```
and under `repositories`:
```json
    "modelManualPlaceholder": "e.g. llama3.1:8b"
```

- [ ] **Step 2:** Add the same keys to `uk.json`:

```json
    "apiKeyOptional": "API-ключ (необов’язково)",
    "baseUrl": "Базова URL-адреса",
    "baseUrlPlaceholder": "http://localhost:11434/v1",
    "customEmbeddingsNote": "Чат, звіти та розпізнавання зображень працюють на вашому сервері. Ембединги документів у цій версії все ще використовують Gemini."
```
and under `repositories`:
```json
    "modelManualPlaceholder": "напр. llama3.1:8b"
```

- [ ] **Step 3: Verify EN/UK key parity and build**

Run: `cd frontend && npm run test && npm run build`
Expected: PASS (no missing-key/parity failures; build clean). If the repo has a key-parity test, it must be green.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/locales/en.json frontend/src/i18n/locales/uk.json
git commit -m "i18n(providers): custom endpoint strings (EN + UK)"
```

---

### Task 11: Docs — architecture note + the Gemini-embeddings caveat

**Files:**
- Modify: `docs/architecture.md`
- Test: none (docs); final review reads it.

- [ ] **Step 1:** In `docs/architecture.md`, in the providers/LLM section, add a short subsection documenting: the `custom` (OpenAI-compatible) provider; one global endpoint (`base_url` on `provider_settings`, key optional); that it reuses the OpenAI answer/report/OCR path; per-repo model selection with free-text entry; and the explicit caveat that **embeddings still require a verified Gemini key** (not air-gapped) — pointing to the spec's Phase 2 for the local-embeddings/air-gap direction. Reference the spec file `docs/superpowers/specs/2026-07-24-custom-openai-compatible-provider.md`.

- [ ] **Step 2:** If `docs/architecture.md` enumerates the provider list or a dispatch diagram, update it to include `custom` and note the single `get_call_credentials` resolution seam.

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "docs(architecture): custom OpenAI-compatible provider + embeddings caveat"
```

---

## Final verification (before finishing the branch)

- [ ] **Backend full suite + lint + types + migrations:**
  `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run alembic upgrade head && uv run pytest`
  Expected: all green.
- [ ] **Frontend full gate:**
  `cd frontend && npm run lint && npm run format:check && npm run typecheck && npm run test && npm run build`
  Expected: all green.
- [ ] **Manual smoke (optional, if an OpenAI-compatible server is handy):** add a `custom` provider with `http://localhost:11434/v1` (Ollama, keyless), verify it saves + verifies, select it on a repo, load/enter a model, ask a question. Confirm ingestion of a *text* doc still requires a Gemini key (the documented caveat).
- [ ] Note deferred follow-ups for the final review: provider **e2e** selector updates (CI doesn't run e2e); **SSRF** hardening for the admin-supplied `base_url` (shared with `services/web_source.py`); Phase 2 (local embeddings + per-dimension vector tables) and Phase 3 (Ollama-native UX).
```
