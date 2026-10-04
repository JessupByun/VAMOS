"""
What Claude is sent for a postflop decision, kept short on purpose: a role, the user's
profiles, and the hand. Claude's judgment does the rest; there is no strategy guide.

The system prompt is a cached prefix: the role and every profile, pseudonymized. It holds
every profile rather than only the players seated, so names mentioned inside one profile
are hidden too and the prefix stays byte-identical while players come and go; it changes
only when a profile file does. Each decision is then one user message with the hand so far:
the full history in order (board cards where they were dealt) and exact numbers computed
from it. Every request is a fresh conversation rather than a growing one per hand: the
expensive part is the cached prefix either way.
"""

import json
from dataclasses import dataclass

from vamos.brain.pseudonyms import Pseudonyms
from vamos.log.hands import DEAD_POSTS, Hand, Street
from vamos.profiles import ProfileSet

CONTRACT = """\
You are VAMOS, an AI assistant for a private No-Limit Hold'em cash game among friends on
PokerNow: blinds 0.10/0.20, buy-ins of 10.00 (50 big blinds) with rebuys and top-ups, no
rake, usually four players. You advise one player, Hero, at the moment Hero must act after
the flop. Your job is to win Hero the most money by exploiting each opponent's specific
weaknesses, as described in their profile below. Where a profile says nothing about the
situation, use your own judgment.

Each message gives the hand so far: every player's position and stack, with tonight's read
on each opponent (counts from tonight's hands only, so weigh them by the number of hands) and
Hero's own notes on them when Hero wrote any; every action in order with the board cards
where they were dealt; Hero's cards; and exact numbers computed from them (pot, price,
stacks, Hero's made hand, the cards that would complete Hero's draws). Trust those numbers
rather than recounting them. Amounts are in big blinds; an action's to_bb is that player's
total on the street so far, not the amount added.

Answer (the JSON shape is enforced):
- reason: one sentence, at most 25 words: what decides this spot.
- strategy: the actions with their frequencies; mix two actions only when they are close.
  size_pct_pot is a bet as a percentage of the pot, or a raise on top of calling as a
  percentage of the pot after calling (raising a 1bb bet into 6bb to 3bb is 25); use 0 for
  fold, check, call and allin. Only use actions listed as legal.
- plan: one sentence, at most 20 words: what to do next if the hand goes on. Empty when this
  action ends Hero's part.
- Speak to Hero as "you" and to everyone else by the names given (Player A, Player B, ...).
"""


@dataclass(frozen=True)
class Prefix:
    blocks: list[dict]  # the system prompt, cache breakpoint on the last block
    user_text: str  # the part the user wrote (profiles), after hiding, for the leak check

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.blocks)


def prefix(profiles: ProfileSet, pseudonyms: Pseudonyms, keys: set[str] | None = None) -> Prefix:
    """
    The cached part of every call: the role, then the profiles of `keys` (the players at the
    table; every profile when None). Seated profiles only, so heads-up sends one profile; the
    cache is written again when the set of profiled players at the table changes.
    """
    described = []
    chosen = [k for k in profiles.profiles if keys is None or k in keys]
    for key in sorted(chosen, key=lambda k: pseudonyms.label(k)):
        profile = profiles.profiles[key]
        # The body only: the frontmatter's names and PokerNow IDs are of no use to Claude.
        # Only the user's own text is hidden: a player called "river" must not rewrite the
        # role text.
        tag = f" ({pseudonyms.hide(profile.tag)})" if profile.tag else ""
        described.append(f"## {pseudonyms.label(key)}{tag}\n\n{pseudonyms.hide(profile.body)}")
    user_text = "\n\n".join(described)
    sections = [CONTRACT]
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
    player_notes: dict[str, str] | None = None,  # PokerNow ID -> Hero's notes, pseudonymized
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
            **({"hero_notes": player_notes[pid]} if player_notes and pid in player_notes else {}),
        }
        for pid in sorted(stacks, key=lambda p: list(positions).index(p) if p in positions else 99)
    ]
    # Everything in order: posts (dead ones too), actions, and each street's board where it
    # was dealt, so the history reads exactly as the hand went.
    history: list[dict] = []
    shown = {Street.FLOP: 3, Street.TURN: 4, Street.RIVER: 5}

    def deal(street: Street) -> None:
        history.append({"street": street.name.lower(), "board": list(hand.board[: shown[street]])})

    street = Street.PREFLOP
    for a in hand.actions:
        while a.street > street:
            street = Street(street + 1)
            deal(street)
        entry: dict = {"street": street.name.lower(), "player": name(a.player), "action": a.kind}
        if a.kind in DEAD_POSTS:
            entry["dead_bb"] = _bb(a.added, bb)
        elif a.to is not None and a.kind not in ("fold", "check"):
            entry["to_bb"] = _bb(a.to, bb)
        if a.all_in:
            entry["all_in"] = True
        history.append(entry)
    while street < hand.street:
        street = Street(street + 1)
        deal(street)
    spot = {
        "hero": {"name": "Hero", "cards": list(hand.hero_cards), "position": positions.get(hero)},
        "players": players,
        "history": history,
        "board": list(hand.board),
        "facts": facts,
        "legal_actions": legal,
    }
    # Compact: indenting cost about 30% more input tokens for the same content.
    body = json.dumps(spot, separators=(",", ":"))
    text = f"Hand so far (amounts in big blinds):\n{body}\n"
    if notes.strip():
        text += f"\nHero's notes for tonight: {notes.strip()}\n"
    return text + "\nWhat should Hero do?"
