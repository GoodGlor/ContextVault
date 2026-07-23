# Persisted conversations + admin gap rejection — design

- **Date:** 2026-07-23
- **Status:** Approved, ready to plan
- **Feature:** Two independent additions that persist things currently held
  ephemerally. (1) Save each user's chat conversation per repository in the
  database so a page reload restores it. (2) Let an admin **reject** a knowledge
  gap with a required written explanation, and keep those rejections.

## Goal

1. **Persisted conversations.** Store the query-page conversation server-side,
   keyed per `(user, repository)`, so reloading the page (or returning later)
   restores the exact thread — including each past answer's citations and cited
   sources. Make the server, not the client, the authority on conversation
   history.
2. **Admin gap rejection.** On the admin knowledge-gaps view, add a **Reject**
   action beside the existing "Answer this gap". Rejecting requires a reason,
   is persisted, removes the gap from the active list, and is reviewable in a
   separate "Rejected gaps" section.

These two parts are independent and could ship separately; they share one
Alembic migration.

## Background / current state

- **Conversation "memory" is client-held only.** `frontend/src/pages/QueryPage.tsx`
  keeps the thread in React `useState` (`turns`), rebuilds a `history` array from
  it, and re-sends that on every `POST /repositories/{id}/query`. A page reload
  wipes the thread; switching repos calls `setTurns([])`. Nothing about the
  thread is stored server-side. `POST .../query` only writes a per-question
  `QueryLog` row (`question`, `top_score`, `chunk_count`, `not_in_vault`) — not
  the answer, not the thread. The backend threads `history` into the LLM prompt
  transiently via `LLMProvider.answer(question, chunks, history)` and, for
  follow-ups, prepends the previous question to shape the vector search only.
  `MAX_HISTORY_TURNS = 10` (`api/query.py`) caps the prompt history tail.
- **Knowledge gaps are read-only aggregates.** `GET /repositories/{id}/knowledge-gaps`
  (admin-only, `require_admin`) returns `list[KnowledgeGapResponse]`
  (`question`, `ask_count`, `user_count`, `last_asked_at`) derived live from
  `QueryLog` where `not_in_vault IS true`, `GROUP BY` a normalized question
  (`services/knowledge_gaps.py` `_NORMALIZED_QUESTION = normalized_question(...)`).
  There is no gap entity, no status, no mutation. The only admin action today is
  **Answer this gap** → `POST /repositories/{id}/admin-notes` creating an
  `admin_note` Source titled with the gap's question; the frontend
  (`AdminInsightsPage.tsx` → `KnowledgeGapsPanel` → `GapRow`) optimistically
  removes the answered gap from the list. No reject/dismiss/resolve concept
  exists anywhere.

## Existing building blocks this design reuses

- **Per-(user, repo) precedent:** `Grant` has `UniqueConstraint(user_id, repository_id)`
  with both FKs `ON DELETE CASCADE` — the conversation table mirrors this shape.
- **Mixins:** every model uses `UUIDPrimaryKeyMixin` (app-side `uuid4` PK) +
  `TimestampMixin` (DB-side `created_at`/`updated_at`, tz-aware). New tables
  follow the same MRO: `class X(UUIDPrimaryKeyMixin, TimestampMixin, Base)`.
- **Session/DI:** `get_session` (`db/session.py`), request-scoped async session.
- **Auth:** `get_current_user` (`api/deps.py`); `require_admin = require_role(Role.ADMIN)`;
  active-grant enforcement via `grant_service.has_active_grant(session, user.id, repo_id)`.
- **Query normalization:** `normalized_question(...)` (shared by analytics +
  knowledge gaps) is reused verbatim for the gap-rejection identity.
- **Alembic:** current head `d4f1a2b7c9e0`. Autogenerate is wired
  (`migrations/env.py` sets `target_metadata = Base.metadata`). New tables need
  no new Postgres enum types, so no `create_type=False` concerns.

