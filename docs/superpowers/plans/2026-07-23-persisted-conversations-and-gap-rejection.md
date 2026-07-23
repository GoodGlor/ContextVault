# Persisted Conversations + Admin Gap Rejection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each user's chat conversation per repository (restored on reload, server-authoritative history) and let admins reject a knowledge gap with a required, saved explanation.

**Architecture:** Two independent features on the existing FastAPI + async SQLAlchemy + Postgres backend and React/Vite SPA. Part 1 adds `conversations`/`conversation_turns` tables, a conversation service, GET/DELETE conversation endpoints, and moves LLM history from the client to the DB inside `POST /query`. Part 2 adds a `gap_rejections` table, three service functions, POST-reject/GET-rejected endpoints, and admin UI. Parts share nothing at runtime and can ship separately.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, Alembic, FastAPI, Pydantic v2, pgvector Postgres; React 18 + TypeScript + Vite, react-i18next, Vitest + Testing Library.

## Global Constraints

- **Migrations (refines spec):** the spec said "one migration"; this plan uses **two** migrations — one for Part 1's two tables (Task 1) and one for Part 2's table (Task 8) — so the parts stay independently shippable. Both chain from current head `d4f1a2b7c9e0`; whichever ships second must set its `down_revision` to whatever is head at that time. No new Postgres enum types are introduced.
- **Models:** every model subclasses `(UUIDPrimaryKeyMixin, TimestampMixin, Base)` in that MRO. UUID PKs are app-generated (`uuid.uuid4`); `created_at`/`updated_at` are DB-maintained. Register every new model in `src/contextvault/models/__init__.py` (both the import and `__all__`) — the `db_session` test fixture derives its `TRUNCATE` list from `Base.metadata`, so an unregistered model breaks nothing but also gets no cleanup, and migrations autogenerate from `Base.metadata`.
- **JSONB columns:** use `from sqlalchemy.dialects.postgresql import JSONB` in models and `postgresql.JSONB()` in migrations; type them `Mapped[list[dict[str, Any]]]`.
- **Server-authoritative history:** the query endpoint derives conversation history from the DB. Remove `history` from `QueryRequest` and from the frontend `queryRepository` call — the client no longer sends it.
- **Reason required:** the reject endpoint's `reason` uses Pydantic `Field(min_length=1)` → empty reason is `422`.
- **Admin gating:** new gap endpoints use `Depends(require_admin)` (`api/deps.py`); conversation endpoints use `Depends(get_current_user)` + an active-grant check (`grant_service.has_active_grant`), mirroring `/query`.
- **i18n:** every new user-facing string gets a key in **both** `frontend/src/i18n/locales/en.json` and `uk.json`.
- **CI gate (must pass before done):** backend `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` (strict, files=src,tests), `uv run alembic upgrade head`, `uv run pytest`; frontend (in `frontend/`) `npm run lint`, `npm run format:check`, `npm run typecheck`, `npm run test`, `npm run build`.
- **DB-backed tests** use the `db_session` fixture (`tests/conftest.py`): it skips when no migrated DB is reachable, so after each migration task run `uv run alembic upgrade head` against the dev DB so the new tables exist and the tests actually execute (not skip). **API tests** copy the `client` + `_token` + `_auth` fixtures from `tests/test_knowledge_gaps_api.py`.

---

# PART 1 — Persisted conversations

### Task 1: Conversation + ConversationTurn models and migration

**Files:**
- Create: `src/contextvault/models/conversation.py`
- Create: `src/contextvault/models/conversation_turn.py`
- Modify: `src/contextvault/models/__init__.py`
- Create: `migrations/versions/<hash>_conversations.py` (via `alembic revision`)
- Test: `tests/test_conversation_model.py`

**Interfaces:**
- Produces: `Conversation(user_id, repository_id)` with `UniqueConstraint("user_id","repository_id")`; `ConversationTurn(conversation_id, ordinal, question, answer, not_in_vault, citations, sources)`. Both have `id`, `created_at`, `updated_at`.

- [ ] **Step 1: Write the failing test**

`tests/test_conversation_model.py`:
```python
"""Conversation + ConversationTurn model tests (persisted chat, per user+repo)."""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import Conversation, ConversationTurn, Repository, Role, User
from contextvault.services import users as user_service


async def _user(db_session: AsyncSession, name: str) -> User:
    return await user_service.create_user(db_session, username=name, password="pw", role=Role.USER)


async def _repo(db_session: AsyncSession, name: str) -> Repository:
    repo = Repository(name=name)
    db_session.add(repo)
    await db_session.flush()
    return repo


async def test_conversation_turn_round_trips(db_session: AsyncSession) -> None:
    user = await _user(db_session, "alice")
    repo = await _repo(db_session, "Handbook")
    convo = Conversation(user_id=user.id, repository_id=repo.id)
    db_session.add(convo)
    await db_session.flush()
    turn = ConversationTurn(
        conversation_id=convo.id,
        ordinal=0,
        question="What is the VPN?",
        answer="It is X [1].",
        not_in_vault=False,
        citations=[{"number": 1, "chunk_id": str(uuid.uuid4()), "source_id": str(uuid.uuid4()),
                    "char_start": 0, "char_end": 5}],
        sources=[{"id": str(uuid.uuid4()), "title": "vpn.md", "original_filename": "vpn.md",
                  "kind": "document", "verified": False, "author": None}],
    )
    db_session.add(turn)
    await db_session.flush()

    loaded = (
        await db_session.execute(sa.select(ConversationTurn).where(ConversationTurn.conversation_id == convo.id))
    ).scalar_one()
    assert loaded.answer == "It is X [1]."
    assert loaded.citations[0]["number"] == 1
    assert loaded.sources[0]["title"] == "vpn.md"


async def test_one_conversation_per_user_and_repo(db_session: AsyncSession) -> None:
    user = await _user(db_session, "bob")
    repo = await _repo(db_session, "Handbook")
    db_session.add(Conversation(user_id=user.id, repository_id=repo.id))
    await db_session.flush()
    db_session.add(Conversation(user_id=user.id, repository_id=repo.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()
```

- [ ] **Step 2: Run the test — verify it fails**

Run: `uv run pytest tests/test_conversation_model.py -q`
Expected: collection/import error — `cannot import name 'Conversation'`.

- [ ] **Step 3: Create the models**

`src/contextvault/models/conversation.py`:
```python
"""Conversation model — one saved chat thread per (user, repository).

The query page's conversation was previously client-only React state, lost on
reload. A ``Conversation`` persists it server-side, one per user per repository
(the ``Grant`` shape), so a reload restores the thread and the server — not the
client — is the authority on conversation history.
"""

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user's running conversation with one repository."""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("user_id", "repository_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
```

