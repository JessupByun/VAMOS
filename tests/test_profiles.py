from pathlib import Path

import pytest

from vamos.profiles import load_profiles, parse_profile

ALEX = """---
name: alex
tag: Calling station
aliases: [alex, ally]
player_id: Qw3rTy7UiO
sessions: [HU 2026-09-30, A 2026-09-30]
net: -41.30 (HU -15.00, A -25.00, B +6.20, C -7.50)
last_updated: 2026-10-02
---

# alex

## Summary
Loose-passive.
"""


def write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text)


def test_parse_profile_reads_the_fields_vamos_uses():
    profile = parse_profile("alex", ALEX)
    assert profile.name == "alex"
    assert profile.tag == "Calling station"
    assert profile.aliases == ("alex", "ally")
    assert profile.player_ids == ("Qw3rTy7UiO",)
    assert profile.text == ALEX


def test_player_id_may_be_a_list():
    profile = parse_profile("rj", "---\nplayer_id: [Zx9Kp2LmNb, abc123]\n---\nbody\n")
    assert profile.player_ids == ("Zx9Kp2LmNb", "abc123")


def test_missing_optional_fields_default_sensibly():
    profile = parse_profile("sam", "---\n{}\n---\n")
    assert (profile.name, profile.tag, profile.aliases, profile.player_ids) == ("sam", None, (), ())


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# no frontmatter\n", "frontmatter block"),
        ("---\ntag: [unclosed\n---\n", "not valid YAML"),
        ("---\n- just\n- a list\n---\n", "field: value"),
        ("---\nplayer_id: {a: 1}\n---\n", "player_id"),
    ],
)
def test_parse_profile_rejects_malformed_frontmatter(text, message):
    with pytest.raises(ValueError, match=message):
        parse_profile("x", text)


@pytest.mark.parametrize(
    "encode",
    [
        lambda text: text.replace("\n", "\r\n").encode(),  # Windows line endings
        lambda text: "\ufeff".encode() + text.encode(),  # a byte-order mark
        lambda text: text.encode("utf-8-sig").replace(b"\n", b"\r\n"),  # both
    ],
)
def test_profiles_saved_by_windows_editors_are_read(tmp_path, encode):
    (tmp_path / "alex.md").write_bytes(encode(ALEX))
    loaded = load_profiles(tmp_path)
    assert loaded.problems == ()
    profile = loaded.profiles["alex"]
    assert (profile.name, profile.player_ids, profile.text) == ("alex", ("Qw3rTy7UiO",), ALEX)
    assert profile.body == "# alex\n\n## Summary\nLoose-passive."
    assert parse_profile("alex", encode(ALEX).decode()).text == ALEX


def test_load_profiles_skips_underscore_files_and_reports_bad_ones(tmp_path):
    write(tmp_path, "alex.md", ALEX)
    write(tmp_path, "_TEMPLATE.md", "---\nplayer_id: AbCdEf1234\n---\n")
    write(tmp_path, "broken.md", "no frontmatter here\n")
    write(tmp_path, "notes.txt", "ignored\n")

    loaded = load_profiles(tmp_path)

    assert list(loaded.profiles) == ["alex"]
    assert len(loaded.problems) == 1
    assert loaded.problems[0].startswith("broken.md:")


def test_by_player_id(tmp_path):
    write(tmp_path, "alex.md", ALEX)
    loaded = load_profiles(tmp_path)
    assert loaded.by_player_id("Qw3rTy7UiO").key == "alex"
    assert loaded.by_player_id("nobody") is None


def test_a_player_id_claimed_by_two_profiles_is_reported(tmp_path):
    write(tmp_path, "a.md", "---\nplayer_id: same1\n---\n")
    write(tmp_path, "b.md", "---\nplayer_id: same1\n---\n")
    loaded = load_profiles(tmp_path)
    assert loaded.problems == ("player_id same1 is in both a.md and b.md",)
    assert loaded.by_player_id("same1").key == "a"
