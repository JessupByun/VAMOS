"""
The hero's decision, as the log describes it: position, the price, the pot and the board.

Action order runs in rising seat number from the button (verified across the corpus), so
positions come from physical seats, anchored on the button. With a dead button the big
blind anchors them instead, and with a dead small blind the big blind poster is labeled BB
and the empty small blind seat's label goes to nobody.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from vamos.log.hands import DEAD_POSTS, Action, Hand, Street
from vamos.money import Cents

VOLUNTARY = {"check", "call", "bet", "raise", "fold"}


def _middle_labels(count: int) -> list[str]:
    """Names for the seats between the big blind and the button, first to act first."""
    tail = ["LJ", "HJ", "CO"]
    if count <= 2:
        return tail[len(tail) - count :]
    if count == 3:
        return ["UTG", "HJ", "CO"]
    return ["UTG", *[f"UTG+{i}" for i in range(1, count - 3)], *tail]


def seat_order(hand: Hand) -> list[str]:
    """Player IDs dealt in, from the first seat after the button round to the button."""
    ids = [s.player.id for s in sorted(hand.seats, key=lambda s: s.number)]
    if len(ids) < 2:
        return []
    if hand.dealer in ids:
        button = ids.index(hand.dealer)
    else:
        big_blind = next((a.player for a in hand.actions if a.kind == "post_bb"), None)
        if big_blind not in ids:
            return []
        # The empty button seat sits before the small blind (or before the big blind when
        # the small blind is dead too); label the player before it as the button.
        offset = 2 if any(a.kind == "post_sb" for a in hand.actions) else 1
        button = (ids.index(big_blind) - offset) % len(ids)
    return ids[button + 1 :] + ids[: button + 1]


def positions(hand: Hand) -> dict[str, str]:
    """Position label per player ID dealt into the hand."""
    order = seat_order(hand)
    if not order:
        return {}
    if len(order) == 2:
        return {order[1]: "BTN", order[0]: "BB"}
    labels = ["SB", "BB", *_middle_labels(len(order) - 3), "BTN"]
    big_blind = next((a.player for a in hand.actions if a.kind == "post_bb"), None)
    if hand.dead_small_blind and big_blind == order[0]:
        # The small blind's seat is empty, so the big blind sits first after the button and
        # the seats count as if the small blind were there, unlabeled.
        labels = ["BB", *_middle_labels(len(order) - 2), "BTN"]
    return dict(zip(order, labels, strict=True))


def next_to_act(
    order: Sequence[str], actions: Sequence[Action], street: Street, big_blind: Cents | None
) -> str | None:
    """
    Who acts next on `street`, given the hand's actions so far (`order` from seat_order).

    A player still to act has not folded or gone all in, and either has not acted on this
    street (posting a blind is not acting) or owes chips. Action moves on from the last
    player to act; at the start of a street it begins after the button, or preflop after
    the biggest live blind.
    """
    folded = {a.player for a in actions if a.kind == "fold"}
    all_in = {a.player for a in actions if a.all_in}
    current = [a for a in actions if a.street == street]
    committed: dict[str, Cents] = {}
    for a in current:
        if a.to is not None:
            committed[a.player] = max(committed.get(a.player, 0), a.to)
    price = max(committed.values(), default=0)
    if street == Street.PREFLOP and big_blind and any(a.kind == "post_bb" for a in current):
        # A big blind all in for less still leaves the full blind to call.
        price = max(price, big_blind)
    acted = {a.player for a in current if a.kind in VOLUNTARY}
    if sum(p not in folded for p in order) < 2:
        return None
    able = [p for p in order if p not in folded and p not in all_in]
    if len(able) == 1 and committed.get(able[0], 0) >= price:
        # Everyone else is all in: nobody is left to bet against.
        return None

    def owes(p: str) -> bool:
        return p in able and (p not in acted or committed.get(p, 0) < price)

    voluntary = [a for a in current if a.kind in VOLUNTARY]
    if voluntary:
        if voluntary[-1].player not in order:
            return None
        start = order.index(voluntary[-1].player) + 1
    elif street == Street.PREFLOP:
        blinds = [a for a in current if a.kind in ("post_bb", "post_straddle")]
        last_blind = max(blinds, key=lambda a: a.to or 0).player if blinds else None
        start = order.index(last_blind) + 1 if last_blind in order else 0
    else:
        start = 0
    for i in range(len(order)):
        player = order[(start + i) % len(order)]
        if owes(player):
            return player
    return None


def to_act(hand: Hand) -> str | None:
    """Who the log says must act now in `hand`; None between hands or when betting is over."""
    if hand.ended:
        return None
    return next_to_act(seat_order(hand), hand.actions, hand.street, hand.big_blind)


@dataclass(frozen=True)
class Decision:
    node: str  # identifies this exact decision point; advice is only shown while it holds
    hand_number: int
    street: Street
    position: str | None
    players_dealt: int
    opponents: tuple[str, ...]  # still contesting the pot, hero excluded
    pot: Cents  # everything in the middle, this street's bets included
    to_call: Cents
    # What the hero can win by calling, their call included: chips beyond what the hero
    # can match go back to whoever put them in.
    winnable: Cents
    behind: Cents  # hero's chips not yet in the pot
    big_blind: Cents | None
    effective_bb: float | None  # effective stack at the start of the hand, in big blinds
    board: tuple[str, ...]
    hero_cards: tuple[str, ...]


def decision(hand: Hand, hero: str) -> Decision | None:
    """The hero's spot in `hand`, or None if they were not dealt in or already folded."""
    stacks = {s.player.id: s.stack for s in hand.seats}
    if hero not in stacks or hero in hand.folded():
        return None
    folded = hand.folded()
    opponents = tuple(pid for pid in stacks if pid != hero and pid not in folded)
    behind = stacks[hero] - hand.put_in.get(hero, 0)
    to_call = min(max(0, hand.price - hand.street_bets.get(hero, 0)), behind)
    # Dead posts (a missing small blind, antes) belong to the pot whoever wins it; only live
    # chips are capped at what the hero can match.
    dead: dict[str, Cents] = {}
    for action in hand.actions:
        if action.kind in DEAD_POSTS:
            dead[action.player] = dead.get(action.player, 0) + action.added
    hero_live = hand.put_in.get(hero, 0) - dead.get(hero, 0) + to_call
    winnable = sum(dead.values()) + hero_live
    winnable += sum(
        min(put - hand.returned.get(pid, 0) - dead.get(pid, 0), hero_live)
        for pid, put in hand.put_in.items()
        if pid != hero
    )
    effective = None
    if hand.big_blind and opponents:
        deepest = max(stacks[pid] for pid in opponents)
        effective = min(stacks[hero], deepest) / hand.big_blind
    return Decision(
        node=f"{hand.hand_id or hand.number}:{int(hand.street)}:{len(hand.actions)}",
        hand_number=hand.number,
        street=hand.street,
        position=positions(hand).get(hero),
        players_dealt=len(stacks),
        opponents=opponents,
        pot=hand.pot,
        to_call=to_call,
        winnable=winnable,
        behind=behind,
        big_blind=hand.big_blind,
        effective_bb=effective,
        board=tuple(hand.board),
        hero_cards=hand.hero_cards,
    )
