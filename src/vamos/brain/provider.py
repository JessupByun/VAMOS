"""
Claude calls for advice, before and after the flop.

A call streams, so a decision that has already passed can stop it from a watcher thread,
since thinking streams no events while it runs (its text is omitted by default). The watcher
shuts the socket down, which wakes the read waiting on it; the read then fails and the call
closes its own stream. The watcher does not close it too: on macOS a socket closed by another
thread while a read waits on it could leave that read blocked until the 60 s request timeout,
and closing alone always did. Whether the API bills tokens it generated
after the connection dropped is unverified; the ledger records the usage seen so far. Failures
come back as `CallFailed` with a short reason for the card. The SDK's own retries are off:
the advisor owns the deadline and decides whether there is time to try again.

The API key comes from ANTHROPIC_API_KEY in the environment or in the repo's .env file
(gitignored); it is never logged.
"""

import contextlib
import json
import os
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from vamos.brain.answer import SCHEMA
from vamos.config import REPO_ROOT


@dataclass(frozen=True)
class Preset:
    name: str
    model: str
    # Adaptive thinking is on by default; effort sets how much it may use.
    effort: str | None = None
    label: str = ""  # for the side panel
    seconds: str = ""  # measured time to an answer, for the side panel


# Opus 5.5 only. On 36 logged spots judged blind (2026-10-03), Opus at low and medium
# effort both beat Sonnet 5.5 at medium clearly (a third of the factual errors, no clear
# mistakes); low answered in 3.3s typically (never over 11s), medium in 6.6s (3 of 36 past
# a 14s clock). Haiku 4.5 and Sonnet 5.5 were dropped.
PRESETS = {
    "standard": Preset("standard", "claude-opus-5-5", "low", "Opus 5.5 · low thinking", "2-7"),
    "deep": Preset("deep", "claude-opus-5-5", "medium", "Opus 5.5 · medium thinking", "5-14"),
}
DEFAULT_PRESET = "standard"
MAX_TOKENS = 16_000  # room for thinking as well as the answer
REQUEST_TIMEOUT = 60.0  # seconds; the advisor's deadline is usually much shorter


@dataclass(frozen=True)
class Request:
    preset: Preset
    system: list[dict[str, Any]]  # cacheable prefix blocks
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class Reply:
    text: str
    stop_reason: str | None
    usage: dict[str, Any] = field(default_factory=dict)


class CallFailed(Exception):
    """A call that produced no usable answer; `reason` is shown on the card."""

    def __init__(self, reason: str, usage: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.usage = usage or {}


class Provider(Protocol):
    def run(
        self,
        request: Request,
        cancelled: threading.Event,
        replying: threading.Event | None = None,  # set at Claude's first event
    ) -> Reply: ...


def api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env = REPO_ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        name, _, value = line.strip().partition("=")
        if name.strip() == "ANTHROPIC_API_KEY" and value.strip():
            return value.strip().strip("'\"")
    return None


def params(request: Request) -> dict[str, Any]:
    """The Messages API arguments for a request (separate so tests can check them)."""
    preset = request.preset
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": SCHEMA}}
    out: dict[str, Any] = {
        "model": preset.model,
        "max_tokens": MAX_TOKENS,
        "system": request.system,
        "messages": request.messages,
        "output_config": output_config,
    }
    if preset.effort:
        output_config["effort"] = preset.effort
    return out


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    out = {name: getattr(usage, name, None) or 0 for name in fields}
    created = getattr(usage, "cache_creation", None)
    if created is not None:
        out["cache_creation"] = {
            "ephemeral_5m_input_tokens": getattr(created, "ephemeral_5m_input_tokens", 0) or 0,
            "ephemeral_1h_input_tokens": getattr(created, "ephemeral_1h_input_tokens", 0) or 0,
        }
    return out


class AnthropicProvider:
    def __init__(self, client: Any = None) -> None:
        self._client = client  # built lazily from the API key unless given (tests)

    def _client_or_fail(self):
        if self._client is None:
            key = api_key()
            if not key:
                raise CallFailed("no API key")
            import anthropic

            self._client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=REQUEST_TIMEOUT)
        return self._client

    def run(
        self,
        request: Request,
        cancelled: threading.Event,
        replying: threading.Event | None = None,
    ) -> Reply:
        client = self._client_or_fail()
        usage: dict[str, Any] = {}
        try:
            with client.messages.stream(**params(request)) as stream:
                finished = threading.Event()
                watcher = threading.Thread(
                    target=_close_when, args=(cancelled, finished, stream), daemon=True
                )
                watcher.start()
                try:
                    for event in stream:
                        if replying is not None:
                            replying.set()
                        if event.type == "message_start":
                            usage = _usage_dict(event.message.usage)
                        elif event.type == "message_delta" and event.usage is not None:
                            usage["output_tokens"] = event.usage.output_tokens or 0
                        if cancelled.is_set():
                            break
                    if cancelled.is_set():
                        raise CallFailed(STOPPED, usage)
                    final = stream.get_final_message()
                finally:
                    finished.set()
        except CallFailed:
            raise
        except Exception as error:
            if cancelled.is_set():
                # Closing the stream mid-read surfaces as whatever the transport raises.
                raise CallFailed(STOPPED, usage) from error
            raise _failure(error, usage) from error
        usage = _usage_dict(final.usage)
        if final.stop_reason == "refusal":
            raise CallFailed("Claude declined", usage)
        if final.stop_reason == "max_tokens":
            raise CallFailed("answer cut off", usage)
        text = "".join(block.text for block in final.content if block.type == "text")
        return Reply(text, final.stop_reason, usage)


