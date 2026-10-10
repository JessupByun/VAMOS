"""
Pseudonyms for everything sent to Claude: no real name, alias or PokerNow nickname may
reach the API.

Each player at the table gets a stable pseudonym for the session (Player A, Player B, ...,
in the order they are first seen), and the hero's own names become "Hero". Every known name
for a person (profile name, aliases, every PokerNow nickname seen this session) is replaced
in the user's own words (notes). Profile bodies are hidden by profile names, aliases and the
hero's names only: the rulebook keeps nicknames out of profiles, and friends play under
nicknames such as "67" or "trips" that would otherwise rewrite "VPIP 67%" or "slowplays
trips". `check` then refuses any text that still contains a name, and `reveal` maps
Claude's answer back to real names for the card.

Names shorter than two characters are not substituted (a player called "A" would rewrite
every article), nor are names without a letter ("67" would rewrite numbers) or names that
are pseudonyms themselves ("Hero", "Player", "Player B"); they are reported so the user can
rename or alias them. A hero who plays as "Hero" is no leak.
"""

import re
from dataclasses import dataclass, field

HERO = "Hero"
MIN_LENGTH = 2
_PSEUDONYM = re.compile(rf"{HERO}|Player( [A-Z])?", re.IGNORECASE)
_LETTER = re.compile(r"[^\W\d_]")


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
    # person key -> their profile name and aliases
    _names: dict[str, set[str]] = field(default_factory=dict)
    # person key -> the PokerNow nicknames seen for them
    _nicknames: dict[str, set[str]] = field(default_factory=dict)
    # person key -> the name to show for them in Claude's answer
    _shown: dict[str, str] = field(default_factory=dict)
    unusable: set[str] = field(default_factory=set)  # too short, or a pseudonym

    def add(
        self,
        person: str,
        names: set[str] | list[str] | tuple[str, ...],
        shown: str,
        nickname: bool = False,
    ) -> str:
        """
        Register a person (once their first name is known) and return their pseudonym.
        `nickname` marks PokerNow nicknames, which are hidden in notes but not in profiles.
        """
        if person not in self._labels:
            self._labels[person] = f"Player {chr(ord('A') + len(self._labels))}"
            self._names[person] = set()
            self._nicknames[person] = set()
        self._shown[person] = shown
        into = self._nicknames[person] if nickname else self._names[person]
        for name in names:
            self._remember(into, name.strip())
        return self._labels[person]

    def add_hero_name(self, name: str) -> None:
        if name.strip().lower() != HERO.lower():
            self._remember(self.hero_names, name.strip())

    def _remember(self, into: set[str], name: str) -> None:
        if not name:
            return
        if len(name) < MIN_LENGTH or not _LETTER.search(name) or _PSEUDONYM.fullmatch(name):
            self.unusable.add(name)
            return
        into.add(name)

    def merge(self, old: str, new: str) -> None:
        """
        `old` turned out to be `new` (a player mapped to a profile mid-session): their names
        now hide as `new`'s pseudonym. `old` keeps its letter so no one else's changes.
        """
        if old in self._names and new in self._names and old != new:
            for names in (self._names, self._nicknames):
                names[new] |= names[old]
                names[old] = set()

    def knows(self, person: str) -> bool:
        return person in self._labels

    def label(self, person: str) -> str:
        return self._labels[person]

    def _replacements(self, nicknames: bool) -> dict[str, str]:
        """Each name to hide (lowercase) -> its pseudonym; the first registered wins."""
        pairs = [(name, HERO) for name in self.hero_names]
        for person, names in self._names.items():
            pairs += [(name, self._labels[person]) for name in names]
            if nicknames:
                pairs += [(name, self._labels[person]) for name in self._nicknames[person]]
        found: dict[str, str] = {}
        for name, label in pairs:
            found.setdefault(name.lower(), label)
        return found

    def hide(self, text: str, nicknames: bool = True) -> str:
        """
        `text` with every name replaced in one pass, so a pseudonym already put in is never
        matched again; longest first, so "bobby" is replaced before "bob" can split it.
        """
        names = self._replacements(nicknames)
        if not names:
            return text
        alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        pattern = re.compile(rf"(?<![A-Za-z0-9])(?:{alternatives})(?![A-Za-z0-9])", re.IGNORECASE)
        # A match the lowercase key misses (rare Unicode case rules) is left for `check`.
        return pattern.sub(lambda m: names.get(m.group(0).lower(), m.group(0)), text)

    def check(self, text: str, nicknames: bool = True) -> None:
        """Raise LeakError if any known name survives in `text`."""
        found = sorted({n for n in self._replacements(nicknames) if _pattern(n).search(text)})
        if found:
            # The names themselves are not put in the message: it ends up in logs.
            raise LeakError(f"{len(found)} real name(s) left in a prompt; the call was blocked")

    def reveal(self, text: str) -> str:
        """Claude's answer with pseudonyms turned back into names for the card."""
        return reveal(text, {self._labels[p]: self._shown[p] for p in self._labels})


def reveal(text: str, shown: dict[str, str]) -> str:
    """`text` with each pseudonym in `shown` ("Player A") turned into its name."""
    for label in sorted(shown, key=len, reverse=True):
        # A function, so a name with a backslash is not read as an escape.
        text = re.sub(rf"\b{re.escape(label)}\b", lambda _, name=shown[label]: name, text)
    # Claude is asked to say "you"; this covers the times it says "Hero" anyway.
    text = re.sub(rf"\b{HERO}'s\b", "your", text)
    text = re.sub(rf"\b{HERO}\b", "you", text)
    return re.sub(r"(^|[.!?]\s+)(you|your)\b", lambda m: m.group(1) + m.group(2).capitalize(), text)