## Decisions (locked)

1. **Restore fidelity:** store **full turns** — each answer with its citations
   and cited-source snapshots — so a restored conversation renders identically
   (working citation highlighting + source panel) even if a source is later
   deleted.
2. **Threads:** **one** conversation per `(user, repo)` (matches today's
   one-thread-per-repo UX). A **Clear conversation** button wipes it.
3. **History authority:** the **server** owns history — it loads the last
   `MAX_HISTORY_TURNS` turns from the DB to build the LLM prompt. The client no
   longer sends `history`; the `QueryRequest.history` field is removed and the
   frontend stops building it.
4. **Reject behavior:** rejecting **hides** the gap from the active list and is
   reviewable in a separate **Rejected gaps** list (reason + admin + date).
   Undo/restore is **out of scope for v1**.

## Design — Part 1: persisted conversations

### Data model (2 new tables, normalized)

`conversations` (`models/conversation.py`, `class Conversation`):
- `user_id` → FK `users.id` `ondelete="CASCADE"`, not null, indexed.
- `repository_id` → FK `repositories.id` `ondelete="CASCADE"`, not null, indexed.
- `__table_args__ = (UniqueConstraint("user_id", "repository_id"),)`.
- UUID PK + timestamps via mixins.

`conversation_turns` (`models/conversation_turn.py`, `class ConversationTurn`):
- `conversation_id` → FK `conversations.id` `ondelete="CASCADE"`, not null, indexed.
- `ordinal: int` — 0-based position within the conversation.
- `question: str` (`Text`, not null).
- `answer: str` (`Text`, not null).
- `not_in_vault: bool` (not null).
- `citations` — `JSONB`, not null, list of citation dicts
  (`{number, chunk_id, source_id, char_start, char_end}`), the snapshot returned
  by `QueryResponse.citations`.
- `sources` — `JSONB`, not null, list of source-reference dicts
  (`{id, title, original_filename, kind, verified, author}`), the snapshot
  returned by `QueryResponse.sources`.
- UUID PK + timestamps via mixins.

Citations/sources are **display snapshots** (JSONB), not foreign keys — the
restored conversation preserves what the user saw at answer time, independent of
later source edits/deletes. Normalized turn rows (vs a single JSON blob) avoid
read-modify-write races and match the codebase's one-table-per-concept style.

### Service (`services/conversations.py`)

- `get_or_create_conversation(session, user_id, repository_id) -> Conversation`.
- `list_turns(session, conversation_id) -> list[ConversationTurn]` (ordered by
  `ordinal`).
- `recent_history(session, conversation_id, limit) -> list[tuple[str, str]]` —
  the last `limit` `(question, answer)` pairs, for the LLM prompt.
- `append_turn(session, conversation_id, *, question, answer, not_in_vault,
  citations, sources) -> ConversationTurn` — computes the next `ordinal`.
- `clear_conversation(session, user_id, repository_id) -> None` — deletes the
  conversation row (turns cascade).

### Query endpoint changes (`api/query.py`)

`POST /repositories/{id}/query` (unchanged 404/403/409 guards) now:
1. `conversation = get_or_create_conversation(session, user.id, repository_id)`.
2. `history = recent_history(session, conversation.id, MAX_HISTORY_TURNS)` —
   replaces the client-sent history for both the vector-search contextualization
   and `provider.answer(...)`.
3. After producing the `Answer`, build the citation/source response snapshots (as
   today), then `append_turn(...)` with question + answer text + `not_in_vault`
   + the citation/source snapshot dicts.
4. `log_query(...)` (unchanged) and `session.commit()`.

`QueryRequest` drops the `history` field. `ConversationTurn` (the request
Pydantic model in `api/query.py`) is removed.

### New conversation endpoints (`api/conversations.py`, user-scoped)

Both require an authenticated user **and** an active grant (same gate as
`/query`); a user only ever sees their own conversation.

