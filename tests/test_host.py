import io
import json
import os
import queue
import re
import struct
import threading
import time
from datetime import datetime, timedelta

import pytest

from vamos.brain.provider import FakeProvider, Reply
from vamos.config import VERSION
from vamos.host.framing import MAX_OUTGOING_BYTES, BadMessage, read_message, write_message
from vamos.host.install import EXTENSION_ID, HOST_NAME, install
from vamos.host.server import Backend, serve


def frame(message) -> bytes:
    data = json.dumps(message).encode()
    return struct.pack("<I", len(data)) + data


def read_all(stream: io.BytesIO) -> list:
    stream.seek(0)
    messages = []
    while (message := read_message(stream)) is not None:
        messages.append(message)
    return messages


def test_framing_round_trip():
    stream = io.BytesIO()
    write_message(stream, {"type": "ready", "text": "héllo ♠"})
    write_message(stream, [1, 2, 3])
    assert read_all(stream) == [{"type": "ready", "text": "héllo ♠"}, [1, 2, 3]]


@pytest.mark.parametrize("data", [b"", b"\x05\x00", struct.pack("<I", 10) + b"{}"])
def test_read_message_returns_none_on_closed_or_truncated_pipe(data):
    assert read_message(io.BytesIO(data)) is None


@pytest.mark.parametrize("body", [b"{oops", b"\xff\xfe", b"[" * 100_000])
def test_a_message_that_is_not_json_is_refused_and_the_next_one_still_read(body):
    stream = io.BytesIO(struct.pack("<I", len(body)) + body + frame({"type": "hello"}))
    with pytest.raises(BadMessage, match=f"{len(body)}-byte message is not JSON"):
        read_message(stream)
    assert read_message(stream) == {"type": "hello"}


def test_write_message_refuses_oversized_messages():
    with pytest.raises(ValueError, match="1 MB"):
        write_message(io.BytesIO(), {"blob": "x" * MAX_OUTGOING_BYTES})


ALEX = "---\nname: alex\ntag: Calling station\nplayer_id: Qw3rTy7UiO\n---\nLoose-passive.\n"
GAME = "pgl123"
SEATS = {
    "type": "seats",
    "gameId": GAME,
    "players": [{"id": "Qw3rTy7UiO", "name": "alex"}, {"id": "zzzzzzzzzz", "name": "new guy"}],
}


def page(*rows: tuple[int, str]) -> dict:
    logs = [{"created_at": str(order), "msg": msg} for order, msg in reversed(rows)]
    return {"type": "log_page", "gameId": GAME, "fetchedAt": 1, "body": {"logs": logs}}


HAND = [
    (100, '-- starting hand #1 (id: h1)  No Limit Texas Hold\'em (dealer: "alex @ Qw3rTy7UiO") --'),
    (101, 'Player stacks: #1 "alex @ Qw3rTy7UiO" (1000) | #2 "new guy @ zzzzzzzzzz" (1000)'),
    (102, '"alex @ Qw3rTy7UiO" posts a small blind of 10'),
    (103, '"new guy @ zzzzzzzzzz" posts a big blind of 20'),
    (104, '"alex @ Qw3rTy7UiO" calls 20'),
    (105, '"new guy @ zzzzzzzzzz" checks'),
]


@pytest.fixture
def backend(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "alex.md").write_text(ALEX)
    return Backend(tmp_path / "data", profiles)


def test_hello_reports_ready_with_settings_and_costs(backend):
    ready, settings, costs = backend.handle({"type": "hello"})
    assert ready == {"type": "ready", "version": VERSION, "recording": False}
    assert settings == {
        "type": "settings",
        "preset": "standard",
        "presets": [
            {
                "name": "standard",
                "label": "Opus 5.5 · low thinking",
                "effort": "low",
                "seconds": "2-7",
            },
            {
                "name": "deep",
                "label": "Opus 5.5 · medium thinking",
                "effort": "medium",
                "seconds": "5-14",
            },
        ],
        "deep": "deep",
        "aiOn": True,
        "showCard": True,
        "showBadges": True,
    }
    assert (costs["type"], costs["session"], costs["overCap"]) == ("costs", 0, False)


def test_seated_players_resolve_to_profiles_or_ask(backend):
    [players] = backend.handle(SEATS)
    alex, stranger = players["players"]
    assert alex["status"] == "profile"
    assert alex["profile"] == {
        "key": "alex",
        "name": "alex",
        "tag": "Calling station",
        "meta": {},
        "net": None,
        "summary": "Loose-passive.",
        "stats": {},
        "body": "Loose-passive.",
    }
    assert (stranger["status"], stranger["profile"]) == ("unknown", None)
    assert players["profiles"] == [{"key": "alex", "name": "alex"}]


def test_answering_who_is_this_updates_badges_and_persists(backend, tmp_path):
    backend.handle(SEATS)
    [players] = backend.handle(
        {"type": "assign_identity", "playerId": "zzzzzzzzzz", "profile": "alex"}
    )
    assert players["players"][1]["profile"]["key"] == "alex"
    assert players["players"][1]["source"] == "answer"
    assert players["players"][0]["source"] == "player_id"
    assert json.loads((tmp_path / "data/identity.json").read_text()) == {"zzzzzzzzzz": "alex"}
    [players] = backend.handle({"type": "forget_identity", "playerId": "zzzzzzzzzz"})
    assert players["players"][1]["status"] == "unknown"


def test_log_pages_update_tonight_counts_without_saving_anything(backend, tmp_path):
    backend.handle(SEATS)
    [players] = backend.handle(page(*HAND))
    tonight = {p["id"]: p["tonight"] for p in players["players"]}
    assert {pid: (t["hands"], t["vpip"], t["pfr"]) for pid, t in tonight.items()} == {
        "Qw3rTy7UiO": (1, 1, 0),
        "zzzzzzzzzz": (1, 0, 0),
    }
    # The same page again brings nothing new, so no badge update is sent.
    assert backend.handle(page(*HAND)) == []
    assert not (tmp_path / "data/recordings").exists()


