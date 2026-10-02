import pytest

from vamos.cards import ALL_HAND_CLASSES, hand_class, parse_log_card


@pytest.mark.parametrize(
    ("text", "card"),
    [("A♠", "As"), ("10♥", "Th"), ("2♣", "2c"), ("K♦", "Kd"), (" 8♣ ", "8c")],
)
def test_parse_log_card(text, card):
    assert parse_log_card(text) == card


@pytest.mark.parametrize("text", ["", "A", "1♠", "11♥", "AX", "Ace of Spades"])
def test_parse_log_card_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_log_card(text)


def test_hand_class_offsuit():
    assert hand_class("As", "Kh") == "AKo"


def test_hand_class_suited():
    assert hand_class("As", "Ks") == "AKs"


def test_hand_class_pair():
    assert hand_class("Qh", "Qc") == "QQ"


def test_hand_class_order_invariant():
    assert hand_class("2h", "Ac") == hand_class("Ac", "2h") == "A2o"


def test_all_hand_classes_count():
    pairs = [h for h in ALL_HAND_CLASSES if len(h) == 2]
    suited = [h for h in ALL_HAND_CLASSES if h.endswith("s")]
    offsuit = [h for h in ALL_HAND_CLASSES if h.endswith("o")]
    assert (len(pairs), len(suited), len(offsuit)) == (13, 78, 78)
    assert len(set(ALL_HAND_CLASSES)) == 169


def test_every_two_card_combo_maps_to_a_known_class():
    deck = [r + s for r in "23456789TJQKA" for s in "shdc"]
    seen = {hand_class(a, b) for i, a in enumerate(deck) for b in deck[i + 1 :]}
    assert seen == set(ALL_HAND_CLASSES)
