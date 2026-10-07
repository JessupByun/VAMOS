"""
Exact per-player counts and hand records from saved games, for updating the opponent
profiles (profiles/_GUIDELINES.md defines every stat). scripts/prepare_profile_update.py
writes them to a work folder; the update itself runs as a Claude Code workflow on the
user's subscription, never through VAMOS's API calls.

Hands are split into ring (3 or more players dealt in) and heads-up (2), per session.
Players are named by profile name ("jes" for the hero); an account no profile claims is
"other player A", "other player B", ... so no nickname reaches the records.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vamos.engine.madehand import describe
from vamos.engine.state import positions
from vamos.history import net
from vamos.log.hands import DEAD_POSTS, Hand, Street
from vamos.session import GameSession

SUITS = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}


@dataclass(frozen=True)
class SessionSpec:
    label: str
    game: str
    date: str
    path: Path
    after: int = 0  # hands numbered up to this were counted already


@dataclass
class Ratio:
    n: int = 0
    d: int = 0

    def add(self, hit: bool) -> None:
        self.d += 1
        self.n += bool(hit)

    def __str__(self) -> str:
        return f"{round(100 * self.n / self.d) if self.d else 0}% ({self.n}/{self.d})"


@dataclass
class PlayerData:
    hands: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    net_cents: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    stats: dict[str, dict[str, Ratio]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Ratio))
    )
    net_by_position: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    sizes: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    opens_bb: list[float] = field(default_factory=list)
    shown: list[str] = field(default_factory=list)
    digest: list[str] = field(default_factory=list)


Who = Callable[[str, str], str | None]  # (account, nickname) -> profile name, "jes" or None


def _card(c: str) -> str:
    return ("10" if c[0] == "T" else c[0]) + SUITS[c[1]]


def _cards(cs: Iterable[str]) -> str:
    return " ".join(_card(c) for c in cs)


def _bb(cents: int, big: int) -> str:
    return f"{cents / big:.2f}".rstrip("0").rstrip(".") + "bb"


def collect(sessions: list[SessionSpec], who: Who) -> tuple[dict[str, PlayerData], dict]:
    """Every player's counts and hand records over `sessions`, and the accounts no one claims."""
    players: dict[str, PlayerData] = defaultdict(PlayerData)
    unknown: dict[str, dict[str, Any]] = {}
    for spec in sessions:
        log = GameSession(spec.game, spec.path).log
        others: dict[str, str] = {}  # this game's unclaimed accounts -> "other player A", ...
        for hand in log.hands:
            if not hand.ended or hand.number <= spec.after:
                continue
            names = {}
            for seat in hand.seats:
                pid = log.current_id(seat.player.id)
                claimed = who(pid, seat.player.name)
                if not claimed:
                    others.setdefault(pid, f"other player {chr(ord('A') + len(others))}")
                    seen = unknown.setdefault(pid, {"names": set(), "hands": defaultdict(int)})
                    seen["names"].add(seat.player.name)
                    seen["hands"][spec.label] += 1
                names[seat.player.id] = claimed or others[pid]
            _count(hand, spec.label, names, players)
    report = {
        pid: {"names": sorted(v["names"]), "hands": dict(v["hands"])} for pid, v in unknown.items()
    }
    return players, report


def _count(hand: Hand, label: str, names: dict[str, str], players: dict[str, PlayerData]) -> None:
    big = hand.big_blind or 1
    seats = [s.player.id for s in hand.seats]
    dealt = len(seats)
    bucket = f"{label}/{'ring' if dealt >= 3 else 'headsup'}"
    pos = positions(hand)
    folded_at = {a.player: a.street for a in hand.actions if a.kind == "fold"}
    showdown = len([p for p in seats if p not in folded_at]) >= 2
    saw_flop = {p for p in seats if hand.board and folded_at.get(p, Street.RIVER + 1) > 0}
    for pid in seats:
        data = players[names[pid]]
        data.hands[label] += 1
        data.net_cents[label] += net(hand, pid)
    _preflop(hand, bucket, names, players, pos, dealt, big)
    _postflop(hand, bucket, names, players)
    for pid in seats:
        s = players[names[pid]].stats[bucket]
        if pid in saw_flop:
            s["went_to_showdown"].add(showdown and pid not in folded_at)
        if showdown and pid not in folded_at:
            s["won_at_showdown"].add(hand.collected.get(pid, 0) > 0)
        if pid in hand.shows and len(hand.shows[pid]) == 2:
            players[names[pid]].shown.append(f"{label}{hand.number}")
    _digest(hand, label, names, pos, big, players)


