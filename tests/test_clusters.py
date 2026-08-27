from app.sessions import PHASES
from test_cards import (
    CARD_KEYS, advance, assert_nothing_pending, connect, create, create_card, join, room,
)

OPEN = ("cluster", "vote", "discuss")  # the rest of PHASES is closed


def drain(ws):
    """Poke the server and read everything queued ahead of its error reply."""
    ws.send_text("poke")
    while ws.receive_json()["type"] != "error":
        pass


def advance_to(client, r, phase):
    """The facilitator takes the room to `phase`; every socket's phase and reveal events are read."""
    while advance(client, r.session).json()["phase"] != phase:
        pass
    for ws in (r.sam1, r.sam2, r.ann1, r.observer):
        drain(ws)


def create_cluster(ws, name="process"):
    """Send a cluster.create and return the reply: the echoed event or the error."""
    ws.send_json({"type": "cluster.create", "name": name})
    return ws.receive_json()


def stored(db):
    return {
        "clusters": [tuple(row) for row in db.execute("SELECT id, name FROM clusters ORDER BY id")],
        "cards": [tuple(row) for row in db.execute("SELECT id, cluster_id FROM cards ORDER BY id")],
    }


def everyone(r):
    return (r.sam1, r.sam2, r.ann1, r.observer)


def test_create_echoes_the_committed_cluster_to_the_whole_room(client, db):
    with room(client) as r:
        advance_to(client, r, "cluster")
        r.sam1.send_json({"type": "cluster.create", "name": "  process \n"})
        for ws in everyone(r):
            assert ws.receive_json() == {"type": "cluster", "cluster": {"id": 1, "name": "process"}}
        assert_nothing_pending(r.stranger)
        assert stored(db)["clusters"] == [(1, "process")]


def test_anyone_renames_any_cluster_and_names_need_not_be_unique(client, db):
    with room(client) as r:
        advance_to(client, r, "cluster")
        assert create_cluster(r.sam1, "process")["cluster"] == {"id": 1, "name": "process"}
        for ws in everyone(r):
            drain(ws)
        assert create_cluster(r.ann1, "process")["cluster"] == {"id": 2, "name": "process"}
        for ws in everyone(r):
            drain(ws)
        for name in (" people ", "people"):  # the second changes nothing and still echoes
            r.ann1.send_json({"type": "cluster.rename", "id": 1, "name": name})
            for ws in everyone(r):
                assert ws.receive_json() == {"type": "cluster", "cluster": {"id": 1, "name": "people"}}
        assert_nothing_pending(r.stranger)
        assert stored(db)["clusters"] == [(1, "people"), (2, "process")]


def test_anyone_moves_any_card_and_mine_follows_the_author_not_the_mover(client, db):
    with room(client) as r:
        card = create_card(r.sam1, "start", "pairing")["card"]
        advance_to(client, r, "cluster")
        cluster = create_cluster(r.ann1)["cluster"]
        for ws in everyone(r):
            drain(ws)
        for cluster_id in (cluster["id"], None, None):  # in, out, and a no-op that still echoes
            r.ann1.send_json({"type": "card.move", "id": card["id"], "cluster_id": cluster_id})
            for ws, mine in ((r.sam1, True), (r.sam2, True), (r.ann1, False), (r.observer, False)):
                event = ws.receive_json()
                assert event == {
                    "type": "card", "card": card | {"cluster_id": cluster_id, "mine": mine}
                }, (cluster_id, mine)
                assert set(event["card"]) == CARD_KEYS
            assert_nothing_pending(r.stranger)
            assert stored(db)["cards"] == [(card["id"], cluster_id)]


def test_a_moved_anonymous_card_still_names_no_author_for_anyone(client):
    with room(client) as r:
        card = create_card(r.sam1, "stop", "meetings", anonymous=True)["card"]
        advance_to(client, r, "cluster")
        cluster = create_cluster(r.ann1)["cluster"]
        for ws in everyone(r):
            drain(ws)
        r.ann1.send_json({"type": "card.move", "id": card["id"], "cluster_id": cluster["id"]})
        for ws, mine in ((r.sam1, True), (r.sam2, True), (r.ann1, False), (r.observer, False)):
            moved = ws.receive_json()["card"]
            assert (moved["author"], moved["anonymous"], moved["mine"]) == (None, True, mine)
            assert moved["cluster_id"] == cluster["id"]


