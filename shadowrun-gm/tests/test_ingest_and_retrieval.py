from pathlib import Path

from shadowrun_gm.ingest.chunker import chunk_text
from shadowrun_gm.ingest.extract import extract_file, html_to_text, split_into_sections
from shadowrun_gm.ingest.linker import EntityLinker
from shadowrun_gm.ingest.pipeline import ingest_corpus
from shadowrun_gm.retrieval.embed import HashingEmbedder, cosine
from shadowrun_gm.retrieval.search import Retriever
from shadowrun_gm.retrieval.store import Chunk, ChunkStore
from shadowrun_gm.vaultio import Vault

SAMPLE = """# Seattle

The Seattle Metroplex is governed under the terms of the Treaty of Denver.
Renraku Computer Systems maintains extraterritorial holdings downtown.

## The Barrens

Redmond Barrens has no functioning municipal services. The Halloweeners claim
several blocks. Knight Errant patrols the perimeter and nothing else.
"""


def test_split_into_sections_uses_markdown_headings():
    sections = split_into_sections(SAMPLE)
    headings = [s.heading for s in sections]
    assert "Seattle" in headings
    assert "The Barrens" in headings


def test_split_detects_all_caps_headings():
    text = "CHAPTER ONE\n\nSome body text that is long enough to keep.\n"
    sections = split_into_sections(text)
    assert sections[0].heading.startswith("CHAPTER ONE")


def test_html_to_text_strips_markup():
    text = html_to_text("<h2>Denver</h2><p>The <b>Front Range</b> zone.</p>")
    assert "Denver" in text
    assert "<" not in text


def test_chunker_respects_target_size():
    body = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12))
    chunks = chunk_text(body, target=600, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) < 1400 for c in chunks)
    assert "".join(chunks)


def test_chunker_returns_nothing_for_empty_input():
    assert chunk_text("   ") == []


def test_linker_wraps_known_entities_once():
    linker = EntityLinker()
    linked, found = linker.link(SAMPLE)
    assert "[[Renraku Computer Systems]]" in linked
    assert "Renraku Computer Systems" in found
    assert linked.count("[[Renraku Computer Systems]]") == 1


def test_linker_does_not_double_link_existing_wikilinks():
    linker = EntityLinker()
    linked, _ = linker.link("Watch out for [[Knight Errant]] and Knight Errant.")
    assert "[[[[" not in linked


def test_linker_proposes_unknown_repeated_names():
    linker = EntityLinker()
    text = "Kestrel Vance runs the docks. Kestrel Vance never sleeps. Kestrel Vance knows."
    proposals = dict(linker.propose_entities(text, min_count=3))
    assert any("Kestrel" in name for name in proposals)


def test_extract_file_reads_markdown(tmp_path: Path):
    path = tmp_path / "seattle.md"
    path.write_text(SAMPLE, encoding="utf-8")
    doc = extract_file(path)
    assert doc is not None
    assert doc.word_count() > 20


def test_extract_file_ignores_unsupported_suffixes(tmp_path: Path):
    path = tmp_path / "cover.png"
    path.write_bytes(b"\x89PNG")
    assert extract_file(path) is None


def test_hashing_embedder_is_normalised_and_discriminating():
    embedder = HashingEmbedder(dim=256)
    a, b, c = embedder.encode(
        [
            "Renraku maintains extraterritorial holdings downtown",
            "Renraku holds extraterritorial property in the downtown core",
            "the hellhound bit the rigger's drone in half",
        ]
    )
    assert cosine(a, a) > 0.99
    assert cosine(a, b) > cosine(a, c)


def test_chunk_store_bm25_ranks_the_right_chunk(tmp_path: Path):
    store = ChunkStore(tmp_path / "index.sqlite")
    store.add_many(
        [
            Chunk(text="The Halloweeners burn buildings in Redmond.", note="Gangs"),
            Chunk(text="Renraku runs an arcology in downtown Seattle.", note="Corps"),
            Chunk(text="Hellhounds are awakened critters with fire breath.", note="Critters"),
        ]
    )
    ranked = store.bm25("arcology downtown Renraku")
    top_id = ranked[0][0]
    assert store.get(top_id).note == "Corps"
    store.close()


