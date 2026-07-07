# ContextVault — Design Specification

**Date:** 2026-07-07
**Status:** Draft for review

---

## 1. Concept

ContextVault is a NotebookLM-style, **admin-curated RAG assistant** with **per-user
access control** and **per-repository model choice**.

An admin builds trusted knowledge repositories ("vaults") by ingesting sources.
Users ask questions and get answers **grounded in those sources, with citations** —
scoped strictly to the repositories they've been granted access to. The value is
trust: answers come only from a corpus a known curator controls, every claim links
back to its source, and the assistant says "not in this vault" rather than
hallucinating.

The distinguishing twist versus NotebookLM (which is single-player) is the
**curator/consumer split**: one admin curates for many users, and that asymmetry
enables features NotebookLM cannot have — a knowledge-gap dashboard, admin-verified
answers, and usage analytics.

---

## 2. Roles & accounts

### Roles
- **Admin** (single admin in v1, built role-based so multi-admin is a later
  addition, not a rewrite) — full control: repositories, sources, Admin Notes,
  user management, access grants, per-repo LLM config, analytics, knowledge-gap
  dashboard.
- **User** — queries one granted repository at a time; read-only. Cannot alter
  sources or see repos they haven't been granted.

### Account lifecycle
- **Onboarding — invite links.** Admin generates a single-use, expiring invite
  link; the user opens it and sets their own password. The admin never sees or
  handles the user's password.
- **Recovery — admin temporary password.** Admin generates a random temporary
  password (shown to the admin once, optionally expiring). The user logs in with
  it and is **forced to set a new password** before anything else — enforced by a
  `must_change_password` flag that bounces every request to the change-password
  screen until cleared.
- **Deletion — anonymize.** Admin permanently removes a user (confirmation gated,
  e.g. type-the-username). Access grants cascade-delete. Past questions are
  **detached** from the account ("asked by a deleted user") rather than deleted,
  so the knowledge-gap / analytics signal survives.
- Passwords hashed with **Argon2** throughout (including temp passwords).

---

## 3. Repositories

A repository is a curated corpus plus its own model configuration.

- **Sources** — uploaded documents (PDF, DOCX, plain text) and **Admin Notes**
  (admin-authored answers). Admin Notes are a first-class source type, indexed
  alongside documents but flagged as human-authored and attributed to the admin.
- **Per-repository LLM configuration** (required before a repo can answer):
  - `provider` ∈ { Anthropic, OpenAI, Google, OpenRouter }
  - `model` (e.g. a specific Claude / GPT / Gemini / OpenRouter model id)
  - `api_key` — **encrypted at rest**, masked in the UI (`sk-…•••4f2a`), never
    returned in full after entry.
  - No system default: **every repository must be configured** before use.

---

## 4. Retrieval & generation (the RAG loop)

Users query **one repository at a time** (chosen from those they can access).

Per query:
1. **Embed the question** using the system-wide embedding model.
2. **Vector search filtered to the user's granted repo.** Because vectors live in
   the same PostgreSQL database as access grants (pgvector), the permission filter
   and the similarity search are a single SQL query — the access boundary is
   enforced in the query itself, not in app code layered on top.
3. **Assemble top-k chunks**, each tagged with a citation id (`[1]`, `[2]`, …).
4. **Route to the repository's configured provider** and generate a grounded
   answer.
5. **Return answer + citations**; **log the query**; flag weak/empty retrievals as
   knowledge gaps.

### Provider-agnostic citations
Claude has a native citation feature, but OpenAI/Google/OpenRouter do not. To keep
one uniform citation experience across all providers, ContextVault uses a
**provider-agnostic scheme**: retrieved chunks are numbered, the model is instructed
to cite those numbers, and the backend maps each `[n]` back to the exact source
passage (document + character/page span). Clicking a citation jumps to the
highlighted passage in the source.

### Honest "not in this vault"
If retrieval finds nothing sufficiently relevant, the assistant explicitly states
the answer isn't in the repository instead of answering from the model's training
data. This is what makes a curated vault meaningfully different from a general
chatbot.

---

## 5. Signature features (the trust/curation flywheel)

1. **Honest "not in this vault"** (above).
2. **Knowledge-gap dashboard** — every user question is logged; questions with
   weak/empty retrieval are surfaced to the admin as a prioritized to-do list
   ("N users asked about X, no source covers it").
3. **Admin fills a gap → Admin Note** — the admin writes an answer; it becomes an
   indexed source, **cited to the admin's nickname** with a *Verified* badge. The
   next user who asks gets it automatically. This closes the loop: user demand →
   admin curation → permanently smarter vault.
4. **Query analytics** — what's asked most, which repos are active, who's using
   what (byproduct of having real users).

**Out of v1 (explicitly deferred):** audio overview, auto study-guide/briefing-doc
generation, user "suggest an edit", multi-repo cross-search.

---

## 6. Access model

