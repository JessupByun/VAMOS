"""
Who is behind each PokerNow player ID.

A profile's `player_id` is authoritative. IDs no profile lists (a friend on a new device,
a stranger) are matched once through the "Match existing player profile" menu and
remembered in VAMOS's own data/identity.json, never in the user's profiles. Only the
answers are stored.
"""

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from vamos.profiles import ProfileSet

log = logging.getLogger("vamos.identity")


@dataclass(frozen=True)
class Resolution:
    # "profile": a known person; "none": the user said this ID has no profile;
    # "unknown": never answered, so the UI should ask.
    kind: Literal["profile", "none", "unknown"]
    profile: str | None = None
    # Where the answer came from: a profile's player_id (authoritative, not editable in
    # VAMOS) or the user's own match from that menu.
    source: Literal["player_id", "answer"] | None = None


class IdentityMap:
    def __init__(self, path: Path, now: Callable[[], datetime] = datetime.now):
        self.path = path
        # Why the saved answers could not be used, for the side panel; None when they could.
        self.problem: str | None = None
        # pn_id -> profile key, or None for "no profile"
        self._answers: dict[str, str | None] = self._load(now)

    def _load(self, now: Callable[[], datetime]) -> dict[str, str | None]:
        if not self.path.exists():
            return {}
        try:
            answers = json.loads(self.path.read_text())
            if not isinstance(answers, dict) or not all(
                v is None or isinstance(v, str) for v in answers.values()
            ):
                raise ValueError("not a map of PokerNow IDs to profile names")
            return answers
        except (OSError, ValueError) as error:
            reason = error
        # A damaged file must not stop the backend: it is moved aside under a new name, for a
        # look later, and VAMOS starts as if no answer had been saved.
        kept = self.path.with_name(f"{self.path.stem}.damaged-{now():%Y%m%d-%H%M%S}.json")
        where = self.path.parent.name
        try:
            os.replace(self.path, kept)
            outcome = f"the old file is kept as {where}/{kept.name}"
        except OSError:
            log.exception("[VAMOS] could not move %s aside", self.path)
            outcome = f"it could not be moved aside (see {where}/logs/host.log)"
        log.error(
            "[VAMOS] could not read %s: %s; starting with no saved matches", self.path, reason
        )
        self.problem = (
            f"{where}/{self.path.name} was damaged, so the players you matched to profiles "
            f"will be asked again; {outcome}"
        )
        return {}

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
        self._reread()
        self._answers[pn_id] = profile
        self._save()

    def forget(self, pn_id: str) -> None:
        """Drop an answer, so the table asks again."""
        self._reread()
        if self._answers.pop(pn_id, "missing") != "missing":
            self._save()

    def _reread(self) -> None:
        # The file may have changed since it was loaded (the profile update records the
        # accounts the user names); writing back an old copy would undo that.
        try:
            answers = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(answers, dict) and all(
            v is None or isinstance(v, str) for v in answers.values()
        ):
            self._answers = answers

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._answers, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
