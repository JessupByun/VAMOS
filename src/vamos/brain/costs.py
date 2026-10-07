"""
What Claude costs, from each response's own token counts.

Every request whose usage is known, in part or in full, is priced with the model's rates and
appended to data/costs.jsonl; the side panel shows this session, this calendar month and all
time of play at the table (the developer's test replays stay in the file but are left out).
A request stopped before Claude's first event never learns its usage: it is estimated from
the latest request with the same model and cached prefix, or, with none to go by, counted
at $0 as unknown, and the side panel says how much of each figure is estimated. The
Anthropic Console is exact. Going past the session's soft cap only warns.
"""

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vamos.jsonl import append_lines

# Dollars per million tokens (Claude API list prices as of 2026-09-25). Cache writes cost
# 1.25x input for the 5-minute lifetime and 2x for the 1-hour one.
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
}
SOFT_CAP = 5.00  # dollars per session
TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


@dataclass(frozen=True)
class CallCost:
    at: str  # ISO time
    model: str
    kind: str  # advice, preflop, ask (Ask deeper), or replay (the scripts' offline replays)
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    dollars: float
    # The cached prefix's hash, as in data/advice.jsonl: "" before 2026-10-07 and in replays.
    prefix: str = ""
    # "reported" by Claude, or for a request stopped before Claude's first event "estimated"
    # (see Ledger.estimate) or "unknown" (nothing comparable: counted at $0).
    usage: str = "reported"


def known(usage: dict[str, Any]) -> bool:
    """Whether a response's usage holds any tokens: none arrive before Claude's first event."""
    return any(isinstance(usage.get(name), int) and usage[name] for name in TOKENS)


def _get(obj: Any, name: str) -> int:
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return int(value or 0)


def price(model: str, usage: Any, kind: str, at: datetime, prefix: str = "") -> CallCost:
    """Cost of one response; `usage` is the SDK's usage object (or a dict with its fields)."""
    rates = PRICES[model]
    created = (
        usage.get("cache_creation")
        if isinstance(usage, dict)
        else getattr(usage, "cache_creation", None)
    )
    write_5m, write_1h = (
        _get(created, "ephemeral_5m_input_tokens"),
        _get(created, "ephemeral_1h_input_tokens"),
    )
    if created is None:
        # Without the breakdown, every write is charged at the 5-minute rate.
        write_5m = _get(usage, "cache_creation_input_tokens")
    tokens = {
        "input": _get(usage, "input_tokens"),
        "output": _get(usage, "output_tokens"),
        "cache_read": _get(usage, "cache_read_input_tokens"),
    }
    dollars = (
        tokens["input"] * rates["input"]
        + tokens["output"] * rates["output"]
        + tokens["cache_read"] * rates["cache_read"]
        + write_5m * rates["input"] * 1.25
        + write_1h * rates["input"] * 2.0
    ) / 1_000_000
    return CallCost(
        at.isoformat(timespec="seconds"),
        model,
        kind,
        tokens["input"],
        tokens["output"],
        tokens["cache_read"],
        write_5m,
        write_1h,
        round(dollars, 6),
        prefix,
    )


BREAK = timedelta(hours=3)  # a pause this long between calls ends the session


def _session(calls: list[CallCost], now: datetime) -> list[CallCost]:
    """
    The calls of the current session: back from now to the first pause of BREAK or more, so
    a restarted backend (a refreshed tab) keeps counting the same session.
    """
    out: list[CallCost] = []
    later = now
    for call in sorted(calls, key=lambda c: c.at, reverse=True):
        at = datetime.fromisoformat(call.at)
        if later - at >= BREAK:
            break
        out.append(call)
        later = at
    return out


class Ledger:
    def __init__(self, path: Path, soft_cap: float = SOFT_CAP):
        self.path = path
        self.soft_cap = soft_cap

    def record(self, cost: CallCost) -> None:
        append_lines(self.path, [json.dumps(asdict(cost), sort_keys=True)])

    def charge(self, model: str, usage: dict[str, Any], kind: str, at: datetime) -> CallCost | None:
        """Price and record one request's usage, whenever any of its tokens are known."""
        if not known(usage):
            return None
        cost = price(model, usage, kind, at)
        self.record(cost)
        return cost

    def estimate(self, model: str, prefix: str, kind: str, at: datetime) -> CallCost:
        """
        Record a request stopped before Claude's first event, whose usage is never known: it
        is priced with the input, cache read and cache write of the latest request Claude
        reported with the same model and prefix, without output, or at $0 as "unknown" when
        there is none.
        """
        like = next(
            (
                c
                for c in reversed(self._all())
                if prefix and c.prefix == prefix and c.model == model and c.usage == "reported"
            ),
            None,
        )
        if like is None:
            when = at.isoformat(timespec="seconds")
            cost = CallCost(when, model, kind, 0, 0, 0, 0, 0, 0.0, prefix, "unknown")
        else:
            usage = {
                "input_tokens": like.input_tokens,
                "cache_read_input_tokens": like.cache_read_tokens,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": like.cache_write_5m_tokens,
                    "ephemeral_1h_input_tokens": like.cache_write_1h_tokens,
                },
            }
            cost = replace(price(model, usage, kind, at, prefix), usage="estimated")
        self.record(cost)
        return cost

    def _all(self) -> list[CallCost]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(CallCost(**json.loads(line)))
            except (TypeError, ValueError):
                continue  # a torn last line from a crash is skipped, not fatal
        return out

    def totals(self, now: datetime) -> dict[str, Any]:
        """
        Dollars this session, this month and all time, with how much of each is estimated
        and how many stopped requests each leaves out for want of anything comparable.
        """
        # Play at the table only: the developer's test replays are not the user's.
        calls = [c for c in self._all() if c.kind != "replay"]
        month = now.strftime("%Y-%m")
        figures = {
            "session": _session(calls, now),
            "month": [c for c in calls if c.at.startswith(month)],
            "allTime": calls,
        }
        out: dict[str, Any] = {
            name: round(sum(c.dollars for c in rows), 4) for name, rows in figures.items()
        }
        out["overCap"] = sum(c.dollars for c in figures["session"]) > self.soft_cap
        out["estimated"] = {
            name: {
                "dollars": round(sum(c.dollars for c in rows if c.usage == "estimated"), 4),
                "calls": sum(c.usage == "estimated" for c in rows),
            }
            for name, rows in figures.items()
        }
        out["unknown"] = {
            name: sum(c.usage == "unknown" for c in rows) for name, rows in figures.items()
        }
        return out
