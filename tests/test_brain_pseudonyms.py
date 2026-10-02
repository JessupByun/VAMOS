import pytest

from vamos.brain.pseudonyms import LeakError, Pseudonyms


def table() -> Pseudonyms:
    p = Pseudonyms()
    p.add_hero_name("jes")
    p.add_hero_name("Jessup")
    p.add("alex", ["alex", "Alexander", "alex99"], shown="alex")
    p.add("rj", ["rj", "Ray"], shown="rj")
    return p


def test_people_get_stable_letters_in_the_order_they_are_seen():
    p = table()
    assert (p.label("alex"), p.label("rj")) == ("Player A", "Player B")
    p.add("alex", ["Lex"], shown="alex")  # a new nickname keeps the same letter
    assert p.label("alex") == "Player A"
    assert p.add("sam", ["sam"], shown="sam") == "Player C"


def test_every_known_name_is_replaced_case_insensitively_and_whole_word():
    p = table()
    text = "Alexander (alex99) calls too wide; ALEX folds to rj. Raymond is not Ray. jes vs Jessup."
    hidden = p.hide(text)
    assert hidden == (
        "Player A (Player A) calls too wide; Player A folds to Player B. "
        "Raymond is not Player B. Hero vs Hero."
    )
    p.check(hidden)


def test_longer_names_are_replaced_before_names_inside_them():
    p = Pseudonyms()
    p.add("bob", ["bob", "bobby"], shown="bob")
    assert p.hide("bobby and bob") == "Player A and Player A"


def test_names_with_regex_characters():
    p = Pseudonyms()
    p.add("x", ["c++ guy", "a.b"], shown="x")
    assert (
        p.hide("c++ guy raised; a.b called; axb did not")
        == "Player A raised; Player A called; axb did not"
    )


def test_a_surviving_name_blocks_the_call_without_echoing_it():
    p = table()
    with pytest.raises(LeakError) as error:
        p.check("Player A said hi to Ray")
    assert "Ray" not in str(error.value)


def test_one_letter_names_are_reported_not_substituted():
    p = Pseudonyms()
    p.add("q", ["Q"], shown="Q")
    assert p.unusable == {"Q"}
    assert p.hide("Q raises") == "Q raises"


def test_reveal_turns_pseudonyms_back_into_names():
    p = table()
    assert p.reveal("Player A overcalls; Hero should value bet thinner than vs Player B.") == (
        "alex overcalls; you should value bet thinner than vs rj."
    )


def test_names_that_are_pseudonyms_are_never_substituted():
    p = Pseudonyms()
    p.add_hero_name("Hero")
    p.add("x", ["player b", "Bob"], shown="Bob")
    assert p.unusable == {"player b"}
    text = p.hide("Hero raises and Bob calls")
    assert text == "Hero raises and Player A calls"
    p.check(text)