def test_last_move_wins_and_every_socket_hears_both(client, db):
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        advance_to(client, r, "cluster")
        first, second = create_cluster(r.sam1)["cluster"], create_cluster(r.sam1)["cluster"]
        for ws in everyone(r):
            drain(ws)
        r.sam1.send_json({"type": "card.move", "id": card["id"], "cluster_id": first["id"]})
        r.ann1.send_json({"type": "card.move", "id": card["id"], "cluster_id": second["id"]})
        last = set()
        for ws in everyone(r):
            events = [ws.receive_json() for _ in range(2)]
            assert [e["type"] for e in events] == ["card", "card"]  # neither move errs
            assert {e["card"]["cluster_id"] for e in events} == {first["id"], second["id"]}
            last.add(events[-1]["card"]["cluster_id"])
            assert_nothing_pending(ws)
        assert_nothing_pending(r.stranger)
        (committed,) = stored(db)["cards"]
        assert last == {committed[1]}  # the row is the truth, and every socket heard it last


def test_move_and_rename_reject_rows_of_another_session_missing_rows_and_a_forgotten_field(client, db):
    other = create(client)
    with connect(client, other["code"], join(client, other["code"], "Zed")) as zed:
        zed.receive_json()
        their_card = create_card(zed)["card"]
        for _ in range(2):
            advance(client, other)
            drain(zed)
        their_cluster = create_cluster(zed)["cluster"]
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        advance_to(client, r, "cluster")
        cluster = create_cluster(r.sam1)["cluster"]
        for ws in everyone(r):
            drain(ws)
        assert (their_card["id"], their_cluster["id"], card["id"], cluster["id"]) == (1, 1, 2, 2)
        before = stored(db)
        for op in (
            {"type": "card.move", "id": card["id"], "cluster_id": their_cluster["id"]},
            {"type": "card.move", "id": card["id"], "cluster_id": 999},
            {"type": "card.move", "id": card["id"]},  # null leaves a cluster; absent is a mistake
            {"type": "card.move", "id": their_card["id"], "cluster_id": cluster["id"]},
            {"type": "card.move", "id": 999, "cluster_id": cluster["id"]},
            {"type": "cluster.rename", "id": their_cluster["id"], "name": "x"},
            {"type": "cluster.rename", "id": 999, "name": "x"},
        ):
            r.sam1.send_json(op)
            assert r.sam1.receive_json()["type"] == "error", op
        for ws in (r.sam2, r.ann1, r.observer, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == before


def test_ids_and_cluster_ids_outside_a_rowid_are_rejected_not_crashed(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        card = create_card(ws)["card"]
        for _ in range(2):
            advance(client, session)
            drain(ws)
        cluster = create_cluster(ws)["cluster"]
        assert (card["id"], cluster["id"]) == (1, 1)  # so `true`, which SQLite binds as 1, must be refused
        for bad in (True, "1", 1.0, 0, -1, 2**63, 2**70, -(2**70), [1]):
            for op in (
                {"type": "card.move", "id": bad, "cluster_id": cluster["id"]},
                {"type": "card.move", "id": card["id"], "cluster_id": bad},
                {"type": "cluster.rename", "id": bad, "name": "x"},
            ):
                ws.send_json(op)
                assert ws.receive_json()["type"] == "error", op
        for op in (  # null is a legal cluster_id, never a legal id
            {"type": "card.move", "id": None, "cluster_id": cluster["id"]},
            {"type": "cluster.rename", "id": None, "name": "x"},
        ):
            ws.send_json(op)
            assert ws.receive_json()["type"] == "error", op
        assert_nothing_pending(ws)  # the socket is still open and answering
    assert stored(db) == {"clusters": [(1, "process")], "cards": [(1, None)]}


def test_name_is_stripped_and_limited_to_100_characters_on_create_and_rename(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        for _ in range(2):
            advance(client, session)
            drain(ws)
        cluster = create_cluster(ws, "  " + "x" * 100 + "\n")["cluster"]
        assert cluster["name"] == "x" * 100
        for name in ("x" * 101, " " + "x" * 101 + " ", "", "   ", None, 7, ["x"]):
            assert create_cluster(ws, name)["type"] == "error", name
            ws.send_json({"type": "cluster.rename", "id": cluster["id"], "name": name})
            assert ws.receive_json()["type"] == "error", name
        ws.send_json({"type": "cluster.create"})  # missing
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "cluster.rename", "id": cluster["id"]})  # missing
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "cluster.rename", "id": cluster["id"], "name": " " + "y" * 100})
        assert ws.receive_json()["cluster"]["name"] == "y" * 100
    assert stored(db)["clusters"] == [(cluster["id"], "y" * 100)]


