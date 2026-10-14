"""
Updating the opponent profiles from the saved games: the side panel's Update profiles button
(the backend calls the AI model) and scripts/prepare_profile_update.py (an agent session does
the writing instead) share everything here.

1. `prepare`: the saved games no profile has absorbed yet, and each player's exact counts
   and hand records from them (src/vamos/profile_stats.py), in a work folder under
   data/profile-work/. Session labels live in data/profile-sessions.json: every game gets the
   next letter in the order played; tests or games that must not count are listed as excluded.
2. `write` (the button only): one AI call per player, under the rulebook
   (profiles/_GUIDELINES.md), with every name pseudonymized. Code then writes the frontmatter
   (hands, sessions, games, net, last_updated) and the four headline stat lines from the exact
   counts, so the numbers the side panel reads never depend on the model's arithmetic.
3. `install`: checks each new profile, keeps the old and new versions in
   data/profile-history/<name>/, and swaps the new file in whole. `undo` puts back the versions
   the last update replaced.
"""

import hashlib
import json
import os
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from vamos.brain.costs import PRICES, Ledger
from vamos.brain.provider import CallFailed, Preset, Provider, Request
from vamos.brain.pseudonyms import HERO, LeakError, Pseudonyms
from vamos.identity import IdentityMap
from vamos.profile_stats import SessionSpec, collect, summary
from vamos.profiles import load_profiles, parse_profile
from vamos.session import GameSession

_THROUGH = re.compile(r"^(\S+)(?:\s+through hand (\d+))?$")
_AMOUNT = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(?:\((.*)\))?\s*$")

# The writer: the model the table uses, with more thinking, since quality matters more than
# speed here (one call per player).
WRITER = Preset("profiles", "claude-opus-5-5", "high", "Opus 5.5 · high thinking")
WRITER_MAX_TOKENS = 48_000
WRITER_TIMEOUT = 900.0
# For the estimate shown before an update: the thinking and the rewritten profile, per player.
THINKING_TOKENS = 12_000
CHARS_PER_TOKEN = 3.5

# The four headline lines the side panel reads, in the rulebook's exact wording, and the
# counts (profile_stats keys) behind each pair of numbers.
HEADLINES = (
    ("- VPIP {}, PFR {}", ("vpip", "pfr")),
    ("- 3-bet vs an open {}. Facing a 3-bet: fold {}", ("three_bet_vs_open", "facing_3bet_fold")),
    ("- Flop: C-bets {}. Faced a bet: fold {}", ("flop_cbet", "flop_faced_bet_fold")),
    ("- Went to showdown {}, won at showdown {}", ("went_to_showdown", "won_at_showdown")),
)
_STAT = r"(\d+)% \((\d+)/(\d+)\)"


class InstallError(Exception):
    """A new profile the side panel could not read, or that breaks the rulebook."""


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    profiles_dir: Path

    @property
    def registry(self) -> Path:
        return self.data_dir / "profile-sessions.json"

    @property
    def work(self) -> Path:
        return self.data_dir / "profile-work"

    @property
    def history(self) -> Path:
        return self.data_dir / "profile-history"

    @property
    def games(self) -> Path:
        return self.data_dir / "games"

    @property
    def rulebook(self) -> Path:
        return self.profiles_dir / "_GUIDELINES.md"


@dataclass
class Prepared:
    work: Path
    players: list[dict[str, Any]]  # key, sessions (described), notes, create
    unknown: dict[str, Any]  # accounts no profile claims -> names and hands per session


def read_registry(paths: Paths) -> dict:
    if paths.registry.exists():
        return json.loads(paths.registry.read_text())
    return {"sessions": {}, "excluded": {}}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def next_label(used: set[str]) -> str:
    """The letter after the last one used: A, ..., Z, AA, AB, ..."""
    letters = [label for label in used if label.isalpha() and label.isupper() and label != "HU"]
    last = max(letters, key=lambda s: (len(s), s), default="")
    digits = [ord(c) - 65 for c in last]
    i = len(digits) - 1
    while i >= 0 and digits[i] == 25:
        digits[i] = 0
        i -= 1
    if i < 0:
        digits = [0, *digits]
    else:
        digits[i] += 1
    return "".join(chr(65 + d) for d in digits) or "A"


