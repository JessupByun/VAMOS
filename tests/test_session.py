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


def test_tonights_read_counts_aggression_and_keeps_showdowns():
    from vamos.log.hands import build_hands
    from vamos.session import reads

    A, B = '"Al @ aaaaaaaaaa"', '"Bo @ bbbbbbbbbb"'
    lines = [
        f"-- starting hand #1 (id: r1)  No Limit Texas Hold'em (dealer: {A}) --",
        f"Player stacks: #1 {A} (10.00) | #2 {B} (10.00)",
        f"{A} posts a small blind of 0.10",
        f"{B} posts a big blind of 0.20",
        f"{A} raises to 0.60",
        f"{B} raises to 1.80",
        f"{A} calls 1.80",
        "Flop:  [K♠, 7♦, 2♣]",
        f"{B} bets 1.20",
        f"{A} calls 1.20",
        "Turn: K♠, 7♦, 2♣ [9♥]",
        f"{B} checks",
        f"{A} bets 3.00",
        f"{B} calls 3.00",
        "River: K♠, 7♦, 2♣, 9♥ [3c]".replace("3c", "3♣"),
        f"{B} checks",
        f"{A} checks",
        f"{B} shows a K♦, Q♦.",
        f"{A} shows a 7♠, 7♣.",
        f"{A} collected 12.00 from pot",
        "-- ending hand #1 --",
    ]
    r = reads(build_hands((100 + i, line) for i, line in enumerate(lines)))
    al, bo = r["aaaaaaaaaa"], r["bbbbbbbbbb"]
    assert (bo.pfr, bo.three_bets, bo.three_bet_chances, al.three_bets) == (1, 1, 1, 0)
    # Bo was the last preflop raiser and bet the flop: one c-bet in one chance.
    assert (bo.cbets, bo.cbet_chances, al.cbet_chances) == (1, 1, 0)
    assert (al.bets_raises, al.calls, al.faced_bets, al.folds_to_bets) == (1, 1, 1, 0)
    assert (bo.bets_raises, bo.calls, bo.faced_bets) == (1, 1, 1)
    [shown] = bo.showdowns
    assert (shown.cards, shown.result) == (("Kd", "Qd"), "lost")
    assert shown.line == "preflop raise · flop bet · turn check, call · river check"
    assert al.showdowns[0].result == "won" and al.summary()["cbet"] == "0/0"
    assert al.summary()["three_bet"] == "0/0" and bo.summary()["three_bet"] == "1/1"


A, B, C = '"Al @ aaaaaaaaaa"', '"Bo @ bbbbbbbbbb"', '"Cy @ cccccccccc"'


def read_of(*hands: list[str]):
    from vamos.log.hands import build_hands
    from vamos.session import reads

    lines = [line for hand in hands for line in hand]
    return reads(build_hands((100 + i, line) for i, line in enumerate(lines)))


def three_handed(number: int, *body: str, end: bool = True) -> list[str]:
    return [
        f"-- starting hand #{number} (id: h{number})  No Limit Texas Hold'em (dealer: {A}) --",
        f"Player stacks: #1 {A} (10.00) | #2 {B} (10.00) | #3 {C} (10.00)",
        f"{B} posts a small blind of 0.10",
        f"{C} posts a big blind of 0.20",
        *body,
        *([f"-- ending hand #{number} --"] if end else []),
    ]