def test_full_ingest_then_retrieval(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "seattle.md").write_text(SAMPLE, encoding="utf-8")
    (corpus / "denver.md").write_text(
        "# Denver\n\nThe Front Range Free Zone is split between the "
        "Pueblo Corporate Council and Aztlan. Smuggling across sector lines "
        "is the local economy.\n",
        encoding="utf-8",
    )

    vault_dir = tmp_path / "vault"
    index = tmp_path / "index.sqlite"
    report = ingest_corpus(
        corpus, vault_dir, index, embed_backend="hashing", create_stubs=True
    )

    assert report.files_ingested == 2
    assert report.chunks_indexed > 0
    assert report.stubs_created > 0

    vault = Vault(vault_dir)
    assert vault.get("seattle") is not None
    # Entity stubs were created for names the corpus mentions.
    assert vault.get("Renraku Computer Systems") is not None

    retriever = Retriever.open(vault_dir, index, embed_backend="hashing")
    hits = retriever.search("who controls the Front Range Free Zone", k=5)
    assert hits
    assert any("denver" in h.chunk.note.lower() for h in hits)


def test_graph_expansion_surfaces_linked_notes(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault = Vault(vault_dir)
    vault.ensure()
    vault.upsert("Hollow", "npc", body="A fixer who works out of the [[Crime Mall]].")
    vault.upsert("Crime Mall", "location", body="A converted shopping centre in Puyallup.")

    from shadowrun_gm.ingest.pipeline import reindex_vault

    index = tmp_path / "index.sqlite"
    reindex_vault(vault_dir, index, embed_backend="hashing")

    retriever = Retriever.open(vault_dir, index, embed_backend="hashing", graph_hops=1)
    hits = retriever.search("Hollow", k=8)
    notes = {h.chunk.note for h in hits}
    # Crime Mall never says "Hollow" -- it is reachable only through the graph.
    assert "Crime Mall" in notes


def test_aliases_resolve_to_one_canonical_note():
    linker = EntityLinker()
    linked, found = linker.link("Renraku denies it. Renraku Computer Systems declines to comment.")
    # Both surfaces point at the same note; the alias keeps its own display text.
    assert "[[Renraku Computer Systems|Renraku]]" in linked
    assert found == ["Renraku Computer Systems"]


def test_lowercase_vocabulary_is_not_linked():
    from shadowrun_gm.ingest.linker import is_linkable

    linker = EntityLinker()
    linked, _ = linker.link("The fixer is a dwarf who works the grid.")
    assert "[[" not in linked
    assert is_linkable("fixer") is False
    assert is_linkable("the Matrix") is True


def test_placeholder_sections_are_not_indexed(tmp_path: Path):
    from shadowrun_gm.ingest.pipeline import _worth_indexing

    assert _worth_indexing("_none yet_") is False
    assert _worth_indexing("_quiet_") is False
    assert _worth_indexing("   ") is False
    assert _worth_indexing("A converted shopping centre in Puyallup.") is True


def test_corpus_readme_is_not_ingested(tmp_path: Path):
    from shadowrun_gm.ingest.extract import iter_corpus

    corpus = tmp_path / "corpus"
    (corpus / "sub").mkdir(parents=True)
    (corpus / "README.md").write_text("instructions", encoding="utf-8")
    (corpus / "book.md").write_text("content", encoding="utf-8")
    (corpus / "sub" / "README.md").write_text("a real note", encoding="utf-8")

    names = {p.name for p in iter_corpus(corpus)}
    assert "book.md" in names
    assert (corpus / "README.md") not in set(iter_corpus(corpus))
    # Only the top-level scaffold README is skipped; nested ones are content.
    assert len([p for p in iter_corpus(corpus) if p.name == "README.md"]) == 1


def test_notes_are_findable_by_their_own_title(tmp_path: Path):
    from shadowrun_gm.ingest.pipeline import reindex_vault

    vault_dir = tmp_path / "vault"
    vault = Vault(vault_dir)
    vault.ensure()
    vault.upsert(
        "Bracket",
        "npc",
        body="A dwarf who sells information to anyone who buys three rounds.",
    )
    index = tmp_path / "index.sqlite"
    reindex_vault(vault_dir, index, embed_backend="hashing")

    retriever = Retriever.open(vault_dir, index, embed_backend="hashing")
    hits = retriever.search("Bracket", k=3)
    assert hits and hits[0].chunk.note == "Bracket"