def saved_games(paths: Paths) -> list[tuple[int, str, Path]]:
    """(first row's order, game ID, file) for every saved game, oldest first."""
    found = []
    for path in paths.games.glob("*.jsonl"):
        if path.suffixes != [".jsonl"]:
            continue  # the decision records sit next to the games
        with path.open() as f:
            first = next((json.loads(line) for line in f if '"order"' in line), None)
        if first:
            found.append((int(first["order"]), path.stem, path))
    return sorted(found)


def register_new_games(paths: Paths, registry: dict) -> list[str]:
    """Give every saved game with finished hands a label; return the labels added."""
    known = {s.get("game") for s in registry["sessions"].values()}
    added = []
    for order, game, path in saved_games(paths):
        if game in known or game in registry["excluded"]:
            continue
        if not any(h.ended for h in GameSession(game, path).log.hands):
            continue  # nothing finished yet; it gets its label once it has a hand
        label = next_label(set(registry["sessions"]))
        # PokerNow's row order is its creation time in hundred-thousandths of a second.
        day = datetime.fromtimestamp(order / 100_000).strftime("%Y-%m-%d")
        registry["sessions"][label] = {"game": game, "date": day}
        added.append(label)
    return added


def absorbed(profile) -> tuple[set[str], dict[str, int], set[str]]:
    """A profile's sessions counted in full, those counted through a hand, and those excluded."""
    full = {str(s).split()[0] for s in profile.meta.get("sessions") or []}
    partial = {}
    for label, value in (profile.meta.get("games") or {}).items():
        m = _THROUGH.match(str(value).strip())
        if m and m.group(2):
            partial[str(label)] = int(m.group(2))
            full.discard(str(label))
    excluded = {str(e).split()[0] for e in profile.meta.get("excluded") or []}
    return full, partial, excluded


def hero_names(paths: Paths) -> set[str]:
    path = paths.data_dir / "settings.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    return {str(n).strip().lower() for n in saved.get("heroNames", [])}


def hands_by_account(paths: Paths, accounts: list[str]) -> dict[str, int]:
    counts = dict.fromkeys(accounts, 0)
    for _, game, path in saved_games(paths):
        log = GameSession(game, path).log
        for hand in log.hands:
            for seat in hand.seats:
                pid = log.current_id(seat.player.id)
                if pid in counts:
                    counts[pid] += 1
    return counts


def prepare(
    paths: Paths,
    registry: dict,
    create: tuple[str, list[str], str] | None = None,
    stamp: str | None = None,
) -> Prepared | None:
    """
    The work folder for the players with hands no profile has absorbed (or for one new profile:
    `create` is its name, its accounts and the main one); None when every profile is current.
    """
    profiles = load_profiles(paths.profiles_dir)
    identity = IdentityMap(paths.data_dir / "identity.json")
    heroes = hero_names(paths)

    def who(pid: str, nickname: str) -> str | None:
        if nickname.strip().lower() in heroes:
            return HERO
        if create and pid in create[1]:
            return create[0]
        found = identity.resolve(pid, profiles)
        return found.profile if found.kind == "profile" else None

    games = {label: s for label, s in registry["sessions"].items() if s.get("game")}
    files = {game: path for _, game, path in saved_games(paths)}
    # Each player's sessions still to count: a session not in its profile, or the hands of
    # one after the hand it was counted through.
    plan: dict[str, list[SessionSpec]] = {}
    targets = {create[0]: None} if create else dict(profiles.profiles)
    for key, profile in targets.items():
        full, partial, excluded = absorbed(profile) if profile else (set(), {}, set())
        for label, s in games.items():
            if label in full or label in excluded or s["game"] not in files:
                continue
            spec = SessionSpec(label, s["game"], s["date"], files[s["game"]], partial.get(label, 0))
            plan.setdefault(key, []).append(spec)
    work = paths.work / (stamp or datetime.now().strftime("%Y-%m-%d-%H%M%S"))
    entries = []
    sessions_out = {}
    unknown: dict = {}
    for key, specs in sorted(plan.items()):
        # Each player's own sessions only: another player may still need an older one.
        own, seen = collect(specs, who)
        for pid, found in seen.items():
            mine_seen = unknown.setdefault(pid, {"names": [], "hands": {}})
            mine_seen["names"] = sorted(set(mine_seen["names"]) | set(found["names"]))
            mine_seen["hands"] |= found["hands"]
        data = own.get(key)
        mine = [s for s in specs if data and data.hands.get(s.label)]
        if not mine:
            continue
        write_json(work / f"{key}.stats.json", summary(data))
        (work / f"{key}.digest.md").write_text(
            f"# {key}: every hand played in {', '.join(s.label for s in mine)} "
            "(hands only folded before the flop are left to the counts)\n\n"
            + "\n\n".join(data.digest)
            + "\n"
        )
        last = {}
        for s in mine:
            log = GameSession(s.game, s.path).log
            last[s.label] = max(h.number for h in log.hands if h.ended)
            sessions_out[s.label] = {
                "game": s.game,
                "date": s.date,
                "after_hand": s.after,
                "through_hand": last[s.label],
            }
        described = "; ".join(
            f"{s.label} ({s.date}, {data.hands[s.label]} hands"
            + (f", hands after {s.after} only" if s.after else "")
            + f"; record it as `{s.label}: {s.game} through hand {last[s.label]}`)"
            for s in mine
        )
        notes = ""
        if create:
            notes = f"player_id: {create[2]}. Other accounts are matched in data/identity.json."
        entries.append(
            {
                "key": key,
                "sessions": described,
                "labels": [s.label for s in mine],
                "notes": notes,
                "create": bool(create),
                "player_id": create[2] if create else None,
            }
        )
    if not entries:
        return None
    write_json(work / "sessions.json", sessions_out)
    write_json(work / "players.json", entries)
    write_json(work / "unknown.json", unknown)
    return Prepared(work, entries, unknown)


