"""
Claude calls for postflop advice.

A call streams, so a decision that has already passed can stop it: the stream is closed
from a watcher thread, since thinking streams no events while it runs (its text is omitted
by default), and only tokens produced up to then are billed. Failures
come back as `CallFailed` with a short reason for the card. The SDK's own retries are off:
the advisor owns the deadline and decides whether there is time to try again.

The API key comes from ANTHROPIC_API_KEY in the environment or in the repo's .env file
(gitignored); it is never logged.
"""

import contextlib
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from vamos.brain.answer import SCHEMA
from vamos.config import REPO_ROOT


@dataclass(frozen=True)
class Preset:
    name: str
    model: str
    # Adaptive thinking is on by default on both models; effort sets how much they may use.
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
    def run(self, request: Request, cancelled: threading.Event) -> Reply: ...


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

    def run(self, request: Request, cancelled: threading.Event) -> Reply:
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
            with contextlib.suppress(Exception):  # already closing or closed
                stream.close()
            return


def _failure(error: Exception, usage: dict[str, Any]) -> CallFailed:
    import anthropic

    if isinstance(error, anthropic.AuthenticationError):
        return CallFailed("API key rejected")
    if isinstance(error, anthropic.PermissionDeniedError):
        return CallFailed("API key not allowed to use this model")
    if isinstance(error, anthropic.RateLimitError):
        return CallFailed("rate limited")
    if isinstance(error, anthropic.BadRequestError):
        return CallFailed("request rejected")
    if isinstance(error, anthropic.APITimeoutError):
        return CallFailed("timed out", usage)
    if isinstance(error, anthropic.APIConnectionError):
        return CallFailed("no connection to Claude")
    if isinstance(error, anthropic.APIStatusError):
        return CallFailed(f"Claude unavailable ({error.status_code})")
    return CallFailed(f"unexpected error ({type(error).__name__})", usage)


class CannedProvider:
    """End-to-end tests: the same answer to every call, so they never reach the API."""

    def __init__(self, text: str):
        self.text = text

    def run(self, request: Request, cancelled: threading.Event) -> Reply:
        return Reply(self.text, "end_turn", {})


class FakeProvider:
    """A scripted Claude for tests: each call returns (or raises) the next scripted item."""

    def __init__(self, script: list[Reply | CallFailed | tuple[threading.Event, Reply]]):
        self.script = list(script)
        self.requests: list[Request] = []

    def run(self, request: Request, cancelled: threading.Event) -> Reply:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, tuple):
            # (gate, reply): wait for the test to open the gate, as a slow call would.
            gate, item = item
            while not gate.wait(0.01):
                if cancelled.is_set():
                    raise CallFailed("stopped: the decision passed")
        if isinstance(item, CallFailed):
            raise item
        return item
