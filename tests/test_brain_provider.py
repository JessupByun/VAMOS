import threading
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

from vamos.brain import provider
from vamos.brain.provider import PRESETS, AnthropicProvider, CallFailed, Request, params


def request(name="balanced"):
    return Request(
        PRESETS[name], [{"type": "text", "text": "prefix"}], [{"role": "user", "content": "spot"}]
    )


def test_request_parameters_per_preset():
    balanced = params(request("balanced"))
    assert balanced["model"] == "claude-sonnet-5-5"
    assert balanced["output_config"]["effort"] == "low"
    assert balanced["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in balanced  # adaptive is the default and the only mode on 5.5
    deep = params(request("deep"))
    assert (deep["model"], deep["output_config"]["effort"]) == ("claude-opus-5-5", "medium")
    fast = params(request("fast"))
    assert fast["model"] == "claude-haiku-4-5"
    assert "effort" not in fast["output_config"]  # Haiku 4.5 rejects effort


def test_api_key_from_the_environment_or_the_env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider.api_key() is None
    (tmp_path / ".env").write_text("GROQ_API_KEY=x\nANTHROPIC_API_KEY='sk-test'\n")
    assert provider.api_key() == "sk-test"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert provider.api_key() == "sk-env"


def test_no_key_fails_with_a_plain_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(CallFailed, match="no API key"):
        AnthropicProvider().run(request(), threading.Event())


class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


def client_with(stream):
    return NS(messages=NS(stream=lambda **kwargs: stream))


USAGE = NS(
    input_tokens=50,
    output_tokens=0,
    cache_read_input_tokens=9000,
    cache_creation_input_tokens=0,
    cache_creation=None,
)


def message(stop="end_turn", text='{"strategy": [], "note": ""}'):
    return NS(
        stop_reason=stop,
        content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
        usage=USAGE,
    )


def test_a_streamed_answer():
    events = [
        NS(type="message_start", message=NS(usage=USAGE)),
        NS(type="message_delta", usage=NS(output_tokens=120)),
    ]
    reply = AnthropicProvider(client_with(FakeStream(events, message()))).run(
        request(), threading.Event()
    )
    assert reply.text == '{"strategy": [], "note": ""}'
    assert reply.usage["cache_read_input_tokens"] == 9000


def test_a_passed_decision_stops_reading_the_stream():
    cancelled = threading.Event()
    cancelled.set()
    stream = FakeStream([NS(type="message_start", message=NS(usage=USAGE))], message())
    with pytest.raises(CallFailed, match="decision passed") as error:
        AnthropicProvider(client_with(stream)).run(request(), cancelled)
    assert stream.closed
    assert error.value.usage["input_tokens"] == 50  # what was billed so far is still recorded


@pytest.mark.parametrize(("stop", "reason"), [("refusal", "declined"), ("max_tokens", "cut off")])
def test_unusable_stops(stop, reason):
    with pytest.raises(CallFailed, match=reason):
        AnthropicProvider(client_with(FakeStream([], message(stop)))).run(
            request(), threading.Event()
        )


def test_sdk_errors_become_plain_reasons():
    response = httpx2.Response(
        429, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )

    def stream(**kwargs):
        raise anthropic.RateLimitError("slow down", response=response, body=None)

    with pytest.raises(CallFailed, match="rate limited"):
        AnthropicProvider(NS(messages=NS(stream=stream))).run(request(), threading.Event())