def install(paths: Paths, work: Path) -> list[str]:
    """Check each new profile, keep the old and new versions, and put the new one in place."""
    from vamos.host.server import _headline_stats  # the side panel's own reader

    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    labels = "-".join(sorted(json.loads((work / "sessions.json").read_text())))
    new_files = sorted((work / "new").glob("*.md"))
    # Every file is checked before any is installed, so an update never lands halfway.
    for new in new_files:
        text = new.read_text()
        try:
            profile = parse_profile(new.stem, text)
        except ValueError as error:
            raise InstallError(f"{new.name}: {error}") from error
        missing = {"vpip", "pfr", "threeBet", "cbet", "foldToFlopBet"} - set(
            _headline_stats(profile.body)
        )
        if missing:
            raise InstallError(f"{new.name}: the side panel cannot read {sorted(missing)}")
        if "—" in text:
            raise InstallError(f"{new.name}: contains an em dash")
    installed = []
    replaced: dict[str, str] = {}
    for new in new_files:
        history = paths.history / new.stem
        history.mkdir(parents=True, exist_ok=True)
        current = paths.profiles_dir / new.name
        if current.exists():
            before = history / f"{stamp}-before.md"
            digest = hashlib.sha256(current.read_bytes()).hexdigest()
            kept = {hashlib.sha256(p.read_bytes()).hexdigest(): p for p in history.glob("*.md")}
            if digest not in kept:
                shutil.copy2(current, before)
                kept[digest] = before
            replaced[new.stem] = str(kept[digest])
        shutil.copy2(new, history / f"{stamp}-{labels}.md")
        # Swapped in whole, so VAMOS never reads a half-written profile mid-session.
        staged = current.with_name(f".{current.name}.new")
        shutil.copy2(new, staged)
        os.replace(staged, current)
        installed.append(new.stem)
    write_json(work / "installed.json", {"at": stamp, "players": installed, "replaced": replaced})
    return installed


def undo(paths: Paths) -> list[str]:
    """Put back the profiles the last installed update replaced; a new profile is removed."""
    done = sorted(paths.work.glob("*/installed.json"))
    if not done:
        return []
    last = done[-1]
    record = json.loads(last.read_text())
    restored = []
    for key in record["players"]:
        current = paths.profiles_dir / f"{key}.md"
        before = record["replaced"].get(key)
        if before and Path(before).exists():
            staged = current.with_name(f".{current.name}.new")
            shutil.copy2(before, staged)
            os.replace(staged, current)
        elif current.exists():
            current.unlink()  # created by that update; its version stays in the history
        restored.append(key)
    last.rename(last.with_name("undone.json"))
    return restored


