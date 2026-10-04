import io
import json
import queue
import struct
import threading
from datetime import datetime

import pytest

from vamos.brain.provider import FakeProvider, Reply
from vamos.config import VERSION
from vamos.host.framing import MAX_OUTGOING_BYTES, read_message, write_message
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
        "preset": "balanced",
        "presets": [
            {"name": "balanced", "label": "Sonnet 5.5", "effort": "medium", "seconds": "2-5"},
            {"name": "deep", "label": "Opus 5.5", "effort": "medium", "seconds": "5-14"},
        ],
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
    assert tonight == {
        "Qw3rTy7UiO": {"hands": 1, "vpip": 1, "pfr": 0},
        "zzzzzzzzzz": {"hands": 1, "vpip": 0, "pfr": 0},
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
    assert alex["tonight"] == {"hands": 1, "vpip": 1, "pfr": 0}


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
        "1.5bb",
        "10 (0.5bb)",
        25,
    )
    assert found["madeHand"] == "AA"
    # Once the turn is over, new rows no longer produce facts.
    assert backend.handle({"type": "turn", "gameId": GAME, "table": None}) == [
        {"type": "facts", "gameId": GAME, "key": "", "facts": None}
    ]
    assert backend.handle(page(*HERO_HAND, (205, '"Hero @ heroid0001" calls 20'))) == []


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


def test_claude_answers_postflop_under_pseudonyms(tmp_path):
    gate = threading.Event()
    backend, fake, sent = claude_backend(
        tmp_path, [(gate, Reply(BET_HALF, "end_turn", {"input_tokens": 10_000}))]
    )
    backend.handle(page(*FLOP_HAND))
    [reply] = backend.handle(FLOP_TURN)
    advice = reply["facts"]["advice"]
    assert (advice["source"], advice["state"], advice["action"]) == ("Claude", "thinking", None)

    [request] = fake.requests
    assert request.preset.name == "balanced"
    sent_text = json.dumps(request.system) + json.dumps(request.messages)
    assert "alex" not in sent_text and "Qw3rTy7UiO" not in sent_text
    assert "Loose-passive." in sent_text and "Player A" in sent_text
    assert (
        '\\"legal_actions\\": [\\n  \\"check\\",\\n  \\"bet\\",\\n  \\"allin\\"\\n ]' in sent_text
    )

    gate.set()
    update = sent.get(timeout=3)
    while update["type"] != "facts":
        update = sent.get(timeout=3)
    advice = update["facts"]["advice"]
    assert (advice["source"], advice["action"], advice["why"]) == (
        "Claude",
        "BET 60 (3bb)",
        "alex calls too wide.",
    )
    assert backend.ledger.totals(datetime.now())["allTime"] > 0

    # Later log pages keep the answer and never ask again.
    again = backend.handle({**FLOP_TURN, "key": "k3"})[0]
    assert again["facts"]["advice"]["action"] == "BET 60 (3bb)"
    assert len(fake.requests) == 1

    # Asking Opus adds one deep call for the same decision.
    fake.script.append(Reply(BET_HALF, "end_turn", {}))
    backend.handle({"type": "ask", "gameId": GAME, "preset": "deep"})
    assert [r.preset.name for r in fake.requests] == ["balanced", "deep"]


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
    assert [r.preset.name for r in fake.requests] == ["balanced", "balanced"]


def test_the_dial_and_the_heros_names_come_from_settings(tmp_path):
    backend, fake, _ = claude_backend(tmp_path, [Reply(BET_HALF, "end_turn", {})])
    backend.handle({"type": "settings", "preset": "deep"})
    saved = json.loads((tmp_path / "data/settings.json").read_text())
    assert saved == {"preset": "deep"}
    saved["heroNames"] = ["Jessica Q"]
    (tmp_path / "data/settings.json").write_text(json.dumps(saved))
    (tmp_path / "profiles/alex.md").write_text(ALEX + "Hates it when Jessica Q check-raises.\n")

    backend = Backend(tmp_path / "data", tmp_path / "profiles", provider=fake)
    assert backend.preset == "deep"
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)
    sent_text = json.dumps(fake.requests[-1].system)
    assert "Jessica" not in sent_text and "when Hero check-raises" in sent_text


def test_notes_are_sent_under_pseudonyms(tmp_path):
    backend, fake, _ = claude_backend(tmp_path, [Reply(BET_HALF, "end_turn", {})])
    backend.handle({"type": "notes", "text": "alex is on tilt"})
    backend.handle(page(*FLOP_HAND))
    backend.handle(FLOP_TURN)
    content = fake.requests[0].messages[0]["content"]
    assert "Player A is on tilt" in content and "alex" not in content


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