- `GET /repositories/{id}/conversation -> ConversationResponse`
  - `ConversationResponse = { turns: list[ConversationTurnResponse] }` where
    `ConversationTurnResponse = { question, answer, not_in_vault, citations,
    sources }` — shaped so the frontend maps each turn straight into its existing
    `Turn`/`QueryResult`. Empty `turns` when no conversation exists yet (200, not
    404).
- `DELETE /repositories/{id}/conversation -> 204` — the Clear button.

### Frontend (`QueryPage.tsx`, `api/conversations.ts`, `api/query.ts`)

- New `api/conversations.ts`: `getConversation(repoId)`, `clearConversation(repoId)`.
- On repo-select/mount, `getConversation(selected)` and hydrate `turns` from the
  restored turns (replacing the `setTurns([])` on switch). Each restored turn
  becomes a `Turn` with a `QueryResult` built from the stored snapshot.
- Add a **Clear conversation** button (visible when the thread is non-empty) →
  `clearConversation(selected)` then `setTurns([])`.
- `api/query.ts`: `queryRepository(repositoryId, question)` drops the `history`
  argument; `QueryPage.onSubmit` stops building `history`.

### Testing (Part 1)

- Backend service tests: get-or-create idempotency, append ordinal increments,
  `list_turns` ordering, `recent_history` tail + limit, clear cascades turns.
- Query endpoint: a successful query appends exactly one turn with the full
  snapshot; a second query's prompt history is loaded from the DB (assert the
  provider receives the stored prior turn, not a client-sent one); `not_in_vault`
  answer still appends a turn.
- Conversation endpoints: `GET` returns stored turns for the owner; returns empty
  for a fresh repo; a different user does not see another user's thread; `GET`
  without an active grant → 403; `DELETE` clears and cascades.
- Frontend: `QueryPage` hydrates the thread from `getConversation` on mount and
  on repo-switch; Clear button calls the API and empties the thread; asking a
  question still appends a turn. Update the existing "chat with memory" e2e to
  the server-authoritative flow (no client `history`).

## Design — Part 2: admin gap rejection

### Data model (1 new table)

`gap_rejections` (`models/gap_rejection.py`, `class GapRejection`):
- `repository_id` → FK `repositories.id` `ondelete="CASCADE"`, not null, indexed.
- `normalized_question: str` (`Text`, not null) — the gap identity, produced by
  the same `normalized_question(...)` used by `list_knowledge_gaps`.
- `question: str` (`Text`, not null) — a representative original question, for
  display in the rejected list.
- `reason: str` (`Text`, not null) — the required explanation.
- `rejected_by` → FK `users.id` `ondelete="SET NULL"`, nullable (the admin).
- `__table_args__ = (UniqueConstraint("repository_id", "normalized_question"),)`.
- UUID PK + timestamps via mixins (`created_at` = when rejected).

Rejection is **repo-scoped**, not per-user — matching the gap aggregate, which
groups across all askers in the repo.

### Service (`services/knowledge_gaps.py` additions)

- `list_knowledge_gaps(...)` gains a filter that **excludes** any grouped
  question whose normalized form appears in `gap_rejections` for that repo
  (`WHERE normalized NOT IN (SELECT normalized_question FROM gap_rejections
  WHERE repository_id = :id)`, or an equivalent `LEFT JOIN ... IS NULL`). Answered
  gaps already drop out implicitly once ingested; rejected gaps drop out
  explicitly.
- `reject_gap(session, repository_id, *, question, reason, admin_id) -> GapRejection`
  — computes `normalized_question(question)`, upserts on
  `(repository_id, normalized_question)` (idempotent; updates reason/admin if it
  already exists).
- `list_rejected_gaps(session, repository_id) -> list[GapRejection]` — ordered by
  `created_at DESC`.

### New endpoints (`api/knowledge_gaps.py` additions, admin-only)

