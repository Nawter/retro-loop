from contextlib import contextmanager

from app.sessions import PHASES
from test_cards import advance, assert_nothing_pending, connect, create, create_card, join, room
from test_clusters import advance_to, create_cluster, drain, everyone


def vote(card_id, count, mine, left):
    """The five-key event one socket should receive."""
    return {"type": "vote", "card_id": card_id, "count": count, "mine": mine, "left": left}


def cast(ws, card_id):
    """Send a vote.cast and return the reply: the echoed event or the error."""
    ws.send_json({"type": "vote.cast", "id": card_id})
    return ws.receive_json()


def remove(ws, card_id):
    ws.send_json({"type": "vote.remove", "id": card_id})
    return ws.receive_json()


def stored(db):
    return [
        tuple(row)
        for row in db.execute("SELECT id, participant_id, card_id FROM votes ORDER BY id")
    ]


@contextmanager
def voting(client):
    """The room in `vote`: Sam (participant 1) wrote card 1, Ann (2) wrote card 2, anonymous and
    clustered by Sam, so every vote below lands on a card of one of the kinds #6 names."""
    with room(client) as r:
        r.sam_card = create_card(r.sam1)["card"]["id"]
        r.ann_card = create_card(r.ann1, "stop", "meetings", anonymous=True)["card"]["id"]
        advance_to(client, r, "cluster")
        cluster = create_cluster(r.sam1)["cluster"]
        r.sam1.send_json({"type": "card.move", "id": r.ann_card, "cluster_id": cluster["id"]})
        advance_to(client, r, "vote")
        yield r


def test_cast_sends_the_total_to_the_room_and_mine_and_left_to_each_recipient(client, db):
    with voting(client) as r:
        r.sam1.send_json({"type": "vote.cast", "id": r.sam_card, "count": 99})  # extra field: ignored
        assert r.sam1.receive_json() == r.sam2.receive_json() == vote(r.sam_card, 1, 1, 2)
        assert r.ann1.receive_json() == vote(r.sam_card, 1, 0, 3)
        assert r.observer.receive_json() == vote(r.sam_card, 1, 0, 0)
        for ws in (*everyone(r), r.stranger):  # exactly one event each, none next door
            assert_nothing_pending(ws)
        assert stored(db) == [(1, 1, r.sam_card)]


def test_anyone_votes_on_any_card_their_own_anothers_anonymous_or_clustered(client):
    with voting(client) as r:
        assert cast(r.sam1, r.sam_card) == vote(r.sam_card, 1, 1, 2)
        assert cast(r.sam1, r.ann_card) == vote(r.ann_card, 1, 1, 1)
        for ws in everyone(r):
            drain(ws)
        assert cast(r.ann1, r.sam_card) == vote(r.sam_card, 2, 1, 2)
        assert cast(r.ann1, r.ann_card) == vote(r.ann_card, 2, 1, 1)


