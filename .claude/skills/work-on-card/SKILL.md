---
name: work-on-card
description: Work a ContextVault board card (GitHub issue) end-to-end and manage the "ContextVault" GitHub Projects V2 board via the gh CLI. Resolve a card, move its Status, implement it via TDD, and open a PR — stopping at "In review" for a human to merge. Also does bare board ops (list/read, create card, move column, set Priority/labels, tick checkboxes). Use when the user says "start working with card #N", "work on card N", "pick up the next card", "move #N to <column>", "what's on the board", "create a card", or "set #N to P0".
---

# work-on-card — implement one board card, own the board

One combined skill: it **owns the ContextVault board mechanics** *and* the
**work-a-card workflow**. "Card #N" = GitHub issue #N in `GoodGlor/ContextVault`
(cards are issues, 1:1). You take ONE card to an open PR and leave the card in
**In review**; a human merges and the card then goes to **Done**.

## Known constants

| | Value |
|---|---|
| Repo | `GoodGlor/ContextVault` |
| Project owner | `GoodGlor` (user, so `--owner GoodGlor`) |
| Project number | `1` |
| Project node ID | `PVT_kwHOBH_jW84Bcs4d` |
| Status flow | `Backlog → Ready → In progress → In review → Done` (columns) |
| Other fields | `Priority` (P0/P1/P2), `Size` (XS–XL) |

**IDs are discovered at runtime, never hardcoded into commands.** Field IDs and
single-select option IDs drift if a field/column is renamed or recreated, so look
them up before any write (see Discovery). The constants above only identify *which*
board. (Values observed once, for orientation only — re-resolve, don't trust:
Status field `PVTSSF_lAHOBH_jW84Bcs4dzhXTueM`; options Backlog `f75ad846`, Ready
`61e4505c`, In progress `47fc9ee4`, In review `df73e18b`, Done `98236657`.)

## Prerequisite — `project` scope (guard first)

Projects V2 read/write needs the `project` token scope. If a board command fails
with a missing-scope error, STOP and tell the user to run `gh auth refresh -s project`.
Quick check: `gh project list --owner GoodGlor` should list "ContextVault".

## Definition of Done (this repo's CI gate)

There is no `./ci.sh`; the gate is four commands (all must be green). Postgres must
be running for the DB-backed tests (`docker compose up -d`; migrated with
`uv run alembic upgrade head`):

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest
```

## Standing rules

1. **Assign to the user:** new issues and PRs use `--assignee @me` (you are `GoodGlor`).
2. **Card template** (below) for every new issue — never a bare body.
3. **Auto-move as you work, announce every move:** `Backlog`/`Ready` → **In progress**
   at start → **In review** when the PR is open. Never move silently. **Do not move a
   card to `Done` yourself** — that happens after a human merges (offer to do it then).
4. **Tick checkboxes honestly.** When a card reaches Done, tick the acceptance /
   Definition-of-Done boxes in the issue body — **but only the ones actually satisfied.**
   If a box isn't genuinely true, leave it unticked and say so; never blanket-tick.
5. **Reads run immediately. Single writes** (one create/move/label) execute directly
   after stating the exact change. **Bulk writes** (move/create several) are confirmed
   with the user first.
6. **Default labels:** Python/`src/` work → `backend`; React/frontend work → `frontend`;
   plus `enhancement`/`bug`/`tech-debt`/`testing`/`auth`/`rag` as fits. Leave Priority
   unset unless the user names one.
7. **Update docs before the PR.** Update README / `docs/` to match the change, in
   the same PR — before opening it. This is a hard rule for this repo. **Do NOT add
   or maintain an "Implementation status" checklist in the README** — status is
   tracked on the GitHub Projects board (the source of truth); a duplicate in the
   README only drifts. Document behavior/architecture, not project status.

## Card template

```markdown
## Context & Objective

<1–2 short paragraphs: what this is, why, and the scope boundary. Link related
issues with #N and the design spec section (docs/superpowers/specs/…).>

## Technical Requirements

- <concrete, file-path-specific requirements>

## Acceptance Criteria

- [ ] <observable behavior that proves it works>

## Definition of Done

- [ ] `uv run ruff check src tests` and `uv run ruff format --check src tests` pass.
- [ ] `uv run mypy` (strict) passes.
- [ ] `uv run pytest` green (DB up + migrated for DB-backed tests).
- [ ] Docs/README updated where applicable.
- [ ] PR opened and reviewed.
```

## Discovery (run before any write that needs IDs)

Status / Priority field IDs and their option IDs:

```bash
gh project field-list 1 --owner GoodGlor --format json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f['id'], f['name'], '->', [(o['name'],o['id']) for o in f.get('options',[])]) for f in d['fields'] if f.get('options')]"
```

Map an issue number → its project-item ID:

```bash
gh project item-list 1 --owner GoodGlor --limit 200 --format json \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(i['id'], i.get('content',{}).get('number'), i.get('status'), i.get('content',{}).get('title')) for i in d['items']]"
```

## Board operations (bare, on request)

### List / read the board (read-only, immediate)

> The board has 40+ items — always pass `--limit 200` (default is 30, silently truncates).

```bash
gh project item-list 1 --owner GoodGlor --limit 200 --format json \
  | python3 -c "import sys,json,collections; d=json.load(sys.stdin); g=collections.defaultdict(list); [g[i.get('status','No Status')].append((i.get('content',{}).get('number'),i.get('content',{}).get('title'))) for i in d['items']]; [print(f'\n## {k}') or [print(f'  #{n} {t}') for n,t in v] for k,v in g.items()]"
