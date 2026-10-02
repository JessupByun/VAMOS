"""
Pseudonyms for everything sent to Claude: no real name, alias or PokerNow nickname may
reach the API.

Each player at the table gets a stable pseudonym for the session (Player A, Player B, ...,
in the order they are first seen), and the hero's own names become "Hero". Every known name
for a person (profile name, aliases, every PokerNow nickname seen this session) is replaced
in all prompt text, profile bodies included. `check` then refuses any text that still
contains one, and `reveal` maps Claude's answer back to real names for the card.

Names shorter than two characters are not substituted (a player called "A" would rewrite
every article), nor are names that are pseudonyms themselves ("Hero", "Player B"); they are
reported so the user can rename or alias them. A hero who plays as "Hero" is no leak.
"""

import re
from dataclasses import dataclass, field

HERO = "Hero"
MIN_LENGTH = 2
_PSEUDONYM = re.compile(rf"{HERO}|Player [A-Z]", re.IGNORECASE)


class LeakError(Exception):
    """A prompt still contains a real name after substitution."""


def _pattern(name: str) -> re.Pattern[str]:
    # Whole-word, case-insensitive; names may contain characters regex treats specially.
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", re.IGNORECASE)


@dataclass
class Pseudonyms:
    """One table's pseudonyms for a session; keep the same instance for the whole session."""

    hero_names: set[str] = field(default_factory=set)
    # person key (profile key, or PokerNow ID for players without a profile) -> pseudonym
    _labels: dict[str, str] = field(default_factory=dict)
    # person key -> every name known for them
    _names: dict[str, set[str]] = field(default_factory=dict)
    # person key -> the name to show for them in Claude's answer
    _shown: dict[str, str] = field(default_factory=dict)
    unusable: set[str] = field(default_factory=set)  # too short, or a pseudonym

    def add(self, person: str, names: set[str] | list[str] | tuple[str, ...], shown: str) -> str:
        """Register a person (once their first name is known) and return their pseudonym."""
        if person not in self._labels:
            self._labels[person] = f"Player {chr(ord('A') + len(self._labels))}"
            self._names[person] = set()
        self._shown[person] = shown
        for name in names:
            self._remember(self._names[person], name.strip())
        return self._labels[person]

    def add_hero_name(self, name: str) -> None:
        if name.strip().lower() != HERO.lower():
            self._remember(self.hero_names, name.strip())

    def _remember(self, into: set[str], name: str) -> None:
        if not name:
            return
        if len(name) < MIN_LENGTH or _PSEUDONYM.fullmatch(name):
            self.unusable.add(name)
            return
        into.add(name)

    def merge(self, old: str, new: str) -> None:
        """
        `old` turned out to be `new` (a player mapped to a profile mid-session): their names
        now hide as `new`'s pseudonym. `old` keeps its letter so no one else's changes.
        """
        if old in self._names and new in self._names and old != new:
            self._names[new] |= self._names[old]
            self._names[old] = set()

    def knows(self, person: str) -> bool:
        return person in self._labels

    def label(self, person: str) -> str:
        return self._labels[person]

    def _replacements(self) -> list[tuple[str, str]]:
        pairs = [(name, HERO) for name in self.hero_names]
        for person, names in self._names.items():
            pairs += [(name, self._labels[person]) for name in names]
        # Longest first, so "bobby" is replaced before "bob" can split it.
        return sorted(pairs, key=lambda p: -len(p[0]))

    def hide(self, text: str) -> str:
        for name, label in self._replacements():
            text = _pattern(name).sub(label, text)
        return text

    def check(self, text: str) -> None:
        """Raise LeakError if any known name survives in `text`."""
        found = sorted({name for name, _ in self._replacements() if _pattern(name).search(text)})
        if found:
            # The names themselves are not put in the message: it ends up in logs.
            raise LeakError(f"{len(found)} real name(s) left in a prompt; the call was blocked")

    def reveal(self, text: str) -> str:
        """Claude's answer with pseudonyms turned back into names for the card."""
        for person in sorted(self._labels, key=lambda p: -len(self._labels[p])):
            # A function, so a name with a backslash is not read as an escape.
            text = re.sub(
                rf"\b{re.escape(self._labels[person])}\b",
                lambda _, shown=self._shown[person]: shown,
                text,
            )
        # Claude is asked to say "you"; this covers the times it says "Hero" anyway.
        text = re.sub(rf"\b{HERO}'s\b", "your", text)
        text = re.sub(rf"\b{HERO}\b", "you", text)
        return re.sub(
            r"(^|[.!?]\s+)(you|your)\b", lambda m: m.group(1) + m.group(2).capitalize(), text
        )
