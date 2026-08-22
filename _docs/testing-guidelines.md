# Testing guidelines

Read before writing tests. `uv run pytest` runs the suite,
`uv run pytest tests/test_cards.py` runs one file.

## Shape

- pytest only. No second test framework, no fixture library, no factories.
- One file per module under test: `app/cards.py` -> `tests/test_cards.py`.
- Test names state the rule, not the function: `test_fourth_vote_is_rejected`.

## Use the real thing

- Tests run against a real SQLite database on `tmp_path`, built from
  `schema.sql`. Never mock the database.
- Tests drive the app through `TestClient` and `TestClient.websocket_connect`.
  Never mock the room hub or a WebSocket.
- Nothing in this project is slow, remote or destructive enough to deserve a
  fake yet.

## What must have a test

Every rule a client could otherwise lie its way past:

- the three-vote budget, including the rejected fourth vote
- pre-reveal visibility: another participant's card text must not appear in a
  `write`-phase snapshot or broadcast
- anonymous cards carry no author name, in any phase
- only the facilitator token advances the phase
- only the author edits or deletes a card
- room isolation: two sockets in one room, a third in another

These assert on what the server **sent**, not on what a UI would show. A rule
enforced server-side because the client is untrusted has to be tested the same
way.

## What not to test

- Getters, response-model shapes, and framework behaviour.
- The frontend directly. There is no JS test runner and there should not be
  one; assert the server messages the frontend reacts to instead.

## Fixtures

One `conftest.py`, two fixtures: a `db` giving a fresh schema per test, and a
`client` wrapping the app against it. Add a third only when two tests would
otherwise copy the same eight lines.
