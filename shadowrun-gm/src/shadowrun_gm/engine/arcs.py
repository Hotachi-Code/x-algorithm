"""Procedural arcs: where the next job comes from, forever.

Three guarantees keep the campaign from ever stalling:

1. every resolved thread spawns at least one consequence thread
2. every faction whose heat crosses a threshold buys a retaliation thread
3. if the open-thread pool ever drops below a floor, a fresh job is generated

Together these mean the pool of things that could happen next strictly grows
faster than the crew can close it, which is exactly the texture of a long
Shadowrun campaign.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from .state import Campaign, Clock, NPC, Scene, Thread
from .tables import Tables

MIN_OPEN_THREADS = 3
RETALIATION_HEAT = 5.0


@dataclass
class RunSeed:
    """The skeleton of a job, before the Director puts prose on it."""

    employer: str
    objective: str
    site: str
    complication: str
    twist: str
    payout: str
    deadline: str
    johnson: str
    factions: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        return (
            f"EMPLOYER: {self.employer}\n"
            f"JOHNSON: {self.johnson}\n"
            f"OBJECTIVE: {self.objective}\n"
            f"SITE: {self.site}\n"
            f"DEADLINE: {self.deadline}\n"
            f"PAYOUT: {self.payout}\n"
            f"COMPLICATION (reveal in play, not up front): {self.complication}\n"
            f"TWIST (hold until the run is underway): {self.twist}"
        )

    def title(self) -> str:
        head = self.objective.split(",")[0].strip()
        return head[:1].upper() + head[1:]


# Which scene types make sense next, given the last one. Keeps pacing varied
# without ever producing a nonsensical jump.
SCENE_FLOW: dict[str, tuple[str, ...]] = {
    "meet": ("legwork", "social", "twist"),
    "legwork": ("social", "infiltration", "matrix", "chase", "meet"),
    "social": ("legwork", "infiltration", "twist", "combat"),
    "infiltration": ("matrix", "combat", "chase", "twist"),
    "matrix": ("infiltration", "combat", "twist", "legwork"),
    "combat": ("chase", "fallout", "twist"),
    "chase": ("fallout", "combat", "downtime"),
    "twist": ("combat", "infiltration", "social", "chase"),
    "fallout": ("downtime", "meet", "social"),
    "downtime": ("meet", "legwork", "twist"),
}

THREAD_SCENE_BIAS: dict[str, tuple[str, ...]] = {
    "job": ("meet", "legwork", "infiltration", "matrix"),
    "rivalry": ("combat", "chase", "twist", "social"),
    "debt": ("social", "meet", "downtime"),
    "mystery": ("legwork", "matrix", "social", "twist"),
    "personal": ("social", "downtime", "fallout"),
    "heat": ("chase", "combat", "twist", "legwork"),
}


@dataclass
class ArcGenerator:
    tables: Tables
    rng: random.Random = field(default_factory=random.Random)

    # -- job creation -------------------------------------------------------
    def new_run_seed(self, campaign: Campaign) -> RunSeed:
        johnson = self._johnson_name(campaign)
        factions = self._pick_factions(campaign)
        return RunSeed(
            employer=self.tables.pick("jobs.employer", "an anonymous fixer"),
            objective=self.tables.pick("jobs.objective", "steal something valuable"),
            site=self.tables.pick("jobs.target_site", "a warehouse in Puyallup"),
            complication=self.tables.pick("jobs.complication", "the intel is wrong"),
            twist=self.tables.pick("jobs.twist", "the client is lying"),
            payout=self.tables.pick("jobs.payout", "10,000 nuyen each"),
            deadline=self.tables.pick("jobs.time_pressure", "within three days"),
            johnson=johnson,
            factions=factions,
        )

    def new_job_thread(self, campaign: Campaign, seed: RunSeed | None = None) -> Thread:
        """Create a job thread, its Johnson, and the clock that pressures it."""
        seed = seed or self.new_run_seed(campaign)
        thread = Thread(
            title=seed.title(),
            kind="job",
            summary=f"{seed.objective.capitalize()} at {seed.site}. Deadline: {seed.deadline}.",
            stakes=seed.payout,
            tension=1.2,
            factions=seed.factions,
            npcs=[seed.johnson],
            clock=Clock(name=f"Deadline: {seed.deadline}", segments=6),
        )
        thread.notes.append(seed.to_prompt())
        campaign.add_thread(thread)

        if campaign.npc(seed.johnson) is None:
            campaign.add_npc(
                NPC(
                    name=seed.johnson,
                    role="Mr. Johnson",
                    faction=seed.factions[0] if seed.factions else "",
                    disposition=0,
                    wants=self.tables.pick("people.wants", "a clean job"),
                    description=seed.employer,
                    voice=self.tables.pick("people.voice_note", ""),
                )
            )
        return thread

    def _johnson_name(self, campaign: Campaign) -> str:
        for _ in range(12):
            name = str(self.tables.pick("people.street_name", "Johnson"))
            if campaign.npc(name) is None:
                return name
        return f"Johnson-{self.rng.randint(100, 999)}"

    def _pick_factions(self, campaign: Campaign) -> list[str]:
        """Prefer factions already in play -- recurring antagonists beat new ones.

        Not always, though: a campaign whose every job involves the same corp
        stops feeling like a city. Roughly half the time a new name comes in,
        and occasionally two factions have a stake in the same job, which is
        where the best Shadowrun complications come from.
        """
        pool = self._corp_pool()
        known = [f.name for f in campaign.factions.values()]

        chosen: list[str] = []
        if known and self.rng.random() < 0.5:
            chosen.append(self.rng.choice(known))
        elif pool:
            chosen.append(self.rng.choice(pool))

        if pool and self.rng.random() < 0.2:
            rival = self.rng.choice(pool)
            if rival not in chosen:
                chosen.append(rival)
        return chosen

    def _corp_pool(self) -> list[str]:
        """Canonical corporation names. A project `tables/` file can override."""
        pool = [str(v) for v in self.tables.all("corporation")]
        if pool:
            return pool
        from ..ingest.linker import lexicon_names

        return lexicon_names("megacorp")

    # -- consequences -------------------------------------------------------
    def spawn_consequences(
        self, campaign: Campaign, thread: Thread, outcome: str = "mixed"
    ) -> list[Thread]:
        """Resolve a thread into one or two successors. Never zero."""
        bucket = outcome if outcome in ("success", "failure", "mixed") else "mixed"
        seeds = self.tables.all(f"consequences.{bucket}") or self.tables.all(
            "consequences.mixed"
        )
        if not seeds:
            return []

        count = 2 if self.rng.random() < 0.35 else 1
        chosen = self.rng.sample(seeds, k=min(count, len(seeds)))
        spawned: list[Thread] = []
        for seed in chosen:
            factions = list(thread.factions)
            # A consequence that only ever involves the same corp makes the
            # sprawl feel like a two-player game. Sometimes a third party
            # notices and takes an interest of their own.
            if self.rng.random() < 0.3:
                pool = [f for f in self._corp_pool() if f not in factions]
                if pool:
                    factions.append(self.rng.choice(pool))

            child = Thread(
                title=f"{seed['title']}",
                kind=seed.get("kind", "job"),
                summary=seed.get("summary", ""),
                tension=float(seed.get("tension", 1.0)),
                parent=thread.id,
                factions=factions,
                npcs=list(thread.npcs)[:2],
                locations=list(thread.locations)[:2],
            )
            if seed.get("clock"):
                child.clock = Clock(name=child.title, segments=int(seed["clock"]))
            # Consequences usually arrive wearing a face. Minting the NPC here
            # rather than waiting for the narrator means the cast grows even
            # when the offline narrator is driving.
            if self.rng.random() < 0.55:
                npc = self.mint_npc(campaign, faction=child.factions[0] if child.factions else "")
                if npc is not None:
                    child.npcs.insert(0, npc.name)
            campaign.add_thread(child)
            spawned.append(child)
        return spawned

    def mint_npc(self, campaign: Campaign, *, faction: str = "") -> NPC | None:
        """Create a named NPC nobody has met yet."""
        name = ""
        for _ in range(12):
            candidate = str(self.tables.pick("people.street_name", ""))
            if candidate and campaign.npc(candidate) is None:
                name = candidate
                break
        if not name:
            return None
        npc = NPC(
            name=name,
            role=str(self.tables.pick("people.role", "contact")),
            faction=faction,
            disposition=self.rng.choice([-1, 0, 0, 1]),
            wants=str(self.tables.pick("people.wants", "")),
            description=str(self.tables.pick("people.quirk", "")),
            voice=str(self.tables.pick("people.voice_note", "")),
        )
        return campaign.add_npc(npc)

    def faction_retaliation(self, campaign: Campaign) -> list[Thread]:
        """Hot factions eventually come looking. This is heat becoming plot."""
        spawned: list[Thread] = []
        seeds = self.tables.all("consequences.faction_retaliation")
        if not seeds:
            return spawned

        for faction in campaign.factions.values():
            if faction.heat < RETALIATION_HEAT:
                continue
            # One retaliation per heat tier, so a hated corp escalates rather
            # than spamming identical threads.
            tier = int(faction.heat // RETALIATION_HEAT)
            if faction.retaliated >= tier:
                continue
            seed = self.rng.choice(seeds)
            thread = Thread(
                title=f"{seed['title']} ({faction.name})",
                kind="heat",
                summary=str(seed.get("summary", "")).replace("{faction}", faction.name),
                tension=float(seed.get("tension", 1.3)) + 0.2 * tier,
                factions=[faction.name],
                clock=Clock(name=seed["title"], segments=int(seed.get("clock", 6))),
            )
            campaign.add_thread(thread)
            faction.retaliated = tier
            faction.attitude = min(faction.attitude, -1)
            spawned.append(thread)
        return spawned

    # -- the world moves on its own ----------------------------------------
    def world_tick(self, campaign: Campaign, focused: Thread | None = None) -> list[str]:
        """Advance everything the crew is not looking at."""
        events: list[str] = []

        for thread in campaign.open_threads():
            if focused is not None and thread.id == focused.id:
                continue
            # Neglected threads with clocks tick forward: the world does not
            # politely wait for the players.
            if thread.clock and thread.beats_since_touched >= 2:
                if self.rng.random() < 0.4 and thread.clock.tick():
                    events.append(f"CLOCK FULL: {thread.title} -- {thread.clock.name}")
                    thread.tension += 0.5

        for thread in self.faction_retaliation(campaign):
            events.append(f"NEW THREAD (heat): {thread.title}")

        # Top the pool back up. The loop -- rather than a single top-up -- is
        # what actually guarantees the story cannot run out.
        while len(campaign.open_threads()) < MIN_OPEN_THREADS:
            new_thread = self.new_job_thread(campaign)
            events.append(f"NEW THREAD (job): {new_thread.title}")

        return events

    # -- scheduling ---------------------------------------------------------
    def pick_thread(self, campaign: Campaign) -> Thread | None:
        """Weighted draw over open threads by pressure."""
        candidates = campaign.open_threads()
        if not candidates:
            return None
        weights = [t.pressure() for t in candidates]
        total = sum(weights)
        if total <= 0:
            return self.rng.choice(candidates)
        roll = self.rng.random() * total
        upto = 0.0
        for thread, weight in zip(candidates, weights):
            upto += weight
            if roll <= upto:
                return thread
        return candidates[-1]

    def pick_scene_type(self, campaign: Campaign, thread: Thread | None) -> str:
        recent = [s.type for s in campaign.recent_scenes(4)]
        last = recent[-1] if recent else "downtime"

        options = list(SCENE_FLOW.get(last, ("legwork", "social", "meet")))
        if thread is not None:
            options += list(THREAD_SCENE_BIAS.get(thread.kind, ()))

        # Do not repeat a type used in the last two beats unless nothing is left.
        fresh = [o for o in options if o not in recent[-2:]]
        pool = fresh or options

        # Pressure valve: a long stretch without a breather earns downtime.
        if len(recent) >= 4 and "downtime" not in recent and "fallout" not in recent:
            pool = pool + ["downtime", "fallout"]

        return self.rng.choice(pool)

    def spotlight_runner(self, campaign: Campaign, thread: Thread | None) -> str:
        """Whose scene is this? Favour whoever has been quiet longest.

        With three players this matters a lot -- an unmanaged Director will
        write for the loudest character every time.
        """
        if not campaign.runners:
            return ""
        tied = [r for r in campaign.runners if thread and r.name in thread.runners]
        pool = tied or campaign.runners
        fewest = min(r.spotlight for r in pool)
        quiet = [r for r in pool if r.spotlight == fewest]
        chosen = self.rng.choice(quiet)
        chosen.spotlight += 1
        return chosen.name

    def atmosphere(self) -> dict[str, str]:
        return {
            "weather": str(self.tables.pick("atmosphere.weather", "")),
            "sound": str(self.tables.pick("atmosphere.sound", "")),
            "smell": str(self.tables.pick("atmosphere.smell", "")),
            "detail": str(self.tables.pick("atmosphere.detail", "")),
            "crowd": str(self.tables.pick("atmosphere.crowd", "")),
        }

    def downtime_prod(self) -> str:
        return str(self.tables.pick("consequences.downtime", "the commlink rings"))


def seed_starting_campaign(
    campaign: Campaign, generator: ArcGenerator, *, jobs: int = 1, hooks: int = 2
) -> None:
    """Give a brand-new campaign enough threads to feel like it started in motion."""
    for _ in range(max(1, jobs)):
        generator.new_job_thread(campaign)

    seeds: Sequence[dict] = generator.tables.all("consequences.mixed")
    for seed in generator.rng.sample(list(seeds), k=min(hooks, len(seeds))):
        campaign.add_thread(
            Thread(
                title=seed["title"],
                kind=seed.get("kind", "mystery"),
                summary=seed.get("summary", ""),
                tension=float(seed.get("tension", 0.8)) * 0.7,
                status="cold",
            )
        )


def resolve_scene_outcome(scene: Scene, text: str) -> str:
    """Classify how a beat went, for consequence selection."""
    lowered = (text or scene.outcome or "").lower()
    good = sum(w in lowered for w in ("succeed", "clean", "escape", "secured", "paid", "win"))
    bad = sum(w in lowered for w in ("fail", "burned", "caught", "wounded", "lost", "dead"))
    if good > bad:
        return "success"
    if bad > good:
        return "failure"
    return "mixed"
