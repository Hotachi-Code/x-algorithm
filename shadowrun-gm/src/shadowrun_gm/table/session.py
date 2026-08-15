"""The play loop.

Everything three players need in one prompt: advance the story, talk to NPCs,
roll dice, check state. Anything that is not a slash command is treated as a
description of what the crew just did, which the Director reacts to.
"""

from __future__ import annotations

import random
import shutil
import textwrap
from dataclasses import dataclass, field

from ..config import Config
from ..engine.director import Director, SceneResult
from ..engine.state import Campaign
from ..rules import Combatant, DicePool, InitiativeTracker, extended_test, opposed_test, roll
from ..voice.narrator import Narrator

BANNER = r"""
   ___ _  _   _   ___   _____      _____ _   _ _  _
  / __| || | /_\ |   \ / _ \ \    / / _ \ | | | \| |
  \__ \ __ |/ _ \| |) | (_) \ \/\/ /   / |_| | .` |
  |___/_||_/_/ \_\___/ \___/ \_/\_/ _|_\\___/|_|\_|
        never-ending campaign engine -- three runners, one city
"""

HELP = """
  <anything>            describe what the crew does; the Director reacts
  /next [type]          next scene (meet legwork infiltration matrix social
                        combat chase downtime twist fallout)
  /say <npc> <line>     talk to an NPC in their own voice
  /roll <pool> [thr]    roll a dice pool, optional threshold   (/roll 12 3)
  /roll <a> vs <b>      opposed test                           (/roll 9 vs 7)
  /roll <pool> x <thr>  extended test                          (/roll 10 x 8)
  /edge <pool> [thr]    roll with Edge (exploding 6s, no glitch)
  /init                 roll initiative for the crew
  /threads              list open threads and their pressure
  /thread <name>        focus the next scene on a thread
  /npcs                 who is in play
  /crew                 the three runners and their condition
  /state                campaign status
  /recap                write and read the "previously on"
  /session              end this session, start the next
  /voice on|off|status  storyteller voice controls
  /replay               speak the last scene again
  /sync                 rewrite the Obsidian vault from campaign state
  /save                 force a save
  /help                 this list
  /quit                 save and exit
"""


def _wrap(text: str, indent: str = "  ") -> str:
    width = min(shutil.get_terminal_size((100, 24)).columns - 4, 96)
    out = []
    for block in text.split("\n"):
        if not block.strip():
            out.append("")
            continue
        out.append(textwrap.fill(block, width=width, initial_indent=indent,
                                 subsequent_indent=indent))
    return "\n".join(out)