def test_recording_is_opt_in(tmp_path):
    backend = Backend(tmp_path / "data", tmp_path, record=True)
    backend.handle(page(*HAND))
    [ack] = backend.handle(
        {"type": "dom_snapshot", "gameId": GAME, "capturedAt": 7, "html": "<html/>"}
    )
    assert ack == {"type": "ack", "what": "dom_snapshot", "detail": "dom_7.html"}
    assert (tmp_path / f"data/recordings/{GAME}/log_pages.jsonl").exists()


def test_profiles_reload_when_files_change(backend, tmp_path):
    backend.handle(SEATS)
    (tmp_path / "profiles/newguy.md").write_text("---\ntag: Nit\nplayer_id: zzzzzzzzzz\n---\n")
    [players] = backend.handle(SEATS)
    assert players["players"][1]["profile"]["tag"] == "Nit"


def test_serve_survives_a_bad_message(backend):
    stdin = io.BytesIO(frame({"type": "seats"}) + frame({"type": "hello"}))
    stdout = io.BytesIO()
    serve(stdin, stdout, backend)
    assert [m["type"] for m in read_all(stdout)] == ["ready", "settings", "costs"]


def test_serve_skips_a_message_that_is_not_json(backend, caplog):
    stdin = io.BytesIO(struct.pack("<I", 5) + b"{oops" + frame({"type": "hello"}))
    stdout = io.BytesIO()
    serve(stdin, stdout, backend)
    assert [m["type"] for m in read_all(stdout)] == ["ready", "settings", "costs"]
    assert "[VAMOS] skipped a message from Chrome: 5-byte message is not JSON" in caplog.text


def test_a_damaged_identity_file_does_not_stop_the_backend(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "identity.json").write_text('{"zzzzzzzzzz": "alex",')
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "alex.md").write_text(ALEX)
    stdin = io.BytesIO(frame({"type": "hello"}) + frame(SEATS))
    stdout = io.BytesIO()
    serve(stdin, stdout, Backend(data, profiles))
    ready, _, _, players = read_all(stdout)
    assert ready["type"] == "ready"
    [problem] = players["problems"]
    assert problem.startswith("data/identity.json was damaged, so the players you matched")
    assert [p.name for p in data.glob("identity.damaged-*.json")] == [problem.rsplit("/", 1)[1]]
    assert [p["status"] for p in players["players"]] == ["profile", "unknown"]


def test_install_writes_launcher_and_manifest(tmp_path):
    manifest_path = install(
        launcher_dir=tmp_path / "support", hosts_dir=tmp_path / "hosts", python="/py/bin/python"
    )

    manifest = json.loads(manifest_path.read_text())
    assert manifest_path.name == f"{HOST_NAME}.json"
    assert manifest["name"] == HOST_NAME
    assert manifest["type"] == "stdio"
    assert manifest["allowed_origins"] == [f"chrome-extension://{EXTENSION_ID}/"]
    launcher = tmp_path / "support/vamos-host"
    assert manifest["path"] == str(launcher)
    assert launcher.stat().st_mode & 0o111
    assert launcher.read_text() == '#!/bin/sh\nexec "/py/bin/python" -m vamos host "$@"\n'
    install(
        launcher_dir=tmp_path / "support",
        hosts_dir=tmp_path / "hosts",
        python="/py/bin/python",
        record=True,
    )
    assert launcher.read_text().startswith("#!/bin/sh\nVAMOS_RECORD=1 exec")


def test_a_player_whose_id_changed_is_still_recognized(backend):
    # The profile lists alex's guest ID; after logging in the seat shows the new ID.
    backend.handle(
        {"type": "seats", "gameId": GAME, "players": [{"id": "alexNew001", "name": "alex"}]}
    )
    [players] = backend.handle(
        page(
            *HAND,
            (
                110,
                'The player "alex @ alexNew001" changed the ID from Qw3rTy7UiO to alexNew001 because authenticated login.',
            ),
        )
    )
    [alex] = players["players"]
    assert alex["profile"]["key"] == "alex"
    assert (alex["tonight"]["hands"], alex["tonight"]["vpip"], alex["tonight"]["pfr"]) == (1, 1, 0)


HERO_HAND = [
    (200, '-- starting hand #2 (id: h2)  No Limit Texas Hold\'em (dealer: "Hero @ heroid0001") --'),
    (201, 'Player stacks: #1 "Hero @ heroid0001" (1000) | #2 "alex @ Qw3rTy7UiO" (1000)'),
    (202, "Your hand is A♠, A♦"),
    (203, '"Hero @ heroid0001" posts a small blind of 10'),
    (204, '"alex @ Qw3rTy7UiO" posts a big blind of 20'),
]
TURN = {
    "type": "turn",
    "gameId": GAME,
    "key": "k1",
    "table": {
        "heroId": "heroid0001",
        "heroCards": ["As", "Ad"],
        "board": [],
        "pot": "0",
        "bets": ["10", "20", ""],
    },
}


def test_turn_facts_wait_for_the_log_then_follow_it(backend):
    backend.handle(page(*HERO_HAND[:-1]))
    # The big blind is on the table but not yet in the log.
    assert backend.handle(TURN) == [{"type": "facts", "gameId": GAME, "key": "k1", "facts": None}]
    reply, hand = backend.handle(page(*HERO_HAND))
    assert (reply["type"], reply["key"]) == ("facts", "k1")
    # The side panel hears about the hand itself as soon as it changes.
    assert (hand["type"], hand["heroCards"], hand["position"], hand["madeHand"]) == (
        "hand",
        ["As", "Ad"],
        "BTN",
        "AA",
    )
    found = reply["facts"]
    assert (found["position"], found["pot"], found["toCall"], found["need"]) == (
        "BTN",
        "$30 (1.5bb)",
        "$10 (0.5bb)",
        25,
    )
    assert found["madeHand"] == "AA"
    # Once the turn is over, new rows no longer produce facts.
    assert backend.handle({"type": "turn", "gameId": GAME, "table": None}) == [
        {"type": "facts", "gameId": GAME, "key": "", "facts": None}
    ]
    assert backend.handle(page(*HERO_HAND, (205, '"Hero @ heroid0001" calls 20'))) == []


