"""Random tables, loaded from JSON, with a memory.

`Tables.pick` avoids repeating an entry until the table has been exhausted.
Over a long campaign that is the difference between a world that feels
generated and one that feels written.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "tables"


def load_tables(extra_dir: Path | None = None) -> dict[str, list[Any]]:
    """Load bundled tables, then overlay any project-local ones."""
    tables: dict[str, list[Any]] = {}
    for directory in (DATA_DIR, extra_dir):
        if directory is None or not Path(directory).is_dir():
            continue
        for path in sorted(Path(directory).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            namespace = path.stem
            for key, values in data.items():
                if key.startswith("_") or not isinstance(values, list):
                    continue
                tables[f"{namespace}.{key}"] = values
                tables.setdefault(key, values)
    return tables


@dataclass
class Tables:
    """A table collection with per-key non-repeating draw bags."""

    data: dict[str, list[Any]] = field(default_factory=load_tables)
    rng: random.Random = field(default_factory=random.Random)
    _bags: dict[str, list[Any]] = field(
        default_factory=lambda: defaultdict(list), init=False, repr=False
    )

    @classmethod
    def open(cls, extra_dir: Path | None = None, seed: int | None = None) -> "Tables":
        return cls(data=load_tables(extra_dir), rng=random.Random(seed))

    def has(self, key: str) -> bool:
        return bool(self.data.get(key))

    def all(self, key: str) -> list[Any]:
        return list(self.data.get(key, []))

    def pick(self, key: str, default: Any = "") -> Any:
        """Draw without replacement; the bag refills when it empties."""
        source = self.data.get(key)
        if not source:
            return default
        bag = self._bags[key]
        if not bag:
            bag = list(source)
            self.rng.shuffle(bag)
            self._bags[key] = bag
        return bag.pop()

    def pick_many(self, key: str, n: int) -> list[Any]:
        return [self.pick(key) for _ in range(n)]

    def choice(self, key: str, default: Any = "") -> Any:
        """Plain uniform choice, repeats allowed."""
        source = self.data.get(key)
        return self.rng.choice(source) if source else default

    def maybe(self, chance: float) -> bool:
        return self.rng.random() < chance

    def register(self, key: str, values: list[Any]) -> None:
        self.data[key] = values
        self._bags.pop(key, None)
