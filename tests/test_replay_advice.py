"""scripts/replay_advice.py: logged spots replayed through today's prompt, within a budget."""

import importlib.util
import json
import queue
import sys
from pathlib import Path

from vamos.brain.provider import FakeProvider, Reply
from vamos.host.server import Backend

SCRIPT = Path(__file__).parents[1] / "scripts" / "replay_advice.py"
GAME = "pgl123"
ALEX = "---\nname: alex\ntag: Calling station\nplayer_id: Qw3rTy7UiO\n---\nLoose-passive.\n"
FLOP_HAND = [
    (200, '-- starting hand #2 (id: h2)  No Limit Texas Hold\'em (dealer: "Hero @ heroid0001") --'),
    (201, 'Player stacks: #1 "Hero @ heroid0001" (1000) | #2 "alex @ Qw3rTy7UiO" (1000)'),
    (202, "Your hand is A♠, A♦"),
    (203, '"Hero @ heroid0001" posts a small blind of 10'),
    (204, '"alex @ Qw3rTy7UiO" posts a big blind of 20'),
    (205, '"Hero @ heroid0001" raises to 60'),
    (206, '"alex @ Qw3rTy7UiO" calls 60'),
    (207, "Flop:  [K♠, 7♦, 2♣]"),
    (208, '"alex @ Qw3rTy7UiO" checks'),
]
FLOP_TURN = {
    "type": "turn",
    "gameId": GAME,
    "key": "k2",
    "table": {
        "heroId": "heroid0001",
        "heroCards": ["As", "Ad"],
        "board": ["Ks", "7d", "2c"],
        "pot": "120",
        "bets": ["", ""],
    },
}
BET = Reply(
    '{"reason": "Player A calls too wide.", "strategy": [{"action": "bet", "size_pct_pot": 50,'
    ' "freq": 1}], "plan": ""}',
    "end_turn",
    {"input_tokens": 1_000, "output_tokens": 200},  # $0.008
)


def logged_spot(tmp_path: Path) -> dict:
    """One flop spot as play logs it in data/advice.jsonl."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "alex.md").write_text(ALEX)
    backend = Backend(tmp_path / "data", profiles, provider=FakeProvider([BET]))
    sent = queue.Queue()
    backend.emit = sent.put
    logs = [{"created_at": str(order), "msg": msg} for order, msg in reversed(FLOP_HAND)]
    backend.handle({"type": "log_page", "gameId": GAME, "fetchedAt": 1, "body": {"logs": logs}})
    backend.handle(FLOP_TURN)
    while sent.get(timeout=3)["type"] != "facts":
        pass  # Claude's answer reached the card, so the call is logged
    [entry] = map(json.loads, (tmp_path / "data" / "advice.jsonl").read_text().splitlines())
    return entry


def replay(tmp_path: Path, monkeypatch, fake: FakeProvider, *args: str):
    spec = importlib.util.spec_from_file_location("replay_advice", SCRIPT)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(script, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(script, "AnthropicProvider", lambda: fake)
    monkeypatch.setattr(sys, "argv", ["replay_advice.py", *args])
    script.main()


def test_replays_are_billed_as_replay_and_stop_at_the_budget(tmp_path, monkeypatch, capsys):
    entry = logged_spot(tmp_path)
    advice = tmp_path / "data" / "advice.jsonl"
    spots = ({**entry, "node": f"{entry['node']}{n}"} for n in "abc")
    advice.write_text("".join(json.dumps(spot) + "\n" for spot in spots))
    (tmp_path / "data" / "costs.jsonl").unlink()  # the live call's row
    fake = FakeProvider([BET] * 3)
    replay(tmp_path, monkeypatch, fake, "--budget", "0.012")
    assert len(fake.requests) == 2  # $0.016 spent once the second answer came: no third call
    rows = [
        json.loads(line) for line in (tmp_path / "data" / "costs.jsonl").read_text().splitlines()
    ]
    assert [(row["kind"], row["dollars"]) for row in rows] == [("replay", 0.008)] * 2
    assert "budget of $0.01 reached" in capsys.readouterr().out


def test_calls_stopped_or_refused_at_the_table_are_replayed_only_when_named(tmp_path, monkeypatch):
    entry = logged_spot(tmp_path)
    advice = tmp_path / "data" / "advice.jsonl"
    stopped = {**entry, "state": "cancelled", "answer": None, "mix": None, "chosen": None}
    advice.write_text(json.dumps(entry) + "\n" + json.dumps(stopped) + "\n")
    fake = FakeProvider([BET] * 2)
    replay(tmp_path, monkeypatch, fake)
    assert len(fake.requests) == 1
    replay(tmp_path, monkeypatch, fake, "--lines", "2")
    assert len(fake.requests) == 2


def test_a_spot_asked_deeper_too_is_replayed_once_per_preset(tmp_path, monkeypatch):
    entry = logged_spot(tmp_path)
    deeper = {**entry, "kind": "ask", "preset": "deep", "model": "claude-opus-5-5"}
    other = {**entry, "node": f"{entry['node']}b"}
    advice = tmp_path / "data" / "advice.jsonl"
    advice.write_text("".join(json.dumps(e) + "\n" for e in (entry, deeper, other)))
    fake = FakeProvider([BET] * 4)
    replay(tmp_path, monkeypatch, fake)
    assert len(fake.requests) == 2
    replay(tmp_path, monkeypatch, fake, "--preset", "deep")
    assert len(fake.requests) == 4
    assert [r.preset.name for r in fake.requests] == ["standard", "standard", "deep", "deep"]
    # Each replay sits next to the answer logged with its own preset, when there is one.
    saved = {
        path.stem.rsplit("-", 1)[1]: [r["line"] for r in json.loads(path.read_text())]
        for path in (tmp_path / "data" / "replays").glob("*.json")
    }
    assert saved == {"standard": [1, 3], "deep": [2, 3]}
