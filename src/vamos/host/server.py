"""
The native messaging host. Chrome launches it when the first PokerNow table opens and
closes its stdin when the last one closes, which ends `serve`.
"""

import json
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from vamos.brain import prompt
from vamos.brain.advisor import DEFAULT_SECONDS, SPARE_SECONDS, Advisor, Job
from vamos.brain.costs import SOFT_CAP, Ledger
from vamos.brain.provider import (
    DEFAULT_PRESET,
    PRESETS,
    AnthropicProvider,
    CannedProvider,
    Provider,
    Request,
)
from vamos.brain.pseudonyms import HERO, LeakError, Pseudonyms
from vamos.brain.spot import betting, claude_facts, legal
from vamos.config import DATA_DIR, PROFILES_DIR, VERSION
from vamos.engine.advice import preflop_advice
from vamos.engine.facts import TableView, current_spot, facts
from vamos.engine.madehand import describe
from vamos.engine.state import Decision, positions
from vamos.host.framing import read_message, write_message
from vamos.identity import IdentityMap, Resolution
from vamos.log.hands import Hand, HandLog, Street
from vamos.money import parse_chips
from vamos.profiles import Profile, ProfileSet, load_profiles
from vamos.session import GameSession, Read, Showdown, reads

log = logging.getLogger("vamos.host")

