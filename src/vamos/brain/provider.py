"""
Claude calls for postflop advice.

A call streams, so a decision that has already passed stops reading its answer at the next
event (exiting the stream closes the connection; only tokens produced are billed). Failures
come back as `CallFailed` with a short reason for the card. The SDK's own retries are off:
the advisor owns the deadline and decides whether there is time to try again.

The API key comes from ANTHROPIC_API_KEY in the environment or in the repo's .env file
(gitignored); it is never logged.
"""

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
    effort: str | None = (
        None  # Sonnet 5.5 and Opus 5.5: thinking is always on, effort sets how much
    )
    thinking_budget: int | None = (
        None  # Haiku 4.5 takes a thinking budget instead (None: no thinking)
    )


# Starting points, to be tuned by the latency test once an API key exists.
PRESETS = {
    "fast": Preset("fast", "claude-haiku-4-5"),
    "balanced": Preset("balanced", "claude-sonnet-5-5", effort="low"),
    "deep": Preset("deep", "claude-opus-5-5", effort="medium"),
}
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
    if preset.thinking_budget:
        out["thinking"] = {"type": "enabled", "budget_tokens": preset.thinking_budget}
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
        import anthropic

        client = self._client_or_fail()
        usage: dict[str, Any] = {}
        try:
            with client.messages.stream(**params(request)) as stream:
                for event in stream:
                    if event.type == "message_start":
                        usage = _usage_dict(event.message.usage)
                    elif event.type == "message_delta" and event.usage is not None:
                        usage["output_tokens"] = event.usage.output_tokens or 0
                    if cancelled.is_set():
                        raise CallFailed("stopped: the decision passed", usage)
                final = stream.get_final_message()
        except anthropic.AuthenticationError as error:
            raise CallFailed("API key rejected") from error
        except anthropic.PermissionDeniedError as error:
            raise CallFailed("API key not allowed to use this model") from error
        except anthropic.RateLimitError as error:
            raise CallFailed("rate limited") from error
        except anthropic.BadRequestError as error:
            raise CallFailed("request rejected") from error
        except anthropic.APITimeoutError as error:
            raise CallFailed("timed out", usage) from error
        except anthropic.APIConnectionError as error:
            raise CallFailed("no connection to Claude") from error
        except anthropic.APIStatusError as error:
            raise CallFailed(f"Claude unavailable ({error.status_code})") from error
        usage = _usage_dict(final.usage)
        if final.stop_reason == "refusal":
            raise CallFailed("Claude declined", usage)
        if final.stop_reason == "max_tokens":
            raise CallFailed("answer cut off", usage)
        text = "".join(block.text for block in final.content if block.type == "text")
        return Reply(text, final.stop_reason, usage)


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
