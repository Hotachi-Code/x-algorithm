"""Campaign state: the durable memory of a never-ending game.

The design goal is that the story cannot run out. Every resolved thread is
required to spawn consequences, and every faction the crew annoys accumulates
heat that eventually buys a thread of its own. The result is a world that
generates its own next job rather than waiting for one to be written.

State lives in two places on purpose:

  .srgm/campaign.json   the machine-readable truth, loaded every session
  vault/20-Campaign/    the human-readable mirror, editable in Obsidian

`Campaign.sync_to_vault()` pushes state into notes; the Director reads those
notes back through retrieval, so a GM's hand-edits genuinely steer the story.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Heat ceiling. Above roughly 10 every faction is already hunting the crew, so
# letting the number climb further only makes the state file look dramatic.
HEAT_CAP = 12.0
PROPORTIONAL_DECAY = 0.05


def _clamp_heat(value: float) -> float:
    return max(0.0, min(HEAT_CAP, value))


SCENE_TYPES = (
    "meet",
    "legwork",
    "infiltration",
    "matrix",
    "social",
    "combat",
    "chase",
    "downtime",
    "twist",
    "fallout",
)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------
# pieces
# --------------------------------------------------------------------------

@dataclass
class Clock:
    """A Blades-style progress clock. Threads use these to feel like they move."""

    name: str
    segments: int = 6
    filled: int = 0
    hidden: bool = False

    def tick(self, amount: int = 1) -> bool:
        """Advance the clock; returns True if it just completed."""
        was_full = self.is_full
        self.filled = max(0, min(self.segments, self.filled + amount))
        return self.is_full and not was_full

    @property
    def is_full(self) -> bool:
        return self.filled >= self.segments

    def render(self) -> str:
        return f"[{'#' * self.filled}{'.' * max(0, self.segments - self.filled)}] {self.name}"


@dataclass
class Runner:
    """One of the three player characters."""

    name: str
    player: str = ""
    archetype: str = "street samurai"
    metatype: str = "human"
    concept: str = ""
    attributes: dict[str, int] = field(default_factory=dict)
    skills: dict[str, int] = field(default_factory=dict)
    gear: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    edge: int = 3
    edge_spent: int = 0
    karma: int = 0
    nuyen: int = 0
    physical_damage: int = 0
    stun_damage: int = 0
    notoriety: int = 0
    street_cred: int = 0
    public_awareness: int = 0
    conditions: list[str] = field(default_factory=list)
    bonds: dict[str, str] = field(default_factory=dict)   # NPC name -> relationship
    spotlight: int = 0   # scenes where this runner led; the Director balances it

    def pool(self, skill: str, attribute: str = "") -> int:
        """Skill rating + linked attribute, the standard Shadowrun pool."""
        return int(self.skills.get(skill, 0)) + int(self.attributes.get(attribute, 3))

    @property
    def edge_available(self) -> int:
        return max(0, self.edge - self.edge_spent)

    def wound_modifier(self) -> int:
        """-1 die per three boxes of damage, as the core rules have it."""
        return -((self.physical_damage // 3) + (self.stun_damage // 3))

    def summary(self) -> str:
        tags = [self.metatype, self.archetype]
        if self.concept:
            tags.append(self.concept)
        state = f"Edge {self.edge_available}/{self.edge}"
        if self.physical_damage or self.stun_damage:
            state += f", damage P{self.physical_damage}/S{self.stun_damage}"
        return f"{self.name} ({', '.join(tags)}) -- {state}"


@dataclass
class NPC:
    name: str
    role: str = "contact"
    faction: str = ""
    disposition: int = 0        # -3 hostile .. +3 loyal
    connection: int = 2
    loyalty: int = 1
    status: str = "active"      # active | burned | dead | missing | hostile
    knows: list[str] = field(default_factory=list)
    wants: str = ""
    voice: str = ""             # voice id for the TTS cast
    description: str = ""
    last_seen: str = ""
    debt: int = 0               # positive: they owe the crew

    def attitude(self) -> str:
        if self.disposition <= -2:
            return "hostile"
        if self.disposition < 0:
            return "wary"
        if self.disposition == 0:
            return "transactional"
        if self.disposition < 3:
            return "friendly"
        return "loyal"


@dataclass
class Faction:
    name: str
    kind: str = "corp"          # corp | gang | government | syndicate | other
    heat: float = 0.0           # how much attention the crew has drawn
    attitude: int = 0           # -3 hunting the crew .. +3 patron
    assets: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    retaliated: int = 0

    @property
    def threat_level(self) -> str:
        if self.heat >= 8:
            return "actively hunting"
        if self.heat >= 5:
            return "investigating"
        if self.heat >= 2:
            return "aware"
        return "oblivious"


@dataclass
class Thread:
    """An open story thread. The engine never lets the pool of these run dry."""

    title: str
    id: str = field(default_factory=lambda: _uid("thr"))
    kind: str = "job"           # job | rivalry | debt | mystery | personal | heat
    summary: str = ""
    status: str = "open"        # open | active | resolved | cold | failed
    tension: float = 1.0        # how loud this thread is right now
    stakes: str = ""
    clock: Clock | None = None
    npcs: list[str] = field(default_factory=list)
    factions: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    runners: list[str] = field(default_factory=list)
    parent: str = ""            # thread this one was spawned from
    beats_since_touched: int = 0
    created: str = field(default_factory=_now)
    resolved: str = ""
    notes: list[str] = field(default_factory=list)

    def touch(self) -> None:
        self.beats_since_touched = 0
        self.status = "active" if self.status == "open" else self.status

    def cool(self) -> None:
        self.beats_since_touched += 1

    def pressure(self) -> float:
        """Scheduling weight: loud threads and neglected threads both surface.

        The `beats_since_touched` term is what stops the campaign collapsing
        into a single storyline -- ignore a thread long enough and it starts
        demanding the spotlight.
        """
        if self.status in ("resolved", "failed"):
            return 0.0
        staleness = min(2.0, self.beats_since_touched * 0.22)
        clock_pressure = 0.0
        if self.clock is not None and self.clock.segments:
            clock_pressure = 1.2 * (self.clock.filled / self.clock.segments)
        cold_penalty = 0.4 if self.status == "cold" else 1.0
        return max(0.01, (self.tension + staleness + clock_pressure) * cold_penalty)


@dataclass
class Scene:
    """One narrated beat of play."""

    id: str = field(default_factory=lambda: _uid("scn"))
    number: int = 0
    session: int = 1
    type: str = "meet"
    title: str = ""
    thread_id: str = ""
    location: str = ""
    present: list[str] = field(default_factory=list)
    narration: str = ""
    hooks: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    outcome: str = ""
    player_input: str = ""
    created: str = field(default_factory=_now)
    audio: str = ""

    def headline(self) -> str:
        return self.title or f"{self.type.title()} scene {self.number}"


# --------------------------------------------------------------------------
# campaign
# --------------------------------------------------------------------------

@dataclass
class Campaign:
    name: str = "Neon Requiem"
    city: str = "Seattle Metroplex"
    year: int = 2081
    session: int = 1
    scene_number: int = 0
    runners: list[Runner] = field(default_factory=list)
    npcs: dict[str, NPC] = field(default_factory=dict)
    factions: dict[str, Faction] = field(default_factory=dict)
    threads: list[Thread] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    heat: float = 0.0
    payday: int = 0
    tone: str = "gritty, wired, rain-slick neon noir"
    recap: str = ""
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)

    # -- collections --------------------------------------------------------
    def runner(self, name: str) -> Runner | None:
        low = name.strip().lower()
        for r in self.runners:
            if r.name.lower() == low:
                return r
        return None

    def thread(self, thread_id: str) -> Thread | None:
        for t in self.threads:
            if t.id == thread_id or t.title.lower() == thread_id.lower():
                return t
        return None

    def open_threads(self) -> list[Thread]:
        return [t for t in self.threads if t.status in ("open", "active", "cold")]

    def npc(self, name: str) -> NPC | None:
        return self.npcs.get(name) or next(
            (v for k, v in self.npcs.items() if k.lower() == name.lower()), None
        )

    def faction(self, name: str, create: bool = True) -> Faction | None:
        existing = self.factions.get(name) or next(
            (v for k, v in self.factions.items() if k.lower() == name.lower()), None
        )
        if existing or not create:
            return existing
        faction = Faction(name=name)
        self.factions[name] = faction
        return faction

    # -- mutation -----------------------------------------------------------
    def add_thread(self, thread: Thread) -> Thread:
        self.threads.append(thread)
        for name in thread.factions:
            self.faction(name)
        return thread

    def add_npc(self, npc: NPC) -> NPC:
        self.npcs.setdefault(npc.name, npc)
        return self.npcs[npc.name]

    def add_scene(self, scene: Scene) -> Scene:
        self.scene_number += 1
        scene.number = self.scene_number
        scene.session = self.session
        self.scenes.append(scene)
        self.updated = _now()
        return scene

    def raise_heat(self, amount: float, factions: Iterable[str] = ()) -> None:
        self.heat = _clamp_heat(self.heat + amount)
        for name in factions:
            faction = self.faction(name)
            if faction is not None:
                faction.heat = _clamp_heat(faction.heat + amount)

    def decay_heat(self, rate: float = 0.15) -> None:
        """Heat fades when the crew lies low.

        Decay is proportional as well as flat, so heat behaves like a leaky
        bucket: it climbs fast when the crew is loud and plateaus instead of
        growing without bound over a long campaign.
        """
        self.heat = max(0.0, self.heat - rate - self.heat * PROPORTIONAL_DECAY)
        for faction in self.factions.values():
            faction.heat = max(
                0.0,
                faction.heat - rate * 0.6 - faction.heat * PROPORTIONAL_DECAY * 0.6,
            )

    def cool_threads(self, except_id: str = "") -> None:
        for thread in self.threads:
            if thread.id != except_id and thread.status in ("open", "active", "cold"):
                thread.cool()

    def recent_scenes(self, n: int = 5) -> list[Scene]:
        return self.scenes[-n:]

    # -- reporting ----------------------------------------------------------
    def status_line(self) -> str:
        return (
            f"{self.name} | session {self.session}, scene {self.scene_number} | "
            f"heat {self.heat:.1f} | {len(self.open_threads())} open threads"
        )

    def brief(self) -> str:
        """A compact state dump used as the Director's system context."""
        lines = [
            f"CAMPAIGN: {self.name} -- {self.city}, {self.year}",
            f"TONE: {self.tone}",
            f"SESSION {self.session}, SCENE {self.scene_number}, HEAT {self.heat:.1f}",
            "",
            "RUNNERS:",
        ]
        for r in self.runners:
            bonds = "; ".join(f"{k}: {v}" for k, v in r.bonds.items())
            lines.append(f"  - {r.summary()}" + (f" | bonds: {bonds}" if bonds else ""))

        active = sorted(self.open_threads(), key=lambda t: -t.pressure())[:6]
        if active:
            lines += ["", "OPEN THREADS (most pressing first):"]
            for t in active:
                clock = f" {t.clock.render()}" if t.clock else ""
                lines.append(
                    f"  - [{t.kind}] {t.title} ({t.status}, tension {t.tension:.1f})"
                    f"{clock}: {t.summary}"
                )

        hot = [f for f in self.factions.values() if f.heat >= 1]
        if hot:
            lines += ["", "FACTION ATTENTION:"]
            for f in sorted(hot, key=lambda f: -f.heat)[:6]:
                lines.append(f"  - {f.name}: {f.threat_level} (heat {f.heat:.1f})")

        known = [n for n in self.npcs.values() if n.status == "active"][:10]
        if known:
            lines += ["", "NPCS IN PLAY:"]
            for n in known:
                lines.append(
                    f"  - {n.name} ({n.role}"
                    + (f", {n.faction}" if n.faction else "")
                    + f") -- {n.attitude()}"
                    + (f"; wants {n.wants}" if n.wants else "")
                )

        if self.recap:
            lines += ["", "STORY SO FAR:", f"  {self.recap}"]
        return "\n".join(lines)

    # -- persistence --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "city": self.city,
            "year": self.year,
            "session": self.session,
            "scene_number": self.scene_number,
            "heat": self.heat,
            "payday": self.payday,
            "tone": self.tone,
            "recap": self.recap,
            "created": self.created,
            "updated": _now(),
            "runners": [asdict(r) for r in self.runners],
            "npcs": {k: asdict(v) for k, v in self.npcs.items()},
            "factions": {k: asdict(v) for k, v in self.factions.items()},
            "threads": [_thread_to_dict(t) for t in self.threads],
            "scenes": [asdict(s) for s in self.scenes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Campaign":
        campaign = cls(
            name=data.get("name", "Neon Requiem"),
            city=data.get("city", "Seattle Metroplex"),
            year=int(data.get("year", 2081)),
            session=int(data.get("session", 1)),
            scene_number=int(data.get("scene_number", 0)),
            heat=float(data.get("heat", 0.0)),
            payday=int(data.get("payday", 0)),
            tone=data.get("tone", "gritty, wired, rain-slick neon noir"),
            recap=data.get("recap", ""),
            created=data.get("created", _now()),
        )
        campaign.runners = [Runner(**r) for r in data.get("runners", [])]
        campaign.npcs = {k: NPC(**v) for k, v in data.get("npcs", {}).items()}
        campaign.factions = {k: Faction(**v) for k, v in data.get("factions", {}).items()}
        campaign.threads = [_thread_from_dict(t) for t in data.get("threads", [])]
        campaign.scenes = [Scene(**s) for s in data.get("scenes", [])]
        return campaign

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)  # atomic: never lose a campaign to a crash mid-write
        return path

    @classmethod
    def load(cls, path: Path) -> "Campaign | None":
        path = Path(path)
        if not path.is_file():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _thread_to_dict(thread: Thread) -> dict[str, Any]:
    data = asdict(thread)
    data["clock"] = asdict(thread.clock) if thread.clock else None
    return data


def _thread_from_dict(data: dict[str, Any]) -> Thread:
    payload = dict(data)
    clock_data = payload.pop("clock", None)
    thread = Thread(**payload)
    if clock_data:
        thread.clock = Clock(**clock_data)
    return thread