# --- Writing with the AI model (the side panel's button) ---------------------------------

INSTRUCTIONS = """\
You update one opponent profile for VAMOS, a real-time poker assistant for a private
online home game (No-Limit Hold'em cash). The profile is the source of truth about this
player: an AI model reads all of it at every decision against them, to exploit them.
Follow the rulebook below exactly.

Players are named only by pseudonym (Player A, Player B, ...); the person running VAMOS is
Hero; "unknown player 1" and so on are accounts no profile claims. Write only those names.

Reply with exactly:
- first line: `TAG: ` and the player's tag (1 to 3 standard poker words);
- then the whole profile body, from `## Summary` to the end of `## Changelog`, in the
  rulebook's sections and order.
Write no frontmatter: VAMOS writes it (hands, sessions, games, net, last_updated) from its
exact counts, and it also rewrites the four Key stats headline lines from those counts, so
compute them as the rulebook says (new total = current total plus the new ring counts for
the same stat) but never bend a read to fit a number you are unsure of.
Keep everything of value in the current profile, revised where the new evidence says so;
read every hand in the records; back every read with counts or named hands; mark thin
evidence "low confidence"; standard poker terms only; no em dashes.

# Rulebook

"""


@dataclass
class Written:
    key: str
    ok: bool
    summary: str = ""  # a line for the side panel: what changed, or why it failed
    corrected: int = 0  # headline numbers code corrected
    usage: dict[str, Any] = field(default_factory=dict)


def estimate(paths: Paths, prepared: Prepared) -> float:
    """Rough dollars for an update: each player's inputs, the thinking and the new profile."""
    rates = PRICES[WRITER.model]
    system = len(INSTRUCTIONS) + _read(paths.rulebook)
    dollars = 0.0
    for entry in prepared.players:
        key = entry["key"]
        body = _read(paths.profiles_dir / f"{key}.md")
        inputs = system + body + _read(prepared.work / f"{key}.digest.md")
        inputs += _read(prepared.work / f"{key}.stats.json")
        output = max(body, 20_000) / CHARS_PER_TOKEN + THINKING_TOKENS
        dollars += (inputs / CHARS_PER_TOKEN * rates["input"] + output * rates["output"]) / 1e6
    return dollars


def _read(path: Path) -> int:
    return len(path.read_text()) if path.exists() else 0


def pseudonyms(
    paths: Paths, profile_keys: list[str], hero: set[str]
) -> tuple[Pseudonyms, dict[str, set[str]]]:
    """
    Every profile's names (Player A, B, ... in key order) and the hero's, plus every nickname
    in the saved games, under its profile when the account is matched to one: nicknames are
    hidden in the hero's notes and checked everywhere. Also each profile's accounts.
    """
    names = Pseudonyms()
    for name in sorted(hero):
        names.add_hero_name(name)
    profiles = load_profiles(paths.profiles_dir)
    identity = IdentityMap(paths.data_dir / "identity.json")
    for key in sorted(set(profile_keys) | set(profiles.profiles) | set(profiles.unreadable)):
        profile = profiles.profiles.get(key)
        known = [profile.name, *profile.aliases] if profile else [key]
        names.add(key, known, shown=key)
    accounts: dict[str, set[str]] = {}
    for _, _, path in saved_games(paths):
        for line in path.read_text().splitlines():
            for nickname, pid in re.findall(r'"(.+?) @ ([A-Za-z0-9_-]+)"', line):
                if nickname.strip().lower() in hero:
                    continue
                found = identity.resolve(pid, profiles)
                if found.kind == "profile" and found.profile:
                    accounts.setdefault(found.profile, set()).add(pid)
                    names.add(found.profile, [nickname], shown=found.profile, nickname=True)
                else:
                    names.add(f"seen:{pid}", [nickname], shown=nickname, nickname=True)
    return names, accounts


def _session_notes(paths: Paths, sessions: dict, accounts: set[str]) -> list[str]:
    """The hero's notes on this player in the sessions being added, oldest first."""
    found = []
    for label, s in sessions.items():
        notes = paths.games / f"{s['game']}.notes.json"
        try:
            saved = json.loads(notes.read_text())
        except (OSError, ValueError):
            continue
        for pid, text in saved.items():
            if pid in accounts and str(text).strip():
                found.append(f"{label}: {str(text).strip()}")
    return found


