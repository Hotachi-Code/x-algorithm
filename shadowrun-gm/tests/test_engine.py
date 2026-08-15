import random
from pathlib import Path

from shadowrun_gm.config import Config
from shadowrun_gm.engine.arcs import ArcGenerator, seed_starting_campaign
from shadowrun_gm.engine.director import Director, parse_scene
from shadowrun_gm.engine.state import Campaign, Clock, Thread
from shadowrun_gm.engine.tables import Tables
from shadowrun_gm.table.players import pregen_runners


def _config(tmp_path: Path) -> Config:
    return Config.load(
        tmp_path,
        llm_backend="local",
        embed_backend="hashing",
        tts_backend="null",
        speak_by_default=False,
        seed=11,
    )


def test_clock_reports_completion_once():
    clock = Clock("Deadline", segments=2)
    assert clock.tick() is False
    assert clock.tick() is True
    assert clock.tick() is False  # already full
    assert clock.is_full


def test_thread_pressure_rises_when_ignored():
    thread = Thread(title="Audit", tension=1.0)
    baseline = thread.pressure()
    for _ in range(5):
        thread.cool()
    assert thread.pressure() > baseline


def test_resolved_threads_have_no_pressure():
    thread = Thread(title="Done", status="resolved")
    assert thread.pressure() == 0.0


def test_campaign_roundtrips_through_json(tmp_path: Path):
    campaign = Campaign(name="Test Run")
    campaign.runners = pregen_runners(["A", "B", "C"])
    campaign.add_thread(Thread(title="A Job", clock=Clock("Deadline", 6, 2)))
    campaign.raise_heat(3.0, ["Renraku Computer Systems"])
    path = campaign.save(tmp_path / "campaign.json")

    loaded = Campaign.load(path)
    assert loaded is not None
    assert loaded.name == "Test Run"
    assert len(loaded.runners) == 3
    assert loaded.threads[0].clock.filled == 2
    assert loaded.factions["Renraku Computer Systems"].heat == 3.0


def test_every_resolution_spawns_at_least_one_thread():
    tables = Tables.open(seed=3)
    arcs = ArcGenerator(tables=tables, rng=random.Random(3))
    campaign = Campaign()
    thread = arcs.new_job_thread(campaign)

    for outcome in ("success", "failure", "mixed"):
        before = len(campaign.threads)
        spawned = arcs.spawn_consequences(campaign, thread, outcome)
        assert len(spawned) >= 1
        assert len(campaign.threads) > before


def test_hot_factions_generate_retaliation():
    tables = Tables.open(seed=5)
    arcs = ArcGenerator(tables=tables, rng=random.Random(5))
    campaign = Campaign()
    campaign.raise_heat(9.0, ["Ares Macrotechnology"])

    spawned = arcs.faction_retaliation(campaign)
    assert spawned, "a faction at heat 9 should come looking"
    assert any("Ares Macrotechnology" in t.factions for t in spawned)

    # Idempotent within a heat tier: no duplicate threads on the next tick.
    assert arcs.faction_retaliation(campaign) == []


def test_world_tick_keeps_the_thread_pool_stocked():
    tables = Tables.open(seed=9)
    arcs = ArcGenerator(tables=tables, rng=random.Random(9))
    campaign = Campaign()
    arcs.world_tick(campaign)
    assert len(campaign.open_threads()) >= 3


def test_tables_do_not_repeat_until_exhausted():
    tables = Tables.open(seed=2)
    size = len(tables.all("jobs.objective"))
    drawn = [tables.pick("jobs.objective") for _ in range(size)]
    assert len(set(drawn)) == size


def test_spotlight_rotates_between_three_runners():
    tables = Tables.open(seed=4)
    arcs = ArcGenerator(tables=tables, rng=random.Random(4))
    campaign = Campaign()
    campaign.runners = pregen_runners()

    picks = [arcs.spotlight_runner(campaign, None) for _ in range(9)]
    counts = {r.name: picks.count(r.name) for r in campaign.runners}
    assert set(counts.values()) == {3}, counts


def test_parse_scene_reads_tagged_output():
    raw = """
<narration>
Rain. The meet is late.
</narration>
<hooks>
- Ask about the second buyer
- Walk away
</hooks>
<tests>
- Negotiation + Charisma (3)
</tests>
<state>
{"npcs_introduced": ["Kestrel"], "heat_delta": 1.5, "clock_ticks": 1, "outcome": "mixed"}
</state>
"""
    parsed = parse_scene(raw)
    assert parsed["narration"] == "Rain. The meet is late."
    assert len(parsed["hooks"]) == 2
    assert parsed["tests"] == ["Negotiation + Charisma (3)"]
    assert parsed["state"]["npcs_introduced"] == ["Kestrel"]
    assert parsed["state"]["heat_delta"] == 1.5


def test_parse_scene_tolerates_untagged_output():
    parsed = parse_scene("Just prose, no tags at all.")
    assert parsed["narration"] == "Just prose, no tags at all."
    assert parsed["hooks"] == []
    assert parsed["state"] == {}