`src/contextvault/models/conversation_turn.py`:
```python
"""ConversationTurn model — one Q/A exchange in a saved conversation.

Each turn stores the question, the answer text, the honesty flag, and a JSONB
*snapshot* of the answer's citations and cited sources (the exact shapes the
query endpoint returns). Storing snapshots — not foreign keys — means a restored
answer renders identically even if a cited source is later edited or deleted.
"""

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ConversationTurn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single exchange (question + grounded answer) within a conversation."""

    __tablename__ = "conversation_turns"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 0-based position within the conversation, oldest first.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    not_in_vault: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Snapshot of QueryResponse.citations / .sources at answer time (JSON-dumped).
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
```

- [ ] **Step 4: Register the models**

In `src/contextvault/models/__init__.py`, add imports (alphabetical) and `__all__` entries:
```python
from contextvault.models.conversation import Conversation
from contextvault.models.conversation_turn import ConversationTurn
```
Add `"Conversation",` and `"ConversationTurn",` to `__all__`.

- [ ] **Step 5: Create the migration**

Run: `uv run alembic revision -m "conversations and conversation_turns"`
Fill the generated file's `upgrade()`/`downgrade()`:
```python
def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "repository_id", name="uq_conversations_user_id_repository_id"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_repository_id", "conversations", ["repository_id"])
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("not_in_vault", sa.Boolean(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False),
        sa.Column("sources", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_turns_conversation_id", "conversation_turns", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("conversation_turns")
    op.drop_table("conversations")
```
Ensure `from sqlalchemy.dialects import postgresql` is imported (see the head migration for the template). Keep the autogenerated `revision`/`down_revision` (down_revision must be `d4f1a2b7c9e0`).

- [ ] **Step 6: Apply the migration and run the test**

Run: `uv run alembic upgrade head && uv run pytest tests/test_conversation_model.py -q`
Expected: PASS (2 passed). If it skips, the dev DB isn't up — `docker compose up -d` then re-run `alembic upgrade head`.

- [ ] **Step 7: Commit**

```bash
git add src/contextvault/models/conversation.py src/contextvault/models/conversation_turn.py \
        src/contextvault/models/__init__.py migrations/versions/ tests/test_conversation_model.py
git commit -m "feat: Conversation + ConversationTurn models and migration"
```

---

### Task 2: Conversation service

**Files:**
- Create: `src/contextvault/services/conversations.py`
- Test: `tests/test_conversations_service.py`

**Interfaces:**
- Consumes: `Conversation`, `ConversationTurn` (Task 1).
- Produces:
  - `async get_or_create_conversation(session, user_id: UUID, repository_id: UUID) -> Conversation`
  - `async list_turns(session, conversation_id: UUID) -> list[ConversationTurn]` (ordered by `ordinal`)
  - `async recent_history(session, conversation_id: UUID, limit: int) -> list[tuple[str, str]]` (last `limit` `(question, answer)`, oldest-first)
  - `async append_turn(session, conversation_id: UUID, *, question, answer, not_in_vault, citations, sources) -> ConversationTurn`
  - `async clear_conversation(session, user_id: UUID, repository_id: UUID) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_conversations_service.py`:
```python
"""Conversation service tests: get-or-create, append, history tail, clear."""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import Conversation, ConversationTurn, Repository, Role, User
from contextvault.services import conversations as convo_service
from contextvault.services import users as user_service


async def _seed(db_session: AsyncSession) -> tuple[User, Repository]:
    user = await user_service.create_user(db_session, username="alice", password="pw", role=Role.USER)
    repo = Repository(name="Handbook")
    db_session.add(repo)
    await db_session.flush()
    return user, repo


async def test_get_or_create_is_idempotent(db_session: AsyncSession) -> None:
    user, repo = await _seed(db_session)
    a = await convo_service.get_or_create_conversation(db_session, user.id, repo.id)
    b = await convo_service.get_or_create_conversation(db_session, user.id, repo.id)
    assert a.id == b.id


async def test_append_increments_ordinal_and_history_tail(db_session: AsyncSession) -> None:
    user, repo = await _seed(db_session)
    convo = await convo_service.get_or_create_conversation(db_session, user.id, repo.id)
    for i in range(3):
        await convo_service.append_turn(
            db_session, convo.id, question=f"q{i}", answer=f"a{i}",
            not_in_vault=False, citations=[], sources=[],
        )
    turns = await convo_service.list_turns(db_session, convo.id)
    assert [t.ordinal for t in turns] == [0, 1, 2]
    hist = await convo_service.recent_history(db_session, convo.id, limit=2)
    assert hist == [("q1", "a1"), ("q2", "a2")]


async def test_clear_removes_conversation_and_turns(db_session: AsyncSession) -> None:
    user, repo = await _seed(db_session)
    convo = await convo_service.get_or_create_conversation(db_session, user.id, repo.id)
    await convo_service.append_turn(
        db_session, convo.id, question="q", answer="a",
        not_in_vault=False, citations=[], sources=[],
    )
    await convo_service.clear_conversation(db_session, user.id, repo.id)
    assert (await db_session.execute(sa.select(sa.func.count()).select_from(Conversation))).scalar_one() == 0
    assert (await db_session.execute(sa.select(sa.func.count()).select_from(ConversationTurn))).scalar_one() == 0
```

- [ ] **Step 2: Run the test — verify it fails**

Run: `uv run pytest tests/test_conversations_service.py -q`
Expected: `ModuleNotFoundError: contextvault.services.conversations`.

- [ ] **Step 3: Implement the service**

