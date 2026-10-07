import pytest

from vamos.log.grammar import NLH, Player, parse_line

VILLAIN = Player("TestVillain", "FKxeoYccvW")


@pytest.mark.parametrize(
    ("line", "player"),
    [
        ('"all in @ aaaaaaaa01" folds', Player("all in", "aaaaaaaa01")),
        ('"500 @ aaaaaaaa02" folds', Player("500", "aaaaaaaa02")),
        ('"9/2 @ d-4X_F_SSU" folds', Player("9/2", "d-4X_F_SSU")),
        ('"Harry = Fish @ kCN0-Qy2Mz" folds', Player("Harry = Fish", "kCN0-Qy2Mz")),
        ('"spa @ ces @ aaaaaaaa03" folds', Player("spa @ ces", "aaaaaaaa03")),
        ('"DK @ -47FT3Kv-N" folds', Player("DK", "-47FT3Kv-N")),
    ],
)
def test_player_tokens_split_on_the_last_at_and_survive_trap_names(line, player):
    event = parse_line(line)
    assert (event.kind, event.player) == ("fold", player)


def test_hand_start_2026_format():
    e = parse_line(
        '-- starting hand #1 (id: ywhe4rpsfvih)  No Limit Texas Hold\'em (dealer: "TestVillain @ FKxeoYccvW") --'
    )
    assert (e.kind, e.hand_number, e.hand_id, e.variant, e.player) == (
        "hand_start",
        1,
        "ywhe4rpsfvih",
        NLH,
        VILLAIN,
    )


def test_hand_start_dead_button_and_legacy_variants():
    dead = parse_line(
        "-- starting hand #26 (id: ndddmwtmyhzo)  No Limit Texas Hold'em (dead button) --"
    )
    assert (dead.player, dead.variant) == (None, NLH)
    legacy = parse_line(
        '-- starting hand #69  (Pot Limit Omaha Hi) (dealer: "LLATFFEJ @ cp8PZiuFnj") --'
    )
    assert legacy.variant == "Pot Limit Omaha Hi"
    nested = parse_line(
        '-- starting hand #2 (id: abc)  Pot Limit Omaha Hi/Lo (8 or Better) (dealer: "x @ cp8PZiuFnj") --'
    )
    assert nested.variant == "Pot Limit Omaha Hi/Lo (8 or Better)"


def test_player_stacks_keeps_physical_seats_and_names_with_pipes():
    e = parse_line(
        'Player stacks: #1 "TestVillain @ FKxeoYccvW" (1000) | #10 "wooooo @ MBFczOlpuA" (4625.50)'
    )
    assert [(s.number, s.player.name, s.stack) for s in e.seats] == [
        (1, "TestVillain", 100000),
        (10, "wooooo", 462550),
    ]


def test_hero_cards_use_two_character_cards():
    assert parse_line("Your hand is 4♠, 10♠").cards == ("4s", "Ts")


@pytest.mark.parametrize(
    ("line", "kind", "amount", "all_in"),
    [
        ('"Hero @ heroid0001" posts a small blind of 10', "post_sb", 1000, False),
        ('"LingWae @ FQN9hzhzP_" posts a big blind of 1 and go all in ', "post_bb", 100, True),
        ('"Rum Targaryen @ hEwkk2Lyvz" posts a straddle of 1.00', "post_straddle", 100, False),
        ('"fish @ oMsP5L3lJa" posts a missing small blind of 5', "post_missing_sb", 500, False),
        ('"fish @ oMsP5L3lJa" posts a missed big blind of 10', "post_missed_bb", 1000, False),
        ('"JMan @ Z_JcOkRrQ0" posts an ante of 0.10', "post_ante", 10, False),
        ('"Hero @ heroid0001" calls 20', "call", 2000, False),
        ('"Getting even @ a8Gu84mPIA" calls 953 and go all in', "call", 95300, True),
        ('"rk @ PIUrT-JXRC" bets 16.00', "bet", 1600, False),
        ('"hsj @ PEMYRVPxOS" raises to 905 and go all in', "raise", 90500, True),
    ],
)
def test_posts_and_wagers(line, kind, amount, all_in):
    e = parse_line(line)
    assert (e.kind, e.amount, e.all_in) == (kind, amount, all_in)


def test_boards_main_and_second_runs():
    flop = parse_line("Flop:  [10♣, 6♣, 3♥]")
    assert (flop.kind, flop.cards, flop.run) == ("flop", ("Tc", "6c", "3h"), None)
    turn = parse_line("Turn (second run): K♠, A♥, J♥ [6♣]")
    assert (turn.kind, turn.cards, turn.run) == ("turn", ("Ks", "Ah", "Jh", "6c"), "second run")
    river = parse_line("River (second board): 2♥, 9♣, 2♣, 3♥ [6♥]")
    assert (river.kind, river.run) == ("river", "second board")


