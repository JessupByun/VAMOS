"""
The hero's decision, as the log describes it: position, the price, the pot and the board.

Action order runs in rising seat number from the button (verified across the corpus), so
positions come from physical seats, anchored on the button. With a dead button the big
blind anchors them instead.
"""

from dataclasses import dataclass

from vamos.log.hands import Hand, Street
from vamos.money import Cents


def _middle_labels(count: int) -> list[str]:
    """Names for the seats between the big blind and the button, first to act first."""
    tail = ["LJ", "HJ", "CO"]
    if count <= 2:
        return tail[len(tail) - count :]
    if count == 3:
        return ["UTG", "HJ", "CO"]
    return ["UTG", *[f"UTG+{i}" for i in range(1, count - 3)], *tail]


def positions(hand: Hand) -> dict[str, str]:
    """Position label per player ID dealt into the hand."""
    ids = [s.player.id for s in sorted(hand.seats, key=lambda s: s.number)]
    if len(ids) < 2:
        return {}
    if hand.dealer in ids:
        button = ids.index(hand.dealer)
    else:
        big_blind = next((a.player for a in hand.actions if a.kind == "post_bb"), None)
        if big_blind not in ids:
            return {}
        # The empty button seat sits before the small blind (or before the big blind when
        # the small blind is dead too); label the player before it as the button.
        offset = 2 if any(a.kind == "post_sb" for a in hand.actions) else 1
        button = (ids.index(big_blind) - offset) % len(ids)
    order = ids[button + 1 :] + ids[: button + 1]  # first after the button ... the button
    if len(ids) == 2:
        return {order[1]: "BTN", order[0]: "BB"}
    labels = ["SB", "BB", *_middle_labels(len(ids) - 3), "BTN"]
    return dict(zip(order, labels, strict=True))


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
        behind=behind,
        big_blind=hand.big_blind,
        effective_bb=effective,
        board=tuple(hand.board),
        hero_cards=hand.hero_cards,
    )