- Users ↔ repositories is a **many-to-many grant** table.
- Optional grant **expiry** (time-boxed access).
- Every query is **hard-filtered** to the user's granted repositories at the SQL
  level — an access-control concern and a retrieval concern satisfied together.

---

## 7. Architecture & stack

```
        ┌──────────── Admin UI ────────────┐   ┌──── User UI ────┐
        │ repos · sources · Admin Notes     │   │ pick repo · ask │
        │ invites · grants · user mgmt      │   │ answer + cites  │
        │ per-repo LLM config · analytics   │   └────────┬────────┘
        │ knowledge-gap dashboard           │            │
        └────────────────┬──────────────────┘           │
                         │      React SPA (frontend)      │
                         └───────────────┬────────────────┘
                                         │ REST/JSON + JWT
                              ┌──────────▼───────────┐
                              │   FastAPI backend     │
                              │ auth · access checks  │
                              │ ingestion · RAG · LLM │
                              │ routing · analytics   │
                              └───┬─────────┬─────┬───┘
                                  │         │     │
                      ┌───────────▼──┐  ┌───▼───┐ │ per-repo provider
                      │ PostgreSQL   │  │ local │ │ (Anthropic / OpenAI /
                      │ + pgvector   │  │ embed │ │  Google / OpenRouter)
                      │ users        │  │ model │ └──────────────►
                      │ repositories │  └───────┘
                      │ sources      │
                      │ chunks + vec │
                      │ grants       │
                      │ query_log    │
                      │ gaps         │
                      └──────────────┘
```

| Layer | Choice | Notes |
|---|---|---|
| Backend | **Python + FastAPI** | async; matches the project home |
| Database | **PostgreSQL + pgvector** | one DB for users, repos, grants, chunks+vectors, logs; access filter + vector search in one query |
| Embeddings | **Local multilingual model** (`multilingual-e5` / `bge-m3` family) | free, runs locally, nothing leaves the server; behind an interface so paid providers can be added later |
| Generation | **Provider-agnostic LLM interface** | implementations: Anthropic, OpenAI (reused for OpenRouter — OpenAI-compatible wire format), Google |
| Citations | **Provider-agnostic numbered-chunk scheme** | uniform across all providers |
| API keys | **Encrypted at rest** | master key in env/secrets, never in DB or code; decrypted only in memory at call time |
| Auth | **JWT sessions + Argon2** | invite tokens + temp-password reset |
| Frontend | **React SPA** | polished/complete; owns all frontend decisions |

### Key abstractions (isolatable units)
- **`EmbeddingProvider`** — `embed(texts) -> vectors`. One local implementation in
  v1; paid implementations later. The pgvector column dimension is tied to the
  active model; changing models means re-embedding (and possibly a new dimension).
- **`LLMProvider`** — `answer(question, chunks) -> {text, citations}`. Three
  implementations (Anthropic, OpenAI/OpenRouter, Google) behind one interface.
- **Ingestion pipeline** — `parse → chunk → embed → store`, shared by document
  uploads and Admin Notes.
- **Access layer** — grant checks expressed as SQL predicates reused by both the
  API authorization and the retrieval query.

---

## 8. Security notes

- Per-repo provider **API keys encrypted at rest**; masked in UI; never re-shown.
- Passwords (incl. temporary) **Argon2-hashed**; temp passwords force a change and
  may expire.
- Invite tokens **single-use and expiring**.
- Destructive admin actions (delete user) **confirmation-gated**.
- Every retrieval **hard-scoped** to the requesting user's grants at the SQL level.

---

## 9. Suggested build order (for the implementation plan)

This is a sizable system; it should be built in coherent phases rather than one
monolithic plan. Proposed sequencing:

1. **Foundation** — FastAPI skeleton, PostgreSQL + pgvector, core schema (users,
   repositories, sources, chunks, grants), Argon2 auth + JWT, admin bootstrap.
2. **Ingestion + retrieval core** — document parse/chunk/embed pipeline (local
   embeddings), pgvector search, access-filtered retrieval. Verified via API.
3. **Generation + citations** — provider-agnostic `LLMProvider` interface with the
   first provider, numbered-chunk citations, honest "not in this vault".
4. **Multi-provider + per-repo config** — remaining providers, encrypted per-repo
   API keys, repo LLM configuration.
5. **User management** — invite links, temp-password reset, delete/anonymize,
   grants (with expiry).
6. **Curation flywheel** — query logging, knowledge-gap dashboard, Admin Notes as
   sources, Verified badge, analytics.
7. **Frontend** — React SPA covering all admin and user surfaces (built to a
   polished standard).

Each phase gets its own implementation plan and can be verified end-to-end before
the next begins.

---

## 10. Open questions / assumptions

- **Language of content:** assumed multilingual (Russian/Ukrainian + English) →
  multilingual embedding model as default. Revisit if content is English-only.
- **Deployment target** (local/dev vs. hosted) not yet specified — affects secrets
  management and the pgvector/Postgres setup, but not the core design.