def test_an_error_on_a_spot_tells_the_card_instead_of_reading_forever(backend, monkeypatch, caplog):
    import vamos.host.server as server

    def broken(*args, **kwargs):
        raise ZeroDivisionError("float division by zero")

    # Reading whose turn it is fails too, not only the facts.
    monkeypatch.setattr(server, "current_spot", broken)
    backend.handle(page(*HERO_HAND))
    [reply] = backend.handle(TURN)
    assert (reply["facts"], reply["error"]) == (None, server.SPOT_ERROR)
    monkeypatch.undo()
    caplog.clear()
    monkeypatch.setattr(server, "facts", broken)
    [reply] = backend.handle({**TURN, "key": "k2"})
    assert reply["key"] == "k2"
    assert reply["facts"] is None
    assert (
        reply["error"]
        == server.SPOT_ERROR
        == ("VAMOS! hit an error on this spot (logged in data/logs/host.log)")
    )
    # Recomputed on every log page while the spot is up, but logged once.
    [again] = backend.handle({**TURN, "key": "k2"})
    assert again["error"] == reply["error"]
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1
    # Fixed, the same spot gets its facts.
    monkeypatch.undo()
    [reply] = backend.handle(TURN)
    assert reply["facts"]["advice"]["action"] is not None and "error" not in reply


def test_a_broken_preflop_rule_hands_the_spot_to_claude_and_keeps_the_facts(tmp_path, monkeypatch):
    import vamos.host.server as server

    def broken(*args, **kwargs):
        raise ZeroDivisionError("float division by zero")

    backend, fake, _ = claude_backend(tmp_path, [Reply(BET_HALF, "end_turn", {})])
    monkeypatch.setattr(server, "preflop_advice", broken)
    backend.handle(page(*HERO_HAND))
    [reply] = backend.handle(TURN)
    found = reply["facts"]
    assert (found["pot"], found["toCall"]) == ("$30 (1.5bb)", "$10 (0.5bb)")
    assert found["advice"]["source"] == "Claude"
    assert [r.preset.name for r in fake.requests] == ["standard"]


FLOP_HAND = [
    *HERO_HAND,
    (205, '"Hero @ heroid0001" raises to 60'),
    (206, '"alex @ Qw3rTy7UiO" calls 60'),
    (207, "Flop:  [K♠, 7♦, 2♣]"),
    (208, '"alex @ Qw3rTy7UiO" checks'),
]
FLOP_TURN = {
    "type": "turn",
    "gameId": GAME,
    "key": "k2",
    "table": {
        "heroId": "heroid0001",
        "heroCards": ["As", "Ad"],
        "board": ["Ks", "7d", "2c"],
        "pot": "120",
        "bets": ["", ""],
    },
}
BET_HALF = (
    '{"reason": "Player A calls too wide.", "strategy": [{"action": "bet", "size_pct_pot": 50,'
    ' "freq": 1}], "plan": ""}'
)


def claude_backend(tmp_path, script):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "alex.md").write_text(ALEX)
    fake = FakeProvider(script)
    backend = Backend(tmp_path / "data", profiles, provider=fake)
    sent = queue.Queue()
    backend.emit = sent.put
    return backend, fake, sent


def claude_backend_with_data(tmp_path, script=()):
    """Another backend on the same data and profiles, as after a restart."""
    fake = FakeProvider(list(script))
    return Backend(tmp_path / "data", tmp_path / "profiles", provider=fake), fake


def test_claude_answers_postflop_under_pseudonyms(tmp_path):
    gate = threading.Event()
    backend, fake, sent = claude_backend(
        tmp_path, [(gate, Reply(BET_HALF, "end_turn", {"input_tokens": 10_000}))]
    )
    # An earlier, finished hand: alex folded the small blind.
    finished = [
        (
            100,
            '-- starting hand #1 (id: h1)  No Limit Texas Hold\'em (dealer: "alex @ Qw3rTy7UiO") --',
        ),
        (101, 'Player stacks: #1 "alex @ Qw3rTy7UiO" (1000) | #2 "Hero @ heroid0001" (1000)'),
        (102, '"alex @ Qw3rTy7UiO" posts a small blind of 10'),
        (103, '"Hero @ heroid0001" posts a big blind of 20'),
        (104, '"alex @ Qw3rTy7UiO" folds'),
        (105, 'Uncalled bet of 10 returned to "Hero @ heroid0001"'),
        (106, '"Hero @ heroid0001" collected 20 from pot'),
        (107, "-- ending hand #1 --"),
    ]
    backend.handle(page(*finished, *FLOP_HAND))
    [reply] = backend.handle(FLOP_TURN)
    advice = reply["facts"]["advice"]
    assert (advice["source"], advice["state"], advice["action"]) == ("Claude", "thinking", None)

    [request] = fake.requests
    assert request.preset.name == "standard"
    sent_text = json.dumps(request.system) + json.dumps(request.messages)
    assert "alex" not in sent_text and "Qw3rTy7UiO" not in sent_text
    assert "Loose-passive." in sent_text and "Player A" in sent_text
    assert '\\"legal_actions\\":[\\"check\\",\\"bet\\",\\"allin\\"]' in sent_text
    # Tonight's read goes with each opponent; Hero's own numbers are not sent.
    content = request.messages[-1]["content"]
    spot = json.JSONDecoder().raw_decode(content[content.index("{") :])[0]
    reads_sent = {p["name"]: p.get("this_session") for p in spot["players"]}
    assert reads_sent["Hero"] is None
    # Finished hands only: alex's call in the hand being played is not yet history.
    assert (reads_sent["Player A"]["hands"], reads_sent["Player A"]["vpip"]) == (1, "0/1")

    gate.set()
    update = sent.get(timeout=3)
    while update["type"] != "facts":
        update = sent.get(timeout=3)
    advice = update["facts"]["advice"]
    assert (advice["source"], advice["action"], advice["why"]) == (
        "Claude",
        "BET $60 (3bb)",
        "alex calls too wide.",
    )
    assert backend.ledger.totals(datetime.now())["allTime"] > 0

    # Later log pages keep the answer and never ask again.
    again = backend.handle({**FLOP_TURN, "key": "k3"})[0]
    assert again["facts"]["advice"]["action"] == "BET $60 (3bb)"
    assert len(fake.requests) == 1

    # Asking deeper adds one call with the deep preset for the same decision.
    fake.script.append(Reply(BET_HALF, "end_turn", {}))
    backend.handle({"type": "ask", "gameId": GAME, "preset": "deep"})
    assert [r.preset.name for r in fake.requests] == ["standard", "deep"]


