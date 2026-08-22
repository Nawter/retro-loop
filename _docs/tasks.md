# Weekly Retro Tool — Backlog

**Stack:** FastAPI + WebSockets + SQLite (stdlib `sqlite3`, no ORM), one static
HTML/JS/CSS frontend served by the same app, deployed as one process.
Dependencies live in `pyproject.toml` and are installed with `uv sync`.

Each task below becomes one GitHub issue, filed with `_docs/task-template.md`
and worked one at a time (`_docs/process.md`). Scope of the tool is in
`_docs/plan.md`, test rules in `_docs/testing-guidelines.md`, UI rules in
`_docs/design-system.md`.

Tasks are ordered so the stack builds bottom-up, but each is written to be
picked up cold and finished in one sitting.

## 1. Project scaffold with a database that creates itself

### Goal
A runnable FastAPI app, a green `uv run pytest`, and a SQLite file that creates
itself on first boot.

### Acceptance criteria
- [ ] `uv sync` installs the project from `pyproject.toml`
- [ ] `uv run pytest` passes on a clean checkout
- [ ] `GET /health` returns 200 and `{"status": "ok"}`
- [ ] `schema.sql` defines `sessions`, `participants`, `cards`, `clusters`,
      `votes` and `decisions`, and a fresh database has all six
- [ ] Connections open with row factory `sqlite3.Row`, foreign keys on and WAL on
- [ ] Booting twice against the same file leaves the schema intact and loses no
      rows
- [ ] `.gitignore` covers `.env`, `*.db` and `__pycache__`, committed first

### Out of scope
- A migration tool. `schema.sql` is the source of truth until a released schema
  has to change under real data.

### Constraints
- stdlib `sqlite3` only, no ORM
- New dependencies need asking first (AGENTS.md)

## 2. Session lifecycle: create, join, advance phase

### Goal
People get into the same room, and exactly one person can drive it.

### Acceptance criteria
- [ ] `POST /sessions` returns a join code and a facilitator token
- [ ] The join code is 6 characters and contains no ambiguous glyphs
      (no `0`/`O`, no `1`/`I`/`l`)
- [ ] `POST /sessions/{code}/join` takes a display name, creates a participant
      row and returns a participant token
- [ ] Joining an unknown code is rejected
- [ ] Phase advances in the fixed order
      `write` -> `reveal` -> `cluster` -> `vote` -> `discuss` -> `done`
- [ ] Advancing with the facilitator token succeeds; with a participant token
      or no token it is rejected
- [ ] Advancing past `done` is rejected

### Out of scope
- Real accounts, passwords, email. Name-on-join plus an opaque token is the
  identity model for a single known team.

### Constraints
- Tokens are opaque random strings stored in the database and compared
  server-side
- The phase order is defined in one place, not repeated per endpoint

## 3. WebSocket room hub with full-state snapshot

### Goal
A message from one client reaches its room, and a fresh client sees the whole
world before it sees any change to it.

### Acceptance criteria
- [ ] `WS /ws/{code}` accepts a connection and joins it to the room for that code
- [ ] A message from one client reaches every other client in the same room and
      no client in another room
- [ ] On connect a client receives one `snapshot` message — phase, cards,
      clusters, vote counts, decisions — before any live event
- [ ] A dropped socket is removed from the room and does not break the fanout to
      the sockets still connected
- [ ] Tested with two `TestClient` sockets in one room and a third in another

### Out of scope
- Horizontal scaling, message brokers, presence indicators. One process owns the
  rooms.

### Constraints
- Rooms are an in-memory `dict[str, set[WebSocket]]`
- Every later change is a small incremental event: clients apply
  snapshot-then-deltas, which is what makes refresh and laptop-sleep survivable

## 4. Cards: create, edit, delete, and pre-reveal visibility

### Goal
People write Start / Stop / Continue cards, and nobody reads anyone else's early.

### Acceptance criteria
- [ ] Create, edit and delete card handlers persist to SQLite and broadcast to
      the room
- [ ] `column` is one of `start`, `stop`, `continue`; anything else is rejected
- [ ] A card carries text and an `anonymous` boolean
- [ ] Editing or deleting someone else's card is rejected server-side against the
      participant token
- [ ] During `write`, a connection receives only its own author's cards — in the
      snapshot and in broadcasts
- [ ] From `reveal` onward every connection receives every card
- [ ] Anonymous cards carry no author name in any phase, snapshot or broadcast

### Out of scope
- Rich text, attachments, per-card colours.

### Constraints
- Filtering happens before the message leaves the server. Never send another
  person's card text and hide it in the client.

## 5. Clusters and decisions

### Goal
Cards can be grouped, and the outcome of the discussion can be written down.

### Acceptance criteria
- [ ] A named cluster can be created and renamed, and both broadcast
- [ ] A card can be moved into and out of a cluster via `cards.cluster_id`
- [ ] Any participant can move any card
- [ ] Two simultaneous moves resolve last-write-wins without an error
- [ ] Decision entries can be added, edited and deleted, each with free text, a
      type of `decision` or `action`, and an optional owner name
- [ ] Any participant can write decisions, not only the facilitator
- [ ] Every accepted change persists and broadcasts

### Out of scope
- Nested clusters, CRDT merge, voting on a cluster rather than a card.

### Constraints
- Last-write-wins is deliberate: good enough for a small team already on a call

## 6. Voting with a server-enforced budget

### Goal
Three votes per person, stackable, impossible to exceed.