def _counts(text: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\d+% \((\d+)/(\d+)\)", str(text).strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d) if d else 0}% ({n}/{d})"


def headlines(old_body: str, new_ring: dict[str, str]) -> list[str | None]:
    """
    The four headline lines from exact counts: the current profile's totals plus the new ring
    counts. None for a line whose current totals cannot be read (left as the model wrote it).
    """
    lines: list[str | None] = []
    for template, keys in HEADLINES:
        pattern = "^" + re.escape(template).replace(r"\{\}", _STAT) + r"\s*$"
        m = re.search(pattern, old_body, re.M) if old_body else None
        if old_body and not m:
            lines.append(None)
            continue
        olds = (
            [(int(m.group(2)), int(m.group(3))), (int(m.group(5)), int(m.group(6)))]
            if m
            else [
                (0, 0),
                (0, 0),
            ]
        )
        parts = []
        for (n, d), key in zip(olds, keys, strict=True):
            add = _counts(new_ring.get(key, "")) or (0, 0)
            parts.append(_pct(n + add[0], d + add[1]))
        lines.append(template.format(*parts))
    return lines


def correct_headlines(body: str, exact: list[str | None]) -> tuple[str, int]:
    """
    `body` with the Key stats section's headline lines replaced by the exact ones (a missing
    one is added at the top of the section's list). Returns the body and how many lines changed.
    """
    m = re.search(r"(?ms)^## Key stats[ \t]*\n(.*?)(?=^## |\Z)", body)
    if not m:
        added = "".join(f"{line}\n" for line in exact if line)
        return body.rstrip() + f"\n\n## Key stats\n{added}", sum(1 for x in exact if x)
    lines = m.group(1).split("\n")
    changed = 0
    missing = []
    for (template, _), line in zip(HEADLINES, exact, strict=True):
        if line is None:
            continue
        prefix = template.split("{}")[0]
        at = next((i for i, text in enumerate(lines) if text.startswith(prefix)), None)
        if at is None:
            missing.append(line)
        elif lines[at].strip() != line:
            lines[at] = line
            changed += 1
    if missing:
        # After the Source line when there is one, else first.
        at = next((i + 1 for i, text in enumerate(lines) if text.startswith("Source:")), 0)
        lines[at:at] = missing
        changed += len(missing)
    return body[: m.start(1)] + "\n".join(lines) + body[m.end(1) :], changed


def frontmatter(
    key: str,
    old: dict,
    tag: str,
    stats: dict,
    sessions: dict,
    player_id: str | None,
    today: date,
) -> str:
    """The new frontmatter: the current one with the new sessions' exact numbers added."""
    meta = dict(old)
    meta["name"] = key
    meta["tag"] = tag or old.get("tag") or ""
    if player_id:
        meta["player_id"] = player_id
    new_hands = sum(stats.get("hands_by_session", {}).values())
    meta["hands"] = int(old.get("hands") or 0) + new_hands
    listed = [str(s) for s in old.get("sessions") or []]
    labels = {s.split()[0] for s in listed}
    for label, s in sessions.items():
        if label not in labels:
            listed.append(f"{label} {s['date']}")
    meta["sessions"] = listed
    games = {str(k): str(v) for k, v in (old.get("games") or {}).items()}
    for label, s in sessions.items():
        games[label] = f"{s['game']} through hand {s['through_hand']}"
    meta["games"] = games
    meta["net"] = _net(old.get("net"), stats.get("net_dollars_by_session", {}))
    meta["last_updated"] = today.isoformat()
    order = ["name", "tag", "player_id", "hands", "sessions", "games", "hole_cards_known", "net"]
    order += ["excluded", "last_updated"]
    keys = [k for k in order if k in meta] + [k for k in meta if k not in order]
    return "---\n" + "".join(f"{k}: {_yaml(meta[k])}\n" for k in keys) + "---\n"


def _net(old: Any, new: dict[str, float]) -> str:
    """The net with the new sessions added: total, then each session by label."""
    total, parts = 0.0, {}
    if isinstance(old, (int, float)) and not isinstance(old, bool):
        total = float(old)
    elif old is not None:
        m = _AMOUNT.match(str(old))
        if m:
            total = float(m.group(1))
            for part in (m.group(2) or "").split(","):
                bits = part.split()
                if len(bits) >= 2:
                    try:
                        parts[" ".join(bits[:-1])] = float(bits[-1])
                    except ValueError:
                        continue
    for label, amount in new.items():
        total += amount
        parts[label] = parts.get(label, 0.0) + amount
    inner = ", ".join(f"{label} {amount:+.2f}" for label, amount in parts.items())
    return f"{total:+.2f}" + (f" ({inner})" if inner else "")


def _yaml(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_scalar(k)}: {_scalar(v)}" for k, v in value.items()) + "}"
    return _scalar(value)


