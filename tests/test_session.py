from vamos.session import Counts, GameSession

A = '"Alice @ aaaaaaaaaa"'
B = '"Bob @ bbbbbbbbbb"'


def body(*rows: tuple[int, str]) -> dict:
    return {"logs": [{"created_at": str(order), "msg": msg} for order, msg in reversed(rows)]}


HAND_1 = [
    (100, f"-- starting hand #1 (id: h1)  No Limit Texas Hold'em (dealer: {A}) --"),
    (101, f"Player stacks: #1 {A} (1000) | #2 {B} (1000)"),
    (102, f"{A} posts a small blind of 10"),
    (103, f"{B} posts a big blind of 20"),
    (104, f"{A} raises to 60"),
    (105, f"{B} folds"),
    (106, f"Uncalled bet of 40 returned to {A}"),
    (107, f"{A} collected 40 from pot"),
    (108, "-- ending hand #1 --"),
]
HAND_2 = [
    (200, f"-- starting hand #2 (id: h2)  No Limit Texas Hold'em (dealer: {B}) --"),
    (201, f"Player stacks: #1 {A} (1020) | #2 {B} (980)"),
    (202, f"{B} posts a small blind of 10"),
    (203, f"{A} posts a big blind of 20"),
    (204, f"{B} calls 20"),
    (205, f"{A} checks"),
]


def test_pages_merge_and_report_whether_anything_was_new():
    session = GameSession("g")
    assert session.add_page(body(*HAND_1))
    assert not session.add_page(body(*HAND_1))
    assert session.add_page(body(*HAND_2[:3]))
    assert [h.number for h in session.log.hands] == [1, 2]


def test_vpip_counts_voluntary_preflop_money_only():
    session = GameSession("g")
    session.add_page(body(*HAND_1, *HAND_2))
    # Alice raised in hand 1 and only checked her big blind in hand 2; Bob folded his
    # big blind in hand 1 and completed the small blind in hand 2. Only Alice raised.
    assert session.counts() == {"aaaaaaaaaa": Counts(2, 1, 1), "bbbbbbbbbb": Counts(2, 1, 0)}


def test_a_hand_joined_midway_without_a_roster_is_not_counted():
    session = GameSession("g")
    session.add_page(body(*HAND_2[3:]))
    assert session.counts() == {}


def test_malformed_rows_are_ignored():
    session = GameSession("g")
    assert not session.add_page({"logs": [{"msg": "no order"}, {"created_at": "x", "msg": "bad"}]})
    assert not session.add_page({"unexpected": True})
    assert not session.add_page(None)


def test_a_fresh_page_that_does_not_reach_known_rows_marks_a_gap():
    session = GameSession("g")
    session.add_page(body(*HAND_1[:6]))
    # A fresh fetch after a long outage: only the newest rows, nothing overlapping.
    session.add_page(body(*HAND_2[1:]), fresh=True)
    first, *rest = session.log.hands
    assert first.number == 1 and not first.ended
    assert rest == []


def test_a_fresh_page_overlapping_known_rows_is_contiguous():
    session = GameSession("g")
    session.add_page(body(*HAND_1[:6]))
    session.add_page(body(*HAND_1[4:], *HAND_2), fresh=True)
    assert [h.number for h in session.log.hands] == [1, 2]
    assert session.log.hands[0].ended


def test_counts_follow_an_id_change():
    session = GameSession("g")
    session.add_page(
        body(
            *HAND_1,
            (
                150,
                'The player "Bob @ newbobid01" changed the ID from bbbbbbbbbb to newbobid01 because authenticated login.',
            ),
            *[
                (order, text.replace("Bob @ bbbbbbbbbb", "Bob @ newbobid01"))
                for order, text in HAND_2
            ],
        )
    )
    assert session.counts() == {"aaaaaaaaaa": Counts(2, 1, 1), "newbobid01": Counts(2, 1, 0)}