```

### Create a card

```bash
URL=$(gh issue create --repo GoodGlor/ContextVault \
  --title "<TITLE>" --assignee @me --label <label> [--label <label>] \
  --body "<TEMPLATE BODY>")
gh project item-add 1 --owner GoodGlor --url "$URL"   # new cards land in Backlog
```

### Change Status (move a column)

```bash
# resolve STATUS_FIELD_ID, target OPTION_ID, and the card's ITEM_ID via Discovery
gh project item-edit --project-id PVT_kwHOBH_jW84Bcs4d \
  --id <ITEM_ID> --field-id <STATUS_FIELD_ID> --single-select-option-id <OPTION_ID>
```

### Set Priority / labels

```bash
gh issue edit <N> --repo GoodGlor/ContextVault --add-label <label>   # labels live on the issue
# Priority field uses the same item-edit path as Status, with the Priority field + option IDs from Discovery.
```

### Tick checkboxes (honestly — rule 4)

Tick only satisfied boxes. Targeted edit (safe): read the body, flip specific
`- [ ]`→`- [x]` lines, write it back.

```bash
gh issue view <N> --repo GoodGlor/ContextVault --json body -q .body   # inspect, edit deliberately, then:
gh issue edit <N> --repo GoodGlor/ContextVault --body "<edited body>"
```

## Working a card, end-to-end

### 1. Resolve the card
- `work on card #N` → that issue. `work on the next card` / bare → rank eligible
  cards and propose the top one, then WAIT for confirmation.
- Eligible = Status `Ready` (or `Backlog` if the user names it), not `Blocked`,
  ordered by Priority (P0<P1<P2, unset last) then issue number:

```bash
gh project item-list 1 --owner GoodGlor --format json --limit 200 \
  | jq -r --argjson prio '["P0","P1","P2"]' '
      .items | map(select(.status=="Ready"))
      | map(.prank = ((.priority // "") as $p | ($prio|index($p)) // ($prio|length)))
      | sort_by(.prank, .content.number)[] | "\(.content.number)\t\(.priority // "-")\t\(.title)"'
```

### 2. Read + claim
Read the full issue **and all comments** (`gh issue view <N> --repo GoodGlor/ContextVault
--json number,title,body,comments`) — human comments OVERRIDE the body. Then move the
card to **In progress** (announce it).

Treat the design spec (`docs/superpowers/specs/…`) and README as authoritative intended
behavior. If the card contradicts intended behavior, STOP (hard stop) with a
"needs-human:" note rather than implementing it.

### 3. Branch (never off stale main)

```bash
git fetch origin
git checkout main
git pull --ff-only origin main      # if this fails, main diverged — stop, surface it
git checkout -b feat/<N>-<short-slug>
```

### 4. Implement via TDD
RED first: write the failing test(s) under `tests/` that express the behavior; run
the specific test and confirm it fails for the right reason. GREEN: minimal code to
pass. Then run the full DoD gate — all four commands green, or hard-stop.
If a test passes on unmodified code you cannot reproduce the need — stop; don't invent a fix.

### 5. Docs, self-review, conflict check (before the PR)
- Update docs per rule 7 (affected README/`docs/` sections), in this branch. No
  Implementation-status checklist in the README — the board tracks status.
- Self-review `git diff main...HEAD` with skeptical eyes (tests assert real behavior;
  `||` vs `??`; DRY; comments don't over-claim). Fix findings with the same TDD
  discipline and re-green.
- Conflict check against remote main — never open an unmergeable PR:

```bash
git fetch origin
git merge-tree --write-tree origin/main HEAD   # exit 0 = clean; 1 = conflicts
```
  If conflicts: unpushed → `git rebase origin/main`; pushed → `git merge origin/main`
  (never force-push). Re-run the full DoD gate green after resolving.

### 6. Open the PR, move to In review — then STOP
Small conventional commits (`feat:`/`fix:`/`test:`), each ending with the repo's
`Co-Authored-By: Claude …` trailer. Reference the card without a closing verb
(`Refs #N`), so the board — not GitHub auto-close — decides the final state.

```bash
git push -u origin feat/<N>-<short-slug>
gh pr create --base main --assignee @me --title "feat: <…> (card #<N>)" --body "<sections>"
```
PR body sections: `## What` · `## Tests` (RED→GREEN evidence) · `## DoD` (paste the
four green commands) · `## Docs` · `## Risk` · `Refs #N`.

Then move the card to **In review**, comment the PR URL on the issue, and **STOP** —
do not merge. Tell the user the PR is ready; on merge, offer to move the card to
**Done** and tick its satisfied checkboxes (rule 4).

## Hard stops
Cannot reproduce, DoD gate won't go green, the card needs work it didn't approve, or
`--ff-only` shows a diverged main: STOP, push the branch as-is (work preserved), and
report why. If no PR yet, move the card to **Blocked** with a comment; if a PR is
already open, leave it and flag for the user.

## Forbidden
Merging or force-pushing without being asked, committing to `main`, editing files
outside this repo, unrelated refactoring, committing secrets, blanket-ticking
checkboxes that aren't actually satisfied.
