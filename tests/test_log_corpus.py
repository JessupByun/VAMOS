"""
Regression over thousands of real PokerNow hands (fetch with scripts/fetch_corpus.py).
Run with `uv run pytest -m corpus`; skipped when the corpus is absent.
"""

import csv
from pathlib import Path

import pytest

from vamos.engine.state import VOLUNTARY, next_to_act, seat_order
from vamos.log.hands import build_hands

CORPUS = Path(__file__).parent / "corpus"
FILES = sorted(CORPUS.glob("*/*/*.csv"))

pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(not FILES, reason="corpus not fetched (scripts/fetch_corpus.py)"),
]


def load(path: Path):
    with path.open(encoding="utf-8", newline="") as f:
        return build_hands((int(r["order"]), r["entry"]) for r in csv.DictReader(f))


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_every_row_parses_and_every_finished_hand_balances(path):
    log = load(path)
    assert log.unknown == []
    finished = [h for h in log.hands if h.ended]
    assert finished
    unbalanced = [h.number for h in finished if not h.balanced]
    assert unbalanced == []


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_all_in_players_put_in_exactly_their_stack(path):
    for hand in load(path).hands:
        stacks = {seat.player.id: seat.stack for seat in hand.seats}
        for player in {a.player for a in hand.actions if a.all_in}:
            assert hand.put_in[player] == stacks[player], (hand.number, player)


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_hand_numbers_increase_without_gaps(path):
    numbers = [h.number for h in load(path).hands]
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers)))


@pytest.mark.skipif(not list(CORPUS.glob("jaaaayden/logs/*.csv")), reason="corpus not fetched")
def test_incomplete_all_in_raises_match_the_documented_count():
    # docs/log-grammar.md: 32 incomplete all-in raises in the pnt/logs exports, none of which
    # PokerNow let earlier players re-raise.
    total = sum(
        1
        for path in sorted(CORPUS.glob("jaaaayden/logs/*.csv"))
        for hand in load(path).hands
        for action in hand.actions
        if action.kind == "raise" and action.reopens is False
    )
    assert total == 32


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_positions_put_the_blinds_where_they_were_posted(path):
    from vamos.engine.state import positions

    for hand in load(path).hands:
        if hand.dealer is None or len(hand.seats) < 3:
            continue
        labels = positions(hand)
        for action in hand.actions:
            if action.kind == "post_sb":
                assert labels[action.player] == "SB", hand.number
            if action.kind == "post_bb":
                assert labels[action.player] == "BB", hand.number


def test_next_to_act_is_who_acted_next():
    mismatches = []
    for path in FILES:
        for hand in load(path).hands:
            order = seat_order(hand)
            for i, action in enumerate(hand.actions):
                if action.kind not in VOLUNTARY:
                    continue
                expected = next_to_act(order, hand.actions[:i], action.street, hand.big_blind)
                if expected != action.player:
                    mismatches.append((path.name, hand.number, hand.dealer))
    # A dead button's seat is not in the log. In one of the 65 dead-button hands a player who
    # had just joined sat between the empty button and the small blind and acted first after
    # the flop, which the log alone cannot show.
    assert [m for m in mismatches if m[2] is not None] == []
    assert len(mismatches) <= 1