def test_a_failure_building_claudes_prompt_keeps_the_facts_and_says_why(tmp_path, monkeypatch):
    import vamos.host.server as server

    def broken(*args, **kwargs):
        raise KeyError("player")

    backend, fake, _ = claude_backend(tmp_path, [])
    monkeypatch.setattr(server, "claude_facts", broken)
    backend.handle(page(*FLOP_HAND))
    [reply] = backend.handle(FLOP_TURN)
    found = reply["facts"]
    assert found["pot"] and found["madeHand"]
    advice = found["advice"]
    assert (advice["state"], advice["reason"], advice["action"]) == (
        "failed",
        "internal error (logged)",
        None,
    )
    assert [a["state"] for a in advice["answers"]] == ["failed"]
    # Recorded once for the decision: later pages neither retry nor pile up failures.
    again = backend.handle({**FLOP_TURN, "key": "k3"})[0]
    assert len(again["facts"]["advice"]["answers"]) == 1 and fake.requests == []


def test_the_turn_ending_stops_claude(tmp_path):
    gate = threading.Event()
    backend, _, sent = claude_backend(tmp_path, [(gate, Reply(BET_HALF, "end_turn", {}))])
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)
    backend.handle({"type": "turn", "gameId": GAME, "table": None})
    gate.set()
    with pytest.raises(queue.Empty):
        sent.get(timeout=0.5)


def test_the_dial_is_locked_for_the_rest_of_the_hand(tmp_path):
    check = '{"reason": "", "strategy": [{"action": "check", "size_pct_pot": 0, "freq": 1}], "plan": ""}'
    backend, fake, _ = claude_backend(tmp_path, [Reply(check, "end_turn", {})] * 2)
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)
    backend.handle({"type": "turn", "gameId": GAME, "table": None})
    assert backend.handle({"type": "settings", "preset": "deep"})[0]["preset"] == "deep"
    turn_card = [
        (209, '"Hero @ heroid0001" checks'),
        (210, "Turn: K♠, 7♦, 2♣ [9♥]"),
        (211, '"alex @ Qw3rTy7UiO" checks'),
    ]
    backend.handle(page(*FLOP_HAND, *turn_card))
    table = {**FLOP_TURN["table"], "board": ["Ks", "7d", "2c", "9h"]}
    backend.handle({**FLOP_TURN, "key": "k4", "table": table})
    assert [r.preset.name for r in fake.requests] == ["standard", "standard"]


def test_the_dial_and_the_heros_names_come_from_settings(tmp_path):
    backend, fake, _ = claude_backend(tmp_path, [Reply(BET_HALF, "end_turn", {})])
    backend.handle({"type": "settings", "preset": "deep"})
    saved = json.loads((tmp_path / "data/settings.json").read_text())
    assert saved == {"preset": "deep", "aiOn": True, "showCard": True, "showBadges": True}
    saved["heroNames"] = ["Jessica Q"]
    (tmp_path / "data/settings.json").write_text(json.dumps(saved))
    (tmp_path / "profiles/alex.md").write_text(ALEX + "Hates it when Jessica Q check-raises.\n")

    backend = Backend(tmp_path / "data", tmp_path / "profiles", provider=fake)
    assert backend.preset == "deep"
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)
    sent_text = json.dumps(fake.requests[-1].system)
    assert "Jessica" not in sent_text and "when Hero check-raises" in sent_text


def test_each_new_decision_restarts_the_clock(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    now = [100.0]
    backend = Backend(tmp_path / "data", profiles, provider=FakeProvider([]), clock=lambda: now[0])
    backend.handle({**FLOP_TURN, "key": "k1"})
    backend.handle({"type": "clock", "gameId": GAME, "secondsLeft": 10})
    assert backend._deadline(GAME) == 105.0  # five seconds to spare
    now[0] = 112.0
    backend.handle({**FLOP_TURN, "key": "k2"})  # the next decision, with no gap in between
    assert backend._deadline(GAME) == 137.0  # 25 seconds until its own clock is read


def test_notes_on_one_player_go_next_to_that_player(tmp_path):
    backend, fake, _ = claude_backend(tmp_path, [Reply(BET_HALF, "end_turn", {})])
    backend.handle(
        {"type": "notes", "playerId": "Qw3rTy7UiO", "text": "very bluffy since alex lost"}
    )
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)
    content = fake.requests[0].messages[0]["content"]
    spot = json.loads(content[content.index("{") : content.rindex("}") + 1])
    by_name = {p["name"]: p for p in spot["players"]}
    assert by_name["Player A"]["hero_notes"] == "very bluffy since Player A lost"


