"""
One PokerNow game as VAMOS sees it tonight: the live log rows received so far, the hands
rebuilt from them, and per-player counts for the seat badges. The rows are also appended
to a file in data/games/, so a backend that restarts (a refreshed tab, a reload) picks the
night up where it was instead of from the last page of the log.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from vamos.jsonl import append_lines
from vamos.log.hands import HandLog, Street, build_hands

VOLUNTARY = {"call", "bet", "raise"}

log = logging.getLogger("vamos.session")


@dataclass(frozen=True)
class Counts:
    hands: int  # hands this player was dealt into
    vpip: int  # of those, hands where they voluntarily put chips in preflop
    pfr: int = 0  # of those, hands where they raised preflop


class GameSession:
    def __init__(self, game_id: str, store: Path | None = None):
        self.game_id = game_id
        self._rows: dict[int, str] = {}
        # Orders before which rows may be missing (see add_page).
        self._gaps: set[int] = set()
        self._log: HandLog | None = None
        self._store = store
        if store is not None:
            self._load(store)

    def _load(self, store: Path) -> None:
        try:
            lines = store.read_text().splitlines() if store.exists() else []
        except OSError:
            log.exception("[VAMOS] could not read %s", store)
            return
        for line in lines:
            try:
                entry = json.loads(line)
                if "gap" in entry:
                    self._gaps.add(int(entry["gap"]))
                else:
                    self._rows[int(entry["order"])] = str(entry["msg"])
            except (TypeError, ValueError, KeyError):
                continue  # a torn last line from a crash is skipped, not fatal

    def _save(self, rows: dict[int, str], gap: int | None) -> None:
        if self._store is None:
            return
        entries = [{"gap": gap}] if gap is not None else []
        entries += [{"order": order, "msg": text} for order, text in sorted(rows.items())]
        try:
            append_lines(self._store, (json.dumps(e) for e in entries))
        except OSError:
            log.exception("[VAMOS] could not save rows to %s", self._store)

    @property
    def newest(self) -> int | None:
        """The newest row held, where a log poller starting over should pick up."""
        return max(self._rows) if self._rows else None

    def add_page(self, body: object, fresh: bool = False) -> bool:
        """
        Merge one /log response; returns whether it brought any new rows. A `fresh` page was
        fetched without a cursor, so it holds only the newest rows: if it does not reach back
        to rows already held, whatever happened in between was never received.
        """
        logs = body.get("logs") if isinstance(body, dict) else None
        page: dict[int, str] = {}
        for row in logs if isinstance(logs, list) else []:
            try:
                page[int(row["created_at"])] = str(row["msg"])
            except (KeyError, TypeError, ValueError):
                continue
        gap = None
        if fresh and page and self._rows and min(page) > max(self._rows):
            gap = min(page)
            self._gaps.add(gap)
        new = {order: text for order, text in page.items() if order not in self._rows}
        self._rows.update(new)
        if new or gap is not None:
            self._save(new, gap)
        if new:
            self._log = None
        return bool(new)

    @property
    def log(self) -> HandLog:
        if self._log is None:
            self._log = build_hands(self._rows.items(), self._gaps)
        return self._log

    def counts(self) -> dict[str, Counts]:
        dealt: dict[str, int] = {}
        voluntary: dict[str, int] = {}
        raised: dict[str, int] = {}
        log = self.log
        for hand in log.hands:
            # A hand seen from the middle (no roster yet) cannot say who was dealt in.
            if not hand.seats:
                continue
            # Counted under each player's current ID, so a guest who logs in mid-game
            # keeps one line.
            put_in = {
                log.current_id(a.player)
                for a in hand.actions
                if a.street == Street.PREFLOP and a.kind in VOLUNTARY
            }
            raisers = {
                log.current_id(a.player)
                for a in hand.actions
                if a.street == Street.PREFLOP and a.kind == "raise"
            }
            for seat in hand.seats:
                pid = log.current_id(seat.player.id)
                dealt[pid] = dealt.get(pid, 0) + 1
                voluntary[pid] = voluntary.get(pid, 0) + (pid in put_in)
                raised[pid] = raised.get(pid, 0) + (pid in raisers)
        return {pid: Counts(dealt[pid], voluntary[pid], raised[pid]) for pid in dealt}


@dataclass(frozen=True)
class Showdown:
    number: int  # the hand number
    cards: tuple[str, ...]
    board: tuple[str, ...]
    second_board: tuple[str, ...]  # run it twice: the second runout, else empty
    line: str  # their actions by street, e.g. "preflop call · flop call · turn check"
    # "won", "lost", "split" (won part: a chop, one run of two, or a side pot), "folded"
    # (shown after folding) or "uncontested" (shown after everyone else folded).
    result: str


@dataclass(frozen=True)
class Read:
    """Tonight's read on one player, counted from tonight's log only."""

    hands: int
    vpip: int
    pfr: int
    three_bets: int  # re-raises of the first preflop raise
    three_bet_chances: int  # hands where they acted facing exactly one preflop raise
    cbets: int  # flop bets as the preflop raiser, with no bet before them
    cbet_chances: int  # flops where the preflop raiser acted first or was checked to
    bets_raises: int  # postflop bets and raises
    calls: int  # postflop calls
    folds_to_bets: int  # postflop folds when facing a bet
    faced_bets: int  # postflop decisions facing a bet
    showdowns: tuple[Showdown, ...]

    def summary(self) -> dict:
        """
        The read as Claude gets it: exact counts, each as x/y where it has a base, so a count
        is never read as a percentage ("vpip": 8 was read as 8% when it was 8 of 25 hands).
        Hands shown go only into drafts.
        """
        return {
            "hands": self.hands,
            "vpip": f"{self.vpip}/{self.hands}",
            "pfr": f"{self.pfr}/{self.hands}",
            "three_bet": f"{self.three_bets}/{self.three_bet_chances}",
            "cbet": f"{self.cbets}/{self.cbet_chances}",
            "postflop_bets_and_raises": self.bets_raises,
            "postflop_calls": self.calls,
            "folds_to_postflop_bets": f"{self.folds_to_bets}/{self.faced_bets}",
        }


def reads(log: HandLog, settled: bool = False) -> dict[str, Read]:
    """
    This session's read on every player dealt in, under their current ID. `settled` leaves
    out the hand still being played, so the bet the hero is facing is not counted as the
    bettor's history (what Claude gets); the side panel shows the live count.
    """
    tally: dict[str, dict] = {}
    shown: dict[str, list[Showdown]] = {}
    for hand in log.hands:
        if not hand.seats or (settled and hand is log.hands[-1] and not hand.ended):
            continue
        ids = [log.current_id(s.player.id) for s in hand.seats]
        for pid in ids:
            t = tally.setdefault(pid, dict.fromkeys(_READ_FIELDS, 0))
            t["hands"] += 1
        preflop = [a for a in hand.actions if a.street == Street.PREFLOP]
        raises = [a for a in preflop if a.kind == "raise"]
        for pid in {log.current_id(a.player) for a in preflop if a.kind in VOLUNTARY}:
            tally[pid]["vpip"] += 1
        for pid in {log.current_id(a.player) for a in raises}:
            tally[pid]["pfr"] += 1
        if raises:
            # Everyone who acted after the first raise and up to the re-raise had the
            # chance to 3-bet; only the first re-raise is one (later ones are 4-bets).
            until = raises[1].order if len(raises) > 1 else None
            opener = log.current_id(raises[0].player)
            faced = {
                log.current_id(a.player)
                for a in preflop
                if a.order > raises[0].order
                and (until is None or a.order <= until)
                and a.kind in VOLUNTARY | {"fold"}
            }
            for pid in faced - {opener}:
                tally[pid]["three_bet_chances"] += 1
            if len(raises) > 1:
                tally[log.current_id(raises[1].player)]["three_bets"] += 1
        _postflop(hand, log, tally, log.current_id(raises[-1].player) if raises else None)
        # Cards shown while an all-in runs out come before the result; wait for the end.
        if not hand.ended:
            continue
        for raw, cards in hand.shows.items():
            pid = log.current_id(raw)
            shown.setdefault(pid, []).append(
                Showdown(
                    hand.number,
                    tuple(cards),
                    tuple(hand.board),
                    tuple(hand.second_run or hand.second_board),
                    _line(hand, log, pid),
                    _result(hand, log, pid, ids),
                )
            )
    return {pid: Read(**t, showdowns=tuple(shown.get(pid, ()))) for pid, t in tally.items()}


_READ_FIELDS = (
    "hands",
    "vpip",
    "pfr",
    "three_bets",
    "three_bet_chances",
    "cbets",
    "cbet_chances",
    "bets_raises",
    "calls",
    "folds_to_bets",
    "faced_bets",
)


def _result(hand, log: HandLog, pid: str, ids: list[str]) -> str:
    # Collected and folded are keyed by the ID at the time, which may since have changed.
    won: dict[str, int] = {}
    for raw, amount in hand.collected.items():
        won[log.current_id(raw)] = won.get(log.current_id(raw), 0) + amount
    folded = {log.current_id(p) for p in hand.folded()}
    if pid in folded:
        return "folded"
    live = [p for p in ids if p not in folded]
    if len(live) < 2:
        return "uncontested"
    if won.get(pid, 0) <= 0:
        return "lost"
    return "split" if any(won.get(p, 0) > 0 for p in live if p != pid) else "won"


def _postflop(hand, log: HandLog, tally: dict[str, dict], aggressor: str | None) -> None:
    # A c-bet chance is the preflop raiser's first flop action with no bet before it; a
    # donk bet, or an all-in preflop that leaves no flop action, is no chance.
    bet_seen = False
    for a in (a for a in hand.actions if a.street == Street.FLOP):
        if log.current_id(a.player) == aggressor:
            tally[aggressor]["cbet_chances"] += not bet_seen
            tally[aggressor]["cbets"] += not bet_seen and a.kind == "bet"
            break
        bet_seen = bet_seen or a.kind in ("bet", "raise")
    for street in (Street.FLOP, Street.TURN, Street.RIVER):
        price = 0
        put: dict[str, int] = {}
        for a in (a for a in hand.actions if a.street == street):
            pid = log.current_id(a.player)
            if a.kind in ("bet", "raise"):
                tally[pid]["bets_raises"] += 1
            elif a.kind == "call":
                tally[pid]["calls"] += 1
            if a.kind in VOLUNTARY | {"fold", "check"} and price > put.get(pid, 0):
                tally[pid]["faced_bets"] += 1
                tally[pid]["folds_to_bets"] += a.kind == "fold"
            if a.to is not None:
                put[pid] = max(put.get(pid, 0), a.to)
                price = max(price, a.to)


def _line(hand, log: HandLog, pid: str) -> str:
    parts = []
    for street in (Street.PREFLOP, Street.FLOP, Street.TURN, Street.RIVER):
        moves = [
            a.kind
            for a in hand.actions
            if a.street == street
            and log.current_id(a.player) == pid
            and a.kind in VOLUNTARY | {"check", "fold"}
        ]
        if moves:
            parts.append(f"{street.name.lower()} {', '.join(moves)}")
    return " · ".join(parts) or "no action"