def _preflop(hand, bucket, names, players, pos, dealt, big) -> None:
    raises = limpers = 0
    opener = None
    limped: set[str] = set()
    limp_answered: set[str] = set()
    acted: set[str] = set()
    vpip: set[str] = set()
    pfr: set[str] = set()
    for a in hand.actions:
        if a.street != Street.PREFLOP or a.kind.startswith("post") or a.kind in DEAD_POSTS:
            continue
        s = players[names[a.player]].stats[bucket]
        if a.kind in ("call", "bet", "raise"):
            vpip.add(a.player)
        if a.kind == "raise":
            pfr.add(a.player)
        if a.player not in acted:
            acted.add(a.player)
            if raises == 0 and limpers == 0 and a.kind != "check":
                s["open_raise_unopened"].add(a.kind == "raise")
                s["open_limp_unopened"].add(a.kind == "call")
                if a.kind == "raise" and dealt >= 3:
                    players[names[a.player]].opens_bb.append(round((a.to or 0) / big, 2))
            elif raises == 0 and limpers > 0:
                # The big blind's check is the other choice to an iso-raise.
                s["iso_raise_over_limps"].add(a.kind == "raise")
                if a.kind != "check":
                    s["over_limp"].add(a.kind == "call")
            elif raises == 1:
                s["three_bet_vs_open"].add(a.kind == "raise")
                s["cold_call_open"].add(a.kind == "call")
                s["fold_to_open"].add(a.kind == "fold")
            elif raises >= 2:
                s["cold_vs_3bet_fold"].add(a.kind == "fold")
                s["cold_vs_3bet_call"].add(a.kind == "call")
                s["cold_vs_3bet_4bet"].add(a.kind == "raise")
        elif a.player == opener and raises == 2:
            s["facing_3bet_fold"].add(a.kind == "fold")
            s["facing_3bet_call"].add(a.kind == "call")
            s["facing_3bet_4bet"].add(a.kind == "raise")
        elif a.player in limped and raises >= 1 and a.player not in limp_answered:
            limp_answered.add(a.player)
            s["limp_then_raised_call"].add(a.kind == "call")
            s["limp_then_raised_fold"].add(a.kind == "fold")
            s["limp_then_raised_reraise"].add(a.kind == "raise")
        if a.kind == "call" and raises == 0:
            limpers += 1
            limped.add(a.player)
        if a.kind == "raise":
            raises += 1
            if raises == 1:
                opener = a.player
    for pid in [s.player.id for s in hand.seats]:
        data = players[names[pid]]
        data.stats[bucket]["vpip"].add(pid in vpip)
        data.stats[bucket]["pfr"].add(pid in pfr)
        if pid in pos:
            data.stats[bucket][f"vpip_{pos[pid]}"].add(pid in vpip)
            data.net_by_position[f"{bucket}/{pos[pid]}"] += net(hand, pid)


def _postflop(hand, bucket, names, players) -> None:
    raisers = [a.player for a in hand.actions if a.street == Street.PREFLOP and a.kind == "raise"]
    aggressor = raisers[-1] if raisers else None
    pot_before: dict[int, int] = {}
    total = 0
    for a in hand.actions:
        pot_before[a.order] = total
        total += a.added
    for street in (Street.FLOP, Street.TURN, Street.RIVER):
        acts = [a for a in hand.actions if a.street == street]
        word = street.name.lower()
        bet_seen = aggressor_acted = False
        acted: set[str] = set()
        answered: set[str] = set()
        aggressed: set[str] = set()
        for a in acts:
            data = players[names[a.player]]
            s = data.stats[bucket]
            if a.player not in acted and not bet_seen and a.kind in ("check", "bet"):
                s[f"{word}_bet_when_checked_to"].add(a.kind == "bet")
                if street == Street.FLOP and a.player == aggressor:
                    s["flop_cbet"].add(a.kind == "bet")
                if (
                    street == Street.FLOP
                    and aggressor
                    and a.player != aggressor
                    and not aggressor_acted
                ):
                    s["flop_lead_into_raiser"].add(a.kind == "bet")
            # The first response to a bet on the street, after checking too; a response to a
            # raise of the player's own bet is not one.
            if (
                bet_seen
                and a.kind in ("fold", "call", "raise")
                and a.player not in answered
                and a.player not in aggressed
            ):
                answered.add(a.player)
                s[f"{word}_faced_bet_fold"].add(a.kind == "fold")
                s[f"{word}_faced_bet_call"].add(a.kind == "call")
                s[f"{word}_faced_bet_raise"].add(a.kind == "raise")
            if a.kind in ("bet", "raise"):
                aggressed.add(a.player)
                before = pot_before[a.order]
                data.sizes[f"{word}_{a.kind}"].append(
                    round(100 * a.added / before) if before else 0
                )
                bet_seen = True
            if a.player == aggressor:
                aggressor_acted = True
            acted.add(a.player)