def _scalar(value: Any) -> str:
    text = str(value)
    # Plain where YAML reads it back as the same string; quoted otherwise.
    if isinstance(value, (int, float)) or re.fullmatch(r"[A-Za-z0-9][\w .+/()-]*", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def write_one(
    paths: Paths,
    prepared: Prepared,
    entry: dict,
    provider: Provider,
    names: Pseudonyms,
    accounts: set[str],
    today: date,
    cancelled: threading.Event,
) -> Written:
    """One player's new profile into the work folder's new/, written by the model."""
    key = entry["key"]
    work = prepared.work
    stats = json.loads((work / f"{key}.stats.json").read_text())
    digest = (work / f"{key}.digest.md").read_text()
    all_sessions = json.loads((work / "sessions.json").read_text())
    sessions = {label: all_sessions[label] for label in entry["labels"]}
    current = paths.profiles_dir / f"{key}.md"
    old = parse_profile(key, current.read_text()) if current.exists() else None
    old_body = old.body if old else ""
    accounts = accounts | (set(old.player_ids) if old else set())
    if entry.get("player_id"):
        accounts.add(entry["player_id"])
    notes = _session_notes(paths, all_sessions, accounts)
    label = names.label(key)
    described = "; ".join(
        f"{lab} ({s['date']}, {stats['hands_by_session'].get(lab, 0)} hands"
        + (f", hands after {s['after_hand']} only" if s["after_hand"] else "")
        + ")"
        for lab, s in sessions.items()
    )
    profile_text = names.hide(old_body, nicknames=False) if old else "(none: a new profile)"
    # Profiles and hand records name players by profile name only (the rulebook); the
    # hero's notes may use any nickname seen at the table.
    records = (
        f"Player: {label}. Today is {today.isoformat()}.\n"
        f"New sessions: {described}.\n\n"
        f"## Current profile\n\n{profile_text}\n\n"
        f"## Exact counts for the new sessions (JSON)\n\n"
        f"{names.hide(json.dumps(stats, indent=1), nicknames=False)}\n\n"
        f"## Every hand {label} played in the new sessions\n\n"
        f"{names.hide(digest, nicknames=False)}\n"
    )
    written_notes = names.hide("\n".join(notes)) if notes else ""
    if not paths.rulebook.exists():
        return Written(key, False, f"the rulebook {paths.rulebook.name} is missing from profiles/")
    # The rulebook's examples use made-up names, which may still be someone's profile.
    system = names.hide(INSTRUCTIONS + paths.rulebook.read_text(), nicknames=False)
    try:
        names.check(system, nicknames=False)
        names.check(records, nicknames=False)
        names.check(written_notes)
    except LeakError as error:
        return Written(key, False, f"not sent: {error}")
    message = records
    if written_notes:
        message += f"\n## Hero's notes on this player\n\n{written_notes}\n"
    request = Request(
        WRITER,
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        [{"role": "user", "content": message}],
        schema=None,
        max_tokens=WRITER_MAX_TOKENS,
    )
    try:
        reply = provider.run(request, cancelled)
    except CallFailed as failure:
        return Written(key, False, failure.reason, usage=failure.usage)
    text = _reveal(reply.text.strip(), names, profile_keys=set(_profile_keys(paths)) | {key})
    tag_line, _, body = text.partition("\n")
    if not tag_line.upper().startswith("TAG:") or "## Summary" not in body:
        return Written(key, False, "the answer was not a profile", usage=reply.usage)
    tag = tag_line.split(":", 1)[1].strip()
    body = body[body.index("## Summary") :].strip() + "\n"
    exact = headlines(old_body, stats.get("stats_all_new_sessions", {}).get("ring", {}))
    body, corrected = correct_headlines(body, exact)
    old_meta = dict(old.meta) if old else {}
    head = frontmatter(key, old_meta, tag, stats, sessions, entry.get("player_id"), today)
    new = work / "new" / f"{key}.md"
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text(head + "\n" + body)
    vpip = exact[0] or "VPIP unchecked"
    summary_line = f"{tag}; {vpip.removeprefix('- ')}"
    return Written(key, True, summary_line, corrected, reply.usage)


def _profile_keys(paths: Paths) -> list[str]:
    profiles = load_profiles(paths.profiles_dir)
    return [*profiles.profiles, *profiles.unreadable]


def _reveal(text: str, names: Pseudonyms, profile_keys: set[str]) -> str:
    """
    Pseudonyms back to profile names; "Hero" stays, as the rulebook writes it. A pseudonym of
    a player with no profile (from the notes) becomes "an unknown player": the rulebook keeps
    nicknames out of profiles.
    """
    shown = {
        label: (person if person in profile_keys else "an unknown player")
        for person, label in names.labels().items()
    }
    for label in sorted(shown, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(label)}\b", lambda _, label=label: shown[label], text)
    return text


@dataclass
class Update:
    """One press of Update profiles (or Create profile), from plan to install."""

    paths: Paths
    provider: Provider
    ledger: Ledger
    report: Callable[[dict[str, Any]], None]
    today: Callable[[], date] = date.today
    prepared: Prepared | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)

    def plan(self, create: tuple[str, str] | None = None) -> dict[str, Any]:
        """What an update would do, for the side panel to confirm: players, hands, cost."""
        registry = read_registry(self.paths)
        register_new_games(self.paths, registry)
        write_json(self.paths.registry, registry)
        made = None
        if create:
            name, account = create
            made = (name, [account], account)
        self.prepared = prepare(self.paths, registry, made)
        if self.prepared is None:
            return {"state": "none", "players": []}
        stats = {
            e["key"]: json.loads((self.prepared.work / f"{e['key']}.stats.json").read_text())
            for e in self.prepared.players
        }
        return {
            "state": "plan",
            "players": [
                {"key": e["key"], "hands": sum(stats[e["key"]]["hands_by_session"].values())}
                for e in self.prepared.players
            ],
            "create": bool(create),
            "estimate": round(estimate(self.paths, self.prepared), 2),
            "unknown": len(self.prepared.unknown),
        }

    def run(self) -> dict[str, Any]:
        """Write every planned profile, then install the ones written; reports as it goes."""
        prepared = self.prepared
        if prepared is None:
            return {"state": "none", "players": []}
        hero = hero_names(self.paths)
        names, accounts = pseudonyms(self.paths, [e["key"] for e in prepared.players], hero)
        rows = [{"key": e["key"], "state": "waiting", "summary": ""} for e in prepared.players]
        spent = 0.0

        def send(state: str, **extra: Any) -> dict[str, Any]:
            out = {"state": state, "players": [dict(r) for r in rows], "cost": round(spent, 2)}
            out |= extra
            self.report(out)
            return out

        for row, entry in zip(rows, prepared.players, strict=True):
            row["state"] = "writing"
            send("running")
            written = write_one(
                self.paths,
                prepared,
                entry,
                self.provider,
                names,
                accounts.get(entry["key"], set()),
                self.today(),
                self.cancelled,
            )
            cost = self.ledger.charge(WRITER.model, written.usage, "profiles", datetime.now())
            spent += cost.dollars if cost else 0.0
            row["state"] = "written" if written.ok else "failed"
            row["summary"] = written.summary
            if written.corrected:
                row["summary"] += f" (code corrected {written.corrected} headline line(s))"
        if not any(r["state"] == "written" for r in rows):
            return send("failed", message="No profile was written.")
        try:
            installed = install(self.paths, prepared.work)
        except InstallError as error:
            return send("failed", message=f"Not installed: {error}")
        for row in rows:
            if row["key"] in installed:
                row["state"] = "updated"
        return send("done", canUndo=True)
