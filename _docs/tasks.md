# Weekly Retro Tool — Backlog

**Stack:** FastAPI + WebSockets + SQLite (stdlib `sqlite3`, no ORM), single static
HTML/JS frontend served by the same app, deployed as one process.

Tasks are ordered so the stack builds bottom-up, but each is written to be picked
up cold and finished in one sitting. Scope of the tool is in `_docs/plan.md`.

## 1. Project scaffold with a database that creates itself
Goal: A runnable FastAPI app, a green `pytest`, and a SQLite file on first boot.
Description: Scaffold with Poetry, pytest, mypy and the standard colour-coded
Makefile (`help`/`deps`/`lint`/`check`/`test`). Add `GET /health` returning
`{"status": "ok"}`. Write `schema.sql` with tables for `sessions`,
`participants`, `cards`, `clusters`, `votes` and `decisions`, plus a helper that
opens a `sqlite3` connection (row factory `sqlite3.Row`, foreign keys and WAL on)
and executes the schema idempotently at startup. No ORM, no migration tool — the
schema file is the source of truth. Add `.gitignore` covering `.env`, `*.db` and
`__pycache__` before the first commit. Tests: `/health` returns 200, and a temp
database has every table.

## 2. Session lifecycle: create, join, advance phase
Goal: People get into the same room, and one person can drive it.
Description: `POST /sessions` creates a retro, generates a short human-typable
join code (6 characters, no ambiguous glyphs) and returns the code plus a
facilitator token. `POST /sessions/{code}/join` takes a display name, creates a
participant row and returns a participant token used for all later calls. Tokens
are opaque random strings stored in the database — no login, no password, no
email. Store the current phase on the session row and allow it to advance in a
fixed order: `write` → `reveal` → `cluster` → `vote` → `discuss` → `done`. Only a
caller presenting the facilitator token may advance; anyone else is rejected.

## 3. WebSocket room hub with full-state snapshot
Goal: A message from one client reaches the room, and a fresh client sees the world.
Description: Add `WS /ws/{code}` backed by an in-memory `dict[str, set[WebSocket]]`
mapping join code to live connections. Handle connect, disconnect and broadcast,
and make sure a dropped socket is removed from the set rather than crashing the
fanout loop. On connect, read the session's phase, cards, clusters, vote counts
and decisions from SQLite and send them as one `snapshot` message before any live
events; every later change is a small incremental event, so the client applies
snapshot-then-deltas. This is what makes refresh and laptop-sleep survivable, so
it is worth getting right early. Test with two `TestClient` websocket connections
in one room and a third in another.

## 4. Cards: create, edit, delete, and pre-reveal visibility
Goal: People can write Start/Stop/Continue cards, and nobody peeks early.
Description: Add handlers for creating a card (column is one of
`start`/`stop`/`continue`, plus text and an `anonymous` boolean), editing its text
and deleting it. A participant may only modify their own cards — enforced
server-side against the participant token, not in the UI. While the session is in
the `write` phase each connection receives only its own author's cards; from
`reveal` onward everyone receives all of them. That filter lives in the snapshot
and broadcast paths — never send another person's card text and hide it in the
client. Strip the author name from anonymous cards in every phase. Each accepted
change writes to SQLite and broadcasts to the room.

## 5. Clusters and decisions
Goal: Cards can be grouped, and outcomes can be written down.
Description: Add handlers to create a named cluster and move a card into or out of
one (`cards.cluster_id`); anyone in the room may move any card, and simultaneous
moves resolve last-write-wins — good enough for a small team in a call. Add
handlers to add, edit and delete decision entries attached to the session, each
with free text, a type (`decision` or `action`) and an optional owner name. Any
participant can write them — a shared scribe pad, not a facilitator-only field.
Persist and broadcast like every other change.

