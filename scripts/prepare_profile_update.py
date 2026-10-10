"""
Prepare an update of the opponent profiles, and install its result: the saved games no
profile has absorbed yet, and each player's exact counts and hand records from them, in a
work folder for the update-profiles workflow (.claude/workflows/update-profiles.js), which
runs in Claude Code on the user's subscription. Nothing here calls the Claude API.

Session labels live in data/profile-sessions.json: every game gets the next letter in the
order played, and tests or games that must not count are listed there as excluded.
Run: uv run python scripts/prepare_profile_update.py
       [--match ACCOUNT=PROFILE ...] [--exclude GAME REASON]
     uv run python scripts/prepare_profile_update.py --create NAME --accounts ID[,ID]
     uv run python scripts/prepare_profile_update.py --install WORK_FOLDER
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from vamos.config import DATA_DIR, PROFILES_DIR
from vamos.host.server import _headline_stats
from vamos.identity import IdentityMap
from vamos.profile_stats import SessionSpec, collect, summary
from vamos.profiles import load_profiles, parse_profile
from vamos.session import GameSession

REGISTRY = DATA_DIR / "profile-sessions.json"
WORK = DATA_DIR / "profile-work"
HISTORY = DATA_DIR / "profile-history"
_THROUGH = re.compile(r"^(\S+)(?:\s+through hand (\d+))?$")
_HEADLINES = {"vpip", "pfr", "threeBet", "cbet", "foldToFlopBet"}


def read_registry() -> dict:
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text())
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


def saved_games() -> list[tuple[int, str, Path]]:
    """(first row's order, game ID, file) for every saved game, oldest first."""
    found = []
    for path in (DATA_DIR / "games").glob("*.jsonl"):
        if path.suffixes != [".jsonl"]:
            continue  # the decision records sit next to the games
        with path.open() as f:
            first = next((json.loads(line) for line in f if '"order"' in line), None)
        if first:
            found.append((int(first["order"]), path.stem, path))
    return sorted(found)


def register_new_games(registry: dict) -> list[str]:
    """Give every saved game with finished hands a label; return the labels added."""
    known = {s.get("game") for s in registry["sessions"].values()}
    added = []
    for order, game, path in saved_games():
        if game in known or game in registry["excluded"]:
            continue
        if not any(h.ended for h in GameSession(game, path).log.hands):
            continue  # nothing finished yet; it gets its label once it has a hand
        label = next_label(set(registry["sessions"]))
        # PokerNow's row order is its creation time in hundred-thousandths of a second.
        date = datetime.fromtimestamp(order / 100_000).strftime("%Y-%m-%d")
        registry["sessions"][label] = {"game": game, "date": date}
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


def hero_names() -> set[str]:
    path = DATA_DIR / "settings.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    return {str(n).strip().lower() for n in saved.get("heroNames", [])}


def hands_by_account(accounts: list[str]) -> dict[str, int]:
    counts = dict.fromkeys(accounts, 0)
    for _, game, path in saved_games():
        log = GameSession(game, path).log
        for hand in log.hands:
            for seat in hand.seats:
                pid = log.current_id(seat.player.id)
                if pid in counts:
                    counts[pid] += 1
    return counts


def prepare(registry: dict, create: tuple[str, list[str], str] | None) -> Path | None:
    profiles = load_profiles(PROFILES_DIR)
    identity = IdentityMap(DATA_DIR / "identity.json")
    heroes = hero_names()

    def who(pid: str, nickname: str) -> str | None:
        if nickname.strip().lower() in heroes:
            return "Hero"
        if create and pid in create[1]:
            return create[0]
        found = identity.resolve(pid, profiles)
        return found.profile if found.kind == "profile" else None

    games = {label: s for label, s in registry["sessions"].items() if s.get("game")}
    paths = {game: path for _, game, path in saved_games()}
    # Each player's sessions still to count: a session not in its profile, or the hands of
    # one after the hand it was counted through.
    plan: dict[str, list[SessionSpec]] = {}
    targets = {create[0]: None} if create else {k: p for k, p in profiles.profiles.items()}
    for key, profile in targets.items():
        full, partial, excluded = absorbed(profile) if profile else (set(), {}, set())
        for label, s in games.items():
            if label in full or label in excluded or s["game"] not in paths:
                continue
            spec = SessionSpec(label, s["game"], s["date"], paths[s["game"]], partial.get(label, 0))
            plan.setdefault(key, []).append(spec)
    work = WORK / datetime.now().strftime("%Y-%m-%d-%H%M%S")
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
        data = own[key]
        mine = [s for s in specs if data.hands.get(s.label)]
        if not mine:
            continue
        (work / f"{key}.digest.md").parent.mkdir(parents=True, exist_ok=True)
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
        entries.append({"key": key, "sessions": described, "notes": notes, "create": bool(create)})
    if not entries:
        print("No new hands for any profile: the profiles are up to date.")
        if unknown:
            report_unknown(unknown)
        return None
    write_json(work / "sessions.json", sessions_out)
    write_json(work / "players.json", entries)
    write_json(work / "unknown.json", unknown)
    print(f"Work folder: {work}")
    for e in entries:
        print(f"- {e['key']}: {e['sessions']}")
    if unknown:
        report_unknown(unknown)
    return work


def report_unknown(unknown: dict) -> None:
    print("Accounts no profile claims (ask who they were):")
    for pid, seen in unknown.items():
        print(f"- {pid}: {', '.join(seen['names'])}; hands {seen['hands']}")


def install(work: Path) -> None:
    """Check each new profile, keep the old and new versions, and put the new one in place."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    labels = "-".join(sorted(json.loads((work / "sessions.json").read_text())))
    for new in sorted((work / "new").glob("*.md")):
        text = new.read_text()
        profile = parse_profile(new.stem, text)  # raises when the frontmatter is broken
        missing = _HEADLINES - set(_headline_stats(profile.body))
        if missing:
            sys.exit(f"{new.name}: the side panel cannot read {sorted(missing)}; not installed")
        if "—" in text:
            sys.exit(f"{new.name}: contains an em dash; not installed")
        history = HISTORY / new.stem
        history.mkdir(parents=True, exist_ok=True)
        current = PROFILES_DIR / new.name
        if current.exists():
            digest = hashlib.sha256(current.read_bytes()).hexdigest()
            kept = {hashlib.sha256(p.read_bytes()).hexdigest() for p in history.glob("*.md")}
            if digest not in kept:
                shutil.copy2(current, history / f"{stamp}-before.md")
        shutil.copy2(new, history / f"{stamp}-{labels}.md")
        # Swapped in whole, so VAMOS never reads a half-written profile mid-session.
        staged = current.with_name(f".{current.name}.new")
        shutil.copy2(new, staged)
        os.replace(staged, current)
        stats = _headline_stats(profile.body)
        line = ", ".join(f"{k} {v['pct']}%" for k, v in stats.items())
        print(f"Installed {new.name} ({len(text.split())} words): {line}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", action="append", default=[], metavar="ACCOUNT=PROFILE")
    parser.add_argument(
        "--exclude", nargs=2, action="append", default=[], metavar=("GAME", "REASON")
    )
    parser.add_argument("--create", metavar="NAME")
    parser.add_argument("--accounts", default="")
    parser.add_argument("--install", type=Path, metavar="WORK_FOLDER")
    args = parser.parse_args()
    if args.install:
        install(args.install)
        return
    registry = read_registry()
    identity = IdentityMap(DATA_DIR / "identity.json")
    for pair in args.match:
        account, _, profile = pair.partition("=")
        identity.assign(account.strip(), profile.strip() or None)
    for game, reason in args.exclude:
        registry["excluded"][game] = reason
    added = register_new_games(registry)
    write_json(REGISTRY, registry)
    if added:
        print(f"New sessions: {', '.join(added)}")
    create = None
    if args.create:
        accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
        if not accounts:
            sys.exit("--create needs --accounts")
        # The account with the most hands is the profile's player_id; the others are matched.
        counts = hands_by_account(accounts)
        main_account = max(accounts, key=lambda a: counts[a])
        name = args.create.strip().lower()
        for account in accounts:
            if account != main_account:
                identity.assign(account, name)
        print(f"Creating {name}: player_id {main_account}; hands {counts}")
        create = (name, accounts, main_account)
    prepare(registry, create)


if __name__ == "__main__":
    main()