### Acceptance criteria
- [ ] A participant can cast a vote on a card and remove one they cast
- [ ] Several votes from the same person on the same card are allowed
- [ ] A fourth vote in a session is rejected, and the rejection has a test
- [ ] The existing-vote count and the insert happen in the same transaction
- [ ] Updated per-card totals broadcast after every change

### Out of scope
- Weighted votes, per-phase re-votes, changing the budget per session.

### Constraints
- The budget is enforced inside the database transaction, never in the client

## 7. Frontend shell: create, join, connect, facilitate

### Goal
One HTML page that gets you into a retro and keeps you there.

### Acceptance criteria
- [ ] One `index.html`, one `app.js` and one `app.css` served by the FastAPI app
- [ ] A create screen shows the join code and a shareable link
- [ ] A join screen takes code and display name, calls the join endpoint, stores
      the token in `localStorage` and opens the WebSocket
- [ ] The page renders whatever phase the snapshot reports, including on a cold
      reload
- [ ] The join code stays visible in every phase so latecomers can be told it
- [ ] "Next phase" and the name of the upcoming phase render only for a client
      holding a facilitator token

### Out of scope
- A framework, a bundler, npm, a second page. Everything after this task renders
  one more phase into this shell.

### Constraints
- `_docs/design-system.md`
- No build step
- The facilitator-only button is UI on top of server enforcement that already
  exists, not a substitute for it

## 8. Write and reveal UI

### Goal
People type cards into three columns, then everyone's appear at once.

### Acceptance criteria
- [ ] Start / Stop / Continue columns, each with a composer and an anonymous
      checkbox
- [ ] Submitting sends a create-card message over the WebSocket
- [ ] A card appears only when the server echoes it back
- [ ] Your own cards can be edited and deleted inline
- [ ] In `write` only your own cards render
- [ ] In `reveal` every card in the room renders with its author's name, except
      anonymous ones
- [ ] `reveal` is read-only
- [ ] A snapshot with dozens of cards scrolls rather than collapsing the layout

### Out of scope
- Reordering cards by hand, card templates, drafts.

### Constraints
- No optimistic rendering: it makes reconnect states inconsistent

## 9. Drag-to-cluster UI

### Goal
Cards can be dragged into groups and everyone sees them move.

### Acceptance criteria
- [ ] A card can be dragged onto another card or onto an empty cluster zone, and
      the move is sent to the server
- [ ] Clusters render as titled boxes with an editable name
- [ ] A card someone else moved jumps to its new group without a refresh
- [ ] A card can also be moved to a cluster without a pointer, since HTML5
      drag-and-drop is mouse-only

### Out of scope
- Multi-select drag, auto-clustering, animation.

### Constraints
- Native HTML5 drag-and-drop (`draggable`, `dragover`, `drop`), no drag library
- Inbound broadcast events are reconciled against local state, not ignored while
  a drag is in progress

## 10. Voting and discussion UI

### Goal
Casting votes is obvious, then the team works top-down through the winners.

### Acceptance criteria
- [ ] In `vote`, every card shows a vote button and a running count
- [ ] A persistent "votes left: N" indicator is visible throughout the phase
- [ ] A vote you cast can be removed
- [ ] A server rejection shows a quiet inline message in the live region, never
      a blocking alert
- [ ] In `discuss`, the same cards render sorted by vote count descending with
      their cluster shown alongside

### Out of scope
- Charts, per-person vote breakdowns, revealing who voted for what.

### Constraints
- `_docs/design-system.md`
- The count shown is the server's broadcast total, not a local tally

## 11. Decision pad and export

### Goal
The result of the retro leaves the meeting.

### Acceptance criteria
- [ ] A shared list below the discuss board where anyone can add, edit and delete
      decisions and action items with an optional owner
- [ ] Every change updates live for everyone in the room
- [ ] A markdown export contains cards by column, clusters, vote counts,
      decisions and actions
- [ ] The export is rendered client-side from current state and can be copied

### Out of scope
- PDF, email, Jira/Linear integration. Copy-paste covers it until someone
  complains.

## 12. Client reconnect and resync

### Goal
A closed laptop lid does not cost you the retro.

### Acceptance criteria
- [ ] The client detects WebSocket close and reconnects with backoff
- [ ] Reconnecting re-sends the stored participant token and re-applies the fresh
      snapshot over local state
- [ ] A "reconnecting" indicator shows while disconnected and is announced in the
      live region
- [ ] Actions attempted during a gap fail visibly and are not queued or replayed
      late
- [ ] Killing and restarting the server with a page open recovers the retro

### Constraints
- Snapshot-then-deltas from task 3 is what makes this cheap; do not add a
  client-side event log to replace it

## 13. Dockerfile and deploy

### Goal
The app runs somewhere the team can reach it.

### Acceptance criteria
- [ ] A Dockerfile runs uvicorn and the image builds
- [ ] Deployed to Fly.io or Railway with a persistent volume mounted at the
      SQLite path
- [ ] A redeploy leaves existing rows in place, confirmed by looking, not assumed
- [ ] The deploy command is documented in the README

### Out of scope
- CI, staging, blue/green, a second region.

## Deliberately not in the MVP

- **Post-meeting media upload** — an upload endpoint, a size cap, a second volume
  and an authenticated download, all to attach a file nobody reads during the
  retro. Paste a link into a decision entry until someone complains.
- **Scheduled database backup** — add it the week after the first real retro that
  would have hurt to lose.
- Retro history / listing past sessions — every row is kept, add the `SELECT`
  when someone asks for it.
- Real accounts — name-on-join plus an opaque token covers a single known team.
- Built-in recording, transcription, and CRDT-based conflict resolution.
