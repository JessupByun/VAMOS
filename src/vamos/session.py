"""
One PokerNow game as VAMOS sees it tonight: the live log rows received so far, the hands
rebuilt from them, and per-player counts for the seat badges. Kept in memory only; nothing
here is saved.
"""

from dataclasses import dataclass

from vamos.log.hands import HandLog, Street, build_hands

VOLUNTARY = {"call", "bet", "raise"}


@dataclass(frozen=True)
class Counts:
    hands: int  # hands this player was dealt into
    vpip: int  # of those, hands where they voluntarily put chips in preflop


class GameSession:
    def __init__(self, game_id: str):
        self.game_id = game_id
        self._rows: dict[int, str] = {}
        self._log: HandLog | None = None

    def add_page(self, body: object) -> bool:
        """Merge one /log response; returns whether it brought any new rows."""
        logs = body.get("logs") if isinstance(body, dict) else None
        added = False
        for row in logs if isinstance(logs, list) else []:
            try:
                order, text = int(row["created_at"]), str(row["msg"])
            except (KeyError, TypeError, ValueError):
                continue
            if order not in self._rows:
                self._rows[order] = text
                added = True
        if added:
            self._log = None
        return added

    @property
    def log(self) -> HandLog:
        if self._log is None:
            self._log = build_hands(self._rows.items())
        return self._log

    def counts(self) -> dict[str, Counts]:
        dealt: dict[str, int] = {}
        voluntary: dict[str, int] = {}
        for hand in self.log.hands:
            # A hand seen from the middle (no roster yet) cannot say who was dealt in.
            if not hand.seats:
                continue
            put_in = {
                a.player for a in hand.actions if a.street == Street.PREFLOP and a.kind in VOLUNTARY
            }
            for seat in hand.seats:
                pid = seat.player.id
                dealt[pid] = dealt.get(pid, 0) + 1
                voluntary[pid] = voluntary.get(pid, 0) + (pid in put_in)
        return {pid: Counts(dealt[pid], voluntary[pid]) for pid in dealt}
