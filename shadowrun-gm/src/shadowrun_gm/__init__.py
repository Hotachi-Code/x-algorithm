"""Shadowrun GM: a never-ending campaign engine for a three-runner table.

The package is deliberately layered so each piece is usable on its own:

    ingest/     turn books you own into clean markdown notes
    vaultio/    read and write an Obsidian vault (frontmatter + wikilinks)
    retrieval/  hybrid lexical + vector + graph search over the vault
    rules/      Shadowrun dice, tests, initiative
    engine/     campaign state, procedural arcs, the Director that writes scenes
    voice/      local text-to-speech and voice conversion for the storyteller
    table/      the interactive loop three players actually sit at
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
