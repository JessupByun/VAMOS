"""
Replay the preflop decisions VAMOS sends to Claude, from saved games, through Claude, to see
its answers in spots it has not met at the table yet.

Each decision is rebuilt from the game's rows up to that moment, and the prompt comes from
the backend's own builder (Backend.prompt_for), so it is what play would have sent: the
profiles of the players dealt in, pseudonyms, this session's read as it stood, the notes.
The answers are saved to data/replays/ next to what the hero actually did. Costs real money
(about a cent a spot), recorded in the cost ledger as "replay", which the side panel's costs
leave out; --dry-run only counts the spots and prints one prompt.
Run: uv run python scripts/replay_saved_preflop.py [--games DIR] [--min-players 3]
     [--limit N] [--first-in] [--budget DOLLARS] [--dry-run]
"""

import argparse
import json
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from vamos.brain.answer import BadAnswer, settle
from vamos.brain.costs import Ledger
from vamos.brain.provider import PRESETS, AnthropicProvider, CallFailed, FakeProvider, Request
from vamos.brain.pseudonyms import LeakError
from vamos.brain.spot import betting, legal
from vamos.config import DATA_DIR, PROFILES_DIR
from vamos.engine.advice import preflop_advice
from vamos.engine.facts import facts, format_bb
from vamos.engine.state import decision
from vamos.host.server import Backend
from vamos.log.hands import Street, build_hands
from vamos.session import GameSession


def hero_names() -> set[str]:
    path = DATA_DIR / "settings.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    return {str(n).strip().lower() for n in saved.get("heroNames", [])}


def did(action, bb: int) -> str:
    """What the hero actually did, e.g. "raise to 3bb"."""
    if action.kind in ("fold", "check"):
        return action.kind
    return f"{action.kind} {'to ' if action.kind == 'raise' else ''}{format_bb(action.to or 0, bb)}"


def spots(path: Path, names: set[str], min_players: int):
    """(rows before the decision, hand number, hero, the hero's action) for each Claude spot."""
    rows = sorted((int(e["order"]), e["msg"]) for e in map(json.loads, path.open()) if "order" in e)
    for hand in build_hands(rows).hands:
        hero = next(
            (s.player.id for s in hand.seats if s.player.name.strip().lower() in names), None
        )
        if hero is None or not hand.hero_cards or len(hand.seats) < min_players:
            continue
        for action in hand.actions:
            if action.street != Street.PREFLOP or action.player != hero:
                continue
            if action.kind not in ("fold", "check", "call", "raise"):
                continue
            yield [r for r in rows if r[0] < action.order], hand.number, hero, action


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=Path, default=DATA_DIR / "games")
    parser.add_argument("--min-players", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--preset", default="standard", choices=list(PRESETS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--budget", type=float, default=3.0, help="stop once this much is spent")
    parser.add_argument("--first-in", action="store_true", help="only spots nobody has entered")
    args = parser.parse_args()

    names = hero_names()
    ledger = Ledger(DATA_DIR / "costs.jsonl")
    out = DATA_DIR / "replays" / f"preflop-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    provider = AnthropicProvider()
    count = spent = 0
    with tempfile.TemporaryDirectory() as tmp:
        # The backend works on a copy, so nothing it writes touches the real data.
        data = Path(tmp) / "data"
        (data / "games").mkdir(parents=True)
        for name in ("identity.json", "settings.json"):
            if (DATA_DIR / name).exists():
                shutil.copy(DATA_DIR / name, data / name)
        for notes in args.games.glob("*.notes.json"):
            shutil.copy(notes, data / "games" / notes.name)
        backend = Backend(data, PROFILES_DIR, provider=FakeProvider([]))
        # Game logs only, not their decision records (<game>.decisions.jsonl).
        for path in sorted(p for p in args.games.glob("*.jsonl") if p.suffixes == [".jsonl"]):
            game_id = path.name.removesuffix(".jsonl")
            backend._load_notes(game_id)
            for before, number, hero, action in spots(path, names, args.min_players):
                session = GameSession(game_id)
                logs = [{"created_at": str(o), "msg": m} for o, m in reversed(before)]
                session.add_page({"logs": logs})
                backend.sessions[game_id] = session
                hand = session.log.hands[-1]
                spot = decision(hand, hero) if hand.number == number else None
                if spot is None or spot.street != Street.PREFLOP:
                    continue
                if preflop_advice(hand, spot, hero) is not None:
                    continue  # a rule or the chart answers; Claude is never asked
                entered = any(a.kind in ("call", "bet", "raise") for a in hand.actions)
                if args.first_in and entered:
                    continue
                if args.limit and count >= args.limit:
                    break
                count += 1
                card = facts(spot)
                system, message, pseudonyms, check = backend.prompt_for(
                    game_id, hand, hero, spot, card
                )
                try:
                    check()
                except LeakError as error:
                    print(f"{game_id} #{number}: skipped, {error}")
                    continue
                if args.dry_run:
                    if count == 1:
                        print(message)
                    continue
                if spent >= args.budget:
                    print(f"budget of ${args.budget:.2f} reached, stopping")
                    break
                # Back-to-back calls keep a 5-minute cache warm, at a cheaper write than 1 hour.
                blocks = [dict(b) for b in system.blocks]
                blocks[-1]["cache_control"] = {"type": "ephemeral", "ttl": "5m"}
                request = Request(
                    PRESETS[args.preset], blocks, [{"role": "user", "content": message}]
                )
                model = request.preset.model
                started = time.monotonic()
                record = {
                    "game": game_id,
                    "hand": number,
                    "node": spot.node,
                    "playersDealt": spot.players_dealt,
                    "position": spot.position,
                    "heroCards": list(spot.hero_cards),
                    "legal": legal(hand, hero, spot),
                    "pot": card["pot"],
                    "toCall": card["toCall"],
                    "did": did(action, spot.big_blind or 1),
                    "message": message,
                }
                cost = None
                try:
                    reply = provider.run(request, threading.Event())
                    cost = ledger.charge(model, reply.usage, "replay", datetime.now())
                    answer = settle(
                        reply.text, betting(hand, hero, spot), spot.node, pseudonyms.reveal
                    )
                    bb = spot.big_blind or 1
                    record |= {
                        "mix": [
                            [c.action, round(c.to / bb, 2), round(c.freq, 3)] for c in answer.mix
                        ],
                        "chosen": [answer.chosen.action, round(answer.chosen.to / bb, 2)],
                        "reason": answer.reason,
                        "plan": answer.plan,
                        "dollars": cost.dollars if cost else 0.0,
                    }
                except CallFailed as error:
                    # Billed for what Claude had read and written before it failed.
                    cost = ledger.charge(model, error.usage, "replay", datetime.now())
                    record["failed"] = str(error)
                except BadAnswer as error:
                    record["failed"] = str(error)
                spent += cost.dollars if cost else 0.0
                record["seconds"] = round(time.monotonic() - started, 2)
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("a") as f:
                    f.write(json.dumps(record) + "\n")
                print(
                    f"{count}: {game_id} #{number} {record.get('chosen') or record.get('failed')}"
                )
            if (args.limit and count >= args.limit) or spent >= args.budget:
                break
    print(
        f"{count} Claude preflop spots; spent ${spent:.2f}" + ("" if args.dry_run else f"; {out}")
    )


if __name__ == "__main__":
    main()