def test_the_hero_sits_at_the_table_too_and_the_hand_is_reported_when_it_ends(backend):
    seats = {**SEATS, "hero": {"id": "heroid0001", "name": "Hero"}}
    [players] = backend.handle(seats)  # no hand dealt yet, so nothing about one
    assert players["players"][0]["hero"] is True and players["players"][0]["name"] == "Hero"
    backend.handle(page(*HERO_HAND))
    ended = backend.handle(
        page(
            *HERO_HAND,
            (205, '"Hero @ heroid0001" folds'),
            (206, 'Uncalled bet of 10 returned to "alex @ Qw3rTy7UiO"'),
            (207, '"alex @ Qw3rTy7UiO" collected 20 from pot'),
            (208, "-- ending hand #2 --"),
        )
    )
    hand = next(m for m in ended if m["type"] == "hand")
    assert (hand["ended"], hand["folded"], hand["resultBb"]) == (True, True, -0.5)


def test_a_seated_player_not_yet_dealt_in_is_hidden_in_notes(tmp_path):
    backend, fake, _ = claude_backend(tmp_path, [Reply(BET_HALF, "end_turn", {})])
    seats = {
        **SEATS,
        "players": [*SEATS["players"], {"id": "fred000001", "name": "fishyfred"}],
    }
    backend.handle(seats)
    backend.handle({"type": "notes", "playerId": "Qw3rTy7UiO", "text": "soft-plays fishyfred"})
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)
    content = fake.requests[0].messages[0]["content"]
    assert "fishyfred" not in content and "soft-plays Player" in content


def test_notes_follow_a_player_whose_id_changed(tmp_path):
    backend, fake, _ = claude_backend(tmp_path, [Reply(BET_HALF, "end_turn", {})])
    backend.handle({"type": "notes", "playerId": "Qw3rTy7UiO", "text": "very bluffy"})
    changed = [
        (
            190,
            'The player "alex @ alexNew001" changed the ID from Qw3rTy7UiO to alexNew001 because authenticated login.',
        ),
        *[(o, t.replace("Qw3rTy7UiO", "alexNew001")) for o, t in FLOP_HAND],
    ]
    backend.handle(page(*changed))
    backend.handle(FLOP_TURN)
    content = fake.requests[0].messages[0]["content"]
    assert "very bluffy" in content
    backend.handle({**SEATS, "players": [{"id": "alexNew001", "name": "alex"}]})
    [players, *_] = backend.handle({**SEATS, "players": [{"id": "alexNew001", "name": "alex"}]})
    assert players["players"][0]["notes"] == "very bluffy"


def test_an_empty_summary_section_is_not_filled_from_the_next_heading():
    from vamos.host.server import _summary

    assert _summary("## Summary\n\n## Key stats\n- VPIP 45%\n") is None
    assert _summary("## Summary\nCalls a lot. Never folds.\n") == "Calls a lot."
    assert _summary("# rj\n\nLoose floater who cold-calls.\n") == "Loose floater who cold-calls."


def test_a_draft_goes_to_drafts_and_never_to_profiles(tmp_path):
    backend, _, _ = claude_backend(tmp_path, [])
    backend.handle(SEATS)
    backend.handle(page(*FLOP_HAND))
    backend.handle({"type": "notes", "playerId": "Qw3rTy7UiO", "text": "very bluffy"})
    before = sorted(p.name for p in (tmp_path / "profiles").iterdir())
    [saved] = backend.handle({"type": "save_draft", "gameId": GAME, "playerId": "Qw3rTy7UiO"})
    assert saved["type"] == "draft_saved"
    assert re.fullmatch(r"drafts/\d{4}-\d{2}-\d{2}-alex\.md", saved["path"])
    text = (tmp_path / saved["path"]).read_text()
    assert "draft_of: alex" in text and "- Hands: 1" in text and "very bluffy" in text
    assert f"game: {GAME}" in text
    assert sorted(p.name for p in (tmp_path / "profiles").iterdir()) == before
    # Another player of the same name in the same game gets "-2" and never overwrites it.
    backend.handle(
        {**SEATS, "players": [*SEATS["players"][:1], {"id": "zzzzzzzzzz", "name": "alex"}]}
    )
    [other] = backend.handle({"type": "save_draft", "gameId": GAME, "playerId": "zzzzzzzzzz"})
    assert other["path"] == saved["path"].replace(".md", "-2.md")
    assert "player_id: Qw3rTy7UiO" in (tmp_path / saved["path"]).read_text()
    # Saving again in the same game replaces the draft; another game that day gets the next number.
    [again] = backend.handle({"type": "save_draft", "gameId": GAME, "playerId": "Qw3rTy7UiO"})
    assert again["path"] == saved["path"]
    (tmp_path / saved["path"]).write_text(text.replace(f"game: {GAME}", "game: earlier"))
    [later] = backend.handle({"type": "save_draft", "gameId": GAME, "playerId": "Qw3rTy7UiO"})
    assert later["path"] == saved["path"].replace(".md", "-3.md")  # "-2" is the other alex


def test_a_draft_frontmatter_stays_valid_for_any_nickname():
    from vamos.host.server import _draft_text
    from vamos.profiles import parse_profile

    for nickname in ("Al: the shark", "#1 fish", "[x]", "Bob, Jr"):
        text = _draft_text(
            "Al", nickname, "aaaaaaaaaa", GAME, None, None, "", datetime(2026, 10, 3)
        )
        # Moved into profiles/ as is, the draft parses, with the nickname as one alias.
        profile = parse_profile("al", text)
        assert profile.aliases == (nickname,) and profile.player_ids == ("aaaaaaaaaa",)


