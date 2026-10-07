"""
Appending to VAMOS's JSON Lines files: each game's saved rows and decision records, the
advice log and the cost ledger.
"""

import os
from collections.abc import Iterable
from pathlib import Path


def append_lines(path: Path, lines: Iterable[str]) -> None:
    """
    Append each line (one JSON value, no newline) to `path`. A crash can leave the file's last
    line half-written; the new lines then start on a line of their own, or the first would be
    glued onto it and lost with it on the next read.
    """
    data = "".join(f"{line}\n" for line in lines).encode()
    if not data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                data = b"\n" + data
        f.write(data)
