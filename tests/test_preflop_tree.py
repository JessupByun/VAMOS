import pytest

from vamos.preflop.tree import PUSH_FOLD, Decision, Menu, Terminal, build


def at(tree, *path: str):
    node = tree.root
    for step in path:
        node = node.children[node.actions.index(step)]
    return node


def test_heads_up_small_blind_opens_limps_or_folds():
    tree = build(2, 50)
    assert tree.root.actions == ("fold", "call", "raise 2.5", "raise 3")
    assert at(tree, "call").actions == ("check", "raise 4")  # the big blind isolates a limp
    assert at(tree, "raise 2.5").actions == ("fold", "call", "raise 7.5", "raise 10")
    assert at(tree, "raise 3", "raise 9").actions == ("fold", "call", "jam")
    assert at(tree, "raise 3", "raise 9", "jam").actions == ("fold", "call")


def test_terminals():
    tree = build(2, 50)
    assert at(tree, "fold").kind == "fold"
    assert at(tree, "call", "check").kind == "flop"
    assert at(tree, "raise 3", "call").pot == 6
    done = at(tree, "raise 3", "raise 9", "jam", "call")
    assert (done.kind, done.committed) == ("showdown", (50, 50))


def test_squeeze_and_isolation_sizes_grow_with_callers_and_limpers():
    tree = build(4, 50)
    # CO opens 2.5 and BTN calls: the blinds' 3-bets are 3x or 4x plus one raise per caller.
    assert at(tree, "raise 2.5", "call").actions == ("fold", "call", "raise 10", "raise 12.5")
    # Two limpers: isolating costs one big blind more than over one.
    assert at(tree, "call", "call").actions[-1] == "raise 5"
    assert at(tree, "call", "fold", "fold").actions == ("check", "raise 4")


def test_sizes_at_or_beyond_the_stack_become_all_in():
    tree = build(2, 10)
    assert at(tree, "raise 3").actions == ("fold", "call", "raise 9", "jam")


def test_the_big_blind_closes_the_action():
    tree = build(4, 50)
    assert at(tree, "call", "call", "call", "check").kind == "flop"
    node = at(tree, "fold", "fold", "raise 3")
    assert isinstance(node, Decision) and tree.positions[node.player] == "BB"


def test_no_cold_calls_of_a_three_bet():
    # CO opens, BTN folds, SB 3-bets: the big blind has put in only the blind.
    open_three_bet = ("raise 2.5", "fold", "raise 10")
    assert at(build(4, 50), *open_three_bet).actions == ("fold", "jam")
    assert "call" in at(build(4, 50, Menu(cold_calls=True)), *open_three_bet).actions
    # The opener, who put chips in voluntarily, may still call.
    assert "call" in at(build(4, 50), *open_three_bet, "fold").actions
    # Facing an all-in, a player who has put nothing in may call.
    assert "call" in at(build(4, 50), "raise 2.5", "raise 10", "jam").actions


def test_push_fold():
    tree = build(2, 10, PUSH_FOLD)
    assert tree.root.actions == ("fold", "jam")
    assert at(tree, "jam").actions == ("fold", "call")
    assert len(tree.decisions) == 2


@pytest.mark.parametrize("players", [2, 3, 4])
def test_every_terminal_balances(players):
    def walk(node):
        if isinstance(node, Terminal):
            yield node
        else:
            for child in node.children:
                yield from walk(child)

    for terminal in walk(build(players, 50).root):
        assert terminal.active
        if terminal.kind == "showdown":
            assert all(terminal.committed[p] == 50 for p in terminal.active)
        if terminal.kind == "flop":
            assert len({terminal.committed[p] for p in terminal.active}) == 1


def test_a_limper_or_completed_small_blind_may_call_a_three_bet():
    # Only a player who has posted nothing but a blind is barred from calling a 3-bet.
    assert "call" in at(build(4, 50), "call", "raise 4", "raise 12", "fold").actions
    assert "call" in at(build(3, 50), "call", "call", "raise 5", "raise 15").actions


def test_squeeze_size_grows_with_each_caller():
    assert at(build(4, 50), "raise 2.5", "call", "call").actions == (
        "fold",
        "call",
        "raise 12.5",
        "raise 15",
    )