def test_collect_variants():
    plain = parse_line('"Hero @ heroid0001" collected 20 from pot')
    assert (plain.kind, plain.amount, plain.description, plain.run) == ("collect", 2000, None, None)
    shown = parse_line(
        "\"wooooo @ MBFczOlpuA\" collected 905 from pot with Two Pair, A's & K's on the second run  "
        "(combination: J♥, K♠, K♣, A♣, A♥)"
    )
    assert (shown.description, shown.run, shown.cards[0]) == (
        "Two Pair, A's & K's",
        "second run",
        "Jh",
    )
    bounty = parse_line('"9/2 @ d-4X_F_SSU" collected 90 from the 7-2 bounty')
    assert (bounty.kind, bounty.amount, bounty.detail) == ("bounty_collected", 9000, "7-2")


def test_shows_uncalled_and_undealt():
    assert parse_line('"gurt @ gpP9uUffpu" shows a A♥.').cards == ("Ah",)
    uncalled = parse_line('Uncalled bet of 10 returned to "Hero @ heroid0001"')
    assert (uncalled.kind, uncalled.amount, uncalled.player.id) == ("uncalled", 1000, "heroid0001")
    assert parse_line("Undealt cards:  [A♥, 4♥, 4♦, K♦, 9♣]").cards == (
        "Ah",
        "4h",
        "4d",
        "Kd",
        "9c",
    )
    assert parse_line("Undealt cards: A♦, 10♠, 9♥, K♦ [2♣]").cards == ("2c",)


def test_run_it_twice_spacing_is_part_of_the_format():
    assert parse_line('"w @ MBFczOlpuA" chooses to  run it twice.').detail == "yes"
    assert parse_line('"w @ MBFczOlpuA" chooses to not run it twice.').detail == "no"
    assert parse_line("All players in hand choose to run it twice.").detail == "yes"
    assert parse_line("Some players choose to not run it twice.").detail == "no"


def test_table_rows():
    approved = parse_line(
        'The admin approved the player "Hero @ heroid0001" participation with a stack of 1000.'
    )
    assert (approved.kind, approved.amount) == ("seat_approved", 100000)
    stand = parse_line('The player "bring it back @ ILQFEWJ0SW" stand up with the stack of 1701.')
    assert stand.kind == "stand_up"
    stakes = parse_line("The game's small blind was changed from 0.10 to 0.05.")
    assert (stakes.kind, stakes.detail, stakes.old_amount, stakes.amount) == (
        "stakes",
        "small blind",
        10,
        5,
    )
    rebuy = parse_line('The player "Harry @ Q4OMcL0rqd" rebought. New stack 1000.')
    assert (rebuy.kind, rebuy.amount) == ("rebought", 100000)


@pytest.mark.parametrize(
    ("change", "detail", "amount"),
    [
        ("adding 500", "adding", 50000),
        ("removing 30.00", "removing", 3000),
        # PokerNow's own spelling; the amount is the new stack, not a change.
        ("reseting to 50.00", "reset", 5000),
    ],
)
def test_queued_admin_stack_changes(change, detail, amount):
    e = parse_line(
        'WARNING: the admin queued the stack change for the player "Hero @ heroid0001" '
        f"{change} chips in the next hand."
    )
    assert (e.kind, e.player, e.detail, e.amount) == (
        "stack_change_queued",
        Player("Hero", "heroid0001"),
        detail,
        amount,
    )


def test_multiline_config_entry():
    e = parse_line(
        "Game Config Changes\n* Blind Schedule: L1: 10/20 » L1: 5/10\n* Double Board: off » always"
    )
    assert e.settings == (
        ("Blind Schedule", "L1: 10/20", "L1: 5/10"),
        ("Double Board", "off", "always"),
    )


def test_id_change_takes_both_ids_from_the_clause():
    e = parse_line(
        'The player "Henry @ FQN9hzhzP_" changed the ID from wgnNFHC2ZI to FQN9hzhzP_ '
        "because authenticated login."
    )
    assert (e.other.id, e.player.id) == ("wgnNFHC2ZI", "FQN9hzhzP_")


@pytest.mark.parametrize(
    "line",
    [
        '"bobo @ Vf1v-slbuU" wins 7400 with Pair, K\'s (hand: Q♠, K♦)',  # 2020 verb
        "flop: [K♠, 2♦, 7♣]",  # 2020 lowercase street
        'Player stacks: #1 "broken @ aaaaaaaa01" (abc)',
        "something new from PokerNow",
    ],
)
def test_unrecognized_lines_are_surfaced_not_guessed(line):
    e = parse_line(line)
    assert (e.kind, e.detail) == ("unknown", line)


def test_player_stacks_survive_pipes_and_hashes_in_names():
    e = parse_line(
        'Player stacks: #1 "DK | 2 @ aaaaaaaa01" (1000) | #3 "a" | #3 "b @ aaaaaaaa02" (5) | #4 "#4 @ aaaaaaaa03" (7)'
    )
    assert [(s.number, s.player.name) for s in e.seats] == [
        (1, "DK | 2"),
        (3, 'a" | #3 "b'),
        (4, "#4"),
    ]
