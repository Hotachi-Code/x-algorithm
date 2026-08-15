import random

from shadowrun_gm.rules import DicePool, extended_test, opposed_test, roll
from shadowrun_gm.rules.initiative import Combatant, InitiativeTracker


class FixedRandom(random.Random):
    """Deterministic d6 source: replays a fixed sequence of faces."""

    def __init__(self, faces):
        super().__init__()
        self.faces = list(faces)
        self.index = 0

    def randint(self, a, b):
        face = self.faces[self.index % len(self.faces)]
        self.index += 1
        return face


def test_hits_counted_on_five_and_six():
    result = roll(6, rng=FixedRandom([1, 2, 3, 4, 5, 6]))
    assert result.hits == 2
    assert result.ones == 1
    assert len(result.dice) == 6


def test_glitch_when_half_the_pool_is_ones():
    result = roll(4, rng=FixedRandom([1, 1, 5, 6]))
    assert result.glitch is True
    assert result.critical_glitch is False


def test_critical_glitch_is_a_glitch_with_no_hits():
    result = roll(4, rng=FixedRandom([1, 1, 1, 2]))
    assert result.glitch is True
    assert result.critical_glitch is True


def test_no_glitch_below_half():
    result = roll(6, rng=FixedRandom([1, 1, 3, 4, 5, 6]))
    assert result.glitch is False


def test_limit_caps_hits():
    result = roll(DicePool(6, limit=2), rng=FixedRandom([5, 5, 6, 6, 5, 6]))
    assert result.raw_hits == 6
    assert result.hits == 2
    assert result.limited is True


def test_edge_ignores_limit_and_glitches():
    # All sixes: exploding dice must add more, and the limit must not bite.
    result = roll(DicePool(3, limit=1), edge=True, rng=FixedRandom([6, 6, 6, 1, 1, 1]))
    assert result.limited is False
    assert result.glitch is False
    assert len(result.dice) > 3


def test_edge_explodes_sixes():
    result = roll(2, edge=True, rng=FixedRandom([6, 6, 2, 2]))
    assert len(result.dice) == 4


def test_opposed_ties_go_to_the_defender():
    rng = FixedRandom([5, 5, 5, 5])  # both sides roll the same
    result = opposed_test(2, 2, rng=rng)
    assert result.success is False
    assert result.net_hits == 0


def test_extended_test_reaches_threshold():
    result = extended_test(10, 3, rng=random.Random(7))
    assert result.total_hits >= 3
    assert result.success is True
    assert result.intervals >= 1


def test_extended_test_gives_up_when_dice_run_out():
    result = extended_test(2, 99, rng=random.Random(1))
    assert result.success is False
    assert result.intervals <= 2


def test_initiative_passes_subtract_ten():
    tracker = InitiativeTracker()
    tracker.add(Combatant("Ratchet", base=20, initiative_dice=1, is_pc=True))
    tracker.add(Combatant("Guard", base=6, initiative_dice=1))
    tracker.new_turn(random.Random(3))
    first = tracker.order()
    assert first[0].name == "Ratchet"

    tracker.next_pass()
    remaining = [c.name for c in tracker.order()]
    assert "Ratchet" in remaining
    assert tracker.pass_number == 2


def test_wound_modifier_scales_with_damage():
    from shadowrun_gm.engine.state import Runner

    runner = Runner(name="Static", physical_damage=6, stun_damage=3)
    assert runner.wound_modifier() == -3
