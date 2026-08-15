"""The d6 pool at the heart of Shadowrun.

Roll a pile of six-siders, count 5s and 6s as hits, and watch the 1s. Edge
turns the roll explosive (Rule of Six) and lifts the limit cap. The engine
uses this both to resolve player actions and to decide how badly a scene goes
sideways when nobody is rolling at all.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

HIT_FACES = (5, 6)


@dataclass(frozen=True)
class DicePool:
    """A described pool: attribute + skill + modifiers, with an optional limit."""

    size: int
    limit: int | None = None
    label: str = ""

    def modified(self, delta: int, reason: str = "") -> "DicePool":
        label = self.label
        if reason:
            sign = "+" if delta >= 0 else ""
            label = f"{label} ({sign}{delta} {reason})".strip()
        return DicePool(max(0, self.size + delta), self.limit, label)


@dataclass
class RollResult:
    """The outcome of one pool of dice, with enough detail to narrate it."""

    dice: list[int] = field(default_factory=list)
    hits: int = 0
    raw_hits: int = 0
    ones: int = 0
    glitch: bool = False
    critical_glitch: bool = False
    limit: int | None = None
    limited: bool = False
    edge_used: bool = False
    label: str = ""

    @property
    def net(self) -> int:
        return self.hits

    def describe(self) -> str:
        bits = [f"{self.hits} hit{'s' if self.hits != 1 else ''}"]
        if self.limited:
            bits.append(f"capped by limit {self.limit} (rolled {self.raw_hits})")
        if self.critical_glitch:
            bits.append("CRITICAL GLITCH")
        elif self.glitch:
            bits.append("glitch")
        if self.edge_used:
            bits.append("Edge burned")
        head = f"{self.label}: " if self.label else ""
        return head + ", ".join(bits)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "dice": list(self.dice),
            "hits": self.hits,
            "raw_hits": self.raw_hits,
            "ones": self.ones,
            "glitch": self.glitch,
            "critical_glitch": self.critical_glitch,
            "limit": self.limit,
            "limited": self.limited,
            "edge_used": self.edge_used,
        }


def _explode(rng: random.Random, count: int, out: list[int]) -> None:
    """Rule of Six: every 6 rolls again, recursively."""
    while count:
        rerolls = 0
        for _ in range(count):
            face = rng.randint(1, 6)
            out.append(face)
            if face == 6:
                rerolls += 1
        count = rerolls


def roll(
    pool: DicePool | int,
    *,
    edge: bool = False,
    rng: random.Random | None = None,
    limit: int | None = None,
    label: str = "",
) -> RollResult:
    """Roll a Shadowrun pool.

    Edge does three things at once, and all three matter to the story: it adds
    the Rule of Six, it ignores the limit, and it means a critical glitch
    cannot happen -- so a player spending Edge is buying narrative safety.
    """
    rng = rng or random.Random()
    if isinstance(pool, int):
        pool = DicePool(pool, limit, label)
    elif limit is not None:
        pool = DicePool(pool.size, limit, label or pool.label)

    dice: list[int] = []
    base = max(0, pool.size)
    for _ in range(base):
        dice.append(rng.randint(1, 6))

    if edge:
        sixes = sum(1 for d in dice if d == 6)
        _explode(rng, sixes, dice)

    raw_hits = sum(1 for d in dice if d in HIT_FACES)
    ones = sum(1 for d in dice if d == 1)

    # A glitch is half or more of the *originally rolled* dice showing 1.
    glitch = base > 0 and ones * 2 >= base
    if edge:
        glitch = False

    effective_limit = pool.limit
    limited = False
    hits = raw_hits
    if effective_limit is not None and not edge and raw_hits > effective_limit:
        hits = effective_limit
        limited = True

    return RollResult(
        dice=dice,
        hits=hits,
        raw_hits=raw_hits,
        ones=ones,
        glitch=glitch,
        critical_glitch=glitch and hits == 0,
        limit=effective_limit,
        limited=limited,
        edge_used=edge,
        label=label or pool.label,
    )
