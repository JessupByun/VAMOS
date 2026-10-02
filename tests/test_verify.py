import csv
import json
from pathlib import Path

from vamos.verify import verify

A = '"Alice @ aaaaaaaaaa"'
B = '"Bob @ bbbbbbbbbb"'

HANDS = [
    f"-- starting hand #1 (id: h1)  No Limit Texas Hold'em (dealer: {A}) --",
    f"Player stacks: #1 {A} (1000) | #2 {B} (1000)",
    f"{A} posts a small blind of 10",
    f"{B} posts a big blind of 20",
    f"{A} raises to 60",
    f"{B} folds",
    f"Uncalled bet of 40 returned to {A}",
    f"{A} collected 40 from pot",
    "-- ending hand #1 --",
    f"-- starting hand #2 (id: h2)  No Limit Texas Hold'em (dealer: {B}) --",
    f"Player stacks: #1 {A} (1020) | #2 {B} (980)",
    f"{B} posts a small blind of 10",
    f"{A} posts a big blind of 20",
    f"{B} calls 20",
    f"{A} checks",
    "Flop:  [10♣, 6♣, 3♥]",
    f"{A} bets 20",
    f"{B} folds",
    f"Uncalled bet of 20 returned to {A}",
    f"{A} collected 40 from pot",
    "-- ending hand #2 --",
]
ROWS = [(179000000000000 + i * 100, text) for i, text in enumerate(HANDS)]


def write_export(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["entry", "at", "order"])
        for order, text in reversed(rows):
            writer.writerow([text, "2026-10-02T09:00:00.000Z", order])


def write_recording(path, pages):
    path.parent.mkdir(parents=True)
    with path.open("w") as f:
        for fresh, rows in pages:
            logs = [{"created_at": str(o), "msg": t} for o, t in reversed(rows)]
            f.write(json.dumps({"type": "log_page", "fresh": fresh, "body": {"logs": logs}}) + "\n")


def test_identical_live_build_passes(tmp_path):
    export, recording = tmp_path / "export.csv", tmp_path / "rec/g1/log_pages.jsonl"
    write_export(export, ROWS)
    write_recording(recording, [(True, ROWS[:5]), (False, ROWS[5:12]), (False, ROWS[12:])])
    report = verify(recording, export)
    assert report.ok
    assert (report.compared, report.matched) == (2, 2)


def test_a_hand_joined_midway_is_reported_not_failed(tmp_path):
    export, recording = tmp_path / "export.csv", tmp_path / "rec/g1/log_pages.jsonl"
    write_export(export, ROWS)
    write_recording(recording, [(True, ROWS[4:])])
    report = verify(recording, export)
    assert report.ok
    assert report.before_attach == [1]
    assert report.compared == 1


def test_a_difference_is_named(tmp_path):
    export, recording = tmp_path / "export.csv", tmp_path / "rec/g1/log_pages.jsonl"
    write_export(export, ROWS)
    live = [
        (
            o,
            t.replace("bets 20", "bets 30").replace("Uncalled bet of 20", "Uncalled bet of 30"),
        )
        for o, t in ROWS
    ]
    write_recording(recording, [(True, live)])
    report = verify(recording, export)
    assert not report.ok
    assert report.differences == ["hand #2: actions, returned differ"]


def test_recorded_guest_table_session_matches_pokernows_export():
    # A real 2026-10-02 guest-table session (anonymized): VAMOS's live recording against the
    # CSV PokerNow exported, including a 3-way all-in with a side pot.
    fixture = Path(__file__).parent / "fixtures" / "guest_table"
    report = verify(fixture / "log_pages.jsonl", fixture / "export.csv")
    assert report.ok
    assert (report.compared, report.matched, report.unknown_live) == (6, 6, 0)
