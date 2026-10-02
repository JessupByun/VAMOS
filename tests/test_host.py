import io
import json
import struct

import pytest

from vamos.config import VERSION
from vamos.host.framing import MAX_OUTGOING_BYTES, read_message, write_message
from vamos.host.install import EXTENSION_ID, HOST_NAME, install
from vamos.host.server import serve


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


def test_serve_answers_hello_and_records_pages(tmp_path):
    page = {"type": "log_page", "gameId": "g1", "fetchedAt": 1, "body": {"logs": [{}, {}]}}
    snapshot = {"type": "dom_snapshot", "gameId": "g1", "capturedAt": 7, "html": "<html/>"}
    stdin = io.BytesIO(frame({"type": "hello"}) + frame(page) + frame(snapshot))
    stdout = io.BytesIO()

    serve(stdin, stdout, tmp_path)

    assert read_all(stdout) == [
        {"type": "ready", "version": VERSION},
        {"type": "ack", "what": "log_page", "detail": "2 rows"},
        {"type": "ack", "what": "dom_snapshot", "detail": "dom_7.html"},
    ]
    recorded = (tmp_path / "recordings/g1/log_pages.jsonl").read_text().splitlines()
    assert json.loads(recorded[0]) == page
    assert (tmp_path / "recordings/g1/dom_7.html").read_text() == "<html/>"


def test_serve_ignores_unknown_messages(tmp_path):
    stdout = io.BytesIO()
    serve(io.BytesIO(frame({"type": "nope"}) + frame("not a dict")), stdout, tmp_path)
    assert read_all(stdout) == []


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
    assert '"/py/bin/python" -m vamos host' in launcher.read_text()
