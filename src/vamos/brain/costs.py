"""
What Claude costs, from each response's own token counts.

Every call is priced from `usage` with the model's rates and appended to data/costs.jsonl;
the side panel shows this session, this calendar month and all time. Going past the
session's soft cap only warns.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Dollars per million tokens (Claude API list prices as of 2026-09-25). Cache writes cost
# 1.25x input for the 5-minute lifetime and 2x for the 1-hour one.
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
}
SOFT_CAP = 5.00  # dollars per session


@dataclass(frozen=True)
class CallCost:
    at: str  # ISO time
    model: str
    kind: str  # advice, explain, ...
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    dollars: float


def _get(obj: Any, name: str) -> int:
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return int(value or 0)


def price(model: str, usage: Any, kind: str, at: datetime) -> CallCost:
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
    )


BREAK = timedelta(hours=3)  # a pause this long between calls ends "tonight"


def _tonight(calls: list[CallCost], now: datetime) -> list[CallCost]:
    """
    The calls of the current session: back from now to the first pause of BREAK or more, so
    a restarted backend (a refreshed tab) keeps counting the same night.
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(cost), sort_keys=True) + "\n")

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

    def totals(self, now: datetime) -> dict[str, float | bool]:
        calls = self._all()
        month = now.strftime("%Y-%m")
        session = sum(c.dollars for c in _tonight(calls, now))
        return {
            "session": round(session, 4),
            "month": round(sum(c.dollars for c in calls if c.at.startswith(month)), 4),
            "allTime": round(sum(c.dollars for c in calls), 4),
            "overCap": session > self.soft_cap,
        }
