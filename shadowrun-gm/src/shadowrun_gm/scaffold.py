"""Project scaffolding: the files `srgm init` lays down.

Includes a working `.obsidian` config so the vault opens with the graph view,
Dataview-friendly frontmatter, and a sensible folder order the first time it is
opened -- rather than looking like a pile of markdown.
"""

from __future__ import annotations

import json
from pathlib import Path

from .vaultio.vault import FOLDERS, Vault

SRGM_TOML = """# shadowrun-gm configuration
# Everything here can be overridden with SRGM_* environment variables.

campaign = "{campaign}"
edition  = "5e"
players  = 3

# --- narration ---------------------------------------------------------------
# local     : offline template narrator, no dependencies (default)
# ollama    : any model you have pulled locally -- recommended
# anthropic : the Claude API, needs ANTHROPIC_API_KEY
llm_backend  = "local"
llm_model    = "llama3.1:8b"
llm_base_url = "http://localhost:11434"
llm_temperature = 0.85

# --- retrieval ---------------------------------------------------------------
# auto tries sentence-transformers, then falls back to built-in hashing vectors
embed_backend = "auto"
retrieval_k   = 12
graph_hops    = 1

# --- voice -------------------------------------------------------------------
# auto picks the best installed backend: piper > coqui > say > espeak
tts_backend     = "auto"
tts_voice       = "en_US-lessac-medium"
piper_model_dir = "voices/piper"
speak_by_default = true

# Local voice conversion. Set rvc_enabled = true and point rvc_cli at your
# converter. Placeholders: {{model}} {{input}} {{output}} {{pitch}} {{index}}
rvc_enabled   = false
rvc_cli       = "rvc infer --model {{model}} --input {{input}} --output {{output}} --pitch {{pitch}}"
rvc_model_dir = "voices/rvc"
"""

GITIGNORE = """.srgm/
voices/piper/*.onnx
voices/piper/*.json
voices/rvc/
voices/samples/
corpus/*
!corpus/README.md
__pycache__/
*.pyc
.venv/
"""

CORPUS_README = """# corpus/

Drop source material here, then run `srgm ingest`.

**Put in things you have the right to use.** This project ships no game text of
any kind. It reads what you give it. That means:

- PDFs or EPUBs of rulebooks and sourcebooks **you own**
- Catalyst's freely distributed material (quick-start rules, free adventures)
- your own campaign notes, session logs, character sheets, homebrew
- wiki exports and fan material whose licence permits local copies

Supported formats: `.pdf` `.epub` `.md` `.txt` `.html` `.json`
(`.pdf` needs `pip install 'shadowrun-gm[ingest]'`; everything else is stdlib.)

Subfolders are fine and are preserved as metadata:

    corpus/
      core/          core rulebook, companion volumes
      setting/       city sourcebooks, faction books
      adventures/    published runs
      mine/          your own notes -- this is the highest-value folder

What ingest does with them:

1. extracts text and splits it on real headings
2. links every recognised entity as an Obsidian `[[wikilink]]`
3. writes one note per source under `05-Sources/`
4. creates stub notes for entities the corpus mentions but the vault lacks
5. proposes new entity names it does not recognise, in the note
   `Entity Proposals` -- add the good ones to `lexicon.json` and re-ingest
6. builds the hybrid search index the Director reads from

Ingest is idempotent. Re-run it whenever you add material.
"""

VAULT_README = """---
type: meta
tags:
  - meta
---

# How this vault works

This is not documentation you have to maintain. It is the campaign's memory,
and the Director reads it every time it writes a scene. Editing a note here
genuinely changes what happens next.

## Folders

| Folder | What lives there | Who writes it |
|---|---|---|
| `00-System` | rules references, house rules | you |
| `05-Sources` | ingested books and notes | `srgm ingest` |
| `10-Setting` | world, factions, locations | both |
| `20-Campaign` | the living game: threads, scenes, NPCs, sessions | the engine |
| `30-Runners` | the three player characters | both |
| `90-Templates` | note templates | you |
| `99-Meta` | housekeeping, review queues | the engine |

## The rules of engagement

- **Anything you write here is canon.** Retrieval weights campaign notes above
  rulebook text, so a hand-written NPC note beats an ingested paragraph.
- **Wikilinks are how the Director finds things.** A note that links to
  `[[Renraku Computer Systems]]` will surface when Renraku becomes relevant,
  even if the scene never uses the word.
- **Dangling links are a to-do list.** `srgm graph` shows every `[[Name]]` that
  has no note yet. Those are promises the story has made.
- **The engine never overwrites your prose.** It updates its own `## Status`
  blocks and appends to logs. Everything else is yours.

## Useful queries

With the Dataview plugin installed:

````
```dataview
TABLE status, tension, kind FROM #thread WHERE status != "resolved" SORT tension DESC
```
````

````
```dataview
LIST FROM #npc WHERE status = "active"
```
````
"""

TEMPLATES = {
    "NPC": """---
type: npc
role:
faction:
disposition: 0
status: active
tags:
  - npc
---

## Summary

## Wants

## Leverage

## Voice

## Notes
""",
    "Location": """---
type: location
district:
owner:
security:
tags:
  - location
---

## Summary

## Getting In

## Who You Meet

## Complications
""",
    "Thread": """---
type: thread
kind: job
status: open
tension: 1.0
tags:
  - thread
---

## Summary

## Status

## Stakes

## People

## Factions

## Scene Log
""",
    "Runner": """---
type: runner
player:
archetype:
metatype:
tags:
  - runner
  - pc
---

## Attributes

## Skills

## Gear

## Contacts

## Bonds

## Condition
""",
}

