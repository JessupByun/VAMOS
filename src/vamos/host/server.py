"""
The native messaging host. Chrome launches it when the first PokerNow table opens and
closes its stdin when the last one closes, which ends `serve`.
"""

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from vamos.brain import prompt
from vamos.brain.advisor import DEFAULT_SECONDS, SPARE_SECONDS, Advisor, Job
from vamos.brain.costs import SOFT_CAP, Ledger
from vamos.brain.provider import PRESETS, AnthropicProvider, CannedProvider, Provider, Request
from vamos.brain.pseudonyms import HERO, LeakError, Pseudonyms
from vamos.brain.spot import betting, claude_facts, legal
from vamos.config import DATA_DIR, PROFILES_DIR, VERSION
from vamos.engine.advice import preflop_advice
from vamos.engine.facts import TableView, current_spot, facts
from vamos.engine.state import Decision, positions
from vamos.host.framing import read_message, write_message
from vamos.identity import IdentityMap, Resolution
from vamos.log.hands import Hand, Street
from vamos.money import parse_chips
from vamos.profiles import ProfileSet, load_profiles
from vamos.session import GameSession

log = logging.getLogger("vamos.host")

type Message = dict[str, Any]


class Backend:
    def __init__(
        self,
        data_dir: Path,
        profiles_dir: Path,
        record: bool = False,
        provider: Provider | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.data_dir = data_dir
        self.profiles_dir = profiles_dir
        # Raw log pages are only written to disk when explicitly asked (fixture recording
        # at test tables); normal play keeps tonight's rows in memory.
        self.record = record
        self.identity = IdentityMap(data_dir / "identity.json")
        self.sessions: dict[str, GameSession] = {}
        # The players each table last reported as seated, for pushing updated badges.
        self.seated: dict[str, list[Message]] = {}
        # Per table, the hero's turn in progress (the last "turn" message) and the facts last
        # computed for it, keyed by decision node.
        self.turns: dict[str, Message] = {}
        self._facts: dict[str, tuple[str, Message]] = {}
        self._profiles = ProfileSet({}, ())
        self._profiles_stamp: tuple | None = None
        # Claude's calls finish on their own threads; their updates take this lock, and so
        # does each incoming message with its replies, so messages never cross.
        self.lock = threading.RLock()
        self.emit: Callable[[Message], None] = lambda message: None
        self.clock = clock
        self.ledger = Ledger(data_dir / "costs.jsonl", datetime.now())
        self.advisor = Advisor(
            provider or AnthropicProvider(),
            self.ledger,
            self._advice_changed,
            self._cost_changed,
            clock=clock,
        )
        # data/settings.json: the dial, and the hero's own names (real name, nicknames) so
        # they are kept out of prompts like everyone else's.
        self.settings_path = data_dir / "settings.json"
        saved = _read_json(self.settings_path)
        self.preset = saved.get("preset") if saved.get("preset") in PRESETS else "balanced"
        self.hero_names = [str(n) for n in saved.get("heroNames", []) if isinstance(n, str)]
        self.notes = ""  # tonight's quick notes, never saved
        # One set of pseudonyms for the whole session, so the notes (shared by every table)
        # hide a name seen at any table.
        self.names = Pseudonyms()
        # Per table: the preset locked for the current hand, when the hero's turn began, and
        # when the table's clock says it ends.
        self._hand_preset: dict[str, tuple[int, str]] = {}
        self._turn_started: dict[str, float] = {}
        self._turn_ends: dict[str, float] = {}

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
            return [
                {"type": "ready", "version": VERSION, "recording": self.record},
                self._settings(),
                self._costs(),
            ]
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
        if kind == "turn":
            game_id = message["gameId"]
            if message.get("table") is None:
                self.turns.pop(game_id, None)
                self._turn_started.pop(game_id, None)
                self._turn_ends.pop(game_id, None)
                self.advisor.stop(game_id)
                # Tells the side panel the decision it shows has passed.
                return [{"type": "facts", "gameId": game_id, "key": "", "facts": None}]
            previous = self.turns.get(game_id)
            if previous is None or previous.get("key") != message.get("key"):
                # A new decision can follow the last one with no gap the extension sees, so
                # each new table state restarts the clock until the extension reads it again.
                self._turn_started[game_id] = self.clock()
                self._turn_ends.pop(game_id, None)
            self.turns[game_id] = message
            return [self._spot_facts(game_id)]
        if kind == "clock":
            return self._clock(message)
        if kind == "settings":
            if message.get("preset") in PRESETS:
                self.preset = message["preset"]
                self._save_settings()
            return [self._settings()]
        if kind == "notes":
            self.notes = str(message.get("text", ""))[:2000]
            return []
        if kind == "ask":
            return self._ask(message)
        if kind == "settings_request":
            return [self._settings(), self._costs()]
        log.warning("unknown message type %r", kind)
        return []

    def _save_settings(self) -> None:
        saved = _read_json(self.settings_path)
        saved["preset"] = self.preset
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.settings_path)

    def _settings(self) -> Message:
        return {"type": "settings", "preset": self.preset, "presets": list(PRESETS)}

    def _costs(self) -> Message:
        return {"type": "costs", **self.ledger.totals(datetime.now()), "softCap": SOFT_CAP}

    def _cost_changed(self) -> None:
        with self.lock:
            self.emit(self._costs())

    def _advice_changed(self, game_id: str) -> None:
        with self.lock:
            if game_id in self.turns:
                self.emit(self._spot_facts(game_id))

    def _clock(self, message: Message) -> list[Message]:
        """The table's clock, read by the extension a moment after the turn began."""
        game_id = message["gameId"]
        seconds = message.get("secondsLeft")
        if game_id not in self.turns or not isinstance(seconds, int | float) or seconds <= 0:
            return []
        self._turn_ends[game_id] = self.clock() + seconds
        cached = self._facts.get(game_id)
        if cached is not None:
            self.advisor.set_deadline(game_id, cached[0], self._deadline(game_id))
        return []

    def _deadline(self, game_id: str) -> float:
        ends = self._turn_ends.get(game_id)
        if ends is not None:
            return ends - SPARE_SECONDS
        return self._turn_started.get(game_id, self.clock()) + DEFAULT_SECONDS

    def _ask(self, message: Message) -> list[Message]:
        """Ask Opus (or another preset) about the decision on the table right now."""
        game_id = message.get("gameId")
        preset = message.get("preset", "deep")
        if game_id not in self.turns or preset not in PRESETS:
            return []
        return [self._spot_facts(game_id, ask=preset)]

    def _session(self, game_id: str) -> GameSession:
        return self.sessions.setdefault(game_id, GameSession(game_id))

    def _log_page(self, message: Message) -> list[Message]:
        game_id = message["gameId"]
        if self.record:
            self._record_page(message)
        changed = self._session(game_id).add_page(message.get("body"), bool(message.get("fresh")))
        if not changed:
            return []
        # New rows can change tonight's counts on the badges, and can be the ones the hero's
        # turn was waiting for.
        replies = [self._players(game_id)] if game_id in self.seated else []
        if game_id in self.turns:
            replies.append(self._spot_facts(game_id))
        return replies

    def _spot_facts(self, game_id: str, ask: str | None = None) -> Message:
        turn = self.turns[game_id]
        view = _table_view(turn["table"])
        log = self._session(game_id).log
        spot = current_spot(log, view) if view else None
        payload = None
        if spot is not None and view is not None:
            hand = log.hands[-1]
            hero = log.current_id(view.hero_id)
            cached = self._facts.get(game_id)
            if cached is None or cached[0] != spot.node:
                found = facts(spot)
                found["advice"] = preflop_advice(hand, spot, hero)
                cached = (spot.node, found)
                self._facts[game_id] = cached
            payload = cached[1]
            if spot.street != Street.PREFLOP:
                self._advise(game_id, hand, hero, spot, payload, ask)
                payload = {**payload, "advice": self.advisor.view(game_id, spot.node)}
        return {"type": "facts", "gameId": game_id, "key": turn["key"], "facts": payload}

    def _preset_for(self, game_id: str, hand: Hand) -> str:
        """The dial's preset, locked for the whole hand (Claude's cache is per model)."""
        locked = self._hand_preset.get(game_id)
        if locked is None or locked[0] != hand.number:
            locked = (hand.number, self.preset)
            self._hand_preset[game_id] = locked
        return locked[1]

    def _advise(
        self, game_id: str, hand: Hand, hero: str, spot: Decision, card: Message, ask: str | None
    ) -> None:
        # One call per decision; asking for another preset adds one for that preset.
        if self.advisor.asked(game_id, spot.node, ask):
            return
        preset = ask or self._preset_for(game_id, hand)
        profiles = self.profiles()
        names = self._pseudonyms(profiles, hero)
        labels = {
            s.player.id: names.label(self._person(game_id, s.player.id, profiles))
            for s in hand.seats
            if s.player.id != hero
        }
        labels[hero] = HERO
        counts = self._session(game_id).counts()
        tonight = {pid: {"hands": c.hands, "vpip": c.vpip} for pid, c in counts.items()}
        # The hand itself names players only by pseudonym; the notes are the user's own words.
        notes = names.hide(self.notes)
        message = prompt.spot_message(
            hand,
            hero,
            labels,
            positions(hand),
            claude_facts(hand, spot, card),
            legal(hand, hero, spot),
            tonight,
            notes,
        )
        system = prompt.prefix(profiles, names)
        request = Request(PRESETS[preset], system.blocks, [{"role": "user", "content": message}])
        job = Job(
            spot.node,
            request,
            betting(hand, hero, spot),
            names.reveal,
            "advice" if ask is None else "ask",
        )
        refuse = None
        try:
            names.check(system.user_text + "\n" + notes)
        except LeakError as error:
            log.error("[VAMOS] %s", error)
            refuse = "blocked: a real name was left in the prompt"
        self.advisor.start(game_id, job, self._deadline(game_id), refuse=refuse)

    def _person(self, game_id: str, pid: str, profiles: ProfileSet) -> str:
        """Who a player ID is for pseudonyms: their profile's key, or the ID itself."""
        resolution = self._resolve(pid, profiles, self._session(game_id).log.aliases)
        return resolution.profile if resolution.kind == "profile" and resolution.profile else pid

    def _pseudonyms(self, profiles: ProfileSet, hero: str) -> Pseudonyms:
        """The session's pseudonyms, with every name seen at any table registered."""
        names = self.names
        for name in self.hero_names:
            names.add_hero_name(name)
        # Profiles first, in key order, so their letters do not depend on who sat first.
        for key in sorted(profiles.profiles):
            profile = profiles.profiles[key]
            names.add(key, [profile.name, *profile.aliases], shown=profile.name)
        for game_id, session in self.sessions.items():
            game_log = session.log
            for hand in game_log.hands:
                for pid, nickname in hand.names.items():
                    pid = game_log.current_id(pid)
                    if pid == hero:
                        names.add_hero_name(nickname)
                        continue
                    person = self._person(game_id, pid, profiles)
                    if person != pid and names.knows(pid):
                        names.merge(pid, person)  # mapped to a profile since first seen
                    profile = profiles.profiles.get(person)
                    names.add(person, [nickname], shown=profile.name if profile else nickname)
        return names

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
            "problems": [
                *profiles.problems,
                *(
                    f'"{name}" is too short or too generic to keep from Claude; '
                    "give that player a longer alias or nickname"
                    for name in sorted(self.names.unusable)
                ),
            ],
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


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _table_view(table: Message) -> TableView | None:
    pot = parse_chips(table["pot"])
    if pot is None:
        return None
    # A seat with no bet shows nothing, which means nothing wagered.
    bets = sum(parse_chips(text) or 0 for text in table["bets"])
    return TableView(
        hero_id=table["heroId"],
        hero_cards=tuple(table["heroCards"]),
        board=tuple(table["board"]),
        pot=pot + bets,
    )


def serve(stdin: BinaryIO, stdout: BinaryIO, backend: Backend) -> None:
    def emit(message: Message) -> None:
        try:
            write_message(stdout, message)
        except (OSError, ValueError):
            log.exception("[VAMOS] could not send %r", message.get("type"))

    backend.emit = emit
    while (message := read_message(stdin)) is not None:
        # Replies go out under the same lock as Claude's updates, so an older state of the
        # card can never be sent after a newer one.
        with backend.lock:
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
                emit(reply)
    backend.emit = lambda message: None


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
    canned = os.environ.get("VAMOS_CANNED_CLAUDE")  # end-to-end tests only
    provider = CannedProvider(Path(canned).read_text()) if canned else None
    try:
        serve(sys.stdin.buffer, stdout, Backend(DATA_DIR, PROFILES_DIR, record, provider))
    except Exception:
        log.exception("[VAMOS] host crashed")
        raise
    finally:
        log.info("[VAMOS] host exiting after %.0fs", time.monotonic() - started)
