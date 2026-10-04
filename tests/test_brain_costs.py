from datetime import datetime

import pytest

from vamos.brain.costs import Ledger, price

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


def test_totals_by_session_month_and_all_time(tmp_path):
    ledger = Ledger(tmp_path / "costs.jsonl", started=datetime(2026, 10, 2, 20, 0), soft_cap=0.5)
    for when, output in (
        (datetime(2026, 9, 30, 22, 0), 50_000),  # last month: $1
        (datetime(2026, 10, 1, 22, 0), 10_000),  # this month, earlier session: $0.20
        (datetime(2026, 10, 2, 20, 30), 15_000),  # this session: $0.30
        (datetime(2026, 10, 2, 20, 45), 15_000),  # this session: $0.30
    ):
        ledger.record(price("claude-opus-5-5", {"output_tokens": output}, "advice", when))
    totals = ledger.totals(datetime(2026, 10, 2, 21, 0))
    assert totals == {"session": 0.6, "month": 0.8, "allTime": 1.8, "overCap": True}


def test_a_torn_last_line_is_skipped(tmp_path):
    path = tmp_path / "costs.jsonl"
    ledger = Ledger(path, started=AT)
    ledger.record(price("claude-opus-5-5", {"output_tokens": 50_000}, "advice", AT))
    with path.open("a") as f:
        f.write('{"at": "2026-10-')
    assert ledger.totals(AT)["allTime"] == pytest.approx(1.0)
