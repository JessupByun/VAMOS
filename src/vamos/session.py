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
        # Orders before which rows may be missing (see add_page).
        self._gaps: set[int] = set()
        self._log: HandLog | None = None

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
        if fresh and page and self._rows and min(page) > max(self._rows):
            self._gaps.add(min(page))
        new = {order: text for order, text in page.items() if order not in self._rows}
        self._rows.update(new)
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
            for seat in hand.seats:
                pid = log.current_id(seat.player.id)
                dealt[pid] = dealt.get(pid, 0) + 1
                voluntary[pid] = voluntary.get(pid, 0) + (pid in put_in)
        return {pid: Counts(dealt[pid], voluntary[pid]) for pid in dealt}
