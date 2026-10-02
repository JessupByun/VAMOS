"""
The native messaging host. Chrome launches it when the first PokerNow table opens and
closes its stdin when the last one closes, which ends `serve`.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO

from vamos.config import DATA_DIR, PROFILES_DIR, VERSION
from vamos.host.framing import read_message, write_message
from vamos.identity import IdentityMap, Resolution
from vamos.profiles import ProfileSet, load_profiles
from vamos.session import GameSession

log = logging.getLogger("vamos.host")

type Message = dict[str, Any]


class Backend:
    def __init__(self, data_dir: Path, profiles_dir: Path, record: bool = False):
        self.data_dir = data_dir
        self.profiles_dir = profiles_dir
        # Raw log pages are only written to disk when explicitly asked (fixture recording
        # at test tables); normal play keeps tonight's rows in memory.
        self.record = record
        self.identity = IdentityMap(data_dir / "identity.json")
        self.sessions: dict[str, GameSession] = {}
        # The players each table last reported as seated, for pushing updated badges.
        self.seated: dict[str, list[Message]] = {}
        self._profiles = ProfileSet({}, ())
        self._profiles_stamp: tuple | None = None

    def profiles(self) -> ProfileSet:
        """The profiles, reloaded whenever a file in profiles/ was added, changed or removed."""
        stamp = tuple(
            (p.name, p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(self.profiles_dir.glob("*.md"))
        )
        if stamp != self._profiles_stamp:
            self._profiles = load_profiles(self.profiles_dir)
            self._profiles_stamp = stamp
            for problem in self._profiles.problems:
                log.warning("[VAMOS] profile problem: %s", problem)
        return self._profiles

    def handle(self, message: Any) -> list[Message]:
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "hello":
            return [{"type": "ready", "version": VERSION}]
        if kind == "log_page":
            return self._log_page(message)
        if kind == "dom_snapshot":
            return [self._save_snapshot(message)] if self.record else []
        if kind == "seats":
            self.seated[message["gameId"]] = message["players"]
            return [self._players(message["gameId"])]
        if kind == "assign_identity":
            self.identity.assign(message["playerId"], message.get("profile"))
            return [self._players(game_id) for game_id in self.seated]
        if kind == "forget_identity":
            self.identity.forget(message["playerId"])
            return [self._players(game_id) for game_id in self.seated]
        log.warning("unknown message type %r", kind)
        return []

    def _session(self, game_id: str) -> GameSession:
        return self.sessions.setdefault(game_id, GameSession(game_id))

    def _log_page(self, message: Message) -> list[Message]:
        game_id = message["gameId"]
        if self.record:
            self._record_page(message)
        changed = self._session(game_id).add_page(message.get("body"), bool(message.get("fresh")))
        # New rows can change tonight's counts on the badges.
        return [self._players(game_id)] if changed and game_id in self.seated else []

    def _resolve(self, pid: str, profiles: ProfileSet, aliases: dict[str, str]) -> Resolution:
        resolution = self.identity.resolve(pid, profiles)
        if resolution.kind == "unknown":
            # A player whose ID changed mid-game (a guest logging in) may be known under the
            # ID they had before.
            for old, current in aliases.items():
                if current != pid:
                    continue
                earlier = self.identity.resolve(old, profiles)
                if earlier.kind != "unknown":
                    return earlier
        return resolution

    def _players(self, game_id: str) -> Message:
        profiles = self.profiles()
        session = self._session(game_id)
        counts = session.counts()
        aliases = session.log.aliases
        players = []
        for seated in self.seated.get(game_id, []):
            pid = seated["id"]
            resolution = self._resolve(pid, profiles, aliases)
            profile = profiles.profiles.get(resolution.profile or "")
            tonight = counts.get(aliases.get(pid, pid))
            players.append(
                {
                    "id": pid,
                    "name": seated.get("name", ""),
                    "status": resolution.kind,
                    "source": resolution.source,
                    "profile": (
                        {"key": profile.key, "name": profile.name, "tag": profile.tag}
                        if profile
                        else None
                    ),
                    "tonight": {"hands": tonight.hands, "vpip": tonight.vpip} if tonight else None,
                }
            )
        return {
            "type": "players",
            "gameId": game_id,
            "players": players,
            "profiles": [{"key": p.key, "name": p.name} for p in profiles.profiles.values()],
            "problems": list(profiles.problems),
        }

    def _record_page(self, message: Message) -> None:
        game_dir = self.data_dir / "recordings" / message["gameId"]
        game_dir.mkdir(parents=True, exist_ok=True)
        with (game_dir / "log_pages.jsonl").open("a") as f:
            f.write(json.dumps(message) + "\n")

    def _save_snapshot(self, message: Message) -> Message:
        game_dir = self.data_dir / "recordings" / message["gameId"]
        game_dir.mkdir(parents=True, exist_ok=True)
        path = game_dir / f"dom_{message['capturedAt']}.html"
        path.write_text(message["html"])
        return {"type": "ack", "what": "dom_snapshot", "detail": path.name}


def serve(stdin: BinaryIO, stdout: BinaryIO, backend: Backend) -> None:
    while (message := read_message(stdin)) is not None:
        try:
            replies = backend.handle(message)
        except Exception:
            # One bad message must not take the backend down for the whole session.
            log.exception(
                "[VAMOS] failed to handle %r",
                message.get("type") if isinstance(message, dict) else message,
            )
            continue
        for reply in replies:
            write_message(stdout, reply)


def main() -> None:
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "host.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # stdout carries framed messages only; a stray print would corrupt the stream.
    stdout = sys.stdout.buffer
    sys.stdout = sys.stderr
    log.info("[VAMOS] host %s started (pid %d)", VERSION, os.getpid())
    started = time.monotonic()
    record = os.environ.get("VAMOS_RECORD") == "1"
    try:
        serve(sys.stdin.buffer, stdout, Backend(DATA_DIR, PROFILES_DIR, record))
    except Exception:
        log.exception("[VAMOS] host crashed")
        raise
    finally:
        log.info("[VAMOS] host exiting after %.0fs", time.monotonic() - started)
