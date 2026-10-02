"""
Regression over thousands of real PokerNow hands (fetch with scripts/fetch_corpus.py).
Run with `uv run pytest -m corpus`; skipped when the corpus is absent.
"""

import csv
from pathlib import Path

import pytest

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