def test_a_donk_bet_or_an_all_in_before_the_flop_is_no_c_bet_chance():
    donked = three_handed(
        1,
        f"{A} raises to 0.60",
        f"{B} calls 0.60",
        f"{C} calls 0.60",
        "Flop:  [K♠, 7♦, 2♣]",
        f"{B} bets 1.00",
        f"{C} folds",
        f"{A} calls 1.00",
        "Turn: K♠, 7♦, 2♣ [9♥]",
        f"{B} checks",
        f"{A} checks",
        "River: K♠, 7♦, 2♣, 9♥ [3♣]",
        f"{B} checks",
        f"{A} checks",
        f"{B} shows a K♦, Q♦.",
        f"{A} shows a 7♠, 7♣.",
        f"{A} collected 4.00 from pot",
    )
    jammed = three_handed(
        2,
        f"{A} raises to 0.60",
        f"{B} raises to 10.00 and go all in",
        f"{C} folds",
        f"{A} calls 10.00 and go all in",
        f"{B} shows a A♦, A♣.",
        f"{A} shows a K♦, K♣.",
        "Flop:  [2♠, 7♦, 9♣]",
        "Turn: 2♠, 7♦, 9♣ [4♥]",
        "River: 2♠, 7♦, 9♣, 4♥ [3♣]",
        f"{B} collected 20.20 from pot",
    )
    checked_to = three_handed(
        3,
        f"{A} raises to 0.60",
        f"{B} folds",
        f"{C} calls 0.60",
        "Flop:  [K♠, 7♦, 2♣]",
        f"{C} checks",
        f"{A} checks",
        "Turn: K♠, 7♦, 2♣ [9♥]",
        f"{C} checks",
        f"{A} bets 1.00",
        f"{C} folds",
        f"{A} collected 1.30 from pot",
    )
    r = read_of(donked, jammed, checked_to)
    # Hand 1: Bo led into Al, so Al never had a c-bet to make. Hand 2: Bo's all-in left no
    # flop action. Hand 3: checked to Al, who checked: one chance, no c-bet.
    assert (r["aaaaaaaaaa"].cbets, r["aaaaaaaaaa"].cbet_chances) == (0, 1)
    assert (r["bbbbbbbbbb"].cbets, r["bbbbbbbbbb"].cbet_chances) == (0, 0)


def test_only_the_first_re_raise_is_a_three_bet():
    four_bet = three_handed(
        1,
        f"{A} raises to 0.60",
        f"{B} raises to 1.80",
        f"{C} raises to 5.00",
        f"{A} folds",
        f"{B} folds",
        f"{C} collected 3.00 from pot",
    )
    r = read_of(four_bet)
    al, bo, cy = r["aaaaaaaaaa"], r["bbbbbbbbbb"], r["cccccccccc"]
    # Cy cold 4-bet: not a 3-bet, and Cy faced a 3-bet, not a single raise.
    assert (bo.three_bets, bo.three_bet_chances) == (1, 1)
    assert (cy.three_bets, cy.three_bet_chances) == (0, 0)
    # The opener never faces their own raise.
    assert (al.three_bets, al.three_bet_chances) == (0, 0)


def test_shown_hands_say_how_they_ended():
    folded_and_shown = three_handed(
        1,
        f"{A} raises to 0.60",
        f"{B} folds",
        f"{C} calls 0.60",
        "Flop:  [K♠, 7♦, 2♣]",
        f"{C} checks",
        f"{A} bets 1.00",
        f"{C} folds",
        f"Uncalled bet of 1.00 returned to {A}",
        f"{A} collected 1.30 from pot",
        f"{C} shows a Q♠, Q♣.",
        f"{A} shows a 5♠, 4♣.",
    )
    chopped = three_handed(
        2,
        f"{A} calls 0.20",
        f"{B} folds",
        f"{C} checks",
        "Flop:  [A♠, K♦, Q♣]",
        f"{C} checks",
        f"{A} checks",
        "Turn: A♠, K♦, Q♣ [J♥]",
        f"{C} checks",
        f"{A} checks",
        "River: A♠, K♦, Q♣, J♥ [T♣]",
        f"{C} checks",
        f"{A} checks",
        f"{C} shows a 2♦, 3♦.",
        f"{A} shows a 4♠, 5♠.",
        f"{C} collected 0.25 from pot",
        f"{A} collected 0.25 from pot",
    )
    r = read_of(folded_and_shown, chopped)
    cy, al = r["cccccccccc"].showdowns, r["aaaaaaaaaa"].showdowns
    assert [s.result for s in cy] == ["folded", "split"]
    assert [s.result for s in al] == ["uncontested", "split"]
    # Claude gets the counts only; shown hands are for drafts.
    assert "hands_shown" not in r["cccccccccc"].summary()