def test_director_generates_scenes_and_never_runs_out(tmp_path: Path):
    config = _config(tmp_path)
    director = Director.bootstrap(config)
    director.start_campaign(pregen_runners(["A", "B", "C"]))

    for _ in range(12):
        result = director.next_scene()
        assert result.scene.narration.strip()
        assert result.thread is not None

    assert director.campaign.scene_number == 12
    assert len(director.campaign.open_threads()) >= 3, "the story must not dry up"

    # Every scene got a note, and the campaign persisted.
    scene_notes = list((config.vault / "20-Campaign" / "Scenes").glob("*.md"))
    assert len(scene_notes) == 12
    assert (config.state / "campaign.json").is_file()

    reloaded = Campaign.load(config.state / "campaign.json")
    assert reloaded.scene_number == 12


def test_director_reacts_to_player_input(tmp_path: Path):
    config = _config(tmp_path)
    director = Director.bootstrap(config)
    director.start_campaign(pregen_runners())
    result = director.next_scene(player_input="Static hacks the door and we walk in.")
    assert result.scene.player_input.startswith("Static hacks")


def test_sync_writes_the_dashboard(tmp_path: Path):
    config = _config(tmp_path)
    director = Director.bootstrap(config)
    director.start_campaign(pregen_runners(["A", "B", "C"]))
    director.next_scene()
    director.sync_to_vault()

    from shadowrun_gm.vaultio import Vault

    vault = Vault(config.vault)
    dashboard = vault.get(director.campaign.name)
    assert dashboard is not None
    assert "Open Threads" in dashboard.body
    for runner in director.campaign.runners:
        assert vault.get(runner.name) is not None


def test_seed_starting_campaign_opens_multiple_threads():
    tables = Tables.open(seed=1)
    arcs = ArcGenerator(tables=tables, rng=random.Random(1))
    campaign = Campaign()
    seed_starting_campaign(campaign, arcs, jobs=1, hooks=2)
    assert len(campaign.threads) == 3
    assert any(t.kind == "job" for t in campaign.threads)


def test_heat_is_capped_and_leaks():
    from shadowrun_gm.engine.state import HEAT_CAP

    campaign = Campaign()
    for _ in range(60):
        campaign.raise_heat(2.0, ["Ares Macrotechnology"])
    assert campaign.heat <= HEAT_CAP
    assert campaign.factions["Ares Macrotechnology"].heat <= HEAT_CAP

    peak = campaign.heat
    for _ in range(30):
        campaign.decay_heat(0.15)
    assert campaign.heat < peak / 2


def test_consequences_populate_the_world():
    tables = Tables.open(seed=12)
    arcs = ArcGenerator(tables=tables, rng=random.Random(12))
    campaign = Campaign()
    thread = arcs.new_job_thread(campaign)

    for _ in range(20):
        arcs.spawn_consequences(campaign, thread, "mixed")

    assert len(campaign.npcs) > 1, "consequences should introduce new faces"
    assert len(campaign.factions) > 1, "third parties should take an interest"


def test_corp_pool_uses_canonical_names_only():
    tables = Tables.open(seed=1)
    arcs = ArcGenerator(tables=tables, rng=random.Random(1))
    pool = arcs._corp_pool()
    assert "Renraku Computer Systems" in pool
    assert "Renraku" not in pool  # the alias must not become its own faction


def test_scene_heat_fallback_drives_retaliation(tmp_path: Path):
    config = _config(tmp_path)
    director = Director.bootstrap(config)
    director.start_campaign(pregen_runners())

    for _ in range(40):
        director.next_scene()

    assert director.campaign.heat > 0, "loud scenes must generate attention"
    assert len(director.campaign.npcs) > 1
    assert len(director.campaign.factions) >= 1


def test_local_narrator_capitalises_prose(tmp_path: Path):
    config = _config(tmp_path)
    director = Director.bootstrap(config)
    director.start_campaign(pregen_runners())
    for _ in range(6):
        scene = director.next_scene().scene
        for paragraph in scene.narration.split("\n\n"):
            text = paragraph.strip().lstrip("*").lstrip()
            if text and text[0].isalpha():
                assert text[0].isupper(), f"paragraph starts lowercase: {text[:60]!r}"


def test_offline_narrator_speaks_in_character(tmp_path: Path):
    config = _config(tmp_path)
    director = Director.bootstrap(config)
    director.start_campaign(pregen_runners())
    director.next_scene()

    name = next(iter(director.campaign.npcs))
    reply = director.speak_as(name, "What do you actually want?")
    assert reply
    assert "no scene spec" not in reply
    assert "What do you do?" not in reply


def test_speak_as_unknown_npc_is_handled(tmp_path: Path):
    config = _config(tmp_path)
    director = Director.bootstrap(config)
    director.start_campaign(pregen_runners())
    assert "nobody called" in director.speak_as("Nobody At All", "hello")


def test_offline_recap_summarises_actual_scenes(tmp_path: Path):
    config = _config(tmp_path)
    director = Director.bootstrap(config)
    director.start_campaign(pregen_runners())
    for _ in range(3):
        director.next_scene()

    recap = director.write_recap()
    assert director.campaign.name in recap
    assert "no scene spec" not in recap
    assert director.campaign.recap == recap
