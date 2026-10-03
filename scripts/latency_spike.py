"""
How fast each preset answers a real-sized postflop request, and whether the cache works.

Each preset is called twice with the same prefix (about 15k tokens: the contract, the
strategy guide and four made-up profiles, so no real profile is sent): the first call
writes the cache, the second should read it. Reports the time to the first event, to the
first answer text and to the end, the cache counts and the cost, and saves the results to
data/latency/. Spends real money (a few cents per run).

Run: uv run python scripts/latency_spike.py [preset ...]
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic

from vamos.brain.costs import price
from vamos.brain.prompt import prefix
from vamos.brain.provider import PRESETS, Request, api_key, params
from vamos.brain.pseudonyms import Pseudonyms
from vamos.config import DATA_DIR
from vamos.profiles import ProfileSet, parse_profile

TARGET_TOKENS = 15_000

PARAGRAPH = (
    "Opens wide from the button and calls 3-bets out of position with suited hands. "
    "Continuation bets small on dry boards and gives up on the turn without equity. "
    "Calls one street with any pair, rarely raises the flop without two pair or better. "
    "River bets are value-heavy; the few bluffs come after a missed flush draw. "
    "Tilts after losing a big pot and starts calling down lighter for half an hour.\n"
)

SPOT = """Hand so far (amounts in big blinds):
{"hero": {"name": "Hero", "cards": ["Ah", "Qd"], "position": "BTN"},
 "players": [{"name": "Player A", "position": "SB", "stack_bb": 50.0, "behind_bb": 44.5},
  {"name": "Player B", "position": "BB", "stack_bb": 50.0, "behind_bb": 49.0, "folded": true},
  {"name": "Hero", "position": "BTN", "stack_bb": 62.5, "behind_bb": 60.0}],
 "history": [{"street": "preflop", "player": "Player A", "action": "post_sb", "to_bb": 0.5},
  {"street": "preflop", "player": "Player B", "action": "post_bb", "to_bb": 1.0},
  {"street": "preflop", "player": "Hero", "action": "raise", "to_bb": 2.5},
  {"street": "preflop", "player": "Player A", "action": "call", "to_bb": 2.5},
  {"street": "preflop", "player": "Player B", "action": "fold"},
  {"street": "flop", "board": ["Qs", "8h", "4c"]},
  {"street": "flop", "player": "Player A", "action": "bet", "to_bb": 3.0}],
 "board": ["Qs", "8h", "4c"],
 "facts": {"street": "flop", "pot_bb": 9.0, "to_call_bb": 3.0, "equity_needed_to_call_pct": 25,
  "effective_stack_bb": 47.5, "hero_acts_last": true, "hero_hand": "top pair, top kicker",
  "next_card_improves_hero_to": {}, "opponents_in_hand": 1,
  "bet_faced": {"by": "Player A", "to_bb": 3.0, "pot_before_it_bb": 6.0, "pct_of_pot": 50,
  "all_in": false}, "min_raise_to_bb": 6.0},
 "legal_actions": ["fold", "call", "raise", "allin"]}

What should Hero do?"""


def made_up_profiles(tokens: int) -> ProfileSet:
    per_profile = tokens // 4
    body = PARAGRAPH * max(1, per_profile * 4 // len(PARAGRAPH))
    profiles = {
        key: parse_profile(key, f"---\nname: {key}\ntag: test\n---\n{body}")
        for key in ("alpha", "bravo", "charlie", "delta")
    }
    return ProfileSet(profiles, ())


def one_call(client: anthropic.Anthropic, request: Request) -> dict:
    started = time.monotonic()
    first_event = first_text = None
    with client.messages.stream(**params(request)) as stream:
        for event in stream:
            now = time.monotonic() - started
            first_event = first_event if first_event is not None else now
            delta = getattr(event, "delta", None)
            if first_text is None and getattr(delta, "type", None) == "text_delta":
                first_text = now
        final = stream.get_final_message()
    total = time.monotonic() - started
    cost = price(request.preset.model, final.usage, "latency", datetime.now())
    return {
        "first_event_s": round(first_event or total, 2),
        "first_text_s": round(first_text or total, 2),
        "total_s": round(total, 2),
        "stop_reason": final.stop_reason,
        "input": cost.input_tokens,
        "output": cost.output_tokens,
        "cache_read": cost.cache_read_tokens,
        "cache_write": cost.cache_write_5m_tokens + cost.cache_write_1h_tokens,
        "dollars": cost.dollars,
        "answer": "".join(b.text for b in final.content if b.type == "text"),
    }


def main() -> None:
    key = api_key()
    if not key:
        sys.exit("[VAMOS] no ANTHROPIC_API_KEY in the environment or .env")
    chosen = sys.argv[1:] or list(PRESETS)
    client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=120)
    names = Pseudonyms()
    profiles = made_up_profiles(TARGET_TOKENS)
    for key_ in sorted(profiles.profiles):
        names.add(key_, [key_], shown=key_)
    system = prefix(profiles, names)
    print(f"[VAMOS] prefix: about {len(system.text) // 4} tokens")
    results = {}
    for name in chosen:
        request = Request(PRESETS[name], system.blocks, [{"role": "user", "content": SPOT}])
        runs = [one_call(client, request) for _ in range(2)]
        results[name] = runs
        for i, run in enumerate(runs, 1):
            print(
                f"{name:9} call {i}: first event {run['first_event_s']}s, answer text "
                f"{run['first_text_s']}s, done {run['total_s']}s, cache read {run['cache_read']}, "
                f"write {run['cache_write']}, ${run['dollars']:.4f}"
            )
    out = DATA_DIR / "latency" / f"{datetime.now():%Y-%m-%dT%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(f"[VAMOS] saved {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")


if __name__ == "__main__":
    main()