def test_a_profile_net_result_is_split_by_session_for_the_panel():
    from datetime import date

    from vamos.host.server import _net

    meta = {
        "net": "-41.30 (HU -15.00, A -25.00, B +6.20, C -7.50)",
        "sessions": ["HU 2026-09-30", "A 2026-09-30", "B 2026-10-01", "C 2026-10-01"],
    }
    assert _net(meta) == {
        "total": "-41.30",
        "sessions": [
            {"label": "HU", "amount": "-15.00", "date": "2026-09-30"},
            {"label": "A", "amount": "-25.00", "date": "2026-09-30"},
            {"label": "B", "amount": "+6.20", "date": "2026-10-01"},
            {"label": "C", "amount": "-7.50", "date": "2026-10-01"},
        ],
    }
    # A label of several words keeps its date.
    spaced = {"net": "-5.00 (Home game -5.00)", "sessions": ["Home game 2026-09-30"]}
    assert _net(spaced)["sessions"] == [
        {"label": "Home game", "amount": "-5.00", "date": "2026-09-30"}
    ]
    # YAML reads a bare amount as a number: shown as money, sign included.
    assert _net({"net": -41.3}) == {"total": "-41.30", "sessions": []}
    assert _net({"net": 3.0}) == {"total": "+3.00", "sessions": []}
    # Sessions given as a count or a single date never break the panel.
    assert _net({"net": "-12.50", "sessions": 3}) == {"total": "-12.50", "sessions": []}
    odd = {"net": "+1.00 (A +1.00)", "sessions": date(2026, 9, 30)}
    assert _net(odd) == {"total": "+1.00 (A +1.00)", "sessions": []}
    # Anything that does not read as sessions is shown as written.
    for text in (
        "up a bit (mostly HU)",
        "+3 (A +4, B ?)",
        "-41.30 (HU -15.00; A -26.30)",
        "-41.30 (approx 41)",
    ):
        assert _net({"net": text}) == {"total": text, "sessions": []}, text
    assert _net({"net": "-1.00 (Z -1.00)", "sessions": ["A 2026-09-30"]})["sessions"] == []
    # A single session named alone carries the whole net.
    one = {"net": "+8.25 (D)", "sessions": ["D 2026-10-04"]}
    assert _net(one) == {
        "total": "+8.25",
        "sessions": [{"label": "D", "amount": "+8.25", "date": "2026-10-04"}],
    }
    assert _net({}) is None


def test_a_profile_with_odd_frontmatter_still_lists_the_players(tmp_path):
    backend, _, _ = claude_backend(tmp_path, [])
    (tmp_path / "profiles" / "alex.md").write_text(
        "---\nname: alex\nplayer_id: Qw3rTy7UiO\nnet: -12.50\nsessions: 3\n---\n# alex\n"
    )
    [players, *_] = backend.handle(SEATS)
    alex = next(p for p in players["players"] if p["id"] == "Qw3rTy7UiO")
    assert alex["profile"]["net"] == {"total": "-12.50", "sessions": []}


def test_turning_claude_off_stops_calls_and_keeps_the_facts(tmp_path):
    backend, fake, _ = claude_backend(tmp_path, [])
    backend.handle(page(*FLOP_HAND))
    [settings] = backend.handle({"type": "settings", "aiOn": False})
    assert settings["aiOn"] is False
    [reply] = backend.handle(FLOP_TURN)
    found = reply["facts"]
    assert (found["advice"], found["aiOff"]) == (None, True) and found["madeHand"]
    assert fake.requests == []
    # Everything but Claude keeps running: the log is read, saved, and tonight's counts move.
    [players, *_] = backend.handle(SEATS)
    alex = next(p for p in players["players"] if p["id"] == "Qw3rTy7UiO")
    assert (alex["tonight"]["hands"], alex["tonight"]["vpip"]) == (1, 1)
    assert (tmp_path / f"data/games/{GAME}.jsonl").exists()
    # Remembered across restarts, and back on when asked.
    again, _ = claude_backend_with_data(tmp_path)
    assert again.ai_on is False


def test_a_restarted_backend_keeps_tonights_rows_and_says_where_to_resume(tmp_path):
    first, _, _ = claude_backend(tmp_path, [])
    first.handle(page(*HERO_HAND))
    [resume] = first.handle({"type": "resume_request", "gameId": GAME})
    assert resume == {"type": "resume", "gameId": GAME, "after": "204"}
    # A new backend on the same data folder (a refreshed tab) has the same night.
    second, _ = claude_backend_with_data(tmp_path)
    assert second._session(GAME).newest == 204
    assert len(second._session(GAME).log.hands) == 1
    [resume] = second.handle({"type": "resume_request", "gameId": GAME})
    assert resume["after"] == "204"
    # An unknown game has nothing to resume from, and odd game IDs never name a file.
    assert second.handle({"type": "resume_request", "gameId": "other"})[0]["after"] is None
    assert second._session("../x")._store is None


def test_notes_on_a_player_are_saved_with_the_game_and_back_after_a_restart(tmp_path):
    first, _, _ = claude_backend(tmp_path, [])
    first.handle(SEATS)
    first.handle({"type": "notes", "gameId": GAME, "playerId": "Qw3rTy7UiO", "text": "tilted"})
    saved = json.loads((tmp_path / f"data/games/{GAME}.notes.json").read_text())
    assert saved == {"Qw3rTy7UiO": "tilted"}
    # A restarted backend has them as soon as the game comes back.
    second, _ = claude_backend_with_data(tmp_path)
    [players, *_] = second.handle(SEATS)
    alex = next(p for p in players["players"] if p["id"] == "Qw3rTy7UiO")
    assert alex["notes"] == "tilted"
    # Clearing a note removes it from the file too.
    second.handle({"type": "notes", "gameId": GAME, "playerId": "Qw3rTy7UiO", "text": ""})
    assert json.loads((tmp_path / f"data/games/{GAME}.notes.json").read_text()) == {}


def test_profile_stats_allow_a_few_words_before_the_number():
    from vamos.host.server import _profile_view
    from vamos.profiles import parse_profile

    body = "---\nname: x\n---\n- C-bets only 20% (1/5)\n- VPIP 49% (23/47)\n- VPIP by seat 10%\n"
    stats = _profile_view(parse_profile("x", body))["stats"]
    assert stats["cbet"] == {"pct": "20", "count": "1/5"}
    assert stats["vpip"] == {"pct": "49", "count": "23/47"}


