"""
Who is behind each PokerNow player ID.

A profile's `player_id` is authoritative. IDs no profile lists (a friend on a new device,
a stranger) are mapped once through the "who is this?" prompt and remembered in VAMOS's
own data/identity.json, never in the user's profiles. Only the answers are stored.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vamos.profiles import ProfileSet


@dataclass(frozen=True)
class Resolution:
    # "profile": a known person; "none": the user said this ID has no profile;
    # "unknown": never answered, so the UI should ask.
    kind: Literal["profile", "none", "unknown"]
    profile: str | None = None
    # Where the answer came from: a profile's player_id (authoritative, not editable in
    # VAMOS) or the user's answer to "who is this?".
    source: Literal["player_id", "answer"] | None = None


class IdentityMap:
    def __init__(self, path: Path):
        self.path = path
        # pn_id -> profile key, or None for "no profile"
        self._answers: dict[str, str | None] = json.loads(path.read_text()) if path.exists() else {}

    def resolve(self, pn_id: str, profiles: ProfileSet) -> Resolution:
        owner = profiles.by_player_id(pn_id)
        if owner is not None:
            return Resolution("profile", owner.key, "player_id")
        if pn_id not in self._answers:
            return Resolution("unknown")
        key = self._answers[pn_id]
        if key is None:
            return Resolution("none", source="answer")
        # The answer may point at a profile that was since renamed or deleted.
        if key not in profiles.profiles:
            return Resolution("unknown")
        return Resolution("profile", key, "answer")

    def assign(self, pn_id: str, profile: str | None) -> None:
        """Record the user's answer: a profile key, or None for "no profile"."""
        self._answers[pn_id] = profile
        self._save()

    def forget(self, pn_id: str) -> None:
        """Drop an answer, so the table asks again."""
        if self._answers.pop(pn_id, "missing") != "missing":
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._answers, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
