# shadowrun-gm

A never-ending Shadowrun campaign engine for a table of three, with an Obsidian
vault for a memory and a local voice for a storyteller.

You give it the books and notes you own. It builds a linked vault out of them,
runs a campaign that generates its own next job forever, and reads the scenes
aloud in voices you choose — all on your machine, no API key required.

```
$ srgm play

   ___ _  _   _   ___   _____      _____ _   _ _  _
  / __| || | /_\ |   \ / _ \ \    / / _ \ | | | \| |
  \__ \ __ |/ _ \| |) | (_) \ \/\/ /   / |_| | .` |
  |___/_||_/_/ \_\___/ \___/ \_/\_/ _|_\\___/|_|\_|
        never-ending campaign engine -- three runners, one city

  Neon Requiem | session 4, scene 63 | heat 6.2 | 7 open threads
  narrator: ollama  |  voice: piper+rvc

> Static jacks into the host while Ratchet holds the stairwell.
```

---

## Why it works this way

Three design decisions carry the whole project.

**The vault is the memory, not a log.** Notes are the campaign state, in both
directions. The engine writes threads, NPCs and scenes into Obsidian; retrieval
reads them back before every scene. Edit an NPC note between sessions and the
Director genuinely behaves differently next time. Campaign notes outrank
rulebook text in the ranking, so your canon beats the book's.

**Retrieval follows links, not just words.** Searching "Hollow" finds Hollow's
note — and also the fixer who links to her, the block her gang owns, and the
thread that made meeting her dangerous, none of which contain the word. That is
the entire reason this is built on a wikilink graph instead of a folder of text
files. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#retrieval).

**The story cannot run out.** Three rules guarantee it: every resolved thread
spawns at least one consequence, every faction whose heat crosses a threshold
buys itself a retaliation thread, and the pool is topped up whenever it drops
below three. Threads that get ignored gain pressure until they demand the
spotlight. Over a 200-scene soak run the open-thread count never falls below 3
and typically drifts up to 12–15. It is a campaign that grows faster than three
players can close it.

---

## Install

```bash
git clone <this repo>
cd shadowrun-gm
pip install -e .
```

The core has **no dependencies** — the standard library only. Everything below
is optional and degrades gracefully:

```bash
pip install -e '.[ingest]'   # PDF and EPUB extraction  (pypdf)
pip install -e '.[embed]'    # better embeddings        (sentence-transformers)
pip install -e '.[dev]'      # tests                    (pytest)
```

Without `[embed]` the retriever falls back to built-in hashed vectors. Without
a language model it falls back to a template narrator. Without a synthesiser it
falls back to text. Nothing in the chain is load-bearing.

## Five minutes to playing

```bash
srgm init --campaign "Neon Requiem" --players "Ana,Bo,Cy"
cp ~/books/*.pdf corpus/          # things you own -- see docs/CORPUS.md
srgm ingest
srgm play
```

`init` scaffolds the project, writes an Obsidian vault that opens cleanly on
first launch, and rolls three pregenerated runners — a street samurai, a decker
and a face, because that is the smallest crew that can open doors, systems and
people.

## At the table

Anything you type that is not a command is what the crew just did; the Director
narrates the consequence. It never rolls for you and never speaks for your
character.

```
  /next [type]          next scene: meet legwork infiltration matrix social
                        combat chase downtime twist fallout
  /say <npc> <line>     talk to an NPC, answered in their own voice
  /roll 12 3            twelve dice against threshold 3
  /roll 9 vs 7          opposed test
  /roll 10 x 8          extended test, threshold 8
  /edge 12 3            with Edge: exploding 6s, no limit, no glitch
  /init                 initiative for the crew
  /threads              open threads, most pressing first
  /crew  /npcs  /state  who and what is in play
  /recap                write and speak the "previously on"
  /session              end the session, start the next
  /voice on|off|status  storyteller voice controls
  /sync                 rewrite the vault from campaign state
```

Outside the loop:

| Command | Does |
|---|---|
| `srgm ingest` | corpus → linked vault notes → search index |
| `srgm reindex` | rebuild the index after hand-editing the vault |
| `srgm scene -n 3` | generate scenes without the interactive loop |
| `srgm search "<q>"` | query the vault, with score breakdown |
| `srgm graph` | link stats, hubs, and every promise the story has not kept |
| `srgm voices --test` | check the whole audio pipeline |
| `srgm status` | project and campaign state |

## The corpus

**No game text ships with this project.** It reads what you give it, and it is
your responsibility that you have the right to use it: rulebooks you own,
Catalyst's freely distributed quick-start material, your own campaign notes and
homebrew, and fan material whose licence permits a local copy.

Formats: `.pdf` `.epub` `.md` `.txt` `.html` `.json`. Your own notes are the
highest-value thing in the folder — they are what makes the campaign yours
rather than generic. Full detail in [`docs/CORPUS.md`](docs/CORPUS.md).

Ingest extracts the text, splits it on real headings, wikilinks every entity it
recognises, writes one note per source, stubs out entities that are mentioned
but missing, and proposes names it has never heard of for your review.

## Voice

Speech is a two-stage pipeline: a synthesiser produces the words, then your
local voice converter makes them belong to *someone*.

```
text ──> TTS (Piper / XTTS / espeak / say) ──> RVC conversion ──> playback
```

Voices are assigned in `voices/cast.json`. Anyone you have not cast is assigned
a voice deterministically from their name, so an NPC sounds the same in session
nine as in session one. Narration and dialogue are separated automatically and
rendered one segment ahead of playback, so there is no gap between lines.

RVC command lines differ between forks, so the invocation is a template you set
in `srgm.toml`:

```toml
rvc_enabled = true
rvc_cli = "rvc infer --model {model} --input {input} --output {output} --pitch {pitch}"
```

Setup and troubleshooting: [`docs/VOICE.md`](docs/VOICE.md).

## Narration backends

| Backend | Setup | Notes |
|---|---|---|
| `local` | none | Template narrator. Plays offline, today, with zero installs. |
| `ollama` | `ollama pull llama3.1:8b` | **Recommended.** Fully local, good prose. |
| `anthropic` | `ANTHROPIC_API_KEY` | Best prose; the only option that leaves your machine. |

Set `llm_backend` in `srgm.toml` or pass `--llm-backend`. If a model backend is
configured but unreachable mid-session, it drops to the local narrator and
keeps playing rather than dying.

## Configuration

`srgm.toml` in the project root; every key is also settable as `SRGM_<KEY>`.

```toml
llm_backend   = "ollama"
llm_model     = "llama3.1:8b"
embed_backend = "auto"
tts_backend   = "auto"
rvc_enabled   = true
retrieval_k   = 12
graph_hops    = 1
seed          = 0        # set for reproducible runs
```

## Layout

```
corpus/          what you feed it
vault/           the Obsidian vault -- open this folder in Obsidian
  00-System/       rules, house rules
  05-Sources/      ingested material
  10-Setting/      factions, locations, world
  20-Campaign/     threads, scenes, NPCs -- the living game
  30-Runners/      the three PCs
voices/          piper models, rvc models, cast.json
tables/          your own random tables, overriding the built-ins
.srgm/           campaign.json, search index, rendered audio
```

## Extending it

- **New random tables** — drop JSON in `tables/`. A key `employer` in
  `mine.json` becomes `mine.employer` and shadows the built-in `employer`.
- **New entities** — add to `lexicon.json` in the project root. An entry is
  auto-linked only if it contains a capitalised word, which is how "Renraku"
  becomes a note and "fixer" does not. Aliases keep one canonical note:
  `{"name": "Renraku Computer Systems", "aliases": ["Renraku"]}`.
- **House rules** — write them in `vault/00-System/House Rules.md`. That note is
  read before every scene.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

77 tests, no network, no model downloads. They cover the dice mechanics against
the published rules, vault round-tripping, retrieval ranking, the never-ending
guarantees, and the voice pipeline's failure modes.

## Licence

MIT, for the code. The corpus you supply is yours and its licence is yours to
respect — nothing from any published Shadowrun book is included here.

Shadowrun is a trademark of The Topps Company, Inc. This is an unaffiliated
fan tool for running games with material you already own.