def test_profile_stats_read_the_templates_wording_and_the_older_one():
    from vamos.host.server import _profile_view
    from vamos.profiles import parse_profile

    template = (
        "---\nname: x\n---\n## Summary\nCalls a lot. Facing a 3-bet: call 82% (14/17).\n"
        "## Key stats\n- VPIP 48% (304/627), PFR 11% (68/627)\n"
        "- 3-bet 5% (11/232); facing a 3-bet or 4-bet: call 82% (14/17)\n"
        "- Flop c-bet 90% (18/20); facing a flop bet: fold 50% (76/152)\n"
    )
    older = (
        "---\nname: y\n---\n- 3-bet vs an open 6% (7/114)\n- C-bets 61% (36/59)\n"
        "- Faced a bet: fold 35% (33/94)\n"
    )
    want = (
        {"pct": "5", "count": "11/232"},
        {"pct": "90", "count": "18/20"},
        {"pct": "50", "count": "76/152"},
    )
    stats = _profile_view(parse_profile("x", template))["stats"]
    assert (stats["threeBet"], stats["cbet"], stats["foldToFlopBet"]) == want
    stats = _profile_view(parse_profile("y", older))["stats"]
    assert (stats["threeBet"]["pct"], stats["cbet"]["pct"], stats["foldToFlopBet"]["pct"]) == (
        "6",
        "61",
        "35",
    )


def test_claude_gets_only_the_profiles_of_players_at_the_table(tmp_path):
    backend, fake, _ = claude_backend(tmp_path, [])
    # A second friend with a profile, not at this table.
    (tmp_path / "profiles" / "zed.md").write_text(
        "---\nname: zed\nplayer_id: zzzzzzzzzz9\n---\n## Summary\nNever folds the river.\n"
    )
    backend.handle(SEATS)
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)
    [request] = fake.requests
    system = json.dumps(request.system)
    assert "Loose-passive." in system  # alex, seated
    assert "Never folds the river." not in system  # zed, not at the table


def test_profile_stats_come_from_key_stats_before_the_summary():
    from vamos.host.server import _profile_view
    from vamos.profiles import parse_profile

    body = (
        "---\nname: x\n---\n## Summary\nTighter lately (recent VPIP 38%, 61/160).\n\n"
        "## Key stats\n- VPIP 44% (264/600), PFR 15% (90/600)\n- Recent: VPIP 38% (61/160)\n"
    )
    stats = _profile_view(parse_profile("x", body))["stats"]
    assert stats["vpip"] == {"pct": "44", "count": "264/600"}
    assert stats["pfr"] == {"pct": "15", "count": "90/600"}


def test_a_player_keeps_one_draft_per_game_past_midnight_and_an_id_change(tmp_path, monkeypatch):
    import vamos.host.server as server

    backend, _, _ = claude_backend(tmp_path, [])
    backend.handle(SEATS)
    backend.handle(page(*FLOP_HAND))
    late = type("Clock", (), {"now": staticmethod(lambda: datetime(2026, 10, 5, 23, 50))})
    monkeypatch.setattr(server, "datetime", late)
    [first] = backend.handle({"type": "save_draft", "gameId": GAME, "playerId": "Qw3rTy7UiO"})
    assert first["path"] == "drafts/2026-10-05-alex.md"
    # After midnight and an ID change, the same game's draft is replaced, not split in two.
    after = type("Clock", (), {"now": staticmethod(lambda: datetime(2026, 10, 6, 0, 10))})
    monkeypatch.setattr(server, "datetime", after)
    changed = (
        300,
        'The player "alex @ alexNew001" changed the ID from Qw3rTy7UiO to alexNew001 because authenticated login.',
    )
    backend.handle(page(*FLOP_HAND, changed))
    [second] = backend.handle({"type": "save_draft", "gameId": GAME, "playerId": "alexNew001"})
    assert second["path"] == first["path"]
    assert sorted(p.name for p in (tmp_path / "drafts").iterdir()) == ["2026-10-05-alex.md"]
    assert "player_id: alexNew001" in (tmp_path / first["path"]).read_text()


def test_two_different_failures_on_one_spot_are_both_logged(tmp_path, monkeypatch, caplog):
    import vamos.host.server as server

    backend, _, _ = claude_backend(tmp_path, [])

    def broken(*args, **kwargs):
        raise ZeroDivisionError("broken")

    monkeypatch.setattr(server, "preflop_advice", broken)
    monkeypatch.setattr(backend, "_advise", broken)
    backend.handle(page(*HERO_HAND))
    backend.handle(TURN)
    backend.handle(TURN)  # the same spot again: nothing new is logged
    logged = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(logged) == 2
    assert "the preflop rules" in logged[0] and "Claude's prompt" in logged[1]


def test_earlier_hands_replay_with_the_advice_given_even_after_a_restart(tmp_path):
    backend, _, _ = claude_backend(tmp_path, [])
    backend.handle({**SEATS, "hero": {"id": "heroid0001", "name": "Hero"}})
    backend.handle(page(*HERO_HAND))
    [facts] = backend.handle(TURN)  # aces first in: a rule raises at once
    assert facts["facts"]["advice"]["source"] == "Rule"
    finished = [
        *HERO_HAND,
        (205, '"Hero @ heroid0001" raises to 50'),
        (206, '"alex @ Qw3rTy7UiO" folds'),
        (207, 'Uncalled bet of 30 returned to "Hero @ heroid0001"'),
        (208, '"Hero @ heroid0001" collected 40 from pot'),
        (209, "-- ending hand #2 --"),
    ]
    backend.handle({"type": "turn", "gameId": GAME, "table": None})
    replies = backend.handle(page(*finished))
    [history] = [m for m in replies if m["type"] == "history"]
    assert [(h["key"], h["resultBb"], h["decisions"]) for h in history["hands"]] == [("h2", 1.0, 1)]

    def replay(b):
        [detail] = b.handle({"type": "hand_detail_request", "gameId": GAME, "key": "h2"})
        return [e.get("text") or e["advice"]["action"] for e in detail["streets"][0]["events"]]

    expected = [
        "posts the small blind",
        "posts the big blind",
        "RAISE TO $50 (2.5bb)",
        "raises to $50 (2.5bb)",
        "folds",
    ]
    assert replay(backend) == expected
    # A restarted backend reads the decisions back from the game's saved file.
    again, _ = claude_backend_with_data(tmp_path)
    again.handle({**SEATS, "hero": {"id": "heroid0001", "name": "Hero"}})
    again.handle(page(*finished))
    assert replay(again) == expected


