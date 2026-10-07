import json
import logging
from datetime import datetime

import pytest

from vamos.identity import IdentityMap, Resolution
from vamos.profiles import ProfileSet, parse_profile

PROFILES = ProfileSet(
    profiles={
        "alex": parse_profile("alex", "---\nplayer_id: Qw3rTy7UiO\n---\n"),
        "rj": parse_profile("rj", "---\nplayer_id: Zx9Kp2LmNb\n---\n"),
    },
    problems=(),
)


def test_profile_player_id_wins(tmp_path):
    identity = IdentityMap(tmp_path / "identity.json")
    assert identity.resolve("Qw3rTy7UiO", PROFILES) == Resolution("profile", "alex", "player_id")


def test_unknown_ids_need_asking(tmp_path):
    identity = IdentityMap(tmp_path / "identity.json")
    assert identity.resolve("newDevice1", PROFILES) == Resolution("unknown")


def test_answers_are_remembered_across_restarts(tmp_path):
    path = tmp_path / "data/identity.json"
    identity = IdentityMap(path)
    identity.assign("newDevice1", "rj")
    identity.assign("stranger99", None)

    reloaded = IdentityMap(path)
    assert reloaded.resolve("newDevice1", PROFILES) == Resolution("profile", "rj", "answer")
    assert reloaded.resolve("stranger99", PROFILES) == Resolution("none", source="answer")


def test_profile_player_id_overrides_an_earlier_answer(tmp_path):
    identity = IdentityMap(tmp_path / "identity.json")
    identity.assign("Qw3rTy7UiO", "rj")
    assert identity.resolve("Qw3rTy7UiO", PROFILES) == Resolution("profile", "alex", "player_id")


def test_an_answer_pointing_at_a_deleted_profile_asks_again(tmp_path):
    identity = IdentityMap(tmp_path / "identity.json")
    identity.assign("newDevice1", "gone")
    assert identity.resolve("newDevice1", PROFILES) == Resolution("unknown")


def test_only_answers_are_stored(tmp_path):
    path = tmp_path / "identity.json"
    identity = IdentityMap(path)
    identity.assign("newDevice1", "rj")
    identity.assign("stranger99", None)
    assert json.loads(path.read_text()) == {"newDevice1": "rj", "stranger99": None}


def test_forgetting_an_answer_asks_again(tmp_path):
    path = tmp_path / "identity.json"
    identity = IdentityMap(path)
    identity.assign("stranger99", None)
    identity.forget("stranger99")
    identity.forget("never-answered")
    assert IdentityMap(path).resolve("stranger99", PROFILES) == Resolution("unknown")


@pytest.mark.parametrize(
    "damaged",
    [b'{"newDevice1": "rj",', b"[1, 2]", b'{"newDevice1": ["rj"]}', b"\xff\xfe{}"],
)
def test_a_damaged_file_is_kept_aside_and_the_map_starts_empty(tmp_path, caplog, damaged):
    path = tmp_path / "identity.json"
    path.write_bytes(damaged)
    with caplog.at_level(logging.ERROR, logger="vamos.identity"):
        identity = IdentityMap(path, now=lambda: datetime(2026, 10, 7, 1, 37, 5))
    kept = tmp_path / "identity.damaged-20261007-013705.json"
    assert kept.read_bytes() == damaged
    assert identity.resolve("newDevice1", PROFILES) == Resolution("unknown")
    assert identity.problem == (
        f"{tmp_path.name}/identity.json was damaged, so the players you matched to profiles "
        f"will be asked again; the old file is kept as {tmp_path.name}/{kept.name}"
    )
    [logged] = caplog.records
    assert logged.getMessage().startswith(f"[VAMOS] could not read {path}: ")
    # Answering again starts a new file and leaves the old one alone.
    identity.assign("newDevice1", "rj")
    assert json.loads(path.read_text()) == {"newDevice1": "rj"}
    assert kept.read_bytes() == damaged
    assert IdentityMap(path).problem is None


def test_a_damaged_file_that_cannot_be_moved_still_starts_empty(tmp_path, monkeypatch):
    path = tmp_path / "identity.json"
    path.write_text("{")

    def refuse(src, dst):
        raise PermissionError(dst)

    monkeypatch.setattr("vamos.identity.os.replace", refuse)
    identity = IdentityMap(path)
    assert identity.resolve("newDevice1", PROFILES) == Resolution("unknown")
    assert identity.problem is not None
    assert identity.problem.endswith(
        f"; it could not be moved aside (see {tmp_path.name}/logs/host.log)"
    )


def test_an_answer_keeps_matches_written_to_the_file_meanwhile(tmp_path):
    path = tmp_path / "identity.json"
    identity = IdentityMap(path)
    identity.assign("phone00001", "alex")
    # Something else (the profile update) records another account while this map is open.
    path.write_text('{"phone00001": "alex", "phone00002": "rj"}')
    identity.assign("phone00003", "sam")
    assert json.loads(path.read_text()) == {
        "phone00001": "alex",
        "phone00002": "rj",
        "phone00003": "sam",
    }
