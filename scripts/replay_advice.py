"""
Replay logged spots (before or after the flop) through today's prompt and facts, to check a
change on real hands before playing with it.

Each spot in data/advice.jsonl is rebuilt into PokerNow log lines (from the hand JSON that
was sent, under the same pseudonyms), so the engine computes today's facts with today's
code; the system prompt holds today's profiles of the players in that spot, as live calls
do. The new answers are shown next to the logged ones and saved to data/replays/. Spends
real money (about 1-2 cents per spot on Opus 5.5 low); --dry-run prints a rebuilt message
without calling Claude.

Run: uv run python scripts/replay_advice.py [--preset standard|deep] [--effort low|medium]
     [--lines 5,6,7] [--dry-run]
"""

import argparse
import dataclasses
import json
import re
import threading
import time
from datetime import datetime

from vamos.brain import prompt
from vamos.brain.answer import BadAnswer, settle
from vamos.brain.costs import price
from vamos.brain.provider import PRESETS, AnthropicProvider, CallFailed, Request
from vamos.brain.pseudonyms import HERO, Pseudonyms
from vamos.brain.spot import betting, claude_facts, legal
from vamos.config import DATA_DIR, PROFILES_DIR
from vamos.engine.facts import facts as card_facts
from vamos.engine.state import decision, positions
from vamos.log.hands import build_hands
from vamos.profiles import load_profiles

BIG_BLIND = 0.20
SUITS = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
ORDER = ["SB", "BB", "UTG", "UTG+1", "LJ", "HJ", "CO", "BTN"]  # first to act after the button


def card(text: str) -> str:
    rank = "10" if text[0] == "T" else text[0]
    return rank + SUITS[text[1]]


def chips(bb: float) -> str:
    return f"{bb * BIG_BLIND:.2f}"


def rebuild(spot: dict) -> tuple[list[str], dict[str, str]]:
    """PokerNow log lines for the hand in a logged spot, and pseudonym -> synthetic ID."""
    players = spot["players"]
    by_position = sorted(players, key=lambda p: ORDER.index(p["position"]))
    if len(players) == 2:  # heads-up: the button posts the small blind and is seated first
        by_position = sorted(players, key=lambda p: p["position"] != "BTN")
    ids = {p["name"]: f"id{i:08d}" for i, p in enumerate(by_position)}
    who = {p["name"]: f'"{p["name"]} @ {ids[p["name"]]}"' for p in by_position}
    button = next(p["name"] for p in players if p["position"] == "BTN")
    stacks = " | ".join(
        f"#{i + 1} {who[p['name']]} ({chips(p['stack_bb'])})" for i, p in enumerate(by_position)
    )
    lines = [
        f"-- starting hand #1 (id: replay) No Limit Texas Hold'em (dealer: {who[button]}) --",
        f"Player stacks: {stacks}",
        "Your hand is " + ", ".join(card(c) for c in spot["hero"]["cards"]),
    ]
    board = spot["board"]
    street = "preflop"
    shown = {"flop": 3, "turn": 4, "river": 5}
    # Spots logged before the history format carry "actions" without board entries.
    for action in spot.get("history") or spot["actions"]:
        if "player" not in action:
            continue
        if action["street"] != street:
            street = action["street"]
            lines.append(street_line(street, board[: shown[street]]))
        name, kind = who[action["player"]], action["action"]
        amount = chips(action.get("to_bb", action.get("dead_bb", 0)))
        tail = " and go all in" if action.get("all_in") else ""
        lines.append(
            {
                "post_sb": f"{name} posts a small blind of {amount}{tail}",
                "post_bb": f"{name} posts a big blind of {amount}{tail}",
                "post_straddle": f"{name} posts a straddle of {amount}{tail}",
                "post_missed_bb": f"{name} posts a missed big blind of {amount}{tail}",
                "post_missing_sb": f"{name} posts a missing small blind of {amount}{tail}",
                "post_ante": f"{name} posts an ante of {amount}{tail}",
                "call": f"{name} calls {amount}{tail}",
                "bet": f"{name} bets {amount}{tail}",
                "raise": f"{name} raises to {amount}{tail}",
                "check": f"{name} checks",
                "fold": f"{name} folds",
            }[kind]
        )
    final = {3: "flop", 4: "turn", 5: "river"}.get(len(board))
    if final and final != street:
        lines.append(street_line(final, board))
    return lines, ids