def test_fourth_vote_is_rejected(client, db):
    with voting(client) as r:
        assert cast(r.sam1, r.sam_card)["left"] == 2
        r.sam2.receive_json()
        assert cast(r.sam2, r.ann_card)["left"] == 1  # the second tab spends the same budget
        r.sam1.receive_json()
        assert cast(r.sam1, r.sam_card)["left"] == 0
        for ws in everyone(r):
            drain(ws)
        before = stored(db)
        assert [row[1] for row in before] == [1, 1, 1]
        for ws, card in ((r.sam1, r.sam_card), (r.sam1, r.ann_card), (r.sam2, r.ann_card)):
            assert cast(ws, card)["type"] == "error", card  # any card, either tab
        for ws in (*everyone(r), r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == before


def test_three_votes_from_one_participant_on_one_card_are_three_rows(client, db):
    with voting(client) as r:
        for n in (1, 2, 3):
            assert cast(r.sam1, r.sam_card) == r.sam2.receive_json() == vote(r.sam_card, n, n, 3 - n)
            assert r.ann1.receive_json() == vote(r.sam_card, n, 0, 3)
            assert r.observer.receive_json() == vote(r.sam_card, n, 0, 0)
        assert_nothing_pending(r.stranger)
        assert stored(db) == [(1, 1, r.sam_card), (2, 1, r.sam_card), (3, 1, r.sam_card)]


def test_budgets_are_per_participant(client, db):
    with voting(client) as r:
        for _ in range(3):
            cast(r.sam1, r.sam_card)
        assert cast(r.sam1, r.ann_card)["type"] == "error"
        for ws in everyone(r):
            drain(ws)
        assert cast(r.ann1, r.sam_card) == vote(r.sam_card, 4, 1, 2)  # Sam's three cost Ann nothing
        assert r.sam1.receive_json() == r.sam2.receive_json() == vote(r.sam_card, 4, 3, 0)
        assert r.observer.receive_json() == vote(r.sam_card, 4, 0, 0)
        assert cast(r.ann1, r.ann_card) == vote(r.ann_card, 1, 1, 1)
        assert cast(r.ann1, r.ann_card) == vote(r.ann_card, 2, 2, 0)
        assert cast(r.ann1, r.sam_card)["type"] == "error"
        assert [row[1] for row in stored(db)] == [1, 1, 1, 2, 2, 2]
        with connect(client, r.code) as ws:
            assert ws.receive_json()["votes"]["counts"] == {str(r.sam_card): 4, str(r.ann_card): 2}


def test_remove_deletes_the_newest_own_vote_and_frees_one(client, db):
    with voting(client) as r:
        for _ in range(3):
            cast(r.sam1, r.sam_card)
        assert cast(r.sam1, r.ann_card)["type"] == "error"
        for ws in everyone(r):
            drain(ws)
        assert remove(r.sam2, r.sam_card) == r.sam1.receive_json() == vote(r.sam_card, 2, 2, 1)
        assert r.ann1.receive_json() == vote(r.sam_card, 2, 0, 3)
        assert r.observer.receive_json() == vote(r.sam_card, 2, 0, 0)
        assert stored(db) == [(1, 1, r.sam_card), (2, 1, r.sam_card)]  # id 3, the newest, is gone
        assert cast(r.sam1, r.ann_card) == vote(r.ann_card, 1, 1, 0)
        for ws in everyone(r):
            drain(ws)
        assert_nothing_pending(r.stranger)
        # three rows for Sam again; SQLite hands the freed rowid 3 to the new one
        assert stored(db) == [(1, 1, r.sam_card), (2, 1, r.sam_card), (3, 1, r.ann_card)]


def test_remove_that_empties_a_card_still_sends_count_zero(client, db):
    with voting(client) as r:
        cast(r.sam1, r.sam_card)
        for ws in everyone(r):
            drain(ws)
        assert remove(r.sam1, r.sam_card) == r.sam2.receive_json() == vote(r.sam_card, 0, 0, 3)
        assert r.ann1.receive_json() == vote(r.sam_card, 0, 0, 3)
        assert r.observer.receive_json() == vote(r.sam_card, 0, 0, 0)
        assert_nothing_pending(r.stranger)
        assert stored(db) == []


def test_remove_with_no_own_vote_on_the_card_is_rejected(client, db):
    with voting(client) as r:
        assert remove(r.sam1, r.sam_card)["type"] == "error"  # never voted for it
        cast(r.ann1, r.ann_card)
        for ws in everyone(r):
            drain(ws)
        assert remove(r.sam1, r.ann_card)["type"] == "error"  # only Ann voted for it
        cast(r.sam1, r.sam_card)
        remove(r.sam1, r.sam_card)
        for ws in everyone(r):
            drain(ws)
        assert remove(r.sam2, r.sam_card)["type"] == "error"  # already taken back
        for ws in (*everyone(r), r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == [(1, 2, r.ann_card)]  # Ann's stays


def test_observers_cannot_cast_or_remove(client, db):
    with voting(client) as r:
        cast(r.sam1, r.sam_card)
        for ws in everyone(r):
            drain(ws)
        before = stored(db)
        for op in ({"type": "vote.cast", "id": r.sam_card}, {"type": "vote.remove", "id": r.sam_card}):
            r.observer.send_json(op)
            assert r.observer.receive_json()["type"] == "error", op
        for ws in (r.sam1, r.sam2, r.ann1, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == before


def test_voting_is_open_in_vote_only(client, db):
    with room(client) as r:
        card = create_card(r.sam1)["card"]["id"]
        drain(r.sam2)
        for phase in PHASES:
            if phase != "write":
                advance_to(client, r, phase)
            before = stored(db)
            for op in ({"type": "vote.cast", "id": card}, {"type": "vote.remove", "id": card}):
                r.sam1.send_json(op)
                reply = r.sam1.receive_json()
                if phase == "vote":
                    assert reply["type"] == "vote", (phase, op)
                    for ws in (r.sam2, r.ann1, r.observer):  # the whole room hears it
                        assert ws.receive_json()["type"] == "vote", (phase, op)
                else:
                    assert reply["type"] == "error", (phase, op)
            for ws in (r.sam2, r.ann1, r.observer, r.stranger):
                assert_nothing_pending(ws)
            assert stored(db) == before  # cast and taken back again; or never touched


def test_cast_and_remove_reject_cards_of_another_session_missing_cards_and_bad_ids(client, db):
    other = create(client)
    with connect(client, other["code"], join(client, other["code"], "Zed")) as zed:
        zed.receive_json()
        theirs = create_card(zed)["card"]["id"]
    with voting(client) as r:
        cast(r.sam1, r.sam_card)  # so a remove has something to lose
        for ws in everyone(r):
            drain(ws)
        assert (theirs, r.sam_card) == (1, 2)  # so `true`, which SQLite binds as 1, must be refused
        before = stored(db)
        for bad in (theirs, 999, True, "1", 1.0, 0, -1, 2**63, 2**70, -(2**70), None, [2]):
            for op in ({"type": "vote.cast", "id": bad}, {"type": "vote.remove", "id": bad}):
                r.sam1.send_json(op)
                assert r.sam1.receive_json()["type"] == "error", op
        for op in ({"type": "vote.cast"}, {"type": "vote.remove"}):  # missing
            r.sam1.send_json(op)
            assert r.sam1.receive_json()["type"] == "error", op
        for ws in (*everyone(r), r.stranger):  # still open and answering, nobody else heard a thing
            assert_nothing_pending(ws)
        assert stored(db) == before


def test_snapshot_shows_every_total_but_only_the_recipients_own_votes_and_budget(client):
    with voting(client) as r:
        for card in (r.sam_card, r.sam_card, r.ann_card):
            cast(r.sam1, card)
        for ws in everyone(r):
            drain(ws)
        counts = {str(r.sam_card): 2, str(r.ann_card): 1}
        for phase in ("vote", "discuss", "done"):  # the tally outlives the voting
            for token, mine, left in ((r.sam, counts, 0), (r.ann, {}, 3), (None, {}, 0)):
                with connect(client, r.code, token) as ws:
                    snap = ws.receive_json()
                assert snap["phase"] == phase
                assert snap["votes"] == {"counts": counts, "mine": mine, "left": left}, (phase, token)
            advance(client, r.session)


def test_budget_is_counted_from_the_table_not_from_memory(client, db):
    with voting(client) as r:
        for _ in range(3):
            db.execute(
                "INSERT INTO votes (session_id, participant_id, card_id) "
                "SELECT session_id, id, ? FROM participants WHERE token = ?",
                (r.sam_card, r.sam),
            )
        db.commit()
        assert cast(r.sam1, r.ann_card)["type"] == "error"  # the fourth
        assert cast(r.sam2, r.sam_card)["type"] == "error"
        for ws in (*everyone(r), r.stranger):
            assert_nothing_pending(ws)
        assert len(stored(db)) == 3
        with connect(client, r.code, r.sam) as ws:
            assert ws.receive_json()["votes"] == {
                "counts": {str(r.sam_card): 3}, "mine": {str(r.sam_card): 3}, "left": 0,
            }
        assert cast(r.ann1, r.sam_card) == vote(r.sam_card, 4, 1, 2)  # Ann's budget is her own
        assert r.sam1.receive_json() == r.sam2.receive_json() == vote(r.sam_card, 4, 3, 0)


def test_vote_is_a_server_only_type(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        ws.send_json({"type": "vote", "card_id": 1, "count": 3, "mine": 3, "left": 0})
        assert ws.receive_json()["detail"] == "unknown type: vote"
    assert stored(db) == []
