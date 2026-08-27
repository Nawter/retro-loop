from app.sessions import CODE_ALPHABET, PHASES


def create(client):
    return client.post("/sessions").json()


def join(client, code, name="Sam"):
    return client.post(f"/sessions/{code}/join", json={"name": name})


def advance(client, code, token):
    return client.post(f"/sessions/{code}/advance", json={"token": token})


def finish(client, session):
    for _ in PHASES[1:]:
        advance(client, session["code"], session["facilitator_token"])


def test_create_returns_code_and_facilitator_token(client):
    session = create(client)
    assert len(session["code"]) == 6
    assert session["facilitator_token"]


def test_code_has_no_ambiguous_glyphs(client):
    assert not set("0O1Il") & set(CODE_ALPHABET)
    assert set(create(client)["code"]) <= set(CODE_ALPHABET)


def test_join_creates_a_participant_row(client, db):
    response = join(client, create(client)["code"])
    assert response.status_code == 201
    token = response.json()["participant_token"]
    row = db.execute(
        "SELECT * FROM participants WHERE token = ?", (token,)
    ).fetchone()
    assert row["name"] == "Sam"


def test_join_is_case_insensitive(client):
    assert join(client, create(client)["code"].lower()).status_code == 201


def test_blank_name_is_rejected(client):
    assert join(client, create(client)["code"], name="   ").status_code == 422


def test_two_participants_may_share_a_name(client):
    code = create(client)["code"]
    first, second = join(client, code), join(client, code)
    assert first.status_code == second.status_code == 201
    assert first.json()["participant_token"] != second.json()["participant_token"]


def test_join_unknown_code_is_rejected(client):
    assert join(client, "XXXXXX").status_code == 404


def test_join_allowed_in_every_phase_except_done(client):
    session = create(client)
    assert join(client, session["code"]).status_code == 201  # write
    for phase in PHASES[1:]:
        advance(client, session["code"], session["facilitator_token"])
        expected = 409 if phase == "done" else 201
        assert join(client, session["code"]).status_code == expected


def test_code_collision_is_retried(client, monkeypatch):
    import secrets

    taken = create(client)["code"]
    fallback = "BBBBBB" if taken != "BBBBBB" else "CCCCCC"
    rigged = iter(taken + fallback)  # first roll collides, second is free
    monkeypatch.setattr(secrets, "choice", lambda _: next(rigged))
    assert create(client)["code"] == fallback


def test_phase_advances_in_fixed_order(client):
    session = create(client)
    seen = [
        advance(client, session["code"], session["facilitator_token"]).json()["phase"]
        for _ in PHASES[1:]
    ]
    assert seen == list(PHASES[1:])


def test_advance_past_done_is_rejected(client):
    session = create(client)
    finish(client, session)
    assert (
        advance(client, session["code"], session["facilitator_token"]).status_code
        == 409
    )


def test_only_the_facilitator_token_advances(client, db):
    session = create(client)
    participant = join(client, session["code"]).json()["participant_token"]
    other = create(client)["facilitator_token"]

    assert advance(client, session["code"], participant).status_code == 403
    assert advance(client, session["code"], other).status_code == 403
    assert advance(client, session["code"], "not-a-token").status_code == 403
    assert client.post(f"/sessions/{session['code']}/advance", json={}).status_code == 422

    phase = db.execute(
        "SELECT phase FROM sessions WHERE code = ?", (session["code"],)
    ).fetchone()["phase"]
    assert phase == "write"