def test_cards_shown_before_an_all_in_runs_out_wait_for_the_result():
    running = three_handed(
        1,
        f"{A} raises to 10.00 and go all in",
        f"{B} folds",
        f"{C} calls 10.00 and go all in",
        f"{A} shows a A♦, A♣.",
        f"{C} shows a K♦, K♣.",
        "Flop:  [2♠, 7♦, 9♣]",
        end=False,
    )
    assert read_of(running)["aaaaaaaaaa"].showdowns == ()


def test_run_it_twice_keeps_both_boards_and_a_split_result():
    rit = three_handed(
        1,
        f"{A} raises to 10.00 and go all in",
        f"{B} folds",
        f"{C} calls 10.00 and go all in",
        f"{A} shows a A♦, A♣.",
        f"{C} shows a K♦, K♣.",
        "Remaining players decide whether to run it twice.",
        f"{A} chooses to  run it twice.",
        f"{C} chooses to  run it twice.",
        "All players in hand choose to run it twice.",
        "Flop:  [2♠, 7♦, 9♣]",
        "Turn: 2♠, 7♦, 9♣ [4♥]",
        "River: 2♠, 7♦, 9♣, 4♥ [3♣]",
        "Flop (second run):  [K♠, 8♦, 5♣]",
        "Turn (second run): K♠, 8♦, 5♣ [6♥]",
        "River (second run): K♠, 8♦, 5♣, 6♥ [J♣]",
        f"{A} collected 10.05 from pot",
        f"{C} collected 10.05 from pot",
    )
    [shown] = read_of(rit)["aaaaaaaaaa"].showdowns
    assert shown.board == ("2s", "7d", "9c", "4h", "3c")
    assert shown.second_board == ("Ks", "8d", "5c", "6h", "Jc")
    assert shown.result == "split"


def test_a_show_from_before_an_id_change_still_counts_as_won():
    hand_1 = three_handed(
        1,
        f"{A} raises to 0.60",
        f"{B} folds",
        f"{C} calls 0.60",
        "Flop:  [K♠, 7♦, 2♣]",
        f"{C} checks",
        f"{A} checks",
        "Turn: K♠, 7♦, 2♣ [9♥]",
        f"{C} checks",
        f"{A} checks",
        "River: K♠, 7♦, 2♣, 9♥ [3♣]",
        f"{C} checks",
        f"{A} checks",
        f"{C} shows a 7♠, 7♣.",
        f"{A} shows a K♦, Q♦.",
        f"{A} collected 1.30 from pot",
    )
    hand_2 = three_handed(
        2,
        'The player "Al @ newalid001" changed the ID from aaaaaaaaaa to newalid001 because '
        "authenticated login.",
        f"{A.replace('aaaaaaaaaa', 'newalid001')} folds",
        f"{B} folds",
        f"{C} collected 0.30 from pot",
    )
    r = read_of(hand_1, hand_2)
    assert "aaaaaaaaaa" not in r
    assert [s.result for s in r["newalid001"].showdowns] == ["won"]


def test_a_settled_read_leaves_out_the_hand_being_played():
    # Hand 2 is still going: Bob's limp is live history for the panel, not yet for Claude.
    s = GameSession("g")
    s.add_page(body(*HAND_1, *HAND_2))
    from vamos.session import reads

    assert (reads(s.log)["bbbbbbbbbb"].hands, reads(s.log)["bbbbbbbbbb"].vpip) == (2, 1)
    settled = reads(s.log, settled=True)["bbbbbbbbbb"]
    assert (settled.hands, settled.vpip) == (1, 0)
