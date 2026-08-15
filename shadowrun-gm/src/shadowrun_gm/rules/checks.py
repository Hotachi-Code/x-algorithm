"""Tests built on top of the pool: success, opposed, and extended.

Every one of these returns a structured result rather than a bare boolean,
because the Director narrates the *margin*, not just the pass/fail. Three net
hits reads very differently from one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .dice import DicePool, RollResult, roll


@dataclass
class OpposedResult:
    attacker: RollResult
    defender: RollResult
    net_hits: int
    success: bool

    def describe(self) -> str:
        verb = "beats" if self.success else "fails against"
        return (
            f"{self.attacker.describe()} {verb} {self.defender.describe()} "
            f"-> net {self.net_hits}"
        )


@dataclass
class ExtendedResult:
    rolls: list[RollResult] = field(default_factory=list)
    total_hits: int = 0
    intervals: int = 0
    success: bool = False
    glitched: bool = False
    critical_glitched: bool = False

    def describe(self) -> str:
        state = "completed" if self.success else "ran out of dice"
        return (
            f"Extended test {state} after {self.intervals} interval(s), "
            f"{self.total_hits} hits total"
        )


def success_test(
    pool: DicePool | int,
    threshold: int = 1,
    *,
    edge: bool = False,
    rng: random.Random | None = None,
    label: str = "",
) -> tuple[bool, RollResult]:
    """Simple test against a threshold. Returns (met_threshold, roll)."""
    result = roll(pool, edge=edge, rng=rng, label=label)
    return result.hits >= threshold, result


def opposed_test(
    attacker: DicePool | int,
    defender: DicePool | int,
    *,
    attacker_edge: bool = False,
    defender_edge: bool = False,
    rng: random.Random | None = None,
) -> OpposedResult:
    """Two pools head to head. Ties go to the defender, as they should."""
    rng = rng or random.Random()
    a = roll(attacker, edge=attacker_edge, rng=rng, label="attacker")
    d = roll(defender, edge=defender_edge, rng=rng, label="defender")
    net = a.hits - d.hits
    return OpposedResult(attacker=a, defender=d, net_hits=max(0, net), success=net > 0)


def extended_test(
    pool: DicePool | int,
    threshold: int,
    *,
    max_intervals: int = 12,
    rng: random.Random | None = None,
    label: str = "",
) -> ExtendedResult:
    """Grind toward a threshold, losing one die per interval.

    Stops early on a critical glitch -- the decker did not merely fail to crack
    the host, they tripped something.
    """
    rng = rng or random.Random()
    size = pool.size if isinstance(pool, DicePool) else int(pool)
    limit = pool.limit if isinstance(pool, DicePool) else None

    out = ExtendedResult()
    while size > 0 and out.intervals < max_intervals:
        r = roll(DicePool(size, limit, label), rng=rng, label=label)
        out.rolls.append(r)
        out.intervals += 1
        out.total_hits += r.hits
        if r.glitch:
            out.glitched = True
        if r.critical_glitch:
            out.critical_glitched = True
            break
        if out.total_hits >= threshold:
            out.success = True
            break
        size -= 1
    return out
