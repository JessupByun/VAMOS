import json
from datetime import datetime

import pytest

from vamos.brain.costs import CallCost, Ledger, price

AT = datetime(2026, 10, 2, 21, 0, 0)


def test_price_counts_every_kind_of_token():
    usage = {
        "input_tokens": 1_000,
        "output_tokens": 500,
        "cache_read_input_tokens": 10_000,
        "cache_creation_input_tokens": 12_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 2_000, "ephemeral_1h_input_tokens": 10_000},
    }
    cost = price("claude-opus-5-5", usage, "advice", AT)
    # 1k x $4 + 500 x $20 + 10k x $0.20 + 2k x $5 + 10k x $8, per million.
    assert cost.dollars == pytest.approx((4_000 + 10_000 + 2_000 + 10_000 + 80_000) / 1e6)
    assert (cost.cache_write_5m_tokens, cost.cache_write_1h_tokens) == (2_000, 10_000)


def test_writes_without_a_breakdown_are_charged_at_the_five_minute_rate():
    cost = price("claude-opus-5-5", {"cache_creation_input_tokens": 1_000}, "advice", AT)
    assert cost.dollars == pytest.approx(1_000 * 4.00 * 1.25 / 1e6)


def test_sdk_style_usage_objects_work_too():
    class Usage:
        input_tokens = 100
        output_tokens = 10
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0
        cache_creation = None

    assert price("claude-opus-5-5", Usage(), "advice", AT).dollars == pytest.approx(
        (400 + 200) / 1e6
    )


def test_a_response_is_charged_whenever_any_of_its_tokens_are_known(tmp_path):
    ledger = Ledger(tmp_path / "costs.jsonl")
    # Stopped before Claude's first event: nothing is known, so nothing is written.
    assert ledger.charge("claude-opus-5-5", {}, "advice", AT) is None
    assert ledger.charge("claude-opus-5-5", {"input_tokens": 0}, "advice", AT) is None
    assert not ledger.path.exists()
    cost = ledger.charge("claude-opus-5-5", {"input_tokens": 500, "output_tokens": 1}, "ask", AT)
    assert cost is not None and cost.kind == "ask"
    assert cost.dollars == pytest.approx((2_000 + 20) / 1e6)
    [row] = ledger.path.read_text().splitlines()
    assert json.loads(row)["dollars"] == cost.dollars


def test_totals_by_session_month_and_all_time(tmp_path):
    ledger = Ledger(tmp_path / "costs.jsonl", soft_cap=0.5)
    for when, output in (
        (datetime(2026, 9, 30, 22, 0), 50_000),  # last month: $1
        (datetime(2026, 10, 1, 22, 0), 10_000),  # this month, earlier session: $0.20
        (datetime(2026, 10, 2, 20, 30), 15_000),  # this session: $0.30
        (datetime(2026, 10, 2, 20, 45), 15_000),  # this session: $0.30
    ):
        ledger.record(price("claude-opus-5-5", {"output_tokens": output}, "advice", when))
    totals = ledger.totals(datetime(2026, 10, 2, 21, 0))
    assert totals == {
        "session": 0.6,
        "month": 0.8,
        "allTime": 1.8,
        "overCap": True,
        "estimated": {key: {"dollars": 0, "calls": 0} for key in ("session", "month", "allTime")},
        "unknown": {"session": 0, "month": 0, "allTime": 0},
    }


def test_tonight_runs_back_to_the_last_long_pause_whatever_restarted_in_between(tmp_path):
    ledger = Ledger(tmp_path / "costs.jsonl")
    for when in (
        datetime(2026, 10, 3, 14, 0),  # the afternoon: a separate session
        datetime(2026, 10, 3, 20, 0),  # tonight starts
        datetime(2026, 10, 3, 22, 30),  # a refresh restarted the backend in between
        datetime(2026, 10, 4, 1, 15),  # past midnight, still tonight
    ):
        ledger.record(price("claude-opus-5-5", {"output_tokens": 10_000}, "advice", when))
    assert ledger.totals(datetime(2026, 10, 4, 1, 30))["session"] == pytest.approx(0.6)
    # After three hours with no call, tonight starts over.
    assert ledger.totals(datetime(2026, 10, 4, 4, 30))["session"] == 0


def test_a_torn_last_line_is_skipped(tmp_path):
    path = tmp_path / "costs.jsonl"
    ledger = Ledger(path)
    ledger.record(price("claude-opus-5-5", {"output_tokens": 50_000}, "advice", AT))
    with path.open("a") as f:
        f.write('{"at": "2026-10-')
    assert ledger.totals(AT)["allTime"] == pytest.approx(1.0)
    # The next call's row starts on a line of its own, so it is counted too.
    ledger.record(price("claude-opus-5-5", {"output_tokens": 50_000}, "advice", AT))
    assert ledger.totals(AT)["allTime"] == pytest.approx(2.0)


def test_the_developers_test_replays_are_left_out_of_every_figure(tmp_path):
    ledger = Ledger(tmp_path / "costs.jsonl")
    for kind, dollars in (("advice", 0.01), ("replay", 1.00), ("ask", 0.02), ("preflop", 0.03)):
        ledger.record(
            CallCost("2026-10-06T20:00:00", "claude-opus-5-5", kind, 0, 0, 0, 0, 0, dollars)
        )
    totals = ledger.totals(datetime(2026, 10, 6, 20, 30))
    assert (totals["session"], totals["month"], totals["allTime"]) == (0.06, 0.06, 0.06)
    assert len(ledger.path.read_text().splitlines()) == 4  # the replay stays in the file


