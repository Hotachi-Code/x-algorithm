"""The Director: chooses the next beat, writes it, and remembers it.

One turn of the loop:

    pick a thread   -> pressure-weighted, so nothing is forgotten forever
    pick a beat     -> scene type that flows from the last one
    retrieve        -> vault context, expanded through the wikilink graph
    narrate         -> LLM (or the offline narrator) writes the scene
    absorb          -> parse the state block, update heat/clocks/NPCs
    persist         -> campaign.json + a scene note in the vault
    tick the world  -> off-screen threads advance, consequences spawn

Because the last step always leaves at least as many open threads as it found,
the loop has no terminal state.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..retrieval.search import Retriever
from ..vaultio import Note, Vault
from .arcs import ArcGenerator, resolve_scene_outcome, seed_starting_campaign
from .llm import LLM, get_llm
from .prompts import (
    NPC_SYSTEM,
    RECAP_SYSTEM,
    SYSTEM,
    npc_prompt,
    recap_prompt,
    scene_prompt,
)
from .state import Campaign, NPC, Scene, Thread
from .tables import Tables

TAG_RE = {
    "narration": re.compile(r"<narration>(.*?)</narration>", re.DOTALL | re.IGNORECASE),
    "hooks": re.compile(r"<hooks>(.*?)</hooks>", re.DOTALL | re.IGNORECASE),
    "tests": re.compile(r"<tests>(.*?)</tests>", re.DOTALL | re.IGNORECASE),
    "state": re.compile(r"<state>(.*?)</state>", re.DOTALL | re.IGNORECASE),
}
BULLET_RE = re.compile(r"^\s*[-*•]\s*", re.MULTILINE)

# How much attention each kind of beat draws, when the narration does not say.
# Negative values are the crew deliberately going quiet.
SCENE_HEAT = {
    "combat": 0.9,
    "chase": 0.7,
    "infiltration": 0.45,
    "matrix": 0.45,
    "twist": 0.3,
    "fallout": 0.2,
    "social": 0.1,
    "meet": 0.0,
    "legwork": 0.0,
    "downtime": -0.4,
}


@dataclass
class SceneResult:
    scene: Scene
    thread: Thread | None
    events: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts = [self.scene.narration.strip()]
        if self.scene.hooks:
            parts += ["", "HOOKS", *[f"  - {h}" for h in self.scene.hooks]]
        if self.scene.tests:
            parts += ["", "TESTS", *[f"  - {t}" for t in self.scene.tests]]
        return "\n".join(parts)


@dataclass
class Director:
    config: Config
    campaign: Campaign
    vault: Vault
    tables: Tables
    arcs: ArcGenerator
    llm: LLM
    retriever: Retriever | None = None
    rng: random.Random = field(default_factory=random.Random)

    # -- construction -------------------------------------------------------
    @classmethod
    def bootstrap(cls, config: Config, *, campaign: Campaign | None = None) -> "Director":
        rng = random.Random(config.seed)
        tables = Tables.open(config.root / "tables", seed=config.seed)
        vault = Vault(config.vault)
        vault.ensure()

        campaign = campaign or Campaign.load(config.state / "campaign.json") or Campaign(
            name=config.campaign
        )

        retriever: Retriever | None = None
        index_path = config.state / "index.sqlite"
        if index_path.exists():
            try:
                retriever = Retriever.open(
                    config.vault,
                    index_path,
                    embed_backend=config.embed_backend,
                    embed_model=config.embed_model,
                    graph_hops=config.graph_hops,
                )
            except Exception as exc:  # a broken index must not block play
                print(f"  [director] retrieval unavailable ({exc}); running without vault context")

        llm = get_llm(
            config.llm_backend,
            model=config.llm_model,
            base_url=config.llm_base_url,
            temperature=config.llm_temperature,
            seed=config.seed,
        )

        return cls(
            config=config,
            campaign=campaign,
            vault=vault,
            tables=tables,
            arcs=ArcGenerator(tables=tables, rng=rng),
            llm=llm,
            retriever=retriever,
            rng=rng,
        )

    # -- lifecycle ----------------------------------------------------------
    def start_campaign(self, runners: list) -> None:
        for runner in runners:
            if self.campaign.runner(runner.name) is None:
                self.campaign.runners.append(runner)
        if not self.campaign.threads:
            seed_starting_campaign(self.campaign, self.arcs)
        self.save()

    def save(self) -> None:
        self.campaign.save(self.config.state / "campaign.json")

    # -- the main loop ------------------------------------------------------
    def next_scene(
        self,
        player_input: str = "",
        *,
        scene_type: str | None = None,
        thread_id: str = "",
        directive: str = "",
    ) -> SceneResult:
        """Generate, record, and persist the next beat of the story."""
        thread = (
            self.campaign.thread(thread_id)
            if thread_id
            else self.arcs.pick_thread(self.campaign)
        )
        if thread is None:
            thread = self.arcs.new_job_thread(self.campaign)

        beat = scene_type or self.arcs.pick_scene_type(self.campaign, thread)
        spotlight = self.arcs.spotlight_runner(self.campaign, thread)
        atmosphere = self.arcs.atmosphere()
        location = self._pick_location(thread)
        present = self._pick_present(thread)

        spec = self._build_spec(
            thread=thread,
            beat=beat,
            spotlight=spotlight,
            atmosphere=atmosphere,
            location=location,
            present=present,
            directive=directive,
        )

        context, citations = self._retrieve(thread, beat, location, present, player_input)

        prompt = scene_prompt(
            campaign_brief=self.campaign.brief(),
            vault_context=context,
            spec=spec,
            player_input=player_input,
            recent=self._recent_beats(),
        )
        raw = self.llm.complete(SYSTEM, prompt, max_tokens=self.config.llm_max_tokens)
        parsed = parse_scene(raw)

        scene = Scene(
            type=beat,
            title=self._scene_title(thread, beat),
            thread_id=thread.id,
            location=location,
            present=present,
            narration=parsed["narration"],
            hooks=parsed["hooks"],
            tests=parsed["tests"],
            player_input=player_input,
            outcome=str(parsed["state"].get("outcome", "")),
        )
        self.campaign.add_scene(scene)

        events = self._absorb(scene, thread, parsed["state"])
        self._write_scene_note(scene, thread, citations)
        self.save()

        return SceneResult(scene=scene, thread=thread, events=events, citations=citations)

    # -- spec + context -----------------------------------------------------
    def _build_spec(
        self,
        *,
        thread: Thread,
        beat: str,
        spotlight: str,
        atmosphere: dict[str, str],
        location: str,
        present: list[str],
        directive: str,
    ) -> dict[str, Any]:
        # The complication and twist stay in the spec but flagged as withheld,
        # so the model can foreshadow without spending them early.
        withhold = ""
        for note in thread.notes:
            match = re.search(r"TWIST[^:]*:\s*(.+)", note)
            if match:
                withhold = match.group(1).strip()
                break

        must_include: list[str] = []
        if beat in ("meet", "social", "legwork") and present:
            must_include.append(f"a beat with {present[0]}")
        if thread.clock and thread.clock.filled >= thread.clock.segments - 2:
            must_include.append(f"visible time pressure ({thread.clock.name})")
        if self.campaign.heat >= 6:
            must_include.append("a sign the crew is being watched")

        beats_so_far = sum(1 for s in self.campaign.scenes if s.thread_id == thread.id)

        return {
            "scene_type": beat,
            "spotlight": spotlight,
            "location": location,
            "present": present,
            "atmosphere": atmosphere,
            "directive": directive,
            "must_include": must_include,
            "withhold": withhold,
            "thread_beat": beats_so_far,
            "heat": round(self.campaign.heat, 1),
            "thread": {
                "id": thread.id,
                "title": thread.title,
                "kind": thread.kind,
                "summary": thread.summary,
                "stakes": thread.stakes,
                "npcs": thread.npcs,
                "factions": thread.factions,
                "clock": thread.clock.render() if thread.clock else "",
            },
        }

    def _retrieve(
        self,
        thread: Thread,
        beat: str,
        location: str,
        present: list[str],
        player_input: str,
    ) -> tuple[str, list[str]]:
        if self.retriever is None:
            return "", []
        query_parts = [thread.title, thread.summary, beat, location, *present]
        if player_input:
            query_parts.append(player_input[:400])
        query = " ".join(p for p in query_parts if p)
        pins = [*present, *thread.locations, *thread.factions]
        try:
            context, hits = self.retriever.context_block(
                query,
                k=self.config.retrieval_k,
                pin_notes=[p for p in pins if p],
            )
        except Exception as exc:
            print(f"  [director] retrieval failed ({exc})")
            return "", []
        return context, [h.cite() for h in hits]

    def _recent_beats(self, n: int = 3) -> str:
        out = []
        for scene in self.campaign.recent_scenes(n):
            text = re.sub(r"\s+", " ", scene.narration).strip()
            out.append(f"[{scene.number}] {scene.type}: {text[:420]}")
        return "\n".join(out)

    # -- world updates ------------------------------------------------------
    def _absorb(self, scene: Scene, thread: Thread, state: dict[str, Any]) -> list[str]:
        """Fold the narration's declared state changes back into the campaign."""
        events: list[str] = []
        thread.touch()

        for name in _as_list(state.get("npcs_introduced")):
            if not name or self.campaign.npc(name):
                continue
            npc = NPC(
                name=name,
                role=str(self.tables.pick("people.role", "contact")),
                faction=thread.factions[0] if thread.factions else "",
                wants=str(self.tables.pick("people.wants", "")),
                voice=str(self.tables.pick("people.voice_note", "")),
                last_seen=scene.headline(),
            )
            self.campaign.add_npc(npc)
            if name not in thread.npcs:
                thread.npcs.append(name)
            self._write_npc_note(npc)
            events.append(f"NPC introduced: {name}")

        for place in _as_list(state.get("locations")):
            if place and place not in thread.locations:
                thread.locations.append(place)

        # Heat comes from the narration when the model reports it, and from the
        # kind of scene it was when the model does not. Without the fallback the
        # heat -> retaliation -> new-thread loop would never fire offline.
        heat_delta = _as_float(state.get("heat_delta")) or SCENE_HEAT.get(scene.type, 0.0)
        if heat_delta > 0:
            self.campaign.raise_heat(heat_delta, thread.factions)
            events.append(f"Heat {heat_delta:+.1f} (now {self.campaign.heat:.1f})")
        else:
            self.campaign.decay_heat(
                self.config.heat_decay_per_scene + abs(min(0.0, heat_delta))
            )

        ticks = int(_as_float(state.get("clock_ticks")))
        if ticks and thread.clock is not None:
            if thread.clock.tick(ticks):
                events.append(f"CLOCK FULL: {thread.clock.name}")
                events += self._resolve_thread(thread, scene.outcome or "mixed")

        # A job whose scenes have run their course resolves on its own, which
        # is what keeps the consequence engine fed even on a quiet night.
        thread_scenes = sum(1 for s in self.campaign.scenes if s.thread_id == thread.id)
        if thread.status != "resolved" and thread_scenes >= 6 and self.rng.random() < 0.4:
            events += self._resolve_thread(thread, scene.outcome or "mixed")

        self.campaign.cool_threads(except_id=thread.id)
        events += self.arcs.world_tick(self.campaign, focused=thread)
        return events

    def _resolve_thread(self, thread: Thread, outcome: str) -> list[str]:
        outcome = resolve_scene_outcome(Scene(), outcome)
        thread.status = "resolved" if outcome != "failure" else "failed"
        thread.resolved = scene_timestamp()
        spawned = self.arcs.spawn_consequences(self.campaign, thread, outcome)
        events = [f"THREAD {thread.status.upper()}: {thread.title} ({outcome})"]
        events += [f"NEW THREAD: {t.title}" for t in spawned]
        return events

    # -- helpers ------------------------------------------------------------
    def _pick_location(self, thread: Thread) -> str:
        if thread.locations and self.rng.random() < 0.6:
            return self.rng.choice(thread.locations)
        site = re.search(r"at (.+?)\.", thread.summary)
        if site:
            return site.group(1)
        return str(self.tables.pick("jobs.target_site", "a bar in Puyallup"))

    def _pick_present(self, thread: Thread) -> list[str]:
        candidates = [n for n in thread.npcs if self.campaign.npc(n)]
        if not candidates:
            active = [n.name for n in self.campaign.npcs.values() if n.status == "active"]
            candidates = active[:1]
        return candidates[:2]

    def _scene_title(self, thread: Thread, beat: str) -> str:
        return f"{thread.title} -- {beat.title()} {self.campaign.scene_number + 1}"

    # -- vault mirroring ----------------------------------------------------
    def _write_scene_note(self, scene: Scene, thread: Thread, citations: list[str]) -> None:
        links = "\n".join(f"- {c}" for c in dict.fromkeys(citations)) or "_none_"
        present = ", ".join(f"[[{p}]]" for p in scene.present) or "_nobody_"
        hooks = "\n".join(f"- {h}" for h in scene.hooks) or "_none_"
        tests = "\n".join(f"- {t}" for t in scene.tests) or "_none_"
        body = (
            f"**Thread:** [[{thread.title}]]  \n"
            f"**Location:** {scene.location}  \n"
            f"**Present:** {present}\n\n"
            f"## Narration\n\n{scene.narration}\n\n"
            f"## Hooks\n\n{hooks}\n\n"
            f"## Tests\n\n{tests}\n\n"
            f"## Drawn from\n\n{links}\n"
        )
        note = Note(
            title=f"S{self.campaign.session:02d}-{scene.number:03d} {scene.headline()}",
            body=body,
            meta={
                "type": "scene",
                "session": self.campaign.session,
                "scene": scene.number,
                "scene_type": scene.type,
                "thread": thread.title,
                "location": scene.location,
                "tags": ["scene", f"session/{self.campaign.session}"],
            },
        )
        self.vault.write(note, "scene")
        self._write_thread_note(thread)

    def _write_thread_note(self, thread: Thread) -> None:
        clock = thread.clock.render() if thread.clock else "_no clock_"
        npcs = "\n".join(f"- [[{n}]]" for n in thread.npcs) or "_none yet_"
        factions = "\n".join(f"- [[{f}]]" for f in thread.factions) or "_none yet_"
        scenes = [s for s in self.campaign.scenes if s.thread_id == thread.id]
        log = "\n".join(
            f"- [[S{s.session:02d}-{s.number:03d} {s.headline()}]]" for s in scenes[-12:]
        ) or "_none yet_"

        note = self.vault.get(thread.title) or Note(title=thread.title, body="")
        note.meta.update(
            {
                "type": "thread",
                "thread_id": thread.id,
                "kind": thread.kind,
                "status": thread.status,
                "tension": round(thread.tension, 2),
                "tags": ["thread", f"thread/{thread.kind}"],
            }
        )
        if "## Summary" not in note.body:
            note.body = f"## Summary\n\n{thread.summary}\n"
        note.upsert_section("Status", f"**{thread.status}** -- {clock}")
        note.upsert_section("Stakes", thread.stakes or "_unstated_")
        note.upsert_section("People", npcs)
        note.upsert_section("Factions", factions)
        note.upsert_section("Scene Log", log)
        self.vault.write(note, "thread")

    def _write_npc_note(self, npc: NPC) -> None:
        body = (
            f"## Summary\n\n{npc.description or '_TODO_'}\n\n"
            f"## Wants\n\n{npc.wants or '_unknown_'}\n\n"
            f"## Voice\n\n{npc.voice or '_uncast_'}\n\n"
            f"## Notes\n\n- First seen: {npc.last_seen}\n"
        )
        self.vault.upsert(
            npc.name,
            "npc",
            body=body,
            meta={
                "role": npc.role,
                "faction": npc.faction,
                "disposition": npc.disposition,
                "status": npc.status,
                "tags": ["npc"],
            },
        )

    def sync_to_vault(self) -> int:
        """Push the whole campaign state into readable notes. Safe to re-run."""
        written = 0
        for thread in self.campaign.threads:
            self._write_thread_note(thread)
            written += 1
        for npc in self.campaign.npcs.values():
            self._write_npc_note(npc)
            written += 1
        for runner in self.campaign.runners:
            self._write_runner_note(runner)
            written += 1
        self._write_dashboard()
        return written + 1

    def _write_runner_note(self, runner) -> None:
        skills = "\n".join(f"- {k}: {v}" for k, v in sorted(runner.skills.items())) or "_none_"
        attrs = ", ".join(f"{k.upper()} {v}" for k, v in sorted(runner.attributes.items()))
        gear = "\n".join(f"- {g}" for g in runner.gear) or "_none_"
        contacts = "\n".join(f"- [[{c}]]" for c in runner.contacts) or "_none_"
        bonds = "\n".join(f"- [[{k}]]: {v}" for k, v in runner.bonds.items()) or "_none_"
        body = (
            f"**{runner.metatype} {runner.archetype}**"
            + (f" -- {runner.concept}" if runner.concept else "")
            + f"  \n**Player:** {runner.player or '_unassigned_'}\n\n"
            f"## Attributes\n\n{attrs or '_unset_'}\n\n"
            f"## Skills\n\n{skills}\n\n"
            f"## Gear\n\n{gear}\n\n"
            f"## Contacts\n\n{contacts}\n\n"
            f"## Bonds\n\n{bonds}\n"
        )
        note = self.vault.get(runner.name) or Note(title=runner.name, body=body)
        if "## Attributes" not in note.body:
            note.body = body
        note.meta.update(
            {
                "type": "runner",
                "player": runner.player,
                "archetype": runner.archetype,
                "metatype": runner.metatype,
                "karma": runner.karma,
                "nuyen": runner.nuyen,
                "tags": ["runner", "pc"],
            }
        )
        note.upsert_section(
            "Condition",
            f"- Edge {runner.edge_available}/{runner.edge}\n"
            f"- Physical damage {runner.physical_damage}\n"
            f"- Stun damage {runner.stun_damage}\n"
            f"- Wound modifier {runner.wound_modifier()}\n"
            f"- Street cred {runner.street_cred} / notoriety {runner.notoriety}",
        )
        self.vault.write(note, "runner")

    def _write_dashboard(self) -> None:
        c = self.campaign
        threads = "\n".join(
            f"- [[{t.title}]] -- {t.status}, tension {t.tension:.1f}"
            + (f" {t.clock.render()}" if t.clock else "")
            for t in sorted(c.open_threads(), key=lambda t: -t.pressure())
        ) or "_none_"
        crew = "\n".join(f"- [[{r.name}]] -- {r.summary()}" for r in c.runners) or "_none_"
        hot = "\n".join(
            f"- [[{f.name}]] -- {f.threat_level} (heat {f.heat:.1f})"
            for f in sorted(c.factions.values(), key=lambda f: -f.heat)
            if f.heat >= 1
        ) or "_quiet_"
        recent = "\n".join(
            f"- [[S{s.session:02d}-{s.number:03d} {s.headline()}]]"
            for s in c.recent_scenes(10)
        ) or "_none_"

        body = (
            f"> {c.status_line()}\n\n"
            f"## Recap\n\n{c.recap or '_not written yet_'}\n\n"
            f"## The Crew\n\n{crew}\n\n"
            f"## Open Threads\n\n{threads}\n\n"
            f"## Who Is Angry\n\n{hot}\n\n"
            f"## Recent Scenes\n\n{recent}\n"
        )
        self.vault.upsert(
            c.name,
            "campaign",
            body=body,
            meta={
                "session": c.session,
                "scene": c.scene_number,
                "heat": round(c.heat, 1),
                "tags": ["campaign", "dashboard"],
            },
            overwrite_body=True,
        )

    # -- extras -------------------------------------------------------------
    def write_recap(self) -> str:
        scenes = []
        for s in self.campaign.recent_scenes(8):
            flat = re.sub(r"\s+", " ", s.narration).strip()
            scenes.append(f"Scene {s.number} ({s.type}): {flat[:600]}")
        beats = []
        for s in self.campaign.recent_scenes(4):
            first = re.split(r"(?<=[.!?])\s", re.sub(r"\s+", " ", s.narration).strip())
            if first and first[0]:
                beats.append(first[0].rstrip("."))

        text = self.llm.complete(
            RECAP_SYSTEM,
            recap_prompt(
                self.campaign.brief(),
                scenes,
                spec={
                    "campaign": self.campaign.name,
                    "beats": beats,
                    "open_threads": [t.title for t in self.campaign.open_threads()[:4]],
                },
            ),
            max_tokens=400,
        ).strip()
        if text:
            self.campaign.recap = text
            self.save()
        return text or self.campaign.recap

    def speak_as(self, npc_name: str, question: str, speaker: str = "a runner") -> str:
        """Direct dialogue with an NPC -- the players talk, the NPC answers."""
        npc = self.campaign.npc(npc_name)
        if npc is None:
            return f"(nobody called {npc_name} is in play)"

        note = self.vault.get(npc.name)
        block = (
            f"Name: {npc.name}\nRole: {npc.role}\nFaction: {npc.faction or 'independent'}\n"
            f"Attitude to the crew: {npc.attitude()}\nWants: {npc.wants}\n"
            f"Voice: {npc.voice}\n"
        )
        if note:
            block += f"\nDossier:\n{note.excerpt(700)}"

        context = ""
        if self.retriever is not None:
            try:
                context, _ = self.retriever.context_block(
                    f"{npc.name} {npc.faction} {question}", k=6, pin_notes=[npc.name]
                )
            except Exception:
                context = ""

        return self.llm.complete(
            NPC_SYSTEM,
            npc_prompt(
                npc_block=block,
                campaign_brief=self.campaign.brief(),
                vault_context=context,
                question=question,
                speaker=speaker,
                spec={
                    "name": npc.name,
                    "role": npc.role,
                    "faction": npc.faction,
                    "attitude": npc.attitude(),
                    "wants": npc.wants,
                },
            ),
            max_tokens=300,
        ).strip()

    def new_session(self) -> str:
        self.campaign.session += 1
        recap = self.write_recap()
        self.save()
        return recap


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def parse_scene(raw: str) -> dict[str, Any]:
    """Pull the tagged sections out of a narration, tolerating sloppy models."""
    out: dict[str, Any] = {"narration": "", "hooks": [], "tests": [], "state": {}}

    narration = TAG_RE["narration"].search(raw)
    if narration:
        out["narration"] = narration.group(1).strip()
    else:
        # No tags at all: treat everything before the first list as prose.
        stripped = re.split(r"\n\s*(?:HOOKS|TESTS)\b", raw, maxsplit=1)[0]
        out["narration"] = stripped.strip()

    for key in ("hooks", "tests"):
        match = TAG_RE[key].search(raw)
        if match:
            out[key] = _bullets(match.group(1))
        else:
            loose = re.search(
                rf"^\s*{key.upper()}:?\s*$(.*?)(?=^\s*[A-Z]{{4,}}:?\s*$|\Z)",
                raw,
                re.DOTALL | re.MULTILINE,
            )
            if loose:
                out[key] = _bullets(loose.group(1))

    state = TAG_RE["state"].search(raw)
    if state:
        try:
            parsed = json.loads(state.group(1).strip())
            if isinstance(parsed, dict):
                out["state"] = parsed
        except json.JSONDecodeError:
            out["state"] = {}
    return out


def _bullets(block: str) -> list[str]:
    lines = [BULLET_RE.sub("", line).strip() for line in block.strip().splitlines()]
    return [line for line in lines if line and not line.startswith("_")]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def scene_timestamp() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S")
