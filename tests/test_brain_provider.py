import threading
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

from vamos.brain import provider
from vamos.brain.provider import PRESETS, AnthropicProvider, CallFailed, Request, params


def request(name="standard"):
    return Request(
        PRESETS[name], [{"type": "text", "text": "prefix"}], [{"role": "user", "content": "spot"}]
    )


def test_request_parameters_per_preset():
    standard = params(request("standard"))
    assert standard["model"] == "claude-opus-5-5"
    assert standard["output_config"]["effort"] == "low"
    assert standard["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in standard  # adaptive is the default on Opus 5.5
    deep = params(request("deep"))
    assert (deep["model"], deep["output_config"]["effort"]) == ("claude-opus-5-5", "medium")
    assert set(PRESETS) == {"standard", "deep"}  # Opus only: Haiku and Sonnet were dropped


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

    def close(self):
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


@pytest.mark.parametrize(
    ("status", "error", "text"),
    [
        (402, anthropic.APIStatusError, "Billing or payment problem"),
        (400, anthropic.BadRequestError, "Your credit balance is too low to access the API."),
    ],
)
def test_running_out_of_credits_says_so(status, error, text):
    response = httpx2.Response(
        status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )

    def stream(**kwargs):
        raise error(text, response=response, body=None)

    with pytest.raises(CallFailed, match=r"^out of API credits$"):
        AnthropicProvider(NS(messages=NS(stream=stream))).run(request(), threading.Event())


class QuietStream(FakeStream):
    """A stream that sends nothing while the model thinks, until it is closed."""

    def __init__(self):
        super().__init__([], message())
        self.opened = threading.Event()
        self.close_called = threading.Event()

    def __iter__(self):
        yield NS(type="message_start", message=NS(usage=USAGE))
        self.opened.set()
        if not self.close_called.wait(5):
            raise AssertionError("never closed")
        raise httpx2.ReadError("connection closed")

    def close(self):
        self.close_called.set()


def test_a_passed_decision_closes_a_quiet_stream():
    stream = QuietStream()
    cancelled = threading.Event()
    threading.Thread(target=lambda: (stream.opened.wait(5), cancelled.set())).start()
    with pytest.raises(CallFailed, match="decision passed") as error:
        AnthropicProvider(client_with(stream)).run(request(), cancelled)
    assert stream.close_called.is_set()
    assert error.value.usage["cache_read_input_tokens"] == 9000


def test_a_passed_decision_stops_a_real_connection_at_once():
    # A local stand-in for the API that starts its answer and then goes quiet, as Claude
    # does while it thinks: the real HTTP stack must give up the read as soon as the
    # decision passes, not when the request times out.
    import socket
    import time

    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def serve():
        conn, _ = server.accept()
        conn.recv(65536)
        start = (
            b'event: message_start\ndata: {"type": "message_start", "message": {"id": "m",'
            b' "type": "message", "role": "assistant", "model": "claude-opus-5-5",'
            b' "content": [], "stop_reason": null, "stop_sequence": null,'
            b' "usage": {"input_tokens": 500, "output_tokens": 1}}}\n\n'
        )
        conn.sendall(
            b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
            b"transfer-encoding: chunked\r\n\r\n" + f"{len(start):x}\r\n".encode() + start + b"\r\n"
        )
        time.sleep(15)  # thinking: nothing more is sent
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    client = anthropic.Anthropic(
        api_key="test", base_url=f"http://127.0.0.1:{port}", max_retries=0, timeout=10
    )
    cancelled = threading.Event()
    threading.Timer(0.5, cancelled.set).start()
    began = time.monotonic()
    with pytest.raises(CallFailed, match="decision passed") as error:
        AnthropicProvider(client).run(request(), cancelled)
    assert time.monotonic() - began < 3
    assert error.value.usage["input_tokens"] == 500
    server.close()