`src/contextvault/services/conversations.py`:
```python
"""Conversation persistence service (saved chat, per user+repo).

One ``Conversation`` per (user, repository); turns are appended oldest-first with
a monotonic ``ordinal``. ``recent_history`` returns the most recent tail as
``(question, answer)`` pairs for the LLM prompt — the server, not the client, owns
history. All functions add/flush; the caller commits.
"""

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import Conversation, ConversationTurn


async def get_or_create_conversation(
    session: AsyncSession, user_id: uuid.UUID, repository_id: uuid.UUID
) -> Conversation:
    """Return this user's conversation for the repository, creating it if absent."""
    existing = (
        await session.execute(
            sa.select(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.repository_id == repository_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    convo = Conversation(user_id=user_id, repository_id=repository_id)
    session.add(convo)
    await session.flush()
    return convo


async def list_turns(session: AsyncSession, conversation_id: uuid.UUID) -> list[ConversationTurn]:
    """All turns of a conversation, oldest first."""
    rows = (
        await session.execute(
            sa.select(ConversationTurn)
            .where(ConversationTurn.conversation_id == conversation_id)
            .order_by(ConversationTurn.ordinal.asc())
        )
    ).scalars().all()
    return list(rows)


async def recent_history(
    session: AsyncSession, conversation_id: uuid.UUID, limit: int
) -> list[tuple[str, str]]:
    """The last ``limit`` ``(question, answer)`` pairs, oldest first, for prompting."""
    rows = (
        await session.execute(
            sa.select(ConversationTurn.question, ConversationTurn.answer)
            .where(ConversationTurn.conversation_id == conversation_id)
            .order_by(ConversationTurn.ordinal.desc())
            .limit(limit)
        )
    ).all()
    return [(r.question, r.answer) for r in reversed(rows)]


async def append_turn(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    question: str,
    answer: str,
    not_in_vault: bool,
    citations: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> ConversationTurn:
    """Append one exchange; ``ordinal`` continues after the current last turn."""
    next_ordinal = (
        await session.execute(
            sa.select(sa.func.coalesce(sa.func.max(ConversationTurn.ordinal), -1) + 1).where(
                ConversationTurn.conversation_id == conversation_id
            )
        )
    ).scalar_one()
    turn = ConversationTurn(
        conversation_id=conversation_id,
        ordinal=next_ordinal,
        question=question,
        answer=answer,
        not_in_vault=not_in_vault,
        citations=citations,
        sources=sources,
    )
    session.add(turn)
    await session.flush()
    return turn


async def clear_conversation(
    session: AsyncSession, user_id: uuid.UUID, repository_id: uuid.UUID
) -> None:
    """Delete this user's conversation for the repository (turns cascade)."""
    await session.execute(
        sa.delete(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.repository_id == repository_id,
        )
    )
    await session.flush()
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `uv run pytest tests/test_conversations_service.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/services/conversations.py tests/test_conversations_service.py
git commit -m "feat: conversation persistence service"
```

---

### Task 3: Query endpoint persists turns + server-authoritative history

**Files:**
- Modify: `src/contextvault/api/query.py`
- Test: `tests/test_query_api.py` (extend)

**Interfaces:**
- Consumes: `conversations` service (Task 2).
- Produces: `POST /repositories/{id}/query` persists each turn; `QueryRequest` no longer has `history`; the `ConversationTurn` request model is removed. The `QueryResponse` shape is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_query_api.py` (follow the file's existing fixtures/fake-provider pattern; if it defines a fake LLM builder capturing `history`, reuse it — otherwise add one). The two behaviors to assert:
```python
async def test_query_persists_a_turn(db_session, client):
    # ... seed admin/user + granted answerable repo + fake provider returning a grounded answer ...
    token = await _token(client, "alice")
    resp = await client.post(f"/repositories/{repo.id}/query",
                             json={"question": "What is the VPN?"}, headers=_auth(token))
    assert resp.status_code == 200
    turns = (await db_session.execute(sa.select(ConversationTurn))).scalars().all()
    assert len(turns) == 1
    assert turns[0].question == "What is the VPN?"
    assert turns[0].answer == resp.json()["answer"]


async def test_second_query_uses_db_history_not_client(db_session, client, captured):
    # First question creates a turn; the second must receive the first turn as history
    # even though the request body carries NO history field.
    token = await _token(client, "alice")
    await client.post(f"/repositories/{repo.id}/query",
                      json={"question": "First?"}, headers=_auth(token))
    await client.post(f"/repositories/{repo.id}/query",
                      json={"question": "Second?"}, headers=_auth(token))
    # `captured` records the history tuples the fake provider.answer() received.
    assert captured["history"][-1] == [("First?", "<the first answer>")]
```
The exact fake-provider wiring mirrors what `test_query_api.py` already does for `get_llm_builder`; the reviewer's brief will name the file so the implementer matches its style. Also add a negative assertion that a `history` key in the request body is ignored (Pydantic `extra="ignore"` default → no error, no effect).

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/test_query_api.py -q -k "persists or db_history"`
Expected: FAIL (`ConversationTurn` count 0 / history empty).

- [ ] **Step 3: Edit `api/query.py`**

Remove the `ConversationTurn` request model (lines 43-47) and the `history` field from `QueryRequest`; update the docstring:
```python
class QueryRequest(BaseModel):
    """A user's question against one repository.

    Conversation history is server-side: the endpoint loads this user's saved
    thread for the repository and threads the most recent ``MAX_HISTORY_TURNS``
    into the prompt. Clients send only the question.
    """

    question: str = Field(min_length=1)
```
Add imports:
```python
from contextvault.api.query import ...  # (existing)
from contextvault.services import conversations as convo_service
```
(Place with the other `from contextvault.services import ...` lines.)

In `query_repository`, after `provider = await build_provider(session, repo)`:
```python
    conversation = await convo_service.get_or_create_conversation(session, user.id, repository_id)
    history = await convo_service.recent_history(session, conversation.id, MAX_HISTORY_TURNS)
```
(Delete the old `history = [(turn.question, turn.answer) for turn in payload.history[...]]` line.) The `retrieval_question`/`history[-1][0]` logic below is unchanged (history is oldest-first).

After computing `answer` and building the response sources, persist the turn (reuse the same snapshot the response returns — build `citations`/`sources` once):
```python
    citation_responses = [CitationResponse.model_validate(c) for c in answer.citations]
    source_references = await _cited_sources(session, answer.citations)

    await log_query(
        session,
        user_id=user.id,
        repository_id=repository_id,
        question=payload.question,
        top_score=result.top_score,
        chunk_count=len(result.chunks),
        not_in_vault=answer.not_in_vault,
    )
    await convo_service.append_turn(
        session,
        conversation.id,
        question=payload.question,
        answer=answer.text,
        not_in_vault=answer.not_in_vault,
        citations=[c.model_dump(mode="json") for c in citation_responses],
        sources=[s.model_dump(mode="json") for s in source_references],
    )
    await session.commit()

    return QueryResponse(
        answer=answer.text,
        not_in_vault=answer.not_in_vault,
        citations=citation_responses,
        sources=source_references,
    )
```

- [ ] **Step 4: Run — verify it passes**

Run: `uv run pytest tests/test_query_api.py tests/test_query_logging.py -q`
Expected: PASS. Fix any other test in these files that constructed a request with `history` (it now is simply ignored; assertions on history-driven behavior must seed turns via a prior query or directly).

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/api/query.py tests/test_query_api.py
git commit -m "feat: query endpoint persists turns and loads history from the DB"
```

---

### Task 4: Conversation GET/DELETE endpoints

**Files:**
- Create: `src/contextvault/api/conversations.py`
- Modify: `src/contextvault/main.py` (register the router)
- Test: `tests/test_conversations_api.py`

**Interfaces:**
- Consumes: `conversations` service (Task 2); `CitationResponse`, `SourceReferenceResponse` from `api/query.py`.
- Produces:
  - `GET /repositories/{id}/conversation -> ConversationResponse` = `{turns: [ConversationTurnResponse]}`, `ConversationTurnResponse = {question, answer, not_in_vault, citations: list[CitationResponse], sources: list[SourceReferenceResponse]}`. Empty list (200) when none.
  - `DELETE /repositories/{id}/conversation -> 204`.
  - Both: 404 unknown repo, 403 without active grant.

- [ ] **Step 1: Write the failing test**

`tests/test_conversations_api.py` (copy `client`/`_token`/`_auth` from `test_knowledge_gaps_api.py`; grant helper from `grants` service — check `services/grants.py` for the create/grant call the other tests use):
```python
async def test_get_returns_saved_turns_for_owner(db_session, client):
    # seed user 'alice' with an active grant + a conversation with one turn
    ...
    resp = await client.get(f"/repositories/{repo.id}/conversation", headers=_auth(await _token(client, "alice")))
    assert resp.status_code == 200
    assert resp.json()["turns"][0]["question"] == "q0"
    assert resp.json()["turns"][0]["sources"][0]["title"] == "vpn.md"

async def test_get_empty_when_no_conversation(db_session, client):
    resp = await client.get(f"/repositories/{repo.id}/conversation", headers=_auth(await _token(client, "alice")))
    assert resp.status_code == 200
    assert resp.json() == {"turns": []}

async def test_get_requires_active_grant(db_session, client):
    # 'mallory' has no grant on repo
    resp = await client.get(f"/repositories/{repo.id}/conversation", headers=_auth(await _token(client, "mallory")))
    assert resp.status_code == 403

async def test_delete_clears_conversation(db_session, client):
    # seed a conversation with a turn, then:
    resp = await client.delete(f"/repositories/{repo.id}/conversation", headers=_auth(await _token(client, "alice")))
    assert resp.status_code == 204
    assert (await db_session.execute(sa.select(sa.func.count()).select_from(Conversation))).scalar_one() == 0

async def test_get_404_unknown_repo(db_session, client):
    resp = await client.get(f"/repositories/{uuid.uuid4()}/conversation", headers=_auth(await _token(client, "alice")))
    assert resp.status_code == 404
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/test_conversations_api.py -q`
Expected: 404/route-not-found failures (router not registered).

- [ ] **Step 3: Implement the endpoints**

`src/contextvault/api/conversations.py`:
```python
"""Saved-conversation endpoints (persisted chat, per user+repo).

``GET`` restores this user's thread for a repository so a page reload rebuilds the
conversation exactly (each turn carries its citation/source snapshot). ``DELETE``
is the "Clear conversation" action. Both require an active grant, like ``/query``.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import get_current_user
from contextvault.api.query import CitationResponse, SourceReferenceResponse
from contextvault.db.session import get_session
from contextvault.models import Repository, User
from contextvault.services import conversations as convo_service
from contextvault.services import grants as grant_service

router = APIRouter(tags=["conversation"])


class ConversationTurnResponse(BaseModel):
    question: str
    answer: str
    not_in_vault: bool
    citations: list[CitationResponse]
    sources: list[SourceReferenceResponse]


class ConversationResponse(BaseModel):
    turns: list[ConversationTurnResponse]


async def _guard(session: AsyncSession, user: User, repository_id: uuid.UUID) -> None:
    if await session.get(Repository, repository_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    if not await grant_service.has_active_grant(session, user.id, repository_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No access to this repository"
        )


@router.get("/repositories/{repository_id}/conversation")
async def get_conversation(
    repository_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """This user's saved conversation for the repository (empty when none yet)."""
    await _guard(session, user, repository_id)
    conversation = await convo_service.get_or_create_conversation(session, user.id, repository_id)
    turns = await convo_service.list_turns(session, conversation.id)
    await session.commit()
    return ConversationResponse(
        turns=[
            ConversationTurnResponse(
                question=t.question,
                answer=t.answer,
                not_in_vault=t.not_in_vault,
                citations=[CitationResponse.model_validate(c) for c in t.citations],
                sources=[SourceReferenceResponse.model_validate(s) for s in t.sources],
            )
            for t in turns
        ]
    )


@router.delete("/repositories/{repository_id}/conversation", status_code=status.HTTP_204_NO_CONTENT)
async def clear_conversation(
    repository_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete this user's saved conversation for the repository."""
    await _guard(session, user, repository_id)
    await convo_service.clear_conversation(session, user.id, repository_id)
    await session.commit()
```
> Note: `get_conversation` does a get-or-create + commit so a fresh visit is idempotent; if the reviewer prefers no write on GET, an equivalent read-only lookup returning `{turns: []}` when absent is acceptable — either satisfies the empty-state test.

In `src/contextvault/main.py`: add `from contextvault.api.conversations import router as conversation_router` with the other imports and `app.include_router(conversation_router)` with the others.

- [ ] **Step 4: Run — verify it passes**

Run: `uv run pytest tests/test_conversations_api.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/api/conversations.py src/contextvault/main.py tests/test_conversations_api.py
git commit -m "feat: GET/DELETE saved-conversation endpoints"
```

---

### Task 5: Frontend conversation API client + drop client history

**Files:**
- Create: `frontend/src/api/conversations.ts`
- Modify: `frontend/src/api/query.ts`
- Test: `frontend/src/api/conversations.test.ts`

**Interfaces:**
- Produces: `getConversation(repositoryId): Promise<{turns: SavedTurn[]}>` and `clearConversation(repositoryId): Promise<void>`; `SavedTurn = {question, answer, not_in_vault, citations, sources}` reusing `Citation`/`SourceReference` from `query.ts`. `queryRepository(repositoryId, question)` loses its `history` param.

- [ ] **Step 1: Write the failing test**

`frontend/src/api/conversations.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getConversation, clearConversation } from "./conversations";

function json(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("conversations api", () => {
  const fetchMock = vi.fn();
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => { fetchMock.mockReset(); vi.unstubAllGlobals(); });

  it("GETs the saved conversation", async () => {
    fetchMock.mockResolvedValue(json({ turns: [{ question: "q", answer: "a", not_in_vault: false, citations: [], sources: [] }] }));
    const res = await getConversation("r-1");
    expect(res.turns[0].question).toBe("q");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/repositories/r-1/conversation");
  });

  it("DELETEs the saved conversation", async () => {
    fetchMock.mockResolvedValue(json(null, 204));
    await clearConversation("r-1");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });
});
```

- [ ] **Step 2: Run — verify it fails**

Run (in `frontend/`): `npm run test -- conversations.test.ts`
Expected: FAIL — module `./conversations` not found.

- [ ] **Step 3: Implement the client + edit query.ts**

`frontend/src/api/conversations.ts`:
```ts
import { api } from "./client";
import type { Citation, SourceReference } from "./query";

/** One saved exchange; mirrors ConversationTurnResponse in api/conversations.py. */
export interface SavedTurn {
  question: string;
  answer: string;
  not_in_vault: boolean;
  citations: Citation[];
  sources: SourceReference[];
}

export interface SavedConversation {
  turns: SavedTurn[];
}

/** This user's saved conversation for a repository (empty turns when none yet). */
export function getConversation(repositoryId: string): Promise<SavedConversation> {
  return api.get<SavedConversation>(`/repositories/${repositoryId}/conversation`);
}

/** Clear this user's saved conversation for a repository. */
export function clearConversation(repositoryId: string): Promise<void> {
  return api.del<void>(`/repositories/${repositoryId}/conversation`);
}
```
In `frontend/src/api/query.ts`: delete `ConversationTurnInput`, drop the `history` param, and simplify the body:
```ts
export function queryRepository(repositoryId: string, question: string): Promise<QueryResult> {
  return api.post<QueryResult>(`/repositories/${repositoryId}/query`, { question });
}
```
Update its doc comment to note history is now server-side.

- [ ] **Step 4: Run — verify it passes**

Run (in `frontend/`): `npm run test -- conversations.test.ts`
Expected: PASS. (Do not run the full suite yet — `QueryPage`/e2e still reference the old signature; fixed in Tasks 6–7.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/conversations.ts frontend/src/api/query.ts frontend/src/api/conversations.test.ts
git commit -m "feat(web): conversation api client; drop client-sent history"
```

---

### Task 6: Query page hydrates the saved thread + Clear conversation button

**Files:**
- Modify: `frontend/src/pages/QueryPage.tsx`
- Modify: `frontend/src/i18n/locales/en.json`, `frontend/src/i18n/locales/uk.json`
- Test: `frontend/src/pages/QueryPage.test.tsx` (extend)

**Interfaces:**
- Consumes: `getConversation`, `clearConversation` (Task 5); `queryRepository(repositoryId, question)` (Task 5).

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/pages/QueryPage.test.tsx` (match the file's existing fetch-mock style):
```ts
it("restores the saved conversation on load", async () => {
  // mock GET /repositories/.../conversation to return one saved turn, and
  // GET /repositories to return one repo.
  render(<QueryPage />);
  expect(await screen.findByText("Saved question?")).toBeInTheDocument();
  expect(screen.getByText(/saved answer/i)).toBeInTheDocument();
});

it("clears the conversation when Clear is clicked", async () => {
  // saved thread with one turn; mock DELETE to 204
  render(<QueryPage />);
  await screen.findByText("Saved question?");
  await userEvent.click(screen.getByRole("button", { name: "Clear conversation" }));
  expect(screen.queryByText("Saved question?")).not.toBeInTheDocument();
  const del = fetchMock.mock.calls.find((c) => c[1]?.method === "DELETE");
  expect(String(del?.[0])).toContain("/repositories/r-1/conversation");
});
```

- [ ] **Step 2: Run — verify it fails**

Run (in `frontend/`): `npm run test -- QueryPage.test.tsx`
Expected: FAIL — no saved turn rendered / no Clear button.

- [ ] **Step 3: Edit `QueryPage.tsx`**

- Import `getConversation, clearConversation` and (for mapping) nothing extra — `QueryResult` already imported.
- Add an effect that runs when `selected` changes: load the saved conversation and hydrate `turns`, replacing the `setTurns([])` in `onSelectRepo`. Keep a `cancelled` guard.
```tsx
useEffect(() => {
  if (selected === "") return;
  let cancelled = false;
  setAskError(null);
  getConversation(selected)
    .then((c) => {
      if (cancelled) return;
      setTurns(
        c.turns.map((t, i) => ({
          id: `saved-${i}`,
          question: t.question,
          result: {
            answer: t.answer,
            not_in_vault: t.not_in_vault,
            citations: t.citations,
            sources: t.sources,
          },
          repositoryId: selected,
        })),
      );
      turnSeq.current = c.turns.length;
    })
    .catch(() => {
      if (!cancelled) setTurns([]);
    });
  return () => { cancelled = true; };
}, [selected]);
```
- `onSelectRepo` now only sets `selected` + clears `askError` (the effect above rehydrates). Remove its `setTurns([])`.
- `submit`: remove the `history` array and pass `queryRepository(selected, q)`.
- Add a Clear button in the `chat-header` (only when `turns.length > 0`):
```tsx
{turns.length > 0 && (
  <button type="button" className="chat-clear" onClick={onClear}>
    {t("query.clearConversation")}
  </button>
)}
```
with:
```tsx
const onClear = async () => {
  if (selected === "") return;
  try {
    await clearConversation(selected);
    setTurns([]);
    turnSeq.current = 0;
  } catch (err) {
    setAskError(err instanceof ApiError ? err.detail : t("common.somethingWrong"));
  }
};
```

- [ ] **Step 4: Add i18n keys**

In both `en.json` and `uk.json`, inside the `query` object add:
- en: `"clearConversation": "Clear conversation"`
- uk: `"clearConversation": "Очистити розмову"`

- [ ] **Step 5: Run — verify it passes**

Run (in `frontend/`): `npm run test -- QueryPage.test.tsx`
Expected: PASS. Also fix any pre-existing QueryPage test that asserted the old `history` argument was sent (it no longer is).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/QueryPage.tsx frontend/src/i18n/locales/en.json frontend/src/i18n/locales/uk.json frontend/src/pages/QueryPage.test.tsx
git commit -m "feat(web): restore saved conversation on load; add Clear conversation"
```

---

### Task 7: Update the "chat with memory" e2e for server-authoritative history

**Files:**
- Modify: `frontend/e2e/<the chat-with-memory spec>` (the test added in commit `c0dce42`; locate under `frontend/e2e`)

- [ ] **Step 1: Read the existing e2e and identify history assumptions**

Run: `ls frontend/e2e && grep -rln "history\|memory\|follow" frontend/e2e`. Read the spec; find where it relied on the client sending `history` or on a wiped-on-reload thread.

- [ ] **Step 2: Update the flow**

Adjust so the conversation memory is verified via the server: ask a follow-up that depends on the prior turn and assert the grounded answer; if the test reloads the page, now assert the thread **persists** (previously it would reset). Ensure any request interception no longer expects a `history` field.

- [ ] **Step 3: Run the e2e**

Run the project's e2e command (per `frontend/package.json`, e.g. `npm run test:e2e` — confirm the script name). Expected: PASS. If e2e needs a live backend/DB, follow `docs/HANDOFF.md` (`BACKEND_PORT=8001 VITE_PROXY_TARGET=http://localhost:8001`).

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e
git commit -m "test(e2e): conversation memory persists via the server"
```

---

# PART 2 — Admin gap rejection

### Task 8: GapRejection model and migration

**Files:**
- Create: `src/contextvault/models/gap_rejection.py`
- Modify: `src/contextvault/models/__init__.py`
- Create: `migrations/versions/<hash>_gap_rejections.py`
- Test: `tests/test_gap_rejection_model.py`

**Interfaces:**
- Produces: `GapRejection(repository_id, normalized_question, question, reason, rejected_by)` with `UniqueConstraint("repository_id","normalized_question")`.

- [ ] **Step 1: Write the failing test**

`tests/test_gap_rejection_model.py`:
```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import GapRejection, Repository


async def _repo(db_session: AsyncSession) -> Repository:
    repo = Repository(name="Handbook")
    db_session.add(repo)
    await db_session.flush()
    return repo


async def test_rejection_round_trips(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    db_session.add(GapRejection(repository_id=repo.id, normalized_question="what is the vpn?",
                                question="What is the VPN?", reason="Out of scope", rejected_by=None))
    await db_session.flush()


async def test_unique_per_repo_and_normalized_question(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    db_session.add(GapRejection(repository_id=repo.id, normalized_question="q", question="Q", reason="r"))
    await db_session.flush()
    db_session.add(GapRejection(repository_id=repo.id, normalized_question="q", question="Q", reason="r2"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/test_gap_rejection_model.py -q`
Expected: import error `cannot import name 'GapRejection'`.

- [ ] **Step 3: Create the model + register**

`src/contextvault/models/gap_rejection.py`:
```python
"""GapRejection model — an admin's decision to reject a knowledge gap.

A knowledge gap is an aggregated question (grouped case/whitespace-insensitively)
the vault could not answer. Besides *answering* a gap (an Admin Note), an admin can
*reject* it — decide it won't be covered — with a required written reason. A
rejection is keyed by ``(repository_id, normalized_question)`` (matching the gap
aggregation) and excludes that question from the active gap list.
"""

import uuid

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from contextvault.db.base import Base
from contextvault.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class GapRejection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One rejected knowledge-gap topic for a repository, with the admin's reason."""

    __tablename__ = "gap_rejections"
    __table_args__ = (UniqueConstraint("repository_id", "normalized_question"),)

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The gap identity: the same normalization used by list_knowledge_gaps.
    normalized_question: Mapped[str] = mapped_column(Text, nullable=False)
    # A representative original phrasing, for display in the rejected list.
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Null once the admin is deleted — the decision survives the account.
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
```
Register in `models/__init__.py` (import + `__all__`).

- [ ] **Step 4: Create the migration**

Run: `uv run alembic revision -m "gap_rejections"`. Fill:
```python
def upgrade() -> None:
    op.create_table(
        "gap_rejections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("normalized_question", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("rejected_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rejected_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "normalized_question", name="uq_gap_rejections_repository_id_normalized_question"),
    )
    op.create_index("ix_gap_rejections_repository_id", "gap_rejections", ["repository_id"])
    op.create_index("ix_gap_rejections_rejected_by", "gap_rejections", ["rejected_by"])


def downgrade() -> None:
    op.drop_table("gap_rejections")
```
Set `down_revision` to whatever is head when this ships (the Task-1 revision if Part 1 merged first, else `d4f1a2b7c9e0`).

- [ ] **Step 5: Apply + run the test**

Run: `uv run alembic upgrade head && uv run pytest tests/test_gap_rejection_model.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/contextvault/models/gap_rejection.py src/contextvault/models/__init__.py migrations/versions/ tests/test_gap_rejection_model.py
git commit -m "feat: GapRejection model and migration"
```

---

### Task 9: Gap-rejection service + exclude rejected from the gap list

**Files:**
- Modify: `src/contextvault/services/knowledge_gaps.py`
- Test: `tests/test_knowledge_gaps_service.py` (create if absent, else extend)

**Interfaces:**
- Produces:
  - `async reject_gap(session, repository_id, *, question, reason, admin_id) -> GapRejection` (upsert on `(repo, normalized)`)
  - `async list_rejected_gaps(session, repository_id) -> Sequence[GapRejection]` (newest first)
  - `list_knowledge_gaps` now excludes any grouped question whose normalized form has a rejection.

- [ ] **Step 1: Write the failing test**

`tests/test_knowledge_gaps_service.py`:
```python
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.models import GapRejection, QueryLog, Repository, Role
from contextvault.services import knowledge_gaps as gap_service
from contextvault.services import users as user_service


async def _repo(db_session, name="Handbook"):
    repo = Repository(name=name); db_session.add(repo); await db_session.flush(); return repo


async def _gap_log(db_session, repo_id, question):
    db_session.add(QueryLog(user_id=None, repository_id=repo_id, question=question,
                            top_score=None, chunk_count=0, not_in_vault=True))
    await db_session.flush()


async def test_rejected_question_is_excluded_from_gaps(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    await _gap_log(db_session, repo.id, "What is the VPN?")
    await _gap_log(db_session, repo.id, "How to reset password?")
    admin = await user_service.create_user(db_session, username="admin", password="pw", role=Role.ADMIN)
    await gap_service.reject_gap(db_session, repo.id, question="What is the VPN?", reason="n/a", admin_id=admin.id)
    gaps = await gap_service.list_knowledge_gaps(db_session, repo.id)
    assert [g.question for g in gaps] == ["How to reset password?"]


async def test_reject_is_idempotent_upsert(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    await gap_service.reject_gap(db_session, repo.id, question="Q", reason="first", admin_id=None)
    await gap_service.reject_gap(db_session, repo.id, question="q", reason="second", admin_id=None)  # same normalized
    rows = (await db_session.execute(sa.select(GapRejection))).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason == "second"


async def test_list_rejected_newest_first(db_session: AsyncSession) -> None:
    repo = await _repo(db_session)
    await gap_service.reject_gap(db_session, repo.id, question="A", reason="a", admin_id=None)
    await gap_service.reject_gap(db_session, repo.id, question="B", reason="b", admin_id=None)
    rejected = await gap_service.list_rejected_gaps(db_session, repo.id)
    assert {r.question for r in rejected} == {"A", "B"}
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/test_knowledge_gaps_service.py -q`
Expected: FAIL — `reject_gap`/`list_rejected_gaps` don't exist; the exclusion test fails.

- [ ] **Step 3: Edit `services/knowledge_gaps.py`**

Add imports: `from contextvault.models import GapRejection, QueryLog` (extend existing import); `from contextvault.services.query_log import normalized_question` is already there.

Add a filter subquery in `list_knowledge_gaps`, before `.group_by`:
```python
    rejected = sa.select(GapRejection.normalized_question).where(
        GapRejection.repository_id == repository_id
    )
    stmt = (
        sa.select(...)  # unchanged select list
        .where(
            QueryLog.repository_id == repository_id,
            QueryLog.not_in_vault.is_(True),
            _NORMALIZED_QUESTION.notin_(rejected),
        )
        .group_by(_NORMALIZED_QUESTION)
        .order_by(ask_count.desc(), last_asked_at.desc())
    )
```
Add the new functions:
```python
async def reject_gap(
    session: AsyncSession,
    repository_id: UUID,
    *,
    question: str,
    reason: str,
    admin_id: UUID | None,
) -> GapRejection:
    """Reject a gap (upsert on repo + normalized question); the caller commits."""
    normalized = _normalize_text(question)
    existing = (
        await session.execute(
            sa.select(GapRejection).where(
                GapRejection.repository_id == repository_id,
                GapRejection.normalized_question == normalized,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.question = question
        existing.reason = reason
        existing.rejected_by = admin_id
        await session.flush()
        return existing
    rejection = GapRejection(
        repository_id=repository_id,
        normalized_question=normalized,
        question=question,
        reason=reason,
        rejected_by=admin_id,
    )
    session.add(rejection)
    await session.flush()
    return rejection


async def list_rejected_gaps(
    session: AsyncSession, repository_id: UUID
) -> Sequence[GapRejection]:
    """Rejected gaps for a repository, newest first."""
    rows = (
        await session.execute(
            sa.select(GapRejection)
            .where(GapRejection.repository_id == repository_id)
            .order_by(GapRejection.created_at.desc())
        )
    ).scalars().all()
    return list(rows)
```
Add a Python-side normalizer matching the SQL one (lower + trim + collapse whitespace), since `reject_gap` needs the normalized string as a value, not a column expression:
```python
import re

def _normalize_text(question: str) -> str:
    """Python twin of ``normalized_question`` (SQL) for storing the gap identity."""
    return re.sub(r"\s+", " ", question.strip().lower())
```
> The SQL `normalized_question` collapses whitespace with `regexp_replace(lower(btrim(x)), '\s+', ' ', 'g')`; `_normalize_text` mirrors it. A service test already asserts a differently-cased/spaced re-reject collapses to one row, guarding the parity.

- [ ] **Step 4: Run — verify it passes**

Run: `uv run pytest tests/test_knowledge_gaps_service.py tests/test_knowledge_gaps_api.py -q`
Expected: PASS (existing gap tests still green; new ones pass).

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/services/knowledge_gaps.py tests/test_knowledge_gaps_service.py
git commit -m "feat: reject knowledge gaps and exclude them from the gap list"
```

---

### Task 10: Reject + rejected-list endpoints

**Files:**
- Modify: `src/contextvault/api/knowledge_gaps.py`
- Test: `tests/test_knowledge_gaps_api.py` (extend)

**Interfaces:**
- Consumes: `reject_gap`, `list_rejected_gaps` (Task 9).
- Produces:
  - `POST /repositories/{id}/knowledge-gaps/reject` — body `{question, reason(min_length 1)}` → 201 `GapRejectionResponse`; 422 empty reason; 404 unknown repo; 403 non-admin.
  - `GET /repositories/{id}/knowledge-gaps/rejected -> list[GapRejectionResponse]`.
  - `GapRejectionResponse = {question, reason, rejected_by: str | None, rejected_at: datetime}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_knowledge_gaps_api.py`:
```python
async def test_reject_requires_admin(db_session, client):
    await _user(db_session, Role.USER, "alice")
    repo = await _repo(db_session, "Handbook")
    resp = await client.post(f"/repositories/{repo.id}/knowledge-gaps/reject",
                             json={"question": "Q", "reason": "r"}, headers=_auth(await _token(client, "alice")))
    assert resp.status_code == 403

async def test_reject_empty_reason_is_422(db_session, client):
    await _user(db_session, Role.ADMIN, "admin")
    repo = await _repo(db_session, "Handbook")
    resp = await client.post(f"/repositories/{repo.id}/knowledge-gaps/reject",
                             json={"question": "Q", "reason": ""}, headers=_auth(await _token(client, "admin")))
    assert resp.status_code == 422

async def test_reject_then_gap_hidden_and_listed_rejected(db_session, client):
    await _user(db_session, Role.ADMIN, "admin")
    repo = await _repo(db_session, "Handbook")
    await _log(db_session, repo_id=repo.id, question="What is the VPN?", not_in_vault=True)
    token = await _token(client, "admin")
    r = await client.post(f"/repositories/{repo.id}/knowledge-gaps/reject",
                          json={"question": "What is the VPN?", "reason": "Out of scope"}, headers=_auth(token))
    assert r.status_code == 201
    gaps = (await client.get(f"/repositories/{repo.id}/knowledge-gaps", headers=_auth(token))).json()
    assert gaps == []
    rejected = (await client.get(f"/repositories/{repo.id}/knowledge-gaps/rejected", headers=_auth(token))).json()
    assert rejected[0]["question"] == "What is the VPN?"
    assert rejected[0]["reason"] == "Out of scope"
    assert rejected[0]["rejected_by"] == "admin"
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run pytest tests/test_knowledge_gaps_api.py -q -k reject`
Expected: FAIL (routes 404/405).

- [ ] **Step 3: Edit `api/knowledge_gaps.py`**

Add imports: `from pydantic import BaseModel, Field`, `from contextvault.models import Repository, User` (extend to import `GapRejection` only if needed — the response is built from service objects), `from contextvault.api.deps import get_current_user, require_admin`. Add schemas + routes:
```python
class RejectGapRequest(BaseModel):
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class GapRejectionResponse(BaseModel):
    question: str
    reason: str
    rejected_by: str | None
    rejected_at: datetime


@router.post("/repositories/{repository_id}/knowledge-gaps/reject", status_code=status.HTTP_201_CREATED)
async def reject_knowledge_gap(
    repository_id: uuid.UUID,
    payload: RejectGapRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> GapRejectionResponse:
    """Reject a knowledge gap with a required reason (admin-only)."""
    if await session.get(Repository, repository_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    rejection = await gap_service.reject_gap(
        session, repository_id, question=payload.question, reason=payload.reason, admin_id=admin.id
    )
    await session.commit()
    return GapRejectionResponse(
        question=rejection.question,
        reason=rejection.reason,
        rejected_by=admin.username,
        rejected_at=rejection.created_at,
    )


@router.get("/repositories/{repository_id}/knowledge-gaps/rejected")
async def list_rejected_knowledge_gaps(
    repository_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[GapRejectionResponse]:
    """Rejected knowledge gaps for a repository, newest first (admin-only)."""
    if await session.get(Repository, repository_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    rejections = await gap_service.list_rejected_gaps(session, repository_id)
    author_ids = {r.rejected_by for r in rejections if r.rejected_by}
    authors: dict[uuid.UUID, str] = {}
    if author_ids:
        rows = (await session.execute(select(User).where(User.id.in_(author_ids)))).scalars().all()
        authors = {u.id: u.username for u in rows}
    return [
        GapRejectionResponse(
            question=r.question,
            reason=r.reason,
            rejected_by=authors.get(r.rejected_by) if r.rejected_by else None,
            rejected_at=r.created_at,
        )
        for r in rejections
    ]
```
Add `from sqlalchemy import select` at the top.
> Placement matters: define the `/reject` and `/rejected` routes; FastAPI matches them fine alongside `GET /knowledge-gaps` since paths differ. No ordering hazard (no path param collides with the literal segments).

- [ ] **Step 4: Run — verify it passes**

Run: `uv run pytest tests/test_knowledge_gaps_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/contextvault/api/knowledge_gaps.py tests/test_knowledge_gaps_api.py
git commit -m "feat: reject-gap and rejected-list admin endpoints"
```

---

### Task 11: Admin UI — Reject button, reason, Rejected gaps section

**Files:**
- Modify: `frontend/src/api/knowledgeGaps.ts`
- Modify: `frontend/src/pages/AdminInsightsPage.tsx`
- Modify: `frontend/src/i18n/locales/en.json`, `frontend/src/i18n/locales/uk.json`
- Test: `frontend/src/pages/AdminInsightsPage.test.tsx` (create if absent, else extend)

**Interfaces:**
- Produces: `rejectGap(repositoryId, {question, reason})`, `listRejectedGaps(repositoryId)`; `GapRejection = {question, reason, rejected_by, rejected_at}`.

- [ ] **Step 1: Write the failing test**

Add to `AdminInsightsPage.test.tsx` (match the file's mock style; if the file doesn't exist, create it mirroring another page test's setup that mocks `/repositories` + `/knowledge-gaps`):
```ts
it("rejects a gap with a required reason and removes it from the list", async () => {
  // mock: listAllRepositories -> [{id:'r-1',name:'Handbook'}]
  //       GET /knowledge-gaps -> [{question:'What is the VPN?', ask_count:2, user_count:1, last_asked_at:...}]
  //       GET /knowledge-gaps/rejected -> []
  //       POST /knowledge-gaps/reject -> 201
  render(<AdminInsightsPage />);
  await screen.findByText("What is the VPN?");
  await userEvent.click(screen.getByRole("button", { name: "Reject" }));
  // confirm blocked while reason empty:
  expect(screen.getByRole("button", { name: "Confirm rejection" })).toBeDisabled();
  await userEvent.type(screen.getByLabelText("Reason for rejecting"), "Out of scope");
  await userEvent.click(screen.getByRole("button", { name: "Confirm rejection" }));
  expect(screen.queryByText("What is the VPN?")).not.toBeInTheDocument();
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("/knowledge-gaps/reject"));
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ question: "What is the VPN?", reason: "Out of scope" });
});
```

- [ ] **Step 2: Run — verify it fails**

Run (in `frontend/`): `npm run test -- AdminInsightsPage.test.tsx`
Expected: FAIL — no Reject button.

- [ ] **Step 3: Edit `api/knowledgeGaps.ts`**

```ts
export interface GapRejection {
  question: string;
  reason: string;
  rejected_by: string | null;
  rejected_at: string;
}

export function rejectGap(
  repositoryId: string,
  body: { question: string; reason: string },
): Promise<GapRejection> {
  return api.post<GapRejection>(`/repositories/${repositoryId}/knowledge-gaps/reject`, body);
}

export function listRejectedGaps(repositoryId: string): Promise<GapRejection[]> {
  return api.get<GapRejection[]>(`/repositories/${repositoryId}/knowledge-gaps/rejected`);
}
```

- [ ] **Step 4: Edit `AdminInsightsPage.tsx`**

- Import `rejectGap, listRejectedGaps, type GapRejection`.
- In `GapRow`, add reject state + an inline form beside "Answer this gap":
```tsx
const [rejecting, setRejecting] = useState(false);
const [reason, setReason] = useState("");
const [rejectError, setRejectError] = useState<string | null>(null);

const onReject = async (e: FormEvent) => {
  e.preventDefault();
  if (reason.trim() === "") return;
  try {
    await rejectGap(repositoryId, { question: gap.question, reason: reason.trim() });
    onRejected();  // parent removes the gap + refreshes the rejected list
  } catch (err) {
    setRejectError(errorMessage(err, t("insights.errorRejectGap")));
  }
};
```
Render a **Reject** button (`t("insights.rejectGap")`) that toggles `rejecting`; when `rejecting`, show a labeled `<textarea>` (`t("insights.rejectReason")`, placeholder `t("insights.rejectReasonPlaceholder")`) and a **Confirm rejection** submit (`t("insights.confirmReject")`) `disabled` while `reason.trim() === ""`. Thread an `onRejected` prop from `KnowledgeGapsPanel` that filters the gap out (same as `onAnswered`) and re-loads rejected gaps.
- In `KnowledgeGapsPanel`, add `rejected` state loaded via `listRejectedGaps(selected)` in the existing effect, an `onRejected(question)` handler (drop from `gaps`, refetch `listRejectedGaps`), and render a **Rejected gaps** subsection after the gap list:
```tsx
<h3>{t("insights.rejectedGaps")}</h3>
{rejected === null ? null : rejected.length === 0 ? (
  <p>{t("insights.noRejectedGaps")}</p>
) : (
  <ul className="rejected-gap-list">
    {rejected.map((r) => (
      <li key={r.question}>
        <span className="gap-question">{r.question}</span>
        <span className="gap-reason">{r.reason}</span>
        <span className="gap-signal">
          {t("insights.rejectedBy", { admin: r.rejected_by ?? "—", date: new Date(r.rejected_at).toLocaleDateString() })}
        </span>
      </li>
    ))}
  </ul>
)}
```

- [ ] **Step 5: Add i18n keys**

In both `en.json` and `uk.json`, inside `insights` add:
| key | en | uk |
|---|---|---|
| `rejectGap` | `Reject` | `Відхилити` |
| `rejectReason` | `Reason for rejecting` | `Причина відхилення` |
| `rejectReasonPlaceholder` | `Explain why this gap won't be answered…` | `Поясніть, чому ця прогалина не буде закрита…` |
| `confirmReject` | `Confirm rejection` | `Підтвердити відхилення` |
| `rejectedGaps` | `Rejected gaps` | `Відхилені прогалини` |
| `rejectedBy` | `rejected by {{admin}} · {{date}}` | `відхилено {{admin}} · {{date}}` |
| `noRejectedGaps` | `No rejected gaps.` | `Немає відхилених прогалин.` |
| `errorRejectGap` | `Could not reject the gap.` | `Не вдалося відхилити прогалину.` |

- [ ] **Step 6: Run — verify it passes**

Run (in `frontend/`): `npm run test -- AdminInsightsPage.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/knowledgeGaps.ts frontend/src/pages/AdminInsightsPage.tsx frontend/src/i18n/locales/en.json frontend/src/i18n/locales/uk.json frontend/src/pages/AdminInsightsPage.test.tsx
git commit -m "feat(web): reject knowledge gaps with a reason; Rejected gaps list"
```

---

### Task 12: Full gate + docs

**Files:**
- Modify: `docs/architecture.md` and/or `README.md` if they describe the query/knowledge-gap flows (per the memory rule: update docs before the PR).

- [ ] **Step 1: Backend gate**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run alembic upgrade head && uv run pytest -q`
Fix everything until green.

- [ ] **Step 2: Frontend gate**

Run (in `frontend/`): `npm run lint && npm run format:check && npm run typecheck && npm run test && npm run build`
Fix everything until green.

- [ ] **Step 3: Update docs**

If `docs/architecture.md` describes conversation memory as client-only or the knowledge-gap flow as answer-only, update those sections (persisted per-user conversations; admin can answer *or reject* gaps). Add a one-line note in `README.md` if it enumerates endpoints/features.

- [ ] **Step 4: Commit**

```bash
git add docs README.md
git commit -m "docs: persisted conversations + gap rejection"
```

---

## Self-review notes (author)

- **Spec coverage:** Part 1 (persist per user+repo, full-turn restore, one-per-repo, clear button, server-authoritative history) → Tasks 1–7. Part 2 (reject with required reason, hidden from active list, rejected list) → Tasks 8–11. Migration → Tasks 1 & 8 (two migrations; deviation noted). Testing across every task. ✅
- **Type consistency:** `recent_history` returns `list[tuple[str,str]]` used verbatim by `api/query.py`; `ConversationTurnResponse.citations/sources` reuse `CitationResponse`/`SourceReferenceResponse` so restore matches the query response exactly; frontend `SavedTurn` reuses `Citation`/`SourceReference` from `query.ts`. `_normalize_text` mirrors the SQL `normalized_question`; a service test guards parity.
- **Open coupling to confirm at execution:** the exact fake-LLM-builder wiring in `test_query_api.py` (Task 3) and the grant-seeding helper name in `services/grants.py` (Task 4) — the implementer matches the existing test files rather than inventing fixtures.
```
