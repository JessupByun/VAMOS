"""
The native messaging host. Chrome launches it when the first PokerNow table opens and
closes its stdin when the last one closes, which ends `serve`.
"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO

from vamos.config import DATA_DIR, VERSION
from vamos.host.framing import read_message, write_message

log = logging.getLogger("vamos.host")


def _record(data_dir: Path, message: dict[str, Any]) -> dict[str, str]:
    """Save raw log pages and DOM snapshots for building test fixtures (P0 spike)."""
    game_dir = data_dir / "recordings" / message["gameId"]
    game_dir.mkdir(parents=True, exist_ok=True)
    if message["type"] == "log_page":
        with (game_dir / "log_pages.jsonl").open("a") as f:
            f.write(json.dumps(message) + "\n")
        body = message.get("body")
        rows = len(body["logs"]) if isinstance(body, dict) and "logs" in body else "?"
        return {"type": "ack", "what": "log_page", "detail": f"{rows} rows"}
    path = game_dir / f"dom_{message['capturedAt']}.html"
    path.write_text(message["html"])
    return {"type": "ack", "what": "dom_snapshot", "detail": path.name}


def serve(stdin: BinaryIO, stdout: BinaryIO, data_dir: Path) -> None:
    while (message := read_message(stdin)) is not None:
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "hello":
            write_message(stdout, {"type": "ready", "version": VERSION})
        elif kind in ("log_page", "dom_snapshot"):
            write_message(stdout, _record(data_dir, message))
        else:
            log.warning("unknown message type %r", kind)


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
    log.info("[VAMOS] host %s started", VERSION)
    started = time.monotonic()
    try:
        serve(sys.stdin.buffer, stdout, DATA_DIR)
    except Exception:
        log.exception("[VAMOS] host crashed")
        raise
    finally:
        log.info("[VAMOS] host exiting after %.0fs", time.monotonic() - started)