- `POST /repositories/{id}/knowledge-gaps/reject`
  - Body `RejectGapRequest = { question: str (min_length 1), reason: str (min_length 1) }`
    — empty reason → 422.
  - 404 if repo missing (mirrors the existing GET). Creates/updates the
    rejection; returns 201 with a `GapRejectionResponse`.
- `GET /repositories/{id}/knowledge-gaps/rejected -> list[GapRejectionResponse]`
  - `GapRejectionResponse = { question, reason, rejected_by (username | null),
    rejected_at }`.

### Frontend (`AdminInsightsPage.tsx`, `api/knowledgeGaps.ts`, i18n)

- `api/knowledgeGaps.ts`: add `rejectGap(repoId, { question, reason })` and
  `listRejectedGaps(repoId)`.
- `GapRow`: add a **Reject** button beside "Answer this gap". It toggles an
  inline required-reason `<textarea>` + **Confirm reject** (submit disabled/blocked
  when the reason is blank). On success, optimistically remove the gap from the
  active list (same pattern as answering).
- New **Rejected gaps** section in `KnowledgeGapsPanel` (loaded via
  `listRejectedGaps`) listing each rejected question with its reason, the
  rejecting admin, and the date. Read-only in v1.
- New i18n keys in **both** `en.json` and `uk.json` under `insights`, e.g.
  `rejectGap`, `rejectReason`, `rejectReasonPlaceholder`, `confirmReject`,
  `rejectedGaps`, `rejectedBy`, `gapRejected`, `errorRejectGap`,
  `noRejectedGaps`. (Exact key list finalized in the plan.)

### Testing (Part 2)

- Backend service: `reject_gap` creates a row; `list_knowledge_gaps` excludes a
  rejected question; a re-reject updates rather than duplicates (unique holds);
  `list_rejected_gaps` returns newest-first with the admin username resolved.
- Endpoints: non-admin → 403 on both new routes; empty reason → 422; 404 on
  missing repo; `GET rejected` returns the stored rejections.
- Frontend: Reject button reveals the reason input; confirming with an empty
  reason is blocked; a valid confirm calls the API and removes the gap from the
  active list; the Rejected gaps section renders question + reason + admin.

## Migration

One Alembic revision on top of head `d4f1a2b7c9e0` creating `conversations`,
`conversation_turns`, and `gap_rejections`. No new enum types. UUID PKs are
app-generated; timestamps use `server_default=func.now()` per `TimestampMixin`.
`downgrade` drops the three tables in FK-safe order
(`conversation_turns` → `conversations`, then `gap_rejections`).

## Interfaces / boundaries

- The conversation seam is `services/conversations.py`; the query endpoint and
  the new conversation endpoints are its only consumers. The LLM/provider
  contract (`LLMProvider.answer(question, chunks, history)`) is unchanged — only
  the *source* of `history` moves from client to DB.
- The gap-rejection seam is the three new functions in
  `services/knowledge_gaps.py`; `list_knowledge_gaps`'s response schema is
  unchanged (rejected rows are simply absent).

## Error handling

- Conversation persistence failure during a query is part of the same
  transaction as `log_query` — if the commit fails the whole request fails
  (acceptable; no partial state).
- `GET /conversation` with no stored thread returns an empty list (200), never
  404 — a fresh conversation is a normal state.
- Reject with a blank reason → 422 at the API boundary (Pydantic `min_length`);
  the frontend also blocks an empty submit.

## Out of scope / YAGNI

- Multiple/named conversation threads per repo (one per user+repo only).
- Editing or deleting individual past turns (only whole-conversation clear).
- Undo/restore of a gap rejection (rejected list is read-only in v1).
- Per-turn user feedback / thumbs-up-down on answers (this is admin-side
  rejection of aggregated gaps, not per-answer user rating).
- Sharing/exporting a conversation.

## Scope / decomposition

Part 1 and Part 2 are independent. The implementation plan will present them as
two clearly-separated task groups (Part 1 first), sharing the single migration.
Either part can be reviewed and merged without the other.
