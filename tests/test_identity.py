import json

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
    assert identity.resolve("Qw3rTy7UiO", PROFILES) == Resolution("profile", "alex")


def test_unknown_ids_need_asking(tmp_path):
    identity = IdentityMap(tmp_path / "identity.json")
    assert identity.resolve("newDevice1", PROFILES) == Resolution("unknown")


def test_answers_are_remembered_across_restarts(tmp_path):
    path = tmp_path / "data/identity.json"
    identity = IdentityMap(path)
    identity.assign("newDevice1", "rj")
    identity.assign("stranger99", None)

    reloaded = IdentityMap(path)
    assert reloaded.resolve("newDevice1", PROFILES) == Resolution("profile", "rj")
    assert reloaded.resolve("stranger99", PROFILES) == Resolution("none")


def test_profile_player_id_overrides_an_earlier_answer(tmp_path):
    identity = IdentityMap(tmp_path / "identity.json")
    identity.assign("Qw3rTy7UiO", "rj")
    assert identity.resolve("Qw3rTy7UiO", PROFILES) == Resolution("profile", "alex")


def test_an_answer_pointing_at_a_deleted_profile_asks_again(tmp_path):
    identity = IdentityMap(tmp_path / "identity.json")
    identity.assign("newDevice1", "gone")
    assert identity.resolve("newDevice1", PROFILES) == Resolution("unknown")


def test_observed_names_are_kept_without_answering(tmp_path):
    path = tmp_path / "identity.json"
    identity = IdentityMap(path)
    identity.observe("newDevice1", "rj")
    identity.observe("newDevice1", "zzz")
    identity.observe("newDevice1", "rj")

    assert IdentityMap(path).names_seen("newDevice1") == ["rj", "zzz"]
    assert identity.resolve("newDevice1", PROFILES) == Resolution("unknown")
    assert json.loads(path.read_text()) == {"newDevice1": {"names": ["rj", "zzz"]}}