HOUSE_RULES = """---
type: system
tags:
  - system
  - house-rules
---

# House Rules

The Director reads this note. Anything written here overrides the books.

## Table

- Three players. Every scene gives the spotlighted runner something only they
  can act on -- the engine rotates this automatically.
- The GM calls for a test; players roll their own dice. `/roll 12 3` at the
  prompt if you want the engine to do it.

## Dice

- Hits on 5 or 6. Glitch when half or more of the dice rolled show 1.
  Critical glitch when that happens with zero hits.
- Edge: adds the Rule of Six, ignores limits, and makes a glitch impossible.

## Pacing

- After roughly six scenes on a thread the engine may resolve it and spawn
  consequences. Say so at the table if you want it left open.
- Heat decays slowly when the crew lies low. It never reaches zero.

## Add your own below
"""

OBSIDIAN_APP = {
    "attachmentFolderPath": "99-Meta/attachments",
    "alwaysUpdateLinks": True,
    "newLinkFormat": "shortest",
    "useMarkdownLinks": False,
    "showLineNumber": False,
    "readableLineLength": True,
    "defaultViewMode": "preview",
}

OBSIDIAN_APPEARANCE = {"accentColor": "#00ff9c", "theme": "obsidian"}

OBSIDIAN_GRAPH = {
    "collapse-filter": False,
    "search": "",
    "showTags": True,
    "showAttachments": False,
    "hideUnresolved": False,
    "showOrphans": True,
    "collapse-color-groups": False,
    "colorGroups": [
        {"query": "tag:#thread", "color": {"a": 1, "rgb": 16729156}},
        {"query": "tag:#npc", "color": {"a": 1, "rgb": 16755200}},
        {"query": "tag:#runner", "color": {"a": 1, "rgb": 65436}},
        {"query": "tag:#scene", "color": {"a": 1, "rgb": 10066431}},
        {"query": "tag:#source", "color": {"a": 1, "rgb": 8421504}},
    ],
    "collapse-display": False,
    "showArrow": True,
    "textFadeMultiplier": -0.8,
    "nodeSizeMultiplier": 1.2,
    "lineSizeMultiplier": 1,
    "collapse-forces": False,
    "centerStrength": 0.4,
    "repelStrength": 11,
    "linkStrength": 1,
    "linkDistance": 210,
    "scale": 0.7,
}

OBSIDIAN_CORE_PLUGINS = [
    "file-explorer", "global-search", "switcher", "graph", "backlink",
    "outgoing-link", "tag-pane", "page-preview", "templates", "note-composer",
    "command-palette", "outline", "word-count", "file-recovery", "random-note",
]

OBSIDIAN_TEMPLATES = {"folder": "90-Templates"}


def scaffold_project(root: Path, campaign_name: str = "Neon Requiem") -> list[str]:
    """Create the project layout. Existing files are never overwritten."""
    root = Path(root)
    created: list[str] = []

    def write(relative: str, content: str) -> None:
        path = root / relative
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(relative)

    write("srgm.toml", SRGM_TOML.format(campaign=campaign_name))
    write(".gitignore", GITIGNORE)
    write("corpus/README.md", CORPUS_README)

    vault = Vault(root / "vault")
    vault.ensure()

    write(f"vault/{FOLDERS['meta']}/How This Vault Works.md", VAULT_README)
    write(f"vault/{FOLDERS['system']}/House Rules.md", HOUSE_RULES)
    for name, body in TEMPLATES.items():
        write(f"vault/{FOLDERS['template']}/{name} Template.md", body)

    # Obsidian workspace config -- makes the vault usable on first open.
    obsidian = "vault/.obsidian"
    write(f"{obsidian}/app.json", json.dumps(OBSIDIAN_APP, indent=2))
    write(f"{obsidian}/appearance.json", json.dumps(OBSIDIAN_APPEARANCE, indent=2))
    write(f"{obsidian}/graph.json", json.dumps(OBSIDIAN_GRAPH, indent=2))
    write(f"{obsidian}/core-plugins.json", json.dumps(OBSIDIAN_CORE_PLUGINS, indent=2))
    write(f"{obsidian}/templates.json", json.dumps(OBSIDIAN_TEMPLATES, indent=2))

    for directory in ("voices/piper", "voices/rvc", "voices/samples", "tables"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    write(
        "voices/README.md",
        "# voices/\n\n"
        "- `piper/` -- Piper `.onnx` voice models (plus their `.onnx.json`).\n"
        "  Grab them from the Piper voices release and drop them here.\n"
        "- `rvc/` -- your RVC `.pth` models, with `.index` files beside them.\n"
        "- `samples/` -- reference `.wav` clips for XTTS speaker cloning.\n"
        "- `cast.json` -- who sounds like what. Edit it between sessions.\n\n"
        "Check the pipeline with `srgm voices --test`.\n",
    )
    write(
        "tables/README.md",
        "# tables/\n\nDrop `.json` files here to extend or override the built-in\n"
        "random tables. A file `mine.json` with a key `employer` becomes both\n"
        "`mine.employer` and (if not already taken) `employer`.\n\n"
        "The bundled tables live in `shadowrun_gm/data/tables/` -- copy one out\n"
        "as a starting point.\n",
    )

    return created