def row(at, prefix, input_tokens, cache_read, write_5m=0, write_1h=0, output=100):
    """A request Claude reported, on the cached prefix `prefix`."""
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output,
        "cache_read_input_tokens": cache_read,
        "cache_creation": {
            "ephemeral_5m_input_tokens": write_5m,
            "ephemeral_1h_input_tokens": write_1h,
        },
    }
    return price("claude-opus-5-5", usage, "advice", at, prefix)


def test_a_request_stopped_before_claudes_first_event_is_priced_like_the_last_like_it(tmp_path):
    ledger = Ledger(tmp_path / "costs.jsonl")
    for cost in (
        row(datetime(2026, 10, 2, 20, 0), "p1", 900, 0, write_5m=40_000),  # wrote the cache
        row(datetime(2026, 10, 2, 20, 5), "p1", 1_000, 40_000, write_1h=2_000, output=500),
        row(datetime(2026, 10, 2, 20, 6), "p2", 5_000, 0),  # other profiles at the table
    ):
        ledger.record(cost)
    cost = ledger.estimate("claude-opus-5-5", "p1", "ask", AT)
    # The latest call on the same prefix, without its output: 1k x $4 + 40k x $0.20 + 2k x $8.
    assert cost.dollars == pytest.approx((4_000 + 8_000 + 16_000) / 1e6)
    assert (cost.input_tokens, cost.cache_read_tokens, cost.output_tokens) == (1_000, 40_000, 0)
    assert (cost.cache_write_5m_tokens, cost.cache_write_1h_tokens) == (0, 2_000)
    assert (cost.kind, cost.prefix, cost.usage, cost.at) == (
        "ask",
        "p1",
        "estimated",
        AT.isoformat(),
    )
    assert json.loads(ledger.path.read_text().splitlines()[-1])["usage"] == "estimated"
    # An estimate is never the model for the next one: Claude's own counts are.
    ledger.record(row(datetime(2026, 10, 2, 20, 30), "p1", 2_000, 40_000))
    assert ledger.estimate("claude-opus-5-5", "p1", "advice", AT).input_tokens == 2_000


def test_nothing_comparable_is_never_estimated(tmp_path):
    ledger = Ledger(tmp_path / "costs.jsonl")
    ledger.record(row(datetime(2026, 10, 2, 20, 0), "p1", 1_000, 40_000))
    # Rows from before prefixes were recorded match nothing.
    ledger.record(CallCost("2026-10-02T20:01:00", "claude-opus-5-5", "advice", 1, 1, 1, 0, 0, 1.0))
    for prefix in ("p2", ""):
        cost = ledger.estimate("claude-opus-5-5", prefix, "advice", AT)
        assert (cost.usage, cost.dollars, cost.input_tokens, cost.cache_read_tokens) == (
            "unknown",
            0,
            0,
            0,
        )
    assert [json.loads(line).get("usage") for line in ledger.path.read_text().splitlines()] == [
        "reported",
        "reported",
        "unknown",
        "unknown",
    ]


def test_old_rows_read_as_reported_and_without_a_prefix(tmp_path):
    path = tmp_path / "costs.jsonl"
    old = {
        "at": "2026-10-02T20:00:00",
        "model": "claude-opus-5-5",
        "kind": "advice",
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0,
        "cache_write_1h_tokens": 0,
        "dollars": 0.5,
    }
    path.write_text(json.dumps(old) + "\n")
    totals = Ledger(path).totals(AT)
    assert (totals["allTime"], totals["estimated"]["allTime"]) == (0.5, {"dollars": 0, "calls": 0})


def test_each_figure_says_how_much_of_it_is_estimated(tmp_path):
    ledger = Ledger(tmp_path / "costs.jsonl")
    last_month = datetime(2026, 9, 30, 22, 0)
    earlier = datetime(2026, 10, 1, 22, 0)
    ledger.record(row(last_month, "p1", 1_000, 40_000))
    ledger.estimate("claude-opus-5-5", "p1", "advice", last_month)  # $0.012
    ledger.estimate("claude-opus-5-5", "p9", "advice", last_month)  # nothing comparable
    ledger.estimate("claude-opus-5-5", "p1", "advice", earlier)
    ledger.estimate("claude-opus-5-5", "p1", "advice", AT)
    ledger.estimate("claude-opus-5-5", "p1", "advice", AT)
    ledger.estimate("claude-opus-5-5", "p9", "advice", AT)
    ledger.estimate("claude-opus-5-5", "p1", "replay", AT)  # a test replay: left out
    totals = ledger.totals(datetime(2026, 10, 2, 21, 30))
    assert totals["estimated"] == {
        "session": {"dollars": 0.024, "calls": 2},
        "month": {"dollars": 0.036, "calls": 3},
        "allTime": {"dollars": 0.048, "calls": 4},
    }
    assert totals["unknown"] == {"session": 1, "month": 1, "allTime": 2}
    assert totals["session"] == 0.024