@dataclass
class Session:
    config: Config
    director: Director
    narrator: Narrator | None = None
    speak: bool = True
    rng: random.Random = field(default_factory=random.Random)
    running: bool = field(default=False, init=False)
    _last_scene: SceneResult | None = field(default=None, init=False)

    @classmethod
    def build(cls, config: Config, *, campaign: Campaign | None = None) -> "Session":
        director = Director.bootstrap(config, campaign=campaign)
        narrator = Narrator.build(
            tts_backend=config.tts_backend,
            piper_model_dir=config.path("piper_model_dir"),
            tts_voice=config.tts_voice,
            cast_path=config.root / "voices" / "cast.json",
            out_dir=config.audio,
            rvc_enabled=config.rvc_enabled,
            rvc_cli=config.rvc_cli,
            rvc_model_dir=config.path("rvc_model_dir"),
            enabled=config.speak_by_default,
        )
        return cls(
            config=config,
            director=director,
            narrator=narrator,
            speak=config.speak_by_default,
            rng=random.Random(config.seed),
        )

    # -- loop ---------------------------------------------------------------
    def run(self) -> None:
        self.running = True
        print(BANNER)
        print(f"  {self.director.campaign.status_line()}")
        print(f"  narrator: {self.director.llm.name}", end="")
        if self.narrator:
            print(f"  |  voice: {self.narrator.tts.name}"
                  f"{' (muted)' if not self.speak else ''}")
        else:
            print()
        print("  /help for commands\n")

        if self.director.campaign.recap:
            print("  PREVIOUSLY\n")
            print(_wrap(self.director.campaign.recap))
            print()

        while self.running:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                self.quit()
                break
            if not line:
                continue
            try:
                self.handle(line)
            except Exception as exc:  # never lose a session to one bad command
                print(f"  ! {type(exc).__name__}: {exc}")

    def handle(self, line: str) -> None:
        if not line.startswith("/"):
            self.advance(player_input=line)
            return

        command, _, rest = line[1:].partition(" ")
        rest = rest.strip()
        handler = {
            "next": lambda: self.advance(scene_type=rest or None),
            "n": lambda: self.advance(scene_type=rest or None),
            "say": lambda: self.say(rest),
            "roll": lambda: self.roll(rest),
            "edge": lambda: self.roll(rest, edge=True),
            "init": lambda: self.initiative(),
            "threads": lambda: self.show_threads(),
            "thread": lambda: self.focus_thread(rest),
            "npcs": lambda: self.show_npcs(),
            "crew": lambda: self.show_crew(),
            "state": lambda: self.show_state(),
            "recap": lambda: self.recap(),
            "session": lambda: self.new_session(),
            "voice": lambda: self.voice(rest),
            "replay": lambda: self.replay(),
            "sync": lambda: self.sync(),
            "save": lambda: self.save(),
            "help": lambda: print(HELP),
            "h": lambda: print(HELP),
            "quit": lambda: self.quit(),
            "q": lambda: self.quit(),
            "exit": lambda: self.quit(),
        }.get(command.lower())

        if handler is None:
            print(f"  ? unknown command /{command} -- /help for the list")
            return
        handler()

    # -- story --------------------------------------------------------------
    def advance(
        self,
        player_input: str = "",
        scene_type: str | None = None,
        thread_id: str = "",
    ) -> None:
        print("\n  ...the city turns...\n")
        result = self.director.next_scene(
            player_input=player_input, scene_type=scene_type, thread_id=thread_id
        )
        self._last_scene = result
        self._present(result)

    def _present(self, result: SceneResult) -> None:
        scene = result.scene
        header = f"  [{scene.number}] {scene.type.upper()}"
        if scene.location:
            header += f" -- {scene.location}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        print()
        print(_wrap(scene.narration))

        if scene.hooks:
            print("\n  HOOKS")
            for hook in scene.hooks:
                print(_wrap(f"- {hook}", indent="    "))
        if scene.tests:
            print("\n  TESTS")
            for test in scene.tests:
                print(_wrap(f"- {test}", indent="    "))
        if result.events:
            print("\n  BEHIND THE SCREEN")
            for event in result.events:
                print(f"    * {event}")
        print()

        if self.speak and self.narrator is not None:
            self.narrator.speak_passage(scene.narration)

    def say(self, rest: str) -> None:
        name, _, question = rest.partition(" ")
        if not name or not question.strip():
            print("  usage: /say <npc name> <what you say>")
            return
        reply = self.director.speak_as(name, question.strip())
        print(f"\n  {name.upper()}\n")
        print(_wrap(reply))
        print()
        if self.speak and self.narrator is not None:
            self.narrator.speak(reply, speaker=name)

    def replay(self) -> None:
        if self._last_scene is None:
            print("  nothing to replay yet")
            return
        if self.narrator is None:
            print("  no narrator configured")
            return
        self.narrator.speak_passage(self._last_scene.scene.narration)

    # -- dice ---------------------------------------------------------------
    def roll(self, rest: str, *, edge: bool = False) -> None:
        if not rest:
            print("  usage: /roll <pool> [threshold] | /roll <a> vs <b> | /roll <pool> x <thr>")
            return

        lowered = rest.lower()
        try:
            if " vs " in lowered:
                left, right = lowered.split(" vs ", 1)
                result = opposed_test(int(left.strip()), int(right.strip()), rng=self.rng)
                print(f"  {result.describe()}")
                return
            if " x " in lowered:
                pool, threshold = lowered.split(" x ", 1)
                result = extended_test(
                    int(pool.strip()), int(threshold.strip()), rng=self.rng
                )
                print(f"  {result.describe()}")
                for index, single in enumerate(result.rolls, start=1):
                    print(f"    interval {index}: {single.hits} hits {single.dice}")
                return

            parts = rest.split()
            pool_size = int(parts[0])
            threshold = int(parts[1]) if len(parts) > 1 else 0
            limit = int(parts[2]) if len(parts) > 2 else None
        except ValueError:
            print("  ! pools and thresholds must be whole numbers")
            return

        result = roll(DicePool(pool_size, limit), edge=edge, rng=self.rng)
        print(f"  {result.dice}")
        line = f"  {result.describe()}"
        if threshold:
            line += f" -- {'SUCCESS' if result.hits >= threshold else 'FAILURE'}"
            line += f" (threshold {threshold}, net {result.hits - threshold})"
        print(line)

    def initiative(self) -> None:
        tracker = InitiativeTracker()
        for runner in self.director.campaign.runners:
            base = runner.attributes.get("reaction", 4) + runner.attributes.get(
                "intuition", 4
            )
            dice = 2 if "wired reflexes 1" in " ".join(runner.gear).lower() else 1
            tracker.add(
                Combatant(name=runner.name, base=base, initiative_dice=dice, is_pc=True)
            )
        if not tracker.combatants:
            print("  no runners in the campaign yet")
            return
        tracker.new_turn(self.rng)
        print(tracker.summary())

    # -- inspection ---------------------------------------------------------
    def show_threads(self) -> None:
        threads = sorted(
            self.director.campaign.open_threads(), key=lambda t: -t.pressure()
        )
        if not threads:
            print("  no open threads -- /next will generate one")
            return
        print("\n  OPEN THREADS")
        for thread in threads:
            clock = f"  {thread.clock.render()}" if thread.clock else ""
            print(
                f"    [{thread.pressure():4.1f}] {thread.title} "
                f"({thread.kind}/{thread.status}){clock}"
            )
            if thread.summary:
                print(_wrap(thread.summary, indent="           "))
        print()

    def focus_thread(self, name: str) -> None:
        if not name:
            self.show_threads()
            return
        thread = self.director.campaign.thread(name)
        if thread is None:
            print(f"  no thread matching {name!r}")
            return
        self.advance(thread_id=thread.id)

    def show_npcs(self) -> None:
        npcs = list(self.director.campaign.npcs.values())
        if not npcs:
            print("  nobody in play yet")
            return
        print("\n  NPCS")
        for npc in npcs:
            faction = f", {npc.faction}" if npc.faction else ""
            print(f"    {npc.name} ({npc.role}{faction}) -- {npc.attitude()}, {npc.status}")
            if npc.wants:
                print(f"        wants: {npc.wants}")
        print()

    def show_crew(self) -> None:
        print("\n  THE CREW")
        for runner in self.director.campaign.runners:
            print(f"    {runner.summary()}")
            if runner.wound_modifier():
                print(f"        wound modifier: {runner.wound_modifier()}")
        print()

    def show_state(self) -> None:
        print()
        print(_wrap(self.director.campaign.brief(), indent="  "))
        print()

    def recap(self) -> None:
        text = self.director.write_recap()
        print("\n  PREVIOUSLY\n")
        print(_wrap(text))
        print()
        if self.speak and self.narrator is not None:
            self.narrator.speak_passage(text)

    def new_session(self) -> None:
        recap = self.director.new_session()
        print(f"\n  --- session {self.director.campaign.session} ---\n")
        print(_wrap(recap))
        print()

    # -- utility ------------------------------------------------------------
    def voice(self, rest: str) -> None:
        command = rest.strip().lower()
        if self.narrator is None:
            print("  no narrator configured")
            return
        if command in ("on", "unmute"):
            self.speak = True
            self.narrator.enabled = True
            print("  voice on")
        elif command in ("off", "mute"):
            self.speak = False
            print("  voice off")
        else:
            print(_wrap(self.narrator.status(), indent="  "))

    def sync(self) -> None:
        count = self.director.sync_to_vault()
        print(f"  wrote {count} notes to {self.config.vault}")

    def save(self) -> None:
        self.director.save()
        print(f"  saved to {self.config.state / 'campaign.json'}")

    def quit(self) -> None:
        self.director.save()
        self.director.sync_to_vault()
        print(f"\n  saved. {self.director.campaign.status_line()}")
        print("  see you next run, chummer.\n")
        self.running = False
