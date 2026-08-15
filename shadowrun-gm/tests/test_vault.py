from pathlib import Path

from shadowrun_gm.vaultio import Note, Vault, VaultGraph
from shadowrun_gm.vaultio.note import dump_frontmatter, parse_frontmatter, slugify


def test_slugify_handles_punctuation_and_case():
    assert slugify("Renraku Computer Systems") == "renraku-computer-systems"
    assert slugify("Mr. Johnson's Meet!") == "mr-johnsons-meet"
    assert slugify("") == "untitled"


def test_frontmatter_roundtrip():
    meta = {"type": "npc", "disposition": -2, "active": True, "tags": ["npc", "fixer"]}
    text = f"---\n{dump_frontmatter(meta)}\n---\n\nBody text.\n"
    parsed, body = parse_frontmatter(text)
    assert parsed["type"] == "npc"
    assert parsed["disposition"] == -2
    assert parsed["active"] is True
    assert parsed["tags"] == ["npc", "fixer"]
    assert body.strip() == "Body text."


def test_note_extracts_links_and_tags():
    note = Note(
        title="The Meet",
        body="Hollow waits at [[Crime Mall]] with [[Mox|the fixer]]. #legwork",
        meta={"tags": ["scene"]},
    )
    assert note.links == ["Crime Mall", "Mox"]
    assert "legwork" in note.tags
    assert "scene" in note.tags


def test_upsert_section_replaces_in_place(tmp_path: Path):
    note = Note(title="Thread", body="## Summary\n\nA job.\n\n## Status\n\nopen\n")
    note.upsert_section("Status", "resolved")
    assert "resolved" in note.body
    assert "open" not in note.body
    assert note.body.count("## Status") == 1
    assert "A job." in note.body


def test_upsert_section_appends_when_missing():
    note = Note(title="Thread", body="## Summary\n\nA job.\n")
    note.upsert_section("Stakes", "12,000 nuyen")
    assert "## Stakes" in note.body
    assert "A job." in note.body


def test_vault_write_and_resolve(tmp_path: Path):
    vault = Vault(tmp_path)
    vault.ensure()
    vault.upsert("Hollow", "npc", body="A fixer.", meta={"role": "fixer"})
    found = vault.get("hollow")
    assert found is not None
    assert found.meta["role"] == "fixer"
    assert found.kind == "npc"


def test_vault_upsert_preserves_hand_edits(tmp_path: Path):
    vault = Vault(tmp_path)
    vault.ensure()
    vault.upsert("Hollow", "npc", body="Original.")
    note = vault.get("Hollow")
    note.body += "\n\nGM added this by hand.\n"
    note.write()

    vault.upsert("Hollow", "npc", body="Regenerated.", meta={"disposition": 2})
    after = vault.get("Hollow")
    assert "GM added this by hand." in after.body
    assert after.meta["disposition"] == 2


def test_graph_backlinks_and_neighbours(tmp_path: Path):
    vault = Vault(tmp_path)
    vault.ensure()
    vault.upsert("Hollow", "npc", body="Works with [[Mox]].")
    vault.upsert("Mox", "npc", body="Runs the [[Crime Mall]].")
    vault.upsert("Crime Mall", "location", body="A market.")

    graph = VaultGraph.build(vault)
    assert [n.title for n in graph.backlinks("Mox")] == ["Hollow"]
    assert {n.title for n in graph.neighbours("Hollow", hops=2)} >= {"Mox", "Crime Mall"}
    assert graph.degree("Mox") == 2


def test_graph_reports_broken_links(tmp_path: Path):
    vault = Vault(tmp_path)
    vault.ensure()
    vault.upsert("Hollow", "npc", body="Afraid of [[The Auditor]].")
    graph = VaultGraph.build(vault)
    broken = dict(graph.broken_links())
    assert "the-auditor" in broken
