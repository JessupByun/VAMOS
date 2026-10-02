import io
import json
import struct

import pytest

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


def test_hello_reports_ready(backend):
    assert backend.handle({"type": "hello"}) == [
        {"type": "ready", "version": VERSION, "recording": False}
    ]


def test_seated_players_resolve_to_profiles_or_ask(backend):
    [players] = backend.handle(SEATS)
    alex, stranger = players["players"]
    assert alex["status"] == "profile"
    assert alex["profile"] == {"key": "alex", "name": "alex", "tag": "Calling station"}
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
    assert tonight == {"Qw3rTy7UiO": {"hands": 1, "vpip": 1}, "zzzzzzzzzz": {"hands": 1, "vpip": 0}}
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
    assert read_all(stdout) == [{"type": "ready", "version": VERSION, "recording": False}]


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
    assert alex["tonight"] == {"hands": 1, "vpip": 1}
