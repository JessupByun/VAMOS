"""
Chrome native messaging framing: each message is a 4-byte little-endian length followed by
that many bytes of UTF-8 JSON, on stdin (from Chrome) and stdout (to Chrome).
"""

import json
import struct
from typing import Any, BinaryIO

# Chrome rejects host-to-extension messages larger than 1 MB.
MAX_OUTGOING_BYTES = 1024 * 1024


def read_message(stream: BinaryIO) -> Any | None:
    """The next message, or None once Chrome closes the pipe."""
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    body = stream.read(length)
    if len(body) < length:
        return None
    return json.loads(body)


def write_message(stream: BinaryIO, message: Any) -> None:
    data = json.dumps(message, separators=(",", ":")).encode()
    if len(data) > MAX_OUTGOING_BYTES:
        raise ValueError(f"message is {len(data)} bytes, over Chrome's 1 MB limit")
    stream.write(struct.pack("<I", len(data)))
    stream.write(data)
    stream.flush()
