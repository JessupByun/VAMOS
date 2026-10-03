import json

from vamos.brain.prompt import prefix, spot_message
from vamos.brain.pseudonyms import Pseudonyms
from vamos.engine.state import positions
from vamos.log.hands import build_hands
from vamos.profiles import ProfileSet, parse_profile

ALEX = "---\nname: alex\ntag: calling-station\naliases: [Alexander]\nplayer_id: alexid0001\n---\nCalls everything. Folds to jes on the river; rj bluffs him.\n"
RJ = (
    "---\nname: rj\naliases: [Ray]\nplayer_id: rjid000001\n---\nLoose floater; Alexander hates him.\n"
)


def setup():
    profiles = ProfileSet({"alex": parse_profile("alex", ALEX), "rj": parse_profile("rj", RJ)}, ())
    names = Pseudonyms()
    names.add_hero_name("jes")
    for key in sorted(profiles.profiles):
        p = profiles.profiles[key]
        names.add(key, [p.name, *p.aliases], shown=p.name)
    return profiles, names


def test_the_prefix_is_byte_stable_and_cached_at_its_end():
    profiles, names = setup()
    first, second = prefix(profiles, names), prefix(profiles, names)
    assert first.blocks == second.blocks
    assert [("cache_control" in b) for b in first.blocks] == [False] * (len(first.blocks) - 1) + [
        True
    ]
    assert first.blocks[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_no_real_name_reaches_the_prefix():
    profiles, names = setup()
    text = prefix(profiles, names).text
    names.check(text)
    assert "Strategy guide" not in text  # the role, the profiles and nothing else
    assert len(prefix(profiles, names).blocks) == 2
    assert "## Player A (calling-station)" in text and "## Player B\n" in text
    assert "player_id" not in text and "alexid0001" not in text
    assert "Folds to Hero on the river; Player B bluffs him." in text
    assert "Player A hates him" in text


def hand():
    lines = [
        '-- starting hand #4 (id: h4)  No Limit Texas Hold\'em (dealer: "jes @ heroid0001") --',
        'Player stacks: #1 "jes @ heroid0001" (10.00) | #2 "alex @ alexid0001" (12.00) | #3 "rj @ rjid000001" (8.00)',
        "Your hand is A♠, K♦",
        '"alex @ alexid0001" posts a small blind of 0.10',
        '"rj @ rjid000001" posts a big blind of 0.20',
        '"jes @ heroid0001" raises to 0.60',
        '"alex @ alexid0001" calls 0.60',
        '"rj @ rjid000001" folds',
        "Flop:  [K♠, 7♦, 2♣]",
        '"alex @ alexid0001" bets 0.80',
    ]
    return build_hands((100 + i, line) for i, line in enumerate(lines)).hands[-1]


def test_the_spot_message_describes_the_hand_without_names():
    _, names = setup()
    h = hand()
    ids = {"heroid0001": "Hero", "alexid0001": names.label("alex"), "rjid000001": names.label("rj")}
    facts = {"pot_bb": 7.5, "to_call_bb": 4.0, "made_hand": "top pair, top kicker"}
    text = spot_message(
        h,
        "heroid0001",
        ids,
        positions(h),
        facts,
        ["fold", "call", "raise", "allin"],
        tonight={"alexid0001": {"hands": 30, "vpip": 21}},
        notes=names.hide("alex is tilted after losing a big pot"),
    )
    hidden = text
    names.check(hidden)
    assert "Player A is tilted" in hidden
    spot = json.loads(hidden[hidden.index("{") : hidden.rindex("}") + 1])
    assert spot["hero"] == {"name": "Hero", "cards": ["As", "Kd"], "position": "BTN"}
    assert spot["board"] == ["Ks", "7d", "2c"]
    assert spot["legal_actions"] == ["fold", "call", "raise", "allin"]
    # The whole hand in order, the flop where it was dealt.
    assert [(e.get("player"), e.get("action"), e.get("to_bb")) for e in spot["history"]] == [
        ("Player A", "post_sb", 0.5),
        ("Player B", "post_bb", 1.0),
        ("Hero", "raise", 3.0),
        ("Player A", "call", 3.0),
        ("Player B", "fold", None),
        (None, None, None),
        ("Player A", "bet", 4.0),
    ]
    assert spot["history"][5] == {"street": "flop", "board": ["Ks", "7d", "2c"]}
    assert {p["name"]: p["stack_bb"] for p in spot["players"]} == {
        "Hero": 50.0,
        "Player A": 60.0,
        "Player B": 40.0,
    }
    assert spot["players"][[p["name"] for p in spot["players"]].index("Player A")]["tonight"] == {
        "hands": 30,
        "vpip": 21,
    }
    by_name = {p["name"]: p for p in spot["players"]}
    assert (by_name["Hero"]["behind_bb"], by_name["Player A"]["behind_bb"]) == (47.0, 53.0)
    assert [name for name, p in by_name.items() if p.get("folded")] == ["Player B"]


def test_a_name_that_is_a_poker_word_is_hidden_only_in_the_users_text():
    profiles = ProfileSet(
        {"riv": parse_profile("riv", "---\nname: river\n---\nShoves the turn, folds the river.\n")},
        (),
    )
    names = Pseudonyms()
    names.add("riv", ["river"], shown="river")
    built = prefix(profiles, names)
    assert "folds the Player A" in built.user_text  # privacy first in what the user wrote
    assert "Shoves the turn" in built.user_text and "river" not in built.user_text
    names.check(built.user_text)


def test_the_history_shows_dead_posts_and_streets_dealt_with_no_action_yet():
    lines = [
        '-- starting hand #5 (id: h5)  No Limit Texas Hold\'em (dealer: "jes @ heroid0001") --',
        'Player stacks: #1 "jes @ heroid0001" (10.00) | #2 "alex @ alexid0001" (10.00) | #3 "rj @ rjid000001" (10.00)',
        "Your hand is A♠, K♦",
        '"alex @ alexid0001" posts a small blind of 0.10',
        '"rj @ rjid000001" posts a big blind of 0.20',
        '"rj @ rjid000001" posts a missing small blind of 0.10',
        '"jes @ heroid0001" calls 0.20',
        '"alex @ alexid0001" calls 0.20',
        '"rj @ rjid000001" checks',
        "Flop:  [K♠, 7♦, 2♣]",
        '"alex @ alexid0001" checks',
        '"rj @ rjid000001" checks',
        '"jes @ heroid0001" checks',
        "Turn: K♠, 7♦, 2♣ [9♥]",
    ]
    h = build_hands((100 + i, line) for i, line in enumerate(lines)).hands[-1]
    ids = {"heroid0001": "Hero", "alexid0001": "Player A", "rjid000001": "Player B"}
    text = spot_message(h, "heroid0001", ids, positions(h), {}, ["check", "bet", "allin"])
    history = json.loads(text[text.index("{") : text.rindex("}") + 1])["history"]
    assert {
        "street": "preflop",
        "player": "Player B",
        "action": "post_missing_sb",
        "dead_bb": 0.5,
    } in history
    assert history[-1] == {"street": "turn", "board": ["Ks", "7d", "2c", "9h"]}
