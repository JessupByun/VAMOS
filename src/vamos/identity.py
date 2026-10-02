"""
Who is behind each PokerNow player ID.

A profile's `player_id` is authoritative. IDs no profile lists (a friend on a new device,
a stranger) are mapped once through the "who is this?" prompt and remembered in VAMOS's
own data/identity.json, never in the user's profiles.
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
    # "unknown": never seen or never answered, so the UI should ask.
    kind: Literal["profile", "none", "unknown"]
    profile: str | None = None


class IdentityMap:
    def __init__(self, path: Path):
        self.path = path
        # pn_id -> {"profile": key or None, "names": [nicknames seen]}
        self._entries: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}

    def resolve(self, pn_id: str, profiles: ProfileSet) -> Resolution:
        owner = profiles.by_player_id(pn_id)
        if owner is not None:
            return Resolution("profile", owner.key)
        entry = self._entries.get(pn_id)
        if entry is None or "profile" not in entry:
            return Resolution("unknown")
        key = entry["profile"]
        if key is None:
            return Resolution("none")
        # The answer may point at a profile that was since renamed or deleted.
        return Resolution("profile", key) if key in profiles.profiles else Resolution("unknown")

    def assign(self, pn_id: str, profile: str | None) -> None:
        """Record the user's answer: a profile key, or None for "no profile"."""
        self._entries.setdefault(pn_id, {"names": []})["profile"] = profile
        self._save()

    def observe(self, pn_id: str, name: str) -> None:
        """Remember a nickname seen with this ID, so the panel can show who it was."""
        names = self._entries.setdefault(pn_id, {"names": []})["names"]
        if name not in names:
            names.append(name)
            self._save()

    def names_seen(self, pn_id: str) -> list[str]:
        return list(self._entries.get(pn_id, {}).get("names", []))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._entries, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
