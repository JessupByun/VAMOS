"""
Log rows -> hands.

Replays parsed events in log order and rebuilds every NLH hand with exact chip accounting.
Semantics verified against real exports (docs/log-grammar.md): wager amounts are the
player's street total; small blind, big blind, straddle and missed big blind are live;
a missing small blind and antes are dead; second-run and second-board cards never advance
the street; rows after "-- ending hand" (late shows, rabbit hunts, bounties) still belong
to that hand. Non-NLH hands are skipped whole.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import IntEnum

from vamos.log.grammar import NLH, Event, Seat, parse_line
from vamos.money import Cents


class Street(IntEnum):
    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3


LIVE_POSTS = {"post_sb", "post_bb", "post_straddle", "post_missed_bb"}
DEAD_POSTS = {"post_missing_sb", "post_ante"}
WAGERS = {"call", "bet", "raise"}
_STREET_OF = {"flop": Street.FLOP, "turn": Street.TURN, "river": Street.RIVER}

# Rows that describe the table rather than the hand; they are valid anywhere, including
# between hands and in the middle of one, and never change the hand in progress.
TABLE_KINDS = {
    "seat_requested",
    "seat_request_canceled",
    "seat_approved",
    "seat_rejected",
    "joined",
    "quit",
    "stand_up",
    "sit_back",
    "ownership_passed",
    "stack_update",
    "stack_change_queued",
    "admin_force_away",
    "admin_remove",
    "admin_game_stop",
    "stakes",
    "config",
    "rebuy_prompt",
    "rebuy_waiting",
    "rebuy_requested",
    "rebought",
    "id_changed",
}


@dataclass(frozen=True)
class Action:
    order: int
    street: Street
    player: str  # PokerNow ID
    kind: str
    to: Cents | None  # the player's street total after a live post or wager
    added: Cents  # chips this row put in the pot
    all_in: bool
    # Raises only: False for an all-in raise smaller than a full raise, which does not
    # reopen betting for players who already acted.
    reopens: bool | None = None


@dataclass
class Hand:
    number: int
    hand_id: str | None
    first_order: int
    dealer: str | None  # None when the button seat was empty ("dead button")
    big_blind: Cents | None  # the table's nominal big blind, not what a short BB posted
    seats: tuple[Seat, ...] = ()
    names: dict[str, str] = field(default_factory=dict)
    hero_cards: tuple[str, ...] = ()
    actions: list[Action] = field(default_factory=list)
    board: list[str] = field(default_factory=list)
    second_run: list[str] = field(default_factory=list)
    second_board: list[str] = field(default_factory=list)
    shows: dict[str, tuple[str, ...]] = field(default_factory=dict)
    put_in: dict[str, Cents] = field(default_factory=lambda: defaultdict(int))
    returned: dict[str, Cents] = field(default_factory=lambda: defaultdict(int))
    collected: dict[str, Cents] = field(default_factory=lambda: defaultdict(int))
    collects: list[Event] = field(default_factory=list)
    bounties: list[Event] = field(default_factory=list)
    undealt: tuple[str, ...] = ()
    dead_small_blind: bool = False
    run_it_twice: bool = False
    ended: bool = False
    last_order: int = 0

    @property
    def street(self) -> Street:
        return Street(max(0, len(self.board) - 2)) if self.board else Street.PREFLOP

    @property
    def pot(self) -> Cents:
        """Chips in the middle: everything put in, minus uncalled bets returned."""
        return sum(self.put_in.values()) - sum(self.returned.values())

    @property
    def balanced(self) -> bool:
        """Every chip put in was either returned or collected."""
        return self.pot == sum(self.collected.values())

    def folded(self) -> set[str]:
        return {a.player for a in self.actions if a.kind == "fold"}


@dataclass
class HandLog:
    hands: list[Hand] = field(default_factory=list)
    # Table rows in order: seating, admin, stakes, config. (order, event)
    table: list[tuple[int, Event]] = field(default_factory=list)
    unknown: list[tuple[int, str]] = field(default_factory=list)
    skipped_variants: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class _Builder:
    def __init__(self) -> None:
        self.log = HandLog()
        self.hand: Hand | None = None
        self.skipping = False
        self.big_blind: Cents | None = None
        self._street_reset()

    def _street_reset(self) -> None:
        self.committed: dict[str, Cents] = defaultdict(int)
        self.current_bet: Cents = 0
        self.last_raise: Cents = self.big_blind or 0

    def feed(self, order: int, event: Event) -> None:
        kind = event.kind
        if kind == "unknown":
            self.log.unknown.append((order, event.detail or ""))
            return
        if kind == "hand_start":
            self._start(order, event)
            return
        if kind in TABLE_KINDS:
            self.log.table.append((order, event))
            if kind == "stakes" and event.detail == "big blind":
                self.big_blind = event.amount
            return
        if self.skipping or self.hand is None:
            return
        hand = self.hand
        hand.last_order = order
        if event.player is not None:
            hand.names[event.player.id] = event.player.name
        getattr(self, f"_on_{kind}", self._ignore)(hand, order, event)

    def _start(self, order: int, event: Event) -> None:
        self.hand = None
        if event.variant != NLH:
            self.skipping = True
            self.log.skipped_variants[event.variant or "unknown"] += 1
            return
        self.skipping = False
        self.hand = Hand(
            number=event.hand_number or 0,
            hand_id=event.hand_id,
            first_order=order,
            dealer=event.player.id if event.player else None,
            big_blind=self.big_blind,
            last_order=order,
        )
        self.log.hands.append(self.hand)
        self._street_reset()

    def _ignore(self, hand: Hand, order: int, event: Event) -> None:
        pass

    def _on_player_stacks(self, hand: Hand, order: int, event: Event) -> None:
        hand.seats = event.seats
        for seat in event.seats:
            hand.names[seat.player.id] = seat.player.name

    def _on_hero_cards(self, hand: Hand, order: int, event: Event) -> None:
        hand.hero_cards = event.cards

    def _on_dead_sb(self, hand: Hand, order: int, event: Event) -> None:
        hand.dead_small_blind = True

    def _wager(self, hand: Hand, order: int, event: Event, street: Street) -> None:
        assert event.player is not None and event.amount is not None
        player, to = event.player.id, event.amount
        added = max(0, to - self.committed[player])
        reopens = None
        if event.kind == "raise" or event.kind == "post_straddle":
            raise_by = to - self.current_bet
            reopens = raise_by >= self.last_raise
            if reopens:
                self.last_raise = raise_by
        if event.kind == "post_bb" and self.big_blind is None:
            # No stakes row seen yet (e.g. a log window that starts mid-game).
            hand.big_blind = to
            self.big_blind = to
            self.last_raise = to
        self.committed[player] = max(self.committed[player], to)
        self.current_bet = max(self.current_bet, to)
        hand.put_in[player] += added
        kind = event.kind
        hand.actions.append(Action(order, street, player, kind, to, added, event.all_in, reopens))

    def _on_post_sb(self, hand: Hand, order: int, event: Event) -> None:
        self._wager(hand, order, event, Street.PREFLOP)

    _on_post_bb = _on_post_straddle = _on_post_missed_bb = _on_post_sb

    def _on_post_missing_sb(self, hand: Hand, order: int, event: Event) -> None:
        assert event.player is not None and event.amount is not None
        hand.put_in[event.player.id] += event.amount
        hand.actions.append(
            Action(
                order, Street.PREFLOP, event.player.id, event.kind, None, event.amount, event.all_in
            )
        )

    _on_post_ante = _on_post_missing_sb

    def _on_call(self, hand: Hand, order: int, event: Event) -> None:
        self._wager(hand, order, event, hand.street)

    _on_bet = _on_raise = _on_call

    def _on_fold(self, hand: Hand, order: int, event: Event) -> None:
        assert event.player is not None
        hand.actions.append(Action(order, hand.street, event.player.id, event.kind, None, 0, False))

    _on_check = _on_fold

    def _on_flop(self, hand: Hand, order: int, event: Event) -> None:
        cards = list(event.cards)
        if event.run == "second run":
            hand.second_run = cards
        elif event.run == "second board":
            hand.second_board = cards
        else:
            hand.board = cards
            self._street_reset()

    _on_turn = _on_river = _on_flop

    def _on_uncalled(self, hand: Hand, order: int, event: Event) -> None:
        assert event.player is not None and event.amount is not None
        hand.returned[event.player.id] += event.amount

    def _on_collect(self, hand: Hand, order: int, event: Event) -> None:
        assert event.player is not None and event.amount is not None
        hand.collected[event.player.id] += event.amount
        hand.collects.append(event)

    def _on_show(self, hand: Hand, order: int, event: Event) -> None:
        # Players may reveal one card per row; keep the union in order seen.
        assert event.player is not None
        seen = hand.shows.get(event.player.id, ())
        hand.shows[event.player.id] = seen + tuple(c for c in event.cards if c not in seen)

    def _on_rit_result(self, hand: Hand, order: int, event: Event) -> None:
        hand.run_it_twice = event.detail == "yes"

    def _on_undealt(self, hand: Hand, order: int, event: Event) -> None:
        hand.undealt = event.cards

    def _on_bounty_paid(self, hand: Hand, order: int, event: Event) -> None:
        hand.bounties.append(event)

    def _on_hand_end(self, hand: Hand, order: int, event: Event) -> None:
        hand.ended = True


def build_hands(rows: Iterable[tuple[int, str]]) -> HandLog:
    """
    Rebuild hands from (order, text) rows: CSV `order`/`entry` or live `created_at`/`msg`.
    Rows may arrive in any order and with duplicates; they are sorted and deduplicated on
    `order`, so a page fetched twice or out of order yields the same hands.
    """
    builder = _Builder()
    for order, text in sorted(dict(rows).items()):
        builder.feed(order, parse_line(text))
    return builder.log