def test_observers_cannot_create_rename_or_move(client, db):
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        advance_to(client, r, "cluster")
        cluster = create_cluster(r.sam1)["cluster"]
        for ws in everyone(r):
            drain(ws)
        before = stored(db)
        for op in (
            {"type": "cluster.create", "name": "x"},
            {"type": "cluster.rename", "id": cluster["id"], "name": "x"},
            {"type": "card.move", "id": card["id"], "cluster_id": cluster["id"]},
        ):
            r.observer.send_json(op)
            assert r.observer.receive_json()["type"] == "error", op
        for ws in (r.sam1, r.sam2, r.ann1, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == before


def test_clustering_is_open_from_cluster_to_discuss_and_closed_in_write_reveal_and_done(client, db):
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        drain(r.sam2)
        for phase in PHASES:
            if phase != "write":
                advance_to(client, r, phase)
            before = stored(db)
            ops = (
                {"type": "cluster.create", "name": phase},
                {"type": "cluster.rename", "id": 1, "name": phase},
                {"type": "card.move", "id": card["id"], "cluster_id": 1},
            )
            for op in ops:
                r.sam1.send_json(op)
                reply = r.sam1.receive_json()
                if phase in OPEN:
                    assert reply["type"] in ("cluster", "card"), (phase, op)
                    for ws in (r.sam2, r.ann1, r.observer):  # the whole room hears it
                        assert ws.receive_json()["type"] == reply["type"], (phase, op)
                else:
                    assert reply["type"] == "error", (phase, op)
            if phase in OPEN:
                assert stored(db) != before
            else:
                assert stored(db) == before
            for ws in (r.sam2, r.ann1, r.observer, r.stranger):
                assert_nothing_pending(ws)
        assert stored(db)["clusters"] == [(1, "discuss"), (2, "vote"), (3, "discuss")]


def test_cluster_is_a_server_only_type(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        ws.send_json({"type": "cluster", "cluster": {"name": "x"}})
        assert ws.receive_json()["detail"] == "unknown type: cluster"
    assert stored(db)["clusters"] == []


def test_snapshot_lists_two_key_clusters_by_id_and_cards_with_their_cluster_id(client):
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        advance_to(client, r, "cluster")
        second = create_cluster(r.sam1, "second")["cluster"]
        for ws in everyone(r):
            drain(ws)
        first = create_cluster(r.ann1, "first")["cluster"]
        r.ann1.send_json({"type": "card.move", "id": card["id"], "cluster_id": first["id"]})
        for ws in everyone(r):
            drain(ws)
        assert (second["id"], first["id"]) == (1, 2)
        for token in (r.sam, r.ann, None):
            with connect(client, r.code, token) as ws:
                snap = ws.receive_json()
            assert snap["clusters"] == [{"id": 1, "name": "second"}, {"id": 2, "name": "first"}]
            assert [c["cluster_id"] for c in snap["cards"]] == [first["id"]]
            assert set(snap["cards"][0]) == CARD_KEYS