## 6. Voting with a server-enforced budget
Goal: Three votes per person, stackable, impossible to exceed.
Description: Add a handler that records a vote by a participant on a card and one
that removes it. Before inserting, count that participant's existing votes in the
session and reject if they already hold three — enforced inside the database
transaction, never in the client. Multiple votes from the same person on the same
card are allowed. Broadcast updated per-card totals after each change. Test the
rejection path.

## 7. Frontend shell: create, join, connect, facilitate
Goal: One HTML page that gets you into a retro and stays connected.
Description: Serve one static `index.html` (plus one JS and one CSS file) from the
FastAPI app — no build step, no framework. A create screen returns the join code
and a shareable link; a join screen asks for code and display name, calls the join
endpoint, stores the token in `localStorage`, opens the WebSocket and renders
whatever phase the snapshot reports. Keep the join code visible somewhere
persistent so latecomers can be told it. Show a "Next phase" button and the name
of the upcoming phase only to a client holding a facilitator token — server
enforcement already exists, this is the UI on top. Everything after this task is
rendering one more phase into this shell.

## 8. Write and reveal UI
Goal: People type cards into three columns, then everyone's appear at once.
Description: Render Start / Stop / Continue columns with a composer in each and an
anonymous checkbox per card. Submitting sends a create-card message over the
WebSocket and the card appears when the server echoes it back — do not render
optimistically, it makes reconnect states inconsistent. Support editing and
deleting your own cards inline. On the `reveal` phase the same three columns
render every card in the room with the author's name, except where the card is
anonymous; reveal is read-only. Handle a snapshot arriving with dozens of cards
without the layout collapsing.

## 9. Drag-to-cluster UI
Goal: Cards can be dragged into groups and everyone sees it move.
Description: Use native HTML5 drag-and-drop (`draggable`, `dragover`, `drop`) to
let any participant drag a card onto another card or onto an empty cluster zone,
sending a move message to the server. Render clusters as titled boxes with an
editable name. Reconcile against inbound broadcast events so a card someone else
moved jumps to its new group without a refresh.

## 10. Voting and discussion UI
Goal: Casting votes is obvious, then the team works top-down through the winners.
Description: In the `vote` phase render a vote button and a running count on each
card, plus a persistent "votes left: N" indicator; clicking sends a vote message,
and a server rejection (budget exhausted) shows a quiet inline message rather than
a blocking alert. Allow removing a vote you cast. In the `discuss` phase render
the same cards sorted by vote count descending with their cluster shown alongside.

## 11. Decision pad and export
Goal: The result of the retro leaves the meeting.
Description: Below the discuss board, a shared list where anyone can add, edit and
delete decisions and action items with an optional owner, updating live for
everyone. Add a markdown export of the whole retro — cards by column, clusters,
vote counts, decisions and actions — rendered client-side from current state so it
can be copied and pasted elsewhere.

## 12. Client reconnect and resync
Goal: A closed laptop lid does not cost you the retro.
Description: Detect WebSocket close on the client and reconnect with backoff,
re-sending the stored participant token and re-applying the fresh snapshot over
local state. Show a small "reconnecting" indicator while disconnected and queue
nothing — actions dropped during a gap should fail visibly rather than replay
late. Test by killing and restarting the server with a page open.

## 13. Dockerfile and deploy
Goal: The app runs somewhere the team can reach it.
Description: Write a Dockerfile running uvicorn and deploy to Fly.io or Railway
with a persistent volume mounted for the SQLite file. Confirm the database
survives a redeploy. Document the deploy command in the README.

## Deliberately not in the MVP
- **Post-meeting media upload** — an upload endpoint, a size cap, a second volume
  and an authenticated download, all to attach a file nobody reads during the
  retro. Paste a link into a decision entry until someone complains.
- **Scheduled database backup** — add it the week after the first real retro that
  would have hurt to lose.
- Retro history / listing past sessions — every row is kept, add the `SELECT` when
  someone asks for it.
- Real accounts — name-on-join plus an opaque token covers a single known team.
- Built-in recording, transcription, and CRDT-based conflict resolution.