def _digest(hand, label, names, pos, big, players) -> None:
    seats = [s.player.id for s in hand.seats]
    stacks = {s.player.id: s.stack for s in hand.seats}
    lines = [
        f"### {label}{hand.number} ({len(seats)}-handed) "
        + ", ".join(f"{names[p]} {pos.get(p, '?')} {_bb(stacks[p], big)}" for p in seats)
    ]
    if hand.hero_cards:
        lines.append(f"jes holds {_cards(hand.hero_cards)}")
    total = 0
    street = None
    for a in hand.actions:
        if a.street != street:
            street = a.street
            shown = {Street.FLOP: 3, Street.TURN: 4, Street.RIVER: 5}.get(street)
            if shown:
                board = _cards(hand.board[:shown])
                lines.append(f"{street.name.lower()} {board} (pot {_bb(total, big)})")
        who = names[a.player]
        if a.kind.startswith("post") or a.kind in DEAD_POSTS:
            text = f"{who} {a.kind.replace('_', ' ')}"
        elif a.kind in ("fold", "check"):
            text = f"{who} {a.kind}s"
        elif a.kind == "call":
            text = f"{who} calls {_bb(a.added, big)}"
        else:
            share = f", {round(100 * a.added / total)}% pot" if total and a.kind == "bet" else ""
            verb = "bets" if a.kind == "bet" else "raises to"
            text = f"{who} {verb} {_bb(a.to or 0, big)}{share}"
        lines.append("- " + text + (" all in" if a.all_in else ""))
        total += a.added
    for pid, cards in hand.shows.items():
        made = describe(cards, hand.board) if len(cards) == 2 and len(hand.board) >= 3 else ""
        lines.append(f"shows: {names[pid]} {_cards(cards)}{f' ({made})' if made else ''}")
    if hand.second_run or hand.second_board:
        lines.append(f"second board: {_cards(hand.second_run or hand.second_board)}")
    results = ", ".join(
        f"{names[p]} {'+' if net(hand, p) > 0 else ''}{_bb(net(hand, p), big)}"
        for p in seats
        if net(hand, p)
    )
    lines.append(f"result: {results}")
    text = "\n".join(lines)
    voluntary = {a.player for a in hand.actions if a.kind in ("call", "bet", "raise")}
    folded_pre = {a.player for a in hand.actions if a.kind == "fold" and a.street == Street.PREFLOP}
    for pid in seats:
        who = names[pid]
        # A hand the player only folded before the flop is left to the counts.
        if who != "jes" and (pid in voluntary or pid not in folded_pre or pid in hand.shows):
            players[who].digest.append(text)


def summary(data: PlayerData) -> dict[str, Any]:
    """One player's counts as the work folder's <player>.stats.json."""

    def total(kind: str) -> dict[str, str]:
        out: dict[str, Ratio] = defaultdict(Ratio)
        for bucket, stats in data.stats.items():
            if bucket.endswith("/" + kind):
                for key, r in stats.items():
                    out[key].n += r.n
                    out[key].d += r.d
        return {k: str(r) for k, r in sorted(out.items())}

    return {
        "hands_by_session": dict(data.hands),
        "net_dollars_by_session": {k: round(v / 100, 2) for k, v in data.net_cents.items()},
        "stats_by_session": {
            b: {k: str(r) for k, r in sorted(s.items())} for b, s in sorted(data.stats.items())
        },
        "stats_all_new_sessions": {kind: total(kind) for kind in ("ring", "headsup")},
        "net_dollars_by_position": {
            k: round(v / 100, 2) for k, v in sorted(data.net_by_position.items())
        },
        "preflop_open_sizes_bb_ring": sorted(data.opens_bb),
        "bet_sizes_pct_pot": {k: sorted(v) for k, v in sorted(data.sizes.items())},
        "showdown_hands": data.shown,
    }
