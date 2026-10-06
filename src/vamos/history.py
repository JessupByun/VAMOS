"""
This session's earlier hands for the side panel's replayer: the list of the hero's finished
hands, and one hand street by street, with what VAMOS advised at each of the hero's
decisions next to what the hero did. Names are the real ones: this goes only to the user's
own screen, never to Claude.

`decisions` maps a decision node ("<hand>:<street>:<actions so far>") to what the card
showed there (src/vamos/host/server.py records it), so a decision sits just before the
action it was about: the action at that index in the hand.
Hands played before decisions were recorded (2026-10-05) fall back on `logged`, the game's
entries in data/advice.jsonl: Claude's answers only, since rule and chart answers were
never logged there.
"""

import json
from collections import Counter
from collections.abc import Sequence
from typing import Any

from vamos.brain.advisor import answer_fields, model_label
from vamos.brain.answer import Answer, Choice, Spot, tidy
from vamos.brain.provider import PRESETS
from vamos.brain.pseudonyms import HERO, reveal
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


def hand_list(
    log: HandLog, hero: str, decisions: dict[str, Message], logged: Sequence[Message] = ()
) -> list[Message]:
    """The hero's finished hands this session, newest first, for the replayer's menu."""
    nodes = set(decisions) | {str(e.get("node")) for e in logged}
    advised = Counter(node.split(":")[0] for node in nodes)
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


def _index(node: object, key: str) -> int | None:
    """The action index of a decision node in the hand `key`, or None for another hand."""
    parts = str(node).split(":")
    if len(parts) == 3 and parts[0] == key and parts[2].isdigit():
        return int(parts[2])
    return None


def _message(text: object) -> Message | None:
    """The hand as a logged prompt sent it: the JSON object in the message."""
    if not isinstance(text, str) or "{" not in text:
        return None
    try:
        found, _ = json.JSONDecoder().raw_decode(text, text.index("{"))
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _who(players: list[Message], seats: list[tuple[str, str | None, float]]) -> dict[str, str]:
    """
    The player each pseudonym in a logged prompt stood for: the one opponent who started the
    hand with that stack, or among equal stacks the one in that position. Positions alone
    will not do: how some are counted (a dead small blind) was fixed after logs were written.
    """
    out = {}
    for player in players:
        label = player.get("name")
        if label in (None, HERO):
            continue
        same = [s for s in seats if s[2] == player.get("stack_bb")]
        if len(same) > 1:
            same = [s for s in same if s[1] == player.get("position")]
        if len(same) == 1:
            out[str(label)] = same[0][0]
    return out


def _logged_answer(
    entry: Message, seats: list[tuple[str, str | None, float]], bb: int
) -> Message | None:
    """One logged Claude call as the panel shows an answer, with the real names back."""
    sent = _message(entry.get("spot"))
    if sent is None:
        return None
    players = [p for p in sent.get("players", []) if isinstance(p, dict)]
    shown = _who(players, seats)
    # The preset was logged from 2026-10-05 on; "Ask deeper" always ran the deep one.
    preset = entry.get("preset") or ("deep" if entry.get("kind") == "ask" else None)
    out: Message = {
        "model": model_label(
            str(entry.get("model")), PRESETS[preset].effort if preset in PRESETS else None
        ),
        "preset": preset if preset in PRESETS else None,
        "state": entry.get("state"),
        "reason": entry.get("reason"),
        **dict.fromkeys(("action", "mix", "pick", "pickPct", "why", "plan")),
        "deviation": False,
    }
    try:
        said = json.loads(entry["answer"])
        mix = tuple(Choice(str(a), int(to), float(f)) for a, to, f in entry["mix"])
        chosen = next(c for c in mix if [c.action, c.to] == list(entry["chosen"]))
        hero = next(p for p in players if p.get("name") == HERO)
        to_call = round(float(sent["facts"].get("to_call_bb") or 0) * bb)
        behind = round(float(hero["behind_bb"]) * bb)
    except (KeyError, TypeError, ValueError, StopIteration):
        return out  # a failed call, or an entry from before these fields
    # Only what the card's text reads: the big blind, the price and the hero's chips.
    spot = Spot(0, to_call, 0, 0, 0, behind, bb)
    answer = Answer(
        mix,
        chosen,
        reveal(tidy(str(said.get("reason") or "")), shown),
        reveal(tidy(str(said.get("plan") or "")), shown),
    )
    return out | answer_fields(answer, spot)


def _from_log(hand: Hand, logged: list[Message], hero: str | None) -> dict[int, Message]:
    """Claude's answers in `hand` from the advice log, by action index, as decision records."""
    key = hand_key(hand)
    bb = hand.big_blind or 1
    labels = positions(hand)
    seats = [
        (s.player.name, labels.get(s.player.id), _bb(s.stack, bb))
        for s in hand.seats
        if s.player.id != hero
    ]
    calls: dict[int, list[Message]] = {}
    for entry in logged:
        index = _index(entry.get("node"), key)
        if index is not None:
            calls.setdefault(index, []).append(entry)
    out = {}
    for index, entries in calls.items():
        answers = [a for e in entries if (a := _logged_answer(e, seats, bb))]
        if not answers:
            continue
        shown = next((a for a in reversed(answers) if a["action"]), answers[-1])
        out[index] = {
            "street": int(str(entries[0]["node"]).split(":")[1]),
            "facts": {},
            "advice": {
                "source": "Claude",
                **shown,
                "asked": answers[-1]["model"],
                "askedPreset": answers[-1]["preset"],
                "state": answers[-1]["state"],
                "reason": answers[-1]["reason"],
                "answers": answers,
            },
            "aiOff": False,
        }
    return out


def hand_detail(
    log: HandLog, key: str, hero: str, decisions: dict[str, Message], logged: Sequence[Message] = ()
) -> Message | None:
    """One hand street by street, with VAMOS's advice before each of the hero's actions."""
    hand = next((h for h in log.hands if hand_key(h) == key), None)
    if hand is None:
        return None
    pid = _seated_as(hand, log, hero)
    bb = hand.big_blind or 1
    labels = positions(hand)
    names = {s.player.id: s.player.name for s in hand.seats}
    recorded: dict[int, Message] = {}
    for node, record in decisions.items():
        index = _index(node, key)
        if index is not None:
            recorded[index] = record
    at = _from_log(hand, list(logged), pid) | recorded
    # The hero decided something and nothing was recorded: played before the record began.
    partial = not recorded and any(
        a.player == pid and a.kind in ("fold", "check", "call", "bet", "raise")
        for a in hand.actions
    )

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
        "partial": partial,
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