def test_decision_records_say_when_the_card_showed_them_and_when_the_advice_changed(tmp_path):
    gate = threading.Event()
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    fake = FakeProvider([(gate, Reply(BET_HALF, "end_turn", {}))])
    now = [datetime(2026, 10, 7, 21, 0, 0, 125000).astimezone()]
    backend = Backend(tmp_path / "data", profiles, provider=fake, now=lambda: now[0])
    sent = queue.Queue()
    backend.emit = sent.put
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)  # the card shows the spot while Claude thinks
    now[0] += timedelta(seconds=1)
    backend.handle({**FLOP_TURN, "key": "k3"})  # the same advice: nothing new to record
    now[0] += timedelta(seconds=2.5)
    gate.set()
    while sent.get(timeout=3)["type"] != "facts":
        pass
    path = tmp_path / "data" / "games" / f"{GAME}.decisions.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["advice"]["state"] for r in records] == ["thinking", "done"]
    # To the millisecond, with the time zone, to set against the log rows' times.
    shown = (now[0] - timedelta(seconds=3.5)).isoformat(timespec="milliseconds")
    assert re.fullmatch(r"2026-10-07T21:00:00\.125[+-]\d\d:\d\d", shown)
    assert [r["shownAt"] for r in records] == [shown, shown]
    assert [r["at"] for r in records] == [shown, now[0].isoformat(timespec="milliseconds")]

    # A restarted backend asks again: a new record, still shown since the first one.
    again, _ = claude_backend_with_data(tmp_path, [(threading.Event(), Reply(BET_HALF, "", {}))])
    again.handle(page(*FLOP_HAND))
    again.handle(FLOP_TURN)
    last = json.loads(path.read_text().splitlines()[-1])
    assert (last["advice"]["state"], last["shownAt"]) == ("thinking", shown)
    assert last["at"] != shown
    again.advisor.close()


def test_a_failed_decision_record_leaves_the_card_its_advice(tmp_path, monkeypatch, caplog):
    backend, _, _ = claude_backend(tmp_path, [])
    backend.handle({**SEATS, "hero": {"id": "heroid0001", "name": "Hero"}})
    backend.handle(page(*HERO_HAND))
    monkeypatch.setattr(backend, "_record_decision", lambda *args: 1 / 0)
    [facts] = backend.handle(TURN)
    assert facts["facts"]["advice"]["source"] == "Rule" and "error" not in facts
    assert any("the decision record" in r.getMessage() for r in caplog.records)


def test_hands_played_before_the_record_replay_claudes_answers_from_the_advice_log(tmp_path):
    sent = {
        "players": [
            {"name": "Hero", "position": "BTN", "stack_bb": 50.0, "behind_bb": 49.5},
            {"name": "Player A", "position": "BB", "stack_bb": 50.0, "behind_bb": 49.0},
        ],
        "facts": {"to_call_bb": 0.5},
    }
    entry = {
        "game": GAME,
        "node": "h2:0:2",
        "kind": "preflop",
        "model": "claude-opus-5-5",
        "state": "done",
        "spot": f"Hand so far (amounts in big blinds):\n{json.dumps(sent)}\n",
        "answer": json.dumps({"reason": "Player A folds too often.", "plan": ""}),
        "mix": [["raise", 5000, 1.0]],
        "chosen": ["raise", 5000],
    }
    other_game = {**entry, "game": "pgl999"}
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "advice.jsonl").write_text(
        json.dumps(other_game) + "\n" + json.dumps(entry) + "\n"
    )
    backend, fake, _ = claude_backend(tmp_path, [])
    backend.handle({**SEATS, "hero": {"id": "heroid0001", "name": "Hero"}})
    finished = [
        *HERO_HAND,
        (205, '"Hero @ heroid0001" raises to 50'),
        (206, '"alex @ Qw3rTy7UiO" folds'),
        (207, 'Uncalled bet of 30 returned to "Hero @ heroid0001"'),
        (208, '"Hero @ heroid0001" collected 40 from pot'),
        (209, "-- ending hand #2 --"),
    ]
    [history] = [m for m in backend.handle(page(*finished)) if m["type"] == "history"]
    assert [h["decisions"] for h in history["hands"]] == [1]
    [detail] = backend.handle({"type": "hand_detail_request", "gameId": GAME, "key": "h2"})
    [decision] = [e for e in detail["streets"][0]["events"] if e["kind"] == "decision"]
    [answer] = decision["advice"]["answers"]
    assert (answer["action"], answer["why"]) == ("RAISE TO $50 (2.5bb)", "alex folds too often.")
    assert detail["partial"] is True
    # Replaying reads local files only: Claude is never asked and nothing is billed.
    assert fake.requests == [] and not (tmp_path / "data" / "costs.jsonl").exists()


def test_every_saved_game_is_kept_however_old(tmp_path):
    games = tmp_path / "data" / "games"
    games.mkdir(parents=True)
    old = time.time() - 30 * 86400
    names = ["pglold.decisions.jsonl", "pglold.jsonl", "pglold.notes.json"]
    for name in names:
        (games / name).write_text("{}\n")
        os.utime(games / name, (old, old))
    # A backend starting, as at every table opened, deletes nothing: the user analyzes them later.
    Backend(tmp_path / "data", tmp_path / "profiles")
    assert sorted(p.name for p in games.iterdir()) == names