def street_line(street: str, board: list[str]) -> str:
    cards = [card(c) for c in board]
    if street == "flop":
        return f"Flop:  [{', '.join(cards)}]"
    return f"{street.capitalize()}: {', '.join(cards[:-1])} [{cards[-1]}]"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", default="standard", choices=list(PRESETS))
    parser.add_argument("--effort", help="override the preset's effort (low, medium, high, ...)")
    parser.add_argument("--lines", help="1-based lines of data/advice.jsonl (default: all)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logged = [json.loads(line) for line in (DATA_DIR / "advice.jsonl").read_text().splitlines()]
    wanted = (
        {int(n) for n in args.lines.split(",")} if args.lines else set(range(1, len(logged) + 1))
    )
    profiles = load_profiles(PROFILES_DIR)
    names = Pseudonyms()
    settings = (
        json.loads((DATA_DIR / "settings.json").read_text())
        if (DATA_DIR / "settings.json").exists()
        else {}
    )
    for hero_name in settings.get("heroNames", []):
        names.add_hero_name(hero_name)
    for key in sorted(profiles.profiles):
        profile = profiles.profiles[key]
        names.add(key, [profile.name, *profile.aliases], shown=profile.name)
    provider = AnthropicProvider()
    results, spent = [], 0.0
    for number, entry in enumerate(logged, 1):
        if number not in wanted or not entry.get("spot"):
            continue
        text = entry["spot"]
        spot = json.loads(text[text.index("{") : text.rindex("}") + 1])
        # Live calls send only the profiles of the players at the table.
        present = {p["name"] for p in spot["players"]}
        seated = {key for key in profiles.profiles if names.label(key) in present}
        system = prompt.prefix(profiles, names, seated)
        notes = re.search(r"Hero's notes for tonight: (.*)\n", text)
        lines, ids = rebuild(spot)
        hand = build_hands((100 + i, line) for i, line in enumerate(lines)).hands[-1]
        hero = ids[HERO]
        state = decision(hand, hero)
        if state is None:
            print(f"line {number}: could not rebuild the decision, skipped")
            continue
        labels = {pid: name for name, pid in ids.items()}
        tonight = {ids[p["name"]]: p["tonight"] for p in spot["players"] if p.get("tonight")}
        player_notes = {
            ids[p["name"]]: p["hero_notes"] for p in spot["players"] if p.get("hero_notes")
        }
        message = prompt.spot_message(
            hand,
            hero,
            labels,
            positions(hand),
            claude_facts(hand, hero, state, card_facts(state), labels),
            legal(hand, hero, state),
            tonight,
            notes.group(1) if notes else "",
            player_notes,
        )
        if args.dry_run:
            print(f"--- line {number}\n{message}\n")
            continue
        preset = PRESETS[args.preset]
        if args.effort:
            preset = dataclasses.replace(preset, effort=args.effort)
        request = Request(preset, system.blocks, [{"role": "user", "content": message}])
        started = time.monotonic()
        try:
            reply = provider.run(request, threading.Event())
        except CallFailed as failure:
            print(f"line {number}: failed ({failure.reason})")
            continue
        seconds = round(time.monotonic() - started, 2)
        spent += price(request.preset.model, reply.usage, "replay", datetime.now()).dollars
        try:
            answer = settle(reply.text, betting(hand, hero, state), entry["node"], names.reveal)
            new = {
                "mix": [[c.action, c.to, round(c.freq, 2)] for c in answer.mix],
                "why": answer.reason,
                "plan": answer.plan,
            }
        except BadAnswer as error:
            new = {"error": str(error)}
        old_answer = json.loads(entry["answer"]) if entry.get("answer") else {}
        result = {
            "line": number,
            "logged_model": entry["model"],
            "board": spot["board"],
            "hero": spot["hero"]["cards"],
            "old": {"chosen": entry.get("chosen"), "strategy": old_answer.get("strategy")},
            "new": new,
            "seconds": seconds,
            "output_tokens": reply.usage.get("output_tokens"),
            "words": len(f"{new.get('why', '')} {new.get('plan', '')}".split()),
        }
        results.append(result)
        print(
            f"line {number:>2} {' '.join(spot['hero']['cards'])} on {' '.join(spot['board'])}: "
            f"was {entry.get('chosen')}, now {new.get('mix')} ({seconds}s, "
            f"{result['output_tokens']} tokens out, {result['words']} words)"
        )
        print(f"   why: {new.get('why')}\n   plan: {new.get('plan')}")
    if results:
        label = f"{args.preset}-{args.effort}" if args.effort else args.preset
        out = DATA_DIR / "replays" / f"{datetime.now():%Y-%m-%dT%H%M%S}-{label}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2) + "\n")
        print(f"[VAMOS] {len(results)} spots, ${spent:.3f}, saved {out}")


if __name__ == "__main__":
    main()
