"""
PokerNow log lines -> events.

One anchored pattern per line type, matched against a whole entry with trailing whitespace
stripped. The grammar and every rule's evidence are documented in docs/log-grammar.md;
the CSV export and the live /log endpoint carry the same text.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from vamos.cards import parse_log_card
from vamos.money import Cents, parse_chips

# A quoted player token. The name is lazy and the ID cannot contain spaces, so the token
# splits on its last " @ " (real names include "spa @ ces").
_P = r'"(?P<name>.*?) @ (?P<id>[A-Za-z0-9_-]+)"'
_P2 = r'"(?P<name2>.*?) @ (?P<id2>[A-Za-z0-9_-]+)"'
_N = r"\d+(?:\.\d+)?"
_CARD = r"(?:10|[2-9JQKA])[♠♥♦♣]"
_CARDS = rf"{_CARD}(?:, {_CARD})*"
_ALLIN = r"(?P<allin> and go all in)?"

NLH = "No Limit Texas Hold'em"


@dataclass(frozen=True)
class Player:
    name: str
    id: str


@dataclass(frozen=True)
class Seat:
    number: int
    player: Player
    stack: Cents


@dataclass(frozen=True)
class Event:
    """One log entry. Only the fields that apply to `kind` are set."""

    kind: str
    player: Player | None = None
    other: Player | None = None
    amount: Cents | None = None
    old_amount: Cents | None = None
    all_in: bool = False
    cards: tuple[str, ...] = ()
    run: str | None = None  # "second run" or "second board" on board and collect rows
    hand_number: int | None = None
    hand_id: str | None = None
    variant: str | None = None
    seats: tuple[Seat, ...] = ()
    description: str | None = None
    detail: str | None = None
    settings: tuple[tuple[str, str, str], ...] = field(default=())


def _player(m: re.Match, suffix: str = "") -> Player:
    return Player(m.group(f"name{suffix}"), m.group(f"id{suffix}"))


def _amount(text: str) -> Cents:
    cents = parse_chips(text)
    if cents is None:
        raise ValueError(f"not an amount: {text!r}")
    return cents


def _cards(text: str) -> tuple[str, ...]:
    return tuple(parse_log_card(c) for c in text.split(", ")) if text else ()


def _variant(raw: str | None) -> str:
    # 2026 rows name the variant bare; 2021-2023 rows wrap it in parentheses.
    if raw is None:
        return ""
    raw = raw.strip()
    return raw[1:-1] if raw.startswith("(") and raw.endswith(")") else raw


_SEAT = re.compile(rf"#(?P<seat>\d+) {_P} \((?P<stack>{_N})\)")

type Builder = Callable[[re.Match], Event]

_RULES: list[tuple[re.Pattern, Builder]] = []


def _rule(pattern: str) -> Callable[[Builder], Builder]:
    def register(build: Builder) -> Builder:
        _RULES.append((re.compile(pattern), build))
        return build

    return register


@_rule(
    r"-- starting hand #(?P<n>\d+)(?: \(id: (?P<hid>[a-z0-9]+)\))?(?: +(?P<variant>.+?))? +"
    rf"\((?:dealer: {_P}|(?P<dead>dead button))\) --"
)
def _hand_start(m: re.Match) -> Event:
    return Event(
        "hand_start",
        player=None if m.group("dead") else _player(m),
        hand_number=int(m.group("n")),
        hand_id=m.group("hid"),
        variant=_variant(m.group("variant")),
    )


@_rule(r"-- ending hand #(?P<n>\d+) --")
def _hand_end(m: re.Match) -> Event:
    return Event("hand_end", hand_number=int(m.group("n")))


@_rule(r"Player stacks: (?P<body>#.+)")
def _player_stacks(m: re.Match) -> Event:
    # Tile the row seat by seat: names may themselves contain " | " or "#".
    body, pos, seats = m.group("body"), 0, []
    while True:
        seat = _SEAT.match(body, pos)
        if seat is None:
            raise ValueError(f"could not split the roster: {body!r}")
        seats.append(Seat(int(seat.group("seat")), _player(seat), _amount(seat.group("stack"))))
        if seat.end() == len(body):
            return Event("player_stacks", seats=tuple(seats))
        if not body.startswith(" | ", seat.end()):
            raise ValueError(f"could not split the roster: {body!r}")
        pos = seat.end() + 3


@_rule(rf"Your hand is (?P<cards>{_CARDS})")
def _hero_cards(m: re.Match) -> Event:
    return Event("hero_cards", cards=_cards(m.group("cards")))


_POSTS = {
    "a small blind": "post_sb",
    "a big blind": "post_bb",
    "a straddle": "post_straddle",
    "a missing small blind": "post_missing_sb",
    "a missed big blind": "post_missed_bb",
    "an ante": "post_ante",
}


@_rule(rf"{_P} posts (?P<what>{'|'.join(_POSTS)}) of (?P<amt>{_N}){_ALLIN}")
def _post(m: re.Match) -> Event:
    return Event(
        _POSTS[m.group("what")],
        player=_player(m),
        amount=_amount(m.group("amt")),
        all_in=bool(m.group("allin")),
    )


@_rule(r"Dead Small Blind")
def _dead_sb(m: re.Match) -> Event:
    return Event("dead_sb")


@_rule(rf"{_P} (?P<verb>folds|checks)")
def _fold_check(m: re.Match) -> Event:
    return Event("fold" if m.group("verb") == "folds" else "check", player=_player(m))


@_rule(rf"{_P} (?P<verb>calls|bets|raises to) (?P<amt>{_N}){_ALLIN}")
def _wager(m: re.Match) -> Event:
    kind = {"calls": "call", "bets": "bet", "raises to": "raise"}[m.group("verb")]
    return Event(
        kind, player=_player(m), amount=_amount(m.group("amt")), all_in=bool(m.group("allin"))
    )


@_rule(
    rf"Flop(?: \((?P<run>second run|second board)\))?:  \[(?P<cards>{_CARD}, {_CARD}, {_CARD})\]"
)
def _flop(m: re.Match) -> Event:
    return Event("flop", cards=_cards(m.group("cards")), run=m.group("run"))


@_rule(
    rf"(?P<street>Turn|River)(?: \((?P<run>second run|second board)\))?: "
    rf"(?P<board>{_CARDS}) \[(?P<new>{_CARD})\]"
)
def _turn_river(m: re.Match) -> Event:
    return Event(
        m.group("street").lower(),
        cards=_cards(m.group("board")) + _cards(m.group("new")),
        run=m.group("run"),
    )


@_rule(rf"Uncalled bet of (?P<amt>{_N}) returned to {_P}")
def _uncalled(m: re.Match) -> Event:
    return Event("uncalled", player=_player(m), amount=_amount(m.group("amt")))


@_rule(
    rf"{_P} collected (?P<amt>{_N}) from pot(?: with (?P<desc>.+?)"
    rf"(?: on the (?P<run>second run|second board) )? \(combination: (?P<combo>{_CARDS})\))?"
)
def _collect(m: re.Match) -> Event:
    return Event(
        "collect",
        player=_player(m),
        amount=_amount(m.group("amt")),
        description=m.group("desc"),
        cards=_cards(m.group("combo") or ""),
        run=m.group("run"),
    )


@_rule(rf"{_P} shows a (?P<cards>{_CARDS})\.")
def _show(m: re.Match) -> Event:
    return Event("show", player=_player(m), cards=_cards(m.group("cards")))


@_rule(r"Remaining players decide whether to run it twice\.")
def _rit_prompt(m: re.Match) -> Event:
    return Event("rit_prompt")


@_rule(rf"{_P} chooses to (?P<no> run|not run) it twice\.")
def _rit_choice(m: re.Match) -> Event:
    return Event("rit_choice", player=_player(m), detail="yes" if m.group("no") == " run" else "no")


@_rule(r"(?:(?P<all>All players in hand choose to)|Some players choose to not) run it twice\.")
def _rit_result(m: re.Match) -> Event:
    return Event("rit_result", detail="yes" if m.group("all") else "no")


@_rule(r"The board will run it twice due(?: to)? the game settings\.")
def _rit_forced(m: re.Match) -> Event:
    return Event("rit_result", detail="yes")


@_rule(rf"Undealt cards: (?P<board>{_CARDS})? \[(?P<undealt>{_CARDS})\]")
def _undealt(m: re.Match) -> Event:
    return Event("undealt", cards=_cards(m.group("undealt")))


@_rule(rf"{_P} paid (?P<amt>{_N}) for the (?P<kind>.+?) bounty to {_P2}")
def _bounty_paid(m: re.Match) -> Event:
    return Event(
        "bounty_paid",
        player=_player(m),
        other=_player(m, "2"),
        amount=_amount(m.group("amt")),
        detail=m.group("kind"),
    )


@_rule(rf"{_P} collected (?P<amt>{_N}) from the (?P<kind>.+?) bounty")
def _bounty_collected(m: re.Match) -> Event:
    return Event(
        "bounty_collected",
        player=_player(m),
        amount=_amount(m.group("amt")),
        detail=m.group("kind"),
    )


# Seating and admin rows: they move no chips inside the current hand.
_SEATING = {
    "requested a seat": "seat_requested",
    "canceled the seat request": "seat_request_canceled",
}


@_rule(rf"The player {_P} (?P<what>requested a seat|canceled the seat request)\.")
def _seat_request(m: re.Match) -> Event:
    return Event(_SEATING[m.group("what")], player=_player(m))


@_rule(rf"The admin approved the player {_P} participation with a stack of (?P<amt>{_N})\.")
def _approved(m: re.Match) -> Event:
    return Event("seat_approved", player=_player(m), amount=_amount(m.group("amt")))


@_rule(rf"The admin {_P} rejected the seat request from the player {_P2}\.")
def _rejected(m: re.Match) -> Event:
    return Event("seat_rejected", player=_player(m), other=_player(m, "2"))


_STACK_VERBS = {
    "joined the game with a stack of": "joined",
    "quits the game with a stack of": "quit",
    "stand up with the stack of": "stand_up",
    "sit back with the stack of": "sit_back",
}


@_rule(rf"The player {_P} (?P<verb>{'|'.join(_STACK_VERBS)}) (?P<amt>{_N})\.")
def _stack_verb(m: re.Match) -> Event:
    return Event(_STACK_VERBS[m.group("verb")], player=_player(m), amount=_amount(m.group("amt")))


@_rule(rf"The player {_P} passed the room ownership to {_P2}\.")
def _ownership(m: re.Match) -> Event:
    return Event("ownership_passed", player=_player(m), other=_player(m, "2"))


@_rule(rf"The admin updated the player {_P} stack from (?P<old>{_N}) to (?P<new>{_N})\.")
def _stack_update(m: re.Match) -> Event:
    return Event(
        "stack_update",
        player=_player(m),
        old_amount=_amount(m.group("old")),
        amount=_amount(m.group("new")),
    )


@_rule(
    rf"WARNING: the admin queued the stack change for the player {_P} "
    rf"(?:(?P<dir>adding|removing)|(?P<reset>reseting to)) (?P<amt>{_N}) chips in the next hand\."
)
def _queued_stack(m: re.Match) -> Event:
    # "adding" and "removing" carry the change; a reset ("reseting", sic) the new stack.
    return Event(
        "stack_change_queued",
        player=_player(m),
        amount=_amount(m.group("amt")),
        detail=m.group("dir") or "reset",
    )


@_rule(rf"The admin {_P} forced the player {_P2} to away mode in the next hand\.")
def _force_away(m: re.Match) -> Event:
    return Event("admin_force_away", player=_player(m), other=_player(m, "2"))


@_rule(rf"The admin {_P} enqueued the removal of the player {_P2}\.")
def _remove(m: re.Match) -> Event:
    return Event("admin_remove", player=_player(m), other=_player(m, "2"))


@_rule(rf"The admin {_P} (?P<op>enqueued|canceled) the game stop on next hand\.")
def _game_stop(m: re.Match) -> Event:
    return Event("admin_game_stop", player=_player(m), detail=m.group("op"))


@_rule(
    rf"The game's (?P<which>small blind|big blind|ante) was changed from "
    rf"(?P<old>{_N}) to (?P<new>{_N})\."
)
def _blind_change(m: re.Match) -> Event:
    return Event(
        "stakes",
        detail=m.group("which"),
        old_amount=_amount(m.group("old")),
        amount=_amount(m.group("new")),
    )


# The one multi-line entry: a header, then one "* Key: old » new" line per setting.
_SETTING = re.compile(r"\n\* (?P<key>[^:\n]+): (?P<old>[^\n]*?) » (?P<new>[^\n]*)")


@_rule(r"Game Config Changes(?P<body>(?:\n\* [^:\n]+: [^\n]*? » [^\n]*)+)")
def _config(m: re.Match) -> Event:
    settings = tuple(
        (s.group("key"), s.group("old"), s.group("new")) for s in _SETTING.finditer(m.group("body"))
    )
    return Event("config", settings=settings)


@_rule(r"Asking to busted players the rebuy decision\.")
def _rebuy_prompt(m: re.Match) -> Event:
    return Event("rebuy_prompt")


@_rule(r"Waiting for the game owner to approve or reject pending rebuy requests\.")
def _rebuy_waiting(m: re.Match) -> Event:
    return Event("rebuy_waiting")


@_rule(rf"The player {_P} requested a rebuy of (?P<amt>{_N})\.")
def _rebuy_requested(m: re.Match) -> Event:
    return Event("rebuy_requested", player=_player(m), amount=_amount(m.group("amt")))


@_rule(rf"The player {_P} rebought\. New stack (?P<amt>{_N})\.")
def _rebought(m: re.Match) -> Event:
    return Event("rebought", player=_player(m), amount=_amount(m.group("amt")))


@_rule(
    rf"The player {_P} changed the ID from (?P<old>[A-Za-z0-9_-]+) to (?P<new>[A-Za-z0-9_-]+) "
    r"because (?P<why>.+)\."
)
def _id_changed(m: re.Match) -> Event:
    # Which ID the token carries is unverified; the "from X to Y" clause is authoritative.
    return Event(
        "id_changed",
        player=Player(m.group("name"), m.group("new")),
        other=Player(m.group("name"), m.group("old")),
        detail=m.group("why"),
    )


def parse_line(text: str) -> Event:
    """
    The event for one log entry, or an "unknown" event carrying the text. Unknown lines are
    surfaced (never guessed at) so the grammar can be extended from real examples.
    """
    entry = text.rstrip()
    for pattern, build in _RULES:
        m = pattern.fullmatch(entry)
        if m:
            try:
                return build(m)
            except ValueError:
                break
    return Event("unknown", detail=entry)
