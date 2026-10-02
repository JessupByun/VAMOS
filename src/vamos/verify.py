"""
`vamos verify`: compare the hands VAMOS built live (from a test-table recording) with the
hands in PokerNow's own exported log for the same game, field by field.

Both come from the same viewer, so they must agree exactly on every hand VAMOS saw from its
start. Hands that began before VAMOS attached are reported, not counted as failures.
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from vamos.log.hands import Hand, build_hands
from vamos.session import GameSession


def _fields(hand: Hand) -> dict[str, object]:
    return {
        "seats": [(s.number, s.player.id, s.stack) for s in hand.seats],
        "hero_cards": hand.hero_cards,
        "actions": [
            (a.street, a.player, a.kind, a.to, a.added, a.all_in, a.reopens) for a in hand.actions
        ],
        "board": hand.board,
        "second_run": hand.second_run,
        "second_board": hand.second_board,
        "returned": dict(hand.returned),
        "collected": dict(hand.collected),
        "shows": hand.shows,
        "ended": hand.ended,
    }


@dataclass
class Report:
    compared: int = 0
    matched: int = 0
    before_attach: list[int] = field(default_factory=list)
    missing_live: list[int] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    unknown_live: int = 0

    @property
    def ok(self) -> bool:
        return self.compared > 0 and not self.differences and not self.missing_live

    def text(self) -> str:
        lines = [
            f"[VAMOS] {self.matched}/{self.compared} hands identical to PokerNow's export",
            f"[VAMOS] {len(self.before_attach)} hands started before VAMOS attached (not compared)",
        ]
        if self.missing_live:
            lines.append(f"[VAMOS] MISSING from the live build: hands {self.missing_live}")
        if self.unknown_live:
            lines.append(f"[VAMOS] {self.unknown_live} live rows were not recognized")
        lines += [f"[VAMOS] {d}" for d in self.differences]
        return "\n".join(lines)


def live_hands(recording: Path) -> GameSession:
    """Replay recorded pages through a session exactly as the live backend received them."""
    session = GameSession(recording.parent.name)
    for line in recording.read_text().splitlines():
        page = json.loads(line)
        session.add_page(page.get("body"), bool(page.get("fresh")))
    return session


def export_hands(export: Path) -> list[Hand]:
    with export.open(encoding="utf-8", newline="") as f:
        return build_hands((int(r["order"]), r["entry"]) for r in csv.DictReader(f)).hands


def verify(recording: Path, export: Path) -> Report:
    live_log = live_hands(recording).log
    live = {h.number: h for h in live_log.hands}
    report = Report(unknown_live=len(live_log.unknown))
    first_live = min(live, default=None)
    for hand in export_hands(export):
        built = live.get(hand.number)
        if first_live is None or hand.number < first_live or (built and not built.seats):
            report.before_attach.append(hand.number)
            continue
        if built is None:
            report.missing_live.append(hand.number)
            continue
        report.compared += 1
        expected, got = _fields(hand), _fields(built)
        diffs = [name for name in expected if expected[name] != got[name]]
        if diffs:
            report.differences.append(f"hand #{hand.number}: {', '.join(diffs)} differ")
        else:
            report.matched += 1
    return report
