"""
What Claude is sent for a postflop decision.

The system prompt is a cached prefix: the contract, the strategy guide and every profile,
all pseudonymized. It holds every profile rather than only the players seated, so names
mentioned inside one profile are hidden too and the prefix stays byte-identical while
players come and go; it changes only when a profile file does. Each decision is then one
user message describing the hand so far. Every request is a fresh conversation rather than
a growing one per hand: the expensive part is the cached prefix either way, and fresh
requests never have to replay earlier answers' thinking blocks.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from vamos.brain.pseudonyms import Pseudonyms
from vamos.log.hands import Hand, Street
from vamos.profiles import ProfileSet

GUIDE = Path(__file__).parent / "strategy_guide.md"

CONTRACT = """\
You are VAMOS, a poker coach sitting behind one player (called Hero) in a private online
No-Limit Hold'em cash game among friends, blinds 0.10/0.20, mostly four-handed. You see the
hand so far and recommend Hero's action right now.

How to decide:
- Start from sound, balanced play, then exploit the opponents' tendencies where their
  profiles give real evidence for it. Weigh a tendency by its sample size: a pattern from
  a handful of hands is a nudge, not a rule.
- The engine's facts in the message (pot, price, stack depths, Hero's made hand) are exact.
  Equities there are against random hands, not the opponents' ranges; adjust for the
  ranges their actions and profiles imply.
- next_card_improves_hero_to lists every card that would give Hero a straight, a flush, or a
  full house or better. Hero has no other such draws: never claim one it does not list, and
  take outs from its counts rather than counting them yourself.
- Give a mixed strategy only where mixing is genuinely right; a clear spot gets one action
  at 100%.

How to answer (the JSON shape is enforced):
- strategy: the actions you recommend with their frequencies. For a bet, size_pct_pot is the
  bet as a percentage of the current pot; for a raise, the raise on top of calling, as a
  percentage of the pot after calling; use 0 for fold, check, call and allin. Only use
  actions listed as legal.
- note: what Hero should know, in the voice of a sharp friend. Lead with the reason that
  decides the spot, name the opponent's specific tendency when it matters, and plan the next
  street if that is useful. As short as the spot allows; no filler, no generic warnings.
- In the note, speak to Hero as "you"; refer to everyone else only by the names given
  (Player A, Player B, ...).
"""


@dataclass(frozen=True)
class Prefix:
    blocks: list[dict]  # the system prompt, cache breakpoint on the last block
    user_text: str  # the part the user wrote (profiles), after hiding, for the leak check

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.blocks)


def prefix(profiles: ProfileSet, pseudonyms: Pseudonyms) -> Prefix:
    guide = GUIDE.read_text() if GUIDE.exists() else ""
    sections = [CONTRACT]
    if guide:
        sections.append("# Strategy guide for this game\n\n" + guide)
    described = []
    for key in sorted(profiles.profiles, key=lambda k: pseudonyms.label(k)):
        profile = profiles.profiles[key]
        # The body only: the frontmatter's names and PokerNow IDs are of no use to Claude.
        # Only the user's own text is hidden: a player called "river" must not rewrite
        # the guide.
        tag = f" ({pseudonyms.hide(profile.tag)})" if profile.tag else ""
        described.append(f"## {pseudonyms.label(key)}{tag}\n\n{pseudonyms.hide(profile.body)}")
    user_text = "\n\n".join(described)
    if described:
        sections.append("# Opponent profiles\n\n" + user_text)
    blocks = [{"type": "text", "text": s} for s in sections]
    # One hour, not five minutes: answers use structured output, which rules out the
    # zero-token keep-warm request, and breaks between hands can run past five minutes.
    blocks[-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    return Prefix(blocks, user_text)


def _bb(cents: int, big_blind: int) -> float:
    return round(cents / big_blind, 2)


def spot_message(
    hand: Hand,
    hero: str,
    names: dict[str, str],  # PokerNow ID -> pseudonym ("Hero" for the hero)
    positions: dict[str, str],
    facts: dict,
    legal: list[str],
    tonight: dict[str, dict] | None = None,
    notes: str = "",  # already pseudonymized
) -> str:
    """The hand so far from Hero's seat, as JSON inside a short user message."""
    bb = hand.big_blind or 1
    stacks = {s.player.id: s.stack for s in hand.seats}
    folded = hand.folded()

    def name(pid: str) -> str:
        return names.get(pid, "another player")

    players = [
        {
            "name": name(pid),
            "position": positions.get(pid),
            "stack_bb": _bb(stacks[pid], bb),
            "behind_bb": _bb(stacks[pid] - hand.put_in.get(pid, 0), bb),
            **({"folded": True} if pid in folded else {}),
            **({"tonight": tonight[pid]} if tonight and pid in tonight else {}),
        }
        for pid in sorted(stacks, key=lambda p: list(positions).index(p) if p in positions else 99)
    ]
    actions = [
        {
            "street": Street(a.street).name.lower(),
            "player": name(a.player),
            "action": a.kind,
            **(
                {"to_bb": _bb(a.to, bb)}
                if a.to is not None and a.kind not in ("fold", "check")
                else {}
            ),
            **({"all_in": True} if a.all_in else {}),
        }
        for a in hand.actions
        if a.kind in ("post_sb", "post_bb", "call", "bet", "raise", "check", "fold")
    ]
    spot = {
        "hero": {"name": "Hero", "cards": list(hand.hero_cards), "position": positions.get(hero)},
        "players": players,
        "actions": actions,
        "board": list(hand.board),
        "facts": facts,
        "legal_actions": legal,
    }
    body = json.dumps(spot, indent=1, sort_keys=False)
    text = f"Hand so far (amounts in big blinds):\n{body}\n"
    if notes.strip():
        text += f"\nHero's notes for tonight: {notes.strip()}\n"
    return text + "\nWhat should Hero do?"
