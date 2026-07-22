# Dynamic LLM model dropdown — design

- **Date:** 2026-07-22
- **Status:** Approved, ready to implement
- **Feature:** B of a three-feature request (A: HEIC — shipped #101; B: this; C: EN/UK i18n).

## Goal

After an admin enters a provider API key, let them **fetch that provider's list of
available models** and pick one from a dropdown, instead of typing a model id by hand.
Providers: OpenAI, Anthropic, Google (Gemini), and OpenRouter.

## Decisions (from brainstorming)

- **Trigger:** an explicit **"Load models"** button (not auto-on-key).
- **Field:** **dropdown + manual fallback** — a `<datalist>`-backed `<input>` so the
  admin can pick a fetched model or still type an unlisted one; a fetch failure never
  blocks saving.
- **Providers:** all four configured providers.

## Background

Config is **per-repository** (`Repository.llm_provider` / `llm_model` (free-text) /
`api_key_encrypted`). The key is Fernet-encrypted (`core/crypto.py`) and only ever
returned masked. Providers are built via `llm/get_llm_provider`. There is **no existing
"list models" call**. The Model field is a free-text `<input>` in `RepoConfigPanel`
(`AdminRepositoriesPage.tsx`).

## Design

### Backend — `llm/models.py` (new)

`async def list_models(provider, api_key, *, base_url=None) -> list[str]` dispatches per
provider using each SDK's list call, then applies light chat-model filtering:

- **Anthropic:** `AsyncAnthropic(api_key=…).models.list()` → collect `.id` (Claude models
  only — no filtering needed).
- **OpenAI:** `AsyncOpenAI(api_key=…).models.list()` → filter to chat families
  (`gpt-*`, `o<digit>*`, `chatgpt-*`), sorted/deduped. OpenAI's list includes
  embeddings/tts/whisper/etc., which we drop.
- **Gemini:** `genai.Client(api_key=…).aio.models.list()` → keep models whose
  `supported_actions` include `generateContent`; strip the `models/` prefix.
- **OpenRouter:** `AsyncOpenAI(api_key=…, base_url=…).models.list()` → return all ids
  (already namespaced chat models), sorted.

Any provider/auth/network failure is wrapped in a typed **`ModelListError`** carrying a
clean message.

### Backend — endpoint

`POST /repositories/{id}/llm-models` (admin-only). Body
`ListModelsRequest {provider: LLMProviderName, api_key: str | None = None}`:

- If `api_key` is non-blank → use it (the just-typed key).
- Else → decrypt and use the repo's **stored** key (re-loading for a configured repo).
- If neither is available → `400` "No API key available to list models."

Returns `ListModelsResponse {models: list[str]}`. `ModelListError` → `400` with its
message. Unknown repo → `404`. This keeps the endpoint repo-scoped so it can fall back
to the stored key without the client re-sending it.

### Frontend — `RepoConfigPanel`

- Add a **"Load models"** button. On click → `listModels(repo.id, {provider, api_key})`
  (send the entered key if present, else omit to use the stored key) → store the returned
  list.
- The Model field becomes `<input list={modelListId} …>` backed by a `<datalist>` of the
  fetched models — a dropdown you can still type into. Existing `model` state, label, and
  save flow are unchanged.
- Inline loading + error states for the fetch.
- New API client fn `listModels` in `api/repositories.ts`.

## Testing (TDD)

- **`tests/test_llm_models.py`** — `list_models` per provider with **mocked SDK clients**
  (monkeypatched constructors returning fakes), asserting the collected ids and the
  OpenAI/Gemini filters; a provider error → `ModelListError`.
- **`tests/test_repositories_api.py`** — the endpoint: entered-key path, stored-key
  fallback, no-key → 400, unknown repo → 404, non-admin → 403, `ModelListError` → 400
  (all with `list_models` monkeypatched — no network).
- **Frontend** (`AdminRepositoriesPage.test.tsx`) — "Load models" populates the datalist
  options; a failed fetch shows an error; manual typing into Model still works.
- **e2e** (`repositories` spec) — clicking "Load models" with an invalid/empty key
  surfaces a graceful error end-to-end (proves endpoint + UI wiring without needing a
  real provider key), and the Model input + datalist render.

## Out of scope

- Validating that the saved model id is actually in the fetched list (manual fallback is
  intentional).
- Caching model lists; per-provider capability metadata beyond chat filtering.
- Feature C (i18n).
