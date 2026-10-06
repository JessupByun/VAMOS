"""
This session's earlier hands for the side panel's replayer: the list of the hero's finished
hands, and one hand street by street, with what VAMOS advised at each of the hero's
decisions next to what the hero did. Names are the real ones: this goes only to the user's
own screen, never to Claude.

`decisions` maps a decision node ("<hand>:<street>:<actions so far>") to what the card
showed there (src/vamos/host/server.py records it), so a decision sits just before the
action it was about: the action at that index in the hand.
"""

from collections import Counter
from typing import Any

from vamos.engine.facts import format_bb, format_chips
from vamos.engine.state import positions
from vamos.log.hands import Action, Hand, HandLog, Street

type Message = dict[str, Any]

STREET_NAMES = ("Preflop", "Flop", "Turn", "River")
DEALT = {Street.FLOP: (0, 3), Street.TURN: (3, 4), Street.RIVER: (4, 5)}
POSTS = {
    "post_sb": "posts the small blind",
    "post_bb": "posts the big blind",
    "post_missed_bb": "posts a missed big blind",
    "post_missing_sb": "posts a dead small blind",
    "post_ante": "posts an ante",
}


def hand_key(hand: Hand) -> str:
    """What every decision node in the hand starts with."""
    return str(hand.hand_id or hand.number)


def net(hand: Hand, pid: str) -> int:
    """What a player won or lost in the hand, in cents, bounties included."""
    put = hand.put_in.get(pid, 0) - hand.returned.get(pid, 0)
    bounty = sum(
        (e.amount or 0)
        * (
            (e.other is not None and e.other.id == pid)
            - (e.player is not None and e.player.id == pid)
        )
        for e in hand.bounties
        if e.kind == "bounty_paid"
    )
    return hand.collected.get(pid, 0) - put + bounty


def _seated_as(hand: Hand, log: HandLog, hero: str) -> str | None:
    """The hero's ID as seated in this hand (it may predate an ID change), or None."""
    return next((s.player.id for s in hand.seats if log.current_id(s.player.id) == hero), None)


def hand_list(log: HandLog, hero: str, decisions: dict[str, Message]) -> list[Message]:
    """The hero's finished hands this session, newest first, for the replayer's menu."""
    advised = Counter(node.split(":")[0] for node in decisions)
    out = []
    for hand in log.hands:
        pid = _seated_as(hand, log, hero)
        if pid is None or not hand.ended:
            continue
        key = hand_key(hand)
        out.append(
            {
                "key": key,
                "number": hand.number,
                "heroCards": list(hand.hero_cards),
                "position": positions(hand).get(pid),
                "resultBb": round(net(hand, pid) / (hand.big_blind or 1), 2),
                "folded": pid in hand.folded(),
                "decisions": advised[key],
            }
        )
    return out[::-1]


def _bb(cents: int, bb: int) -> float:
    return round(cents / bb, 2)


def _verb(action: Action) -> str:
    """The action's color on the panel: what it says, as on the card."""
    if action.kind in ("bet", "raise") and action.all_in:
        return "allin"
    return action.kind if action.kind in ("fold", "check", "call", "bet", "raise") else "post"


def _text(action: Action, bb: int) -> str:
    if action.kind in POSTS:
        text = POSTS[action.kind]
    elif action.kind == "post_straddle":
        text = f"straddles to {format_bb(action.to or 0, bb)}"
    elif action.kind in ("fold", "check"):
        text = f"{action.kind}s"
    elif action.kind == "call":
        text = f"calls {format_bb(action.to or 0, bb)}"
    elif action.kind == "bet":
        text = f"bets {format_bb(action.to or 0, bb)}"
    elif action.kind == "raise":
        text = f"raises to {format_bb(action.to or 0, bb)}"
    else:
        text = action.kind.replace("_", " ")
    return f"{text} (all in)" if action.all_in else text


def hand_detail(log: HandLog, key: str, hero: str, decisions: dict[str, Message]) -> Message | None:
    """One hand street by street, with VAMOS's advice before each of the hero's actions."""
    hand = next((h for h in log.hands if hand_key(h) == key), None)
    if hand is None:
        return None
    pid = _seated_as(hand, log, hero)
    bb = hand.big_blind or 1
    labels = positions(hand)
    names = {s.player.id: s.player.name for s in hand.seats}
    at: dict[int, Message] = {}
    for node, record in decisions.items():
        parts = node.split(":")
        if len(parts) == 3 and parts[0] == key and parts[2].isdigit():
            at[int(parts[2])] = record

    def decision(index: int) -> Message:
        record = at[index]
        return {
            "kind": "decision",
            "advice": record.get("advice"),
            "aiOff": bool(record.get("aiOff")),
            "facts": record.get("facts") or {},
        }

    streets: list[Message] = []
    pot = 0
    for street in Street:
        indexed = [(i, a) for i, a in enumerate(hand.actions) if a.street == street]
        # A decision the hero never acted on (the hand ended first) closes its street.
        tail = [
            i
            for i, record in at.items()
            if i >= len(hand.actions) and record.get("street") == street
        ]
        start, end = DEALT.get(street, (0, 0))
        board = hand.board[start:end]
        if not indexed and not tail and (street == Street.PREFLOP or len(board) < end - start):
            continue
        events: list[Message] = []
        for i, action in indexed:
            if i in at:
                events.append(decision(i))
            events.append(
                {
                    "kind": "action",
                    "player": names.get(action.player, action.player),
                    "position": labels.get(action.player),
                    "hero": action.player == pid,
                    "verb": _verb(action),
                    "text": _text(action, bb),
                }
            )
        events += [decision(i) for i in sorted(tail)]
        streets.append(
            {"name": STREET_NAMES[street], "board": board, "potBb": _bb(pot, bb), "events": events}
        )
        pot += sum(a.added for _, a in indexed)
    result = net(hand, pid) if pid is not None else None
    return {
        "key": key,
        "number": hand.number,
        "ended": hand.ended,
        "heroCards": list(hand.hero_cards),
        "board": list(hand.board),
        "secondBoard": list(hand.second_run or hand.second_board),
        "players": [
            {
                "name": s.player.name,
                "position": labels.get(s.player.id),
                "stackBb": _bb(s.stack, bb),
                "hero": s.player.id == pid,
            }
            for s in hand.seats
        ],
        "streets": streets,
        "shows": [
            {"name": names.get(p, p), "cards": list(cards), "hero": p == pid}
            for p, cards in hand.shows.items()
        ],
        "winners": [
            {
                "name": names.get(p, p),
                "amount": format_chips(amount, bb),
                "amountBb": _bb(amount, bb),
                "hero": p == pid,
            }
            for p, amount in hand.collected.items()
            if amount
        ],
        "result": None if result is None else format_chips(abs(result), bb),
        "resultBb": None if result is None else _bb(result, bb),
    }
