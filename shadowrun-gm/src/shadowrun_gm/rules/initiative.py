"""Initiative order for a combat turn.

Shadowrun runs multiple initiative passes inside one combat turn: everyone
acts in descending order, then 10 comes off every score and anyone still above
zero acts again. Wired reflexes are worth the essence precisely because of
this loop.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Combatant:
    name: str
    base: int  # REA + INT (or the 6e equivalent)
    initiative_dice: int = 1
    score: int = 0
    is_pc: bool = False
    tags: list[str] = field(default_factory=list)

    def roll_initiative(self, rng: random.Random) -> int:
        dice = sum(rng.randint(1, 6) for _ in range(max(1, self.initiative_dice)))
        self.score = self.base + dice
        return self.score


@dataclass
class InitiativeTracker:
    """Holds one combat turn's worth of order and walks it pass by pass."""

    combatants: list[Combatant] = field(default_factory=list)
    turn: int = 0
    pass_number: int = 0

    def add(self, combatant: Combatant) -> None:
        self.combatants.append(combatant)

    def new_turn(self, rng: random.Random | None = None) -> list[Combatant]:
        rng = rng or random.Random()
        self.turn += 1
        self.pass_number = 1
        for c in self.combatants:
            c.roll_initiative(rng)
        return self.order()

    def order(self) -> list[Combatant]:
        """Everyone still standing this pass, highest score first."""
        active = [c for c in self.combatants if c.score > 0]
        # PCs win ties: it keeps the table moving and players feel it.
        return sorted(active, key=lambda c: (c.score, c.is_pc), reverse=True)

    def next_pass(self) -> list[Combatant]:
        self.pass_number += 1
        for c in self.combatants:
            c.score -= 10
        return self.order()

    def remove(self, name: str) -> None:
        self.combatants = [c for c in self.combatants if c.name != name]

    def summary(self) -> str:
        rows = [
            f"  {c.score:>3}  {c.name}{' (PC)' if c.is_pc else ''}"
            for c in self.order()
        ]
        head = f"Combat turn {self.turn}, pass {self.pass_number}"
        return "\n".join([head, *rows]) if rows else f"{head}: nobody left to act"
