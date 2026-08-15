"""Shadowrun mechanics: dice pools, tests, initiative."""

from .dice import DicePool, RollResult, roll
from .checks import (
    ExtendedResult,
    OpposedResult,
    extended_test,
    opposed_test,
    success_test,
)
from .initiative import Combatant, InitiativeTracker

__all__ = [
    "DicePool",
    "RollResult",
    "roll",
    "success_test",
    "opposed_test",
    "extended_test",
    "OpposedResult",
    "ExtendedResult",
    "Combatant",
    "InitiativeTracker",
]
