"""
Opponent profiles: the user's own Markdown files in profiles/, one per person.

VAMOS only reads them. A file is YAML frontmatter followed by free-form Markdown; the
fields VAMOS uses are `tag` (seat badge label), `aliases` and `player_id` (one PokerNow ID
or a list of them). Files whose name starts with "_" (template, prompt) are skipped.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    tag: str | None
    aliases: tuple[str, ...]
    player_ids: tuple[str, ...]
    text: str

    @property
    def body(self) -> str:
        """The free-form Markdown after the frontmatter."""
        match = _FRONTMATTER.match(self.text)
        return match.group(2).strip() if match else self.text.strip()


def _strings(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str | int):
        return (str(value),)
    if isinstance(value, list) and all(isinstance(v, str | int) for v in value):
        return tuple(str(v) for v in value)
    raise ValueError(f"`{field}` must be a string or a list of strings")


def parse_profile(key: str, text: str) -> Profile:
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError("missing the --- frontmatter block at the top")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"frontmatter is not valid YAML: {error}") from error
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a list of `field: value` lines")
    tag = meta.get("tag")
    return Profile(
        key=key,
        name=str(meta.get("name") or key),
        tag=str(tag) if tag is not None else None,
        aliases=_strings(meta.get("aliases"), "aliases"),
        player_ids=_strings(meta.get("player_id"), "player_id"),
        text=text,
    )


@dataclass(frozen=True)
class ProfileSet:
    profiles: dict[str, Profile]
    # Human-readable problems to show in the side panel; a bad file never stops the others.
    problems: tuple[str, ...]

    def by_player_id(self, pn_id: str) -> Profile | None:
        for profile in self.profiles.values():
            if pn_id in profile.player_ids:
                return profile
        return None


def load_profiles(directory: Path) -> ProfileSet:
    profiles: dict[str, Profile] = {}
    problems: list[str] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            profiles[path.stem] = parse_profile(path.stem, path.read_text())
        except (OSError, UnicodeDecodeError, ValueError) as error:
            problems.append(f"{path.name}: {error}")

    owners: dict[str, str] = {}
    for profile in profiles.values():
        for pn_id in profile.player_ids:
            if pn_id in owners:
                problems.append(
                    f"player_id {pn_id} is in both {owners[pn_id]}.md and {profile.key}.md"
                )
            owners.setdefault(pn_id, profile.key)
    return ProfileSet(profiles, tuple(problems))