SPOT_ERROR = "VAMOS! hit an error on this spot (logged in data/logs/host.log)"

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
        # Save draft's output, next to profiles/ (in the repo, or the test's temp folder).
        self.drafts_dir = data_dir.parent / "drafts"
        # Raw log pages, as fetched, are only written to disk when explicitly asked (fixture
        # recording at test tables); the parsed rows of each game are saved below.
        self.record = record
        self.identity = IdentityMap(data_dir / "identity.json")
        self.sessions: dict[str, GameSession] = {}
        # Each game's log rows, so a restarted backend picks the night up where it was.
        self.games_dir = data_dir / "games"
        _prune(self.games_dir, KEEP_GAMES_DAYS)
        # The players each table last reported as seated, for pushing updated badges.
        self.seated: dict[str, list[Message]] = {}
        # Per table, the hero's turn in progress (the last "turn" message) and the facts last
        # computed for it, keyed by decision node.
        self.turns: dict[str, Message] = {}
        self._facts: dict[str, tuple[str, Message]] = {}
        # Per game, the spot and the step last logged as failing.
        self._failed_node: dict[str, tuple[str, str]] = {}
        self._profiles = ProfileSet({}, ())
        self._profiles_stamp: tuple | None = None
        # Claude's calls finish on their own threads; their updates take this lock, and so
        # does each incoming message with its replies, so messages never cross.
        self.lock = threading.RLock()
        self.emit: Callable[[Message], None] = lambda message: None
        self.clock = clock
        self.ledger = Ledger(data_dir / "costs.jsonl")
        self.advisor = Advisor(
            provider or AnthropicProvider(),
            self.ledger,
            self._advice_changed,
            self._cost_changed,
            clock=clock,
            advice_log=data_dir / "advice.jsonl",
        )
        # data/settings.json: the dial, and the hero's own names (real name, nicknames) so
        # they are kept out of prompts like everyone else's.
        self.settings_path = data_dir / "settings.json"
        saved = _read_json(self.settings_path)
        self.preset = saved.get("preset") if saved.get("preset") in PRESETS else DEFAULT_PRESET
        # The off switch for Claude: when off, no call is made and the card keeps the facts.
        self.ai_on = saved.get("aiOn") is not False
        self.show_card = saved.get("showCard") is not False  # the card on the table
        self.show_badges = saved.get("showBadges") is not False  # the badges under the seats
        self.hero_names = [str(n) for n in saved.get("heroNames", []) if isinstance(n, str)]
        self.player_notes: dict[str, str] = {}  # PokerNow ID -> tonight's notes on that player
        # Per game, the notes written at that table, saved next to its log so a restarted
        # backend has them too.
        self._game_notes: dict[str, dict[str, str]] = {}
        # Per table: the hero's own seat as the extension reports it, and the summary of the
        # current hand last sent to the side panel.
        self.hero_seat: dict[str, Message] = {}
        self._hand_sent: dict[str, Message] = {}
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
            game_id = message["gameId"]
            self.seated[game_id] = message["players"]
            if message.get("hero"):
                self.hero_seat[game_id] = message["hero"]
            else:
                self.hero_seat.pop(game_id, None)
            return [self._players(game_id), *self._hand_update(game_id)]
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
            if isinstance(message.get("showCard"), bool):
                self.show_card = message["showCard"]
            if isinstance(message.get("showBadges"), bool):
                self.show_badges = message["showBadges"]
            if isinstance(message.get("aiOn"), bool):
                self.ai_on = message["aiOn"]
                if not self.ai_on:
                    # Anything in flight is stopped now; nothing new starts.
                    for game_id in list(self.turns):
                        self.advisor.stop(game_id)
            self._save_settings()
            return [self._settings(), *(self._spot_facts(game_id) for game_id in self.turns)]
        if kind == "resume_request":
            # Where a log poller starting over picks up: after the newest row held.
            newest = self._session(message["gameId"]).newest
            return [
                {
                    "type": "resume",
                    "gameId": message["gameId"],
                    "after": None if newest is None else str(newest),
                }
            ]
        if kind == "notes":
            text = str(message.get("text", ""))[:2000]
            if message.get("playerId"):
                # Notes about one player, kept by PokerNow ID, and saved with the game's log.
                self.player_notes[message["playerId"]] = text
                if message.get("gameId"):
                    self._save_notes(message["gameId"], message["playerId"], text)
            return []
        if kind == "save_draft":
            return [self._save_draft(message["gameId"], message["playerId"])]
        if kind == "ask":
            return self._ask(message)
        log.warning("unknown message type %r", kind)
        return []

    def _save_settings(self) -> None:
        saved = _read_json(self.settings_path)
        saved["preset"] = self.preset
        saved["aiOn"] = self.ai_on
        saved["showCard"] = self.show_card
        saved["showBadges"] = self.show_badges
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.settings_path)

    def _settings(self) -> Message:
        return {
            "type": "settings",
            "preset": self.preset,
            "presets": [
                {"name": p.name, "label": p.label, "effort": p.effort, "seconds": p.seconds}
                for p in PRESETS.values()
            ],
            "deep": "deep",  # the preset "Ask deeper" uses
            "aiOn": self.ai_on,
            "showCard": self.show_card,
            "showBadges": self.show_badges,
        }

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
        """Ask again with another preset (the deeper one) about the decision on the table."""
        game_id = message.get("gameId")
        preset = message.get("preset", "deep")
        if game_id not in self.turns or preset not in PRESETS:
            return []
        return [self._spot_facts(game_id, ask=preset)]

    def _game_file(self, game_id: str, suffix: str) -> Path | None:
        # Game IDs come from the page; only plain ones name a file.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", game_id):
            return None
        return self.games_dir / f"{game_id}{suffix}"

    def _session(self, game_id: str) -> GameSession:
        session = self.sessions.get(game_id)
        if session is None:
            session = self.sessions[game_id] = GameSession(
                game_id, self._game_file(game_id, ".jsonl")
            )
            self._load_notes(game_id)
        return session

    def _load_notes(self, game_id: str) -> None:
        path = self._game_file(game_id, ".notes.json")
        saved = _read_json(path) if path else {}
        notes = {str(k): str(v) for k, v in saved.items() if isinstance(v, str)}
        self._game_notes[game_id] = notes
        for pid, text in notes.items():
            # Notes typed since this backend started are newer than the saved ones.
            self.player_notes.setdefault(pid, text)

    def _save_notes(self, game_id: str, pid: str, text: str) -> None:
        path = self._game_file(game_id, ".notes.json")
        self._session(game_id)  # loads the saved ones first, so none are dropped
        notes = self._game_notes.setdefault(game_id, {})
        if text.strip():
            notes[pid] = text
        else:
            notes.pop(pid, None)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(notes, indent=1, sort_keys=True) + "\n")
            os.replace(tmp, path)
        except OSError:
            log.exception("[VAMOS] could not save notes to %s", path)

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
        return [*replies, *self._hand_update(game_id)]

    def _notes_for(self, pid: str, aliases: dict[str, str]) -> str:
        """Notes on a player, also when written under an ID they had before logging in."""
        if pid in self.player_notes:
            return self.player_notes[pid]
        old = [o for o, new in aliases.items() if new == pid and o in self.player_notes]
        return self.player_notes[old[-1]] if old else ""

    def _save_draft(self, game_id: str, pid: str) -> Message:
        """
        This session's read and notes on one player as a draft in drafts/, for the user to
        review and merge into profiles/ themselves. VAMOS never writes profiles/.
        """
        session = self._session(game_id)
        aliases = session.log.aliases
        current = session.log.current_id(pid)
        seat = next((s for s in self.seated.get(game_id, []) if s["id"] == pid), {"name": pid})
        profiles = self.profiles()
        resolution = self._resolve(pid, profiles, aliases)
        profile = profiles.profiles.get(resolution.profile or "")
        read = reads(session.log).get(current)
        name = profile.name if profile else seat.get("name") or pid
        today = datetime.now()
        # The player's earlier IDs this game, so a draft saved before an ID change is found.
        ids = {current, *(old for old in aliases if session.log.current_id(old) == current)}
        path = _draft_path(self.drafts_dir, f"{today:%Y-%m-%d}-{_slug(name)}", game_id, ids)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _draft_text(
                name,
                seat.get("name", ""),
                current,
                game_id,
                profile,
                read,
                self._notes_for(pid, aliases),
                today,
            )
        )
        return {
            "type": "draft_saved",
            "playerId": pid,
            "path": str(path.relative_to(self.drafts_dir.parent)),
        }

    def _hero_id(self, game_id: str) -> str | None:
        seat = self.hero_seat.get(game_id)
        turn = self.turns.get(game_id)
        pid = seat["id"] if seat else turn["table"]["heroId"] if turn else None
        return self._session(game_id).log.current_id(pid) if pid else None

    def _hand_update(self, game_id: str) -> list[Message]:
        """The hand on the table from the hero's seat, sent to the side panel when it changes."""
        hero = self._hero_id(game_id)
        log = self._session(game_id).log
        if hero is None or not log.hands:
            return []
        hand = log.hands[-1]
        if hero not in {s.player.id for s in hand.seats}:
            return []
        bb = hand.big_blind or 1
        put = hand.put_in.get(hero, 0) - hand.returned.get(hero, 0)
        bounty = sum(
            (e.amount or 0)
            * (
                (e.other is not None and e.other.id == hero)
                - (e.player is not None and e.player.id == hero)
            )
            for e in hand.bounties
            if e.kind == "bounty_paid"
        )
        summary = {
            "type": "hand",
            "gameId": game_id,
            "key": str(hand.hand_id or hand.number),
            "number": hand.number,
            "heroCards": list(hand.hero_cards),
            "board": list(hand.board),
            # Run it twice or a double board: the second board in full.
            "secondBoard": list(hand.second_run or hand.second_board),
            "position": positions(hand).get(hero),
            "madeHand": describe(hand.hero_cards, hand.board) if hand.hero_cards else None,
            "folded": hero in hand.folded(),
            "ended": hand.ended,
            # Won or lost, in big blinds, once the hand is over.
            "resultBb": (
                round((hand.collected.get(hero, 0) - put + bounty) / bb, 2) if hand.ended else None
            ),
        }
        if self._hand_sent.get(game_id) == summary:
            return []
        self._hand_sent[game_id] = summary
        return [summary]

    def _spot_facts(self, game_id: str, ask: str | None = None) -> Message:
        turn = self.turns[game_id]
        payload = None
        try:
            view = _table_view(turn["table"])
            game_log = self._session(game_id).log
            spot = current_spot(game_log, view) if view else None
            if spot is not None and view is not None:
                payload = self._spot_payload(game_id, game_log, view, spot, ask)
        except Exception:
            # The card says so rather than reading the hand forever.
            self._log_failure(game_id, turn["key"], "the spot")
            return {
                "type": "facts",
                "gameId": game_id,
                "key": turn["key"],
                "facts": None,
                "error": SPOT_ERROR,
            }
        return {"type": "facts", "gameId": game_id, "key": turn["key"], "facts": payload}

    def _spot_payload(
        self, game_id: str, game_log: HandLog, view: TableView, spot: Decision, ask: str | None
    ) -> Message:
        hand = game_log.hands[-1]
        hero = game_log.current_id(view.hero_id)
        cached = self._facts.get(game_id)
        if cached is None or cached[0] != spot.node:
            found = facts(spot)
            try:
                # Preflop: the push/fold chart or a rule when one covers the spot.
                found["advice"] = preflop_advice(hand, spot, hero)
            except Exception:
                # Claude decides instead; the facts stay up either way.
                self._log_failure(game_id, spot.node, "the preflop rules")
                found["advice"] = None
            cached = (spot.node, found)
            self._facts[game_id] = cached
        payload = cached[1]
        if payload.get("advice") is not None:
            return payload  # answered at once, no Claude call
        if not self.ai_on:
            return {**payload, "advice": None, "aiOff": True}
        try:
            self._advise(game_id, hand, hero, spot, payload, ask)
        except Exception:
            # Recorded as a failed call, so the card and the panel say why and it is not
            # tried again for this decision and preset.
            self._log_failure(game_id, spot.node, "Claude's prompt")
            preset = ask or self._preset_for(game_id, hand)
            stub = Job(spot.node, Request(PRESETS[preset], [], []), None)  # type: ignore[arg-type]
            self.advisor.start(game_id, stub, 0.0, refuse="internal error (logged)")
        return {**payload, "advice": self.advisor.view(game_id, spot.node)}

    def _log_failure(self, game_id: str, node: str, what: str) -> None:
        # Once per spot and failure: the same spot is recomputed on every log page while it
        # is up, and a second, different failure there must still be logged.
        if self._failed_node.get(game_id) != (node, what):
            self._failed_node[game_id] = (node, what)
            log.exception("[VAMOS] could not compute %s at %s", what, node)

    def _preset_for(self, game_id: str, hand: Hand) -> str:
        """The dial's preset, locked for the whole hand (Claude's cache is per model and effort)."""
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
        # Tonight's read on the opponents only: Hero's own numbers are not sent.
        tonight = {
            pid: r.summary() for pid, r in reads(self._session(game_id).log).items() if pid != hero
        }
        # The hand itself names players only by pseudonym; the notes are the user's own words.
        game_log = self._session(game_id).log
        player_notes = {
            game_log.current_id(pid): names.hide(text)
            for pid, text in self.player_notes.items()
            if text.strip() and game_log.current_id(pid) in labels
        }
        message = prompt.spot_message(
            hand,
            hero,
            labels,
            positions(hand),
            claude_facts(hand, hero, spot, card, labels),
            legal(hand, hero, spot),
            tonight,
            player_notes=player_notes,
        )
        # Only the profiles of players at this table (seated or dealt in), never the hero's.
        at_table = {s["id"] for s in self.seated.get(game_id, [])} | {
            s.player.id for s in hand.seats
        }
        keys = {self._person(game_id, game_log.current_id(pid), profiles) for pid in at_table}
        keys.discard(self._person(game_id, hero, profiles))
        system = prompt.prefix(profiles, names, keys)
        request = Request(PRESETS[preset], system.blocks, [{"role": "user", "content": message}])
        job = Job(
            spot.node,
            request,
            betting(hand, hero, spot),
            names.reveal,
            ("preflop" if spot.street == Street.PREFLOP else "advice") if ask is None else "ask",
        )
        refuse = None
        try:
            names.check("\n".join([system.user_text, *player_notes.values()]))
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
        # Players seated but not yet dealt in (waiting for the big blind, sitting out) are in
        # no hand, but a note can still name them.
        for game_id, seated in self.seated.items():
            game_log = self._session(game_id).log
            for seat in seated:
                pid = game_log.current_id(seat["id"])
                person = self._person(game_id, pid, profiles)
                profile = profiles.profiles.get(person)
                nickname = seat.get("name", "")
                names.add(person, [nickname], shown=profile.name if profile else nickname)
        for seat in self.hero_seat.values():
            names.add_hero_name(seat.get("name", ""))
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
        tonight_reads = reads(session.log)
        aliases = session.log.aliases
        players = []
        hero = self.hero_seat.get(game_id)
        if hero:
            mine = tonight_reads.get(aliases.get(hero["id"], hero["id"]))
            players.append(
                {
                    "id": hero["id"],
                    "name": hero.get("name", ""),
                    "hero": True,
                    "status": "none",
                    "source": None,
                    "profile": None,
                    "tonight": _read_view(mine),
                    "notes": "",
                }
            )
        for seated in self.seated.get(game_id, []):
            pid = seated["id"]
            resolution = self._resolve(pid, profiles, aliases)
            profile = profiles.profiles.get(resolution.profile or "")
            tonight = tonight_reads.get(aliases.get(pid, pid))
            players.append(
                {
                    "id": pid,
                    "name": seated.get("name", ""),
                    "status": resolution.kind,
                    "source": resolution.source,
                    "profile": _profile_view(profile) if profile else None,
                    "tonight": _read_view(tonight),
                    "notes": self._notes_for(pid, aliases),
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
                    f'"{name}" is too short or looks like a pseudonym, so it is not hidden '
                    "from Claude; rename that profile, or ask the player for a longer nickname"
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


KEEP_GAMES_DAYS = 3  # saved game logs older than this are deleted when the backend starts


def _prune(directory: Path, days: int) -> None:
    cutoff = time.time() - days * 86400
    for path in directory.glob("*.json*") if directory.exists() else []:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            log.exception("[VAMOS] could not prune %s", path)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower() or "player"


def _draft_path(folder: Path, stem: str, game_id: str, ids: set[str]) -> Path:
    """
    The player's draft for this game if one exists, whatever day or name it was first saved
    under (past midnight, after an ID change, after a profile match), so saving again
    replaces it; else "2026-10-05-alex.md", or "-2", "-3", ... when another player or game
    has that name.
    """
    for existing in sorted(folder.glob("*.md")):
        found = _draft_of(existing)
        if found is not None and found[0] == game_id and found[1] in ids:
            return existing
    for n in range(1, 100):
        path = folder / (f"{stem}.md" if n == 1 else f"{stem}-{n}.md")
        if not path.exists():
            return path
    raise RuntimeError(f"too many drafts named {stem}")


def _draft_of(path: Path) -> tuple[str, str] | None:
    """The game and player a saved draft is about, from its frontmatter."""
    lines = path.read_text().splitlines()
    try:
        end = lines.index("---", 1) if lines and lines[0] == "---" else 0
        meta = yaml.safe_load("\n".join(lines[1:end])) if end else None
    except (ValueError, yaml.YAMLError):
        return None
    if not isinstance(meta, dict):
        return None
    return str(meta.get("game", "")), str(meta.get("player_id", ""))


def _pct(count: int, of: int) -> str:
    return f"{round(100 * count / of)}% ({count}/{of})" if of else "n/a"


def _draft_text(
    name: str,
    nickname: str,
    pid: str,
    game_id: str,
    profile: Profile | None,
    read: Read | None,
    notes: str,
    today: datetime,
) -> str:
    """
    A draft for the user to merge by hand: frontmatter naming the player and the game, then
    this session's numbers, hands shown and notes.
    """
    # Dumped, not formatted, so a nickname like "Al: the shark" or "Bob, Jr" stays valid.
    meta = {
        "name": name,
        "aliases": [nickname] if nickname and nickname != name else [],
        "player_id": pid,
        "game": game_id,
        "draft_of": profile.key if profile else "none",
        "saved": today.isoformat(timespec="minutes"),
    }
    lines = [
        "---",
        yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip("\n"),
        "---",
        f"# Draft for {name}, {today:%Y-%m-%d}",
        "",
        "Written by VAMOS! from one session, for you to review. It is not a profile: merge what",
        f"is useful into profiles/{profile.key if profile else '<name>'}.md yourself.",
        "",
        "## This session's numbers",
    ]
    if read is None or read.hands == 0:
        lines.append("- No hands dealt this session.")
    else:
        lines += [
            f"- Hands: {read.hands}",
            f"- VPIP: {_pct(read.vpip, read.hands)}",
            f"- PFR: {_pct(read.pfr, read.hands)}",
            f"- 3-bet: {_pct(read.three_bets, read.three_bet_chances)}",
            f"- C-bet: {_pct(read.cbets, read.cbet_chances)}",
            f"- Postflop bets and raises: {read.bets_raises}; calls: {read.calls}",
            f"- Folds to postflop bets: {_pct(read.folds_to_bets, read.faced_bets)}",
        ]
    lines += ["", "## Hands shown"]
    shown = read.showdowns if read else ()
    lines += [
        f"- Hand #{s.number}: {' '.join(s.cards)} on {_boards(s)}; {s.line}; "
        f"{_RESULT_TEXT[s.result]}"
        for s in shown
    ] or ["- None this session."]
    lines += ["", "## Your notes this session", notes.strip() or "None.", ""]
    return "\n".join(lines)


_RESULT_TEXT = {
    "won": "won",
    "lost": "lost at showdown",
    "split": "won part of the pot",
    "folded": "shown after folding",
    "uncontested": "shown after everyone folded",
}


def _boards(s: Showdown) -> str:
    board = " ".join(s.board) or "no board"
    return f"{board} / second run {' '.join(s.second_board)}" if s.second_board else board


def _read_view(read: Read | None) -> Message | None:
    """Tonight's read for the side panel."""
    if read is None:
        return None
    return {
        "hands": read.hands,
        "vpip": read.vpip,
        "pfr": read.pfr,
        "threeBets": read.three_bets,
        "threeBetChances": read.three_bet_chances,
        "cbets": read.cbets,
        "cbetChances": read.cbet_chances,
        "betsRaises": read.bets_raises,
        "calls": read.calls,
        "foldsToBets": read.folds_to_bets,
        "facedBets": read.faced_bets,
    }


# Frontmatter fields shown in the side panel; names, aliases and IDs stay out of the UI.
_SHOWN_META = ("hands", "sessions", "net", "last_updated")


# Headline numbers read from a profile's text for the side panel, first mention of each, in
# the template's wording ("- 3-bet vs an open 11% (11/96)", "- Flop: C-bets 80% (40/50).
# Faced a bet: fold 35% (21/60)") or the older one ("- 3-bet 6% (7/114)", "- Flop c-bet
# 61%", "facing a flop bet: fold 35%"). 3-bets are read from a stats line only, since prose like
# "facing a 3-bet: call 82%" would otherwise be taken for one. Claude reads the whole profile.
_PROFILE_STATS = (
    ("vpip", r"\bVPIP"),
    ("pfr", r"\bPFR"),
    ("threeBet", r"(?m:^-\s*3-bet(?: vs an open)?)"),
    ("cbet", r"\b(?i:flop c-bet|c-bets)"),
    ("foldToFlopBet", r"\b(?i:faced a bet|facing a flop bet): fold"),
)


def _headline_stats(body: str) -> Message:
    """The headline numbers, read from the Key stats section when there is one, so a
    "recent VPIP 40%" in the Summary is never taken for the overall number."""
    section = re.search(r"(?ims)^#+[ \t]*Key stats[ \t]*$(.*?)(?=^#+[ \t]|\Z)", body)
    texts = [section.group(1), body] if section else [body]
    stats = {}
    for name, pattern in _PROFILE_STATS:
        for text in texts:
            m = re.search(rf"{pattern}[^\d\n%]{{0,12}}(\d+)%(?:\s*\((\d+/\d+))?", text)
            if m:
                stats[name] = {"pct": m.group(1), "count": m.group(2)}
                break
    return stats


def _profile_view(profile: Profile) -> Message:
    """What the side panel shows about a profile: the user's own file, read only."""
    meta = {}
    for key in _SHOWN_META:
        value = profile.meta.get(key)
        if value is not None:
            meta[key] = len(value) if isinstance(value, list) and key == "sessions" else str(value)
    return {
        "key": profile.key,
        "name": profile.name,
        "tag": profile.tag,
        "meta": meta,
        "net": _net(profile.meta),
        "summary": _summary(profile.body),
        "stats": _headline_stats(profile.body),
        "body": profile.body,
    }


_AMOUNT = r"[+-]?\d+(?:\.\d+)?"


def _net(meta: dict) -> Message | None:
    """
    The profile's net result with its per-session parts, from frontmatter such as
    `net: -41.30 (HU -15.00, A -26.30)` and `sessions: [HU 2026-09-30, A 2026-09-30]`.
    Anything else, or parts naming no listed session, is shown as written.
    """
    value = meta.get("net")
    if value is None:
        return None
    # YAML reads a bare `net: -41.30` as a number; show it as money, as it was written.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"total": f"{value:+.2f}", "sessions": []}
    text = str(value).strip()
    as_written = {"total": text, "sessions": []}
    m = re.fullmatch(rf"({_AMOUNT})\s*(?:\((.*)\))?", text)
    if not m:
        return as_written
    listed = meta.get("sessions")
    dates = {}
    for entry in listed if isinstance(listed, list) else []:
        # The date is the last word: "Home game 2026-09-30".
        label, _, date = str(entry).strip().rpartition(" ")
        if label:
            dates[label] = date
    parts = [p.strip() for p in (m.group(2) or "").split(",") if p.strip()]
    # One session named alone, as in "+8.25 (D)": the whole net is that session's.
    if len(parts) == 1 and parts[0] in dates:
        return {
            "total": m.group(1),
            "sessions": [{"label": parts[0], "amount": m.group(1), "date": dates[parts[0]]}],
        }
    sessions = []
    for part in parts:
        # Split only by the sessions the profile lists, so prose is never read as a session.
        label, _, amount = part.rpartition(" ")
        if label not in dates or not re.fullmatch(_AMOUNT, amount):
            return as_written
        sessions.append({"label": label, "amount": amount, "date": dates.get(label)})
    return {"total": m.group(1), "sessions": sessions}


def _summary(body: str) -> str | None:
    """The first sentence of the profile's "Summary" section, else of its first paragraph."""
    section = re.search(
        r"^#+[ \t]*Summary[ \t]*\n\s*(?![#-])(\S.+?)(?:\n\s*\n|\n#|\Z)", body, re.M | re.S | re.I
    )
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip() and p.lstrip()[0] not in "#-"]
    text = section.group(1) if section else (paragraphs[0] if paragraphs else "")
    sentence = re.split(r"(?<=[.!?])\s", " ".join(text.split()), maxsplit=1)[0]
    return sentence or None


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
    provider = CannedProvider(Path(canned)) if canned else None
    try:
        serve(sys.stdin.buffer, stdout, Backend(DATA_DIR, PROFILES_DIR, record, provider))
    except Exception:
        log.exception("[VAMOS] host crashed")
        raise
    finally:
        log.info("[VAMOS] host exiting after %.0fs", time.monotonic() - started)