STOPPED = "stopped: the decision passed"


def _close_when(cancelled: threading.Event, finished: threading.Event, stream: Any) -> None:
    while not finished.is_set():
        if cancelled.wait(0.1):
            shut = False
            with contextlib.suppress(Exception):  # already closing or closed
                shut = _shut(stream)
            if not shut:
                # No socket to reach (the SDK changed?): closing still ends the call, if late.
                with contextlib.suppress(Exception):
                    stream.close()
            return


def _shut(stream: Any) -> bool:
    """Shut the stream's socket down, which wakes a read blocked on it (closing does not)."""
    response = getattr(getattr(stream, "_raw_stream", None), "response", None)
    network = response.extensions.get("network_stream") if response is not None else None
    sock = network.get_extra_info("socket") if network is not None else None
    if sock is None:
        return False
    sock.shutdown(socket.SHUT_RDWR)
    return True


def _failure(error: Exception, usage: dict[str, Any]) -> CallFailed:
    """The reason for the card, with the usage seen before the error: an error can arrive
    mid-stream (Claude overloaded, a dropped connection) after the input was billed."""
    import anthropic

    # Out of credits comes as a 402 billing error, or as a 400 naming the credit balance.
    if isinstance(error, anthropic.APIStatusError) and (
        error.status_code == 402 or "credit balance" in str(error).lower()
    ):
        return CallFailed("out of API credits", usage)
    if isinstance(error, anthropic.AuthenticationError):
        return CallFailed("API key rejected", usage)
    if isinstance(error, anthropic.PermissionDeniedError):
        return CallFailed("API key not allowed to use this model", usage)
    if isinstance(error, anthropic.RateLimitError):
        return CallFailed("rate limited", usage)
    if isinstance(error, anthropic.BadRequestError):
        return CallFailed("request rejected", usage)
    if isinstance(error, anthropic.APITimeoutError):
        return CallFailed("timed out", usage)
    if isinstance(error, anthropic.APIConnectionError):
        return CallFailed("no connection to Claude", usage)
    if isinstance(error, anthropic.APIStatusError):
        return CallFailed(f"Claude unavailable ({error.status_code})", usage)
    return CallFailed(f"unexpected error ({type(error).__name__})", usage)


class CannedProvider:
    """
    End-to-end tests: every call answers with the file's text, so they never reach the API.
    The file is read on each call, so a test can change the answer between decisions. Three
    keys in it are for the test, not the answer: "wait_seconds" makes Claude slow, "fail"
    fails the call with that reason, and "usage" is the tokens Claude reports, to be billed.
    """

    def __init__(self, path: Path):
        self.path = path

    def run(
        self,
        request: Request,
        cancelled: threading.Event,
        replying: threading.Event | None = None,
    ) -> Reply:
        if replying is not None:
            replying.set()  # Claude's first event comes at once; the answer may take a while
        text = self.path.read_text()
        try:
            canned = json.loads(text)
        except ValueError:
            return Reply(text, "end_turn", {})
        if not isinstance(canned, dict) or not {"wait_seconds", "fail", "usage"} & canned.keys():
            return Reply(text, "end_turn", {})
        usage = canned.pop("usage", None) or {}
        if cancelled.wait(float(canned.pop("wait_seconds", 0))):
            raise CallFailed(STOPPED)
        if "fail" in canned:
            raise CallFailed(str(canned["fail"]))
        return Reply(json.dumps(canned), "end_turn", usage)


type Scripted = (
    Reply
    | CallFailed
    | tuple[threading.Event, Reply | CallFailed]
    | tuple[threading.Event, threading.Event, Reply | CallFailed]
)


class FakeProvider:
    """
    A scripted Claude for tests: each call returns (or raises) the next scripted item, or,
    scripted by preset name, its preset's item whichever call asks first. Claude's first
    event comes at once. A slow call is scripted as (gate, item), ending once the test opens
    the gate, or as (first, gate, item), whose first event also waits for `first` and never
    comes if the gate opens before it.
    """

    def __init__(self, script: list[Scripted] | dict[str, Scripted]):
        self.script = dict(script) if isinstance(script, dict) else list(script)
        self.requests: list[Request] = []

    def run(
        self,
        request: Request,
        cancelled: threading.Event,
        replying: threading.Event | None = None,
    ) -> Reply:
        self.requests.append(request)
        if isinstance(self.script, dict):
            item = self.script[request.preset.name]
        else:
            item = self.script.pop(0)
        first: threading.Event | None = None
        gate: threading.Event | None = None
        if isinstance(item, tuple):
            first, gate, item = item if len(item) == 3 else (None, *item)

        def began() -> None:
            if replying is not None and (first is None or first.is_set()):
                replying.set()

        began()
        while gate is not None and not gate.wait(0.01):
            began()
            if cancelled.is_set():
                raise CallFailed(STOPPED)
        began()
        if isinstance(item, CallFailed):
            raise item
        return item
