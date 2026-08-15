# How it works

```
corpus/ ──ingest──> vault/ ──index──> .srgm/index.sqlite
                      ▲                      │
                      │                      ▼
                    write                retrieval ──┐
                      │                              ▼
              campaign state  ────────────────>  Director ──> scene
                      ▲                              │
                      └──────── absorb ◀─────────────┘
                                                     ▼
                                                 narrator ──> audio
```

## Layers

| Package | Responsibility |
|---|---|
| `ingest/` | corpus → clean markdown → linked vault notes → chunks |
| `vaultio/` | notes, frontmatter, the wikilink graph |
| `retrieval/` | BM25 + embeddings + graph expansion |
| `rules/` | dice pools, tests, initiative |
| `engine/` | campaign state, procedural arcs, prompts, the Director |
| `voice/` | TTS, RVC, casting, playback |
| `table/` | the interactive loop and pregenerated runners |

Each layer is usable alone. `rules/` has no idea a vault exists; `vaultio/` has
no idea a campaign exists.

## Retrieval

Three signals, blended and then min-max normalised so they are comparable:

- **Lexical** — Okapi BM25 over chunk text. Catches proper nouns and rules
  keywords, which is most of what a GM actually looks up.
- **Vector** — cosine over embeddings. Catches paraphrase. Uses
  sentence-transformers when installed, otherwise a hashed bag of tokens and
  bigrams with sublinear scaling. The fallback is weaker but real: hybrid search
  with it still beats keyword-only.
- **Graph** — credit spreads from the strongest hits to their linked
  neighbours, decaying `0.6^hops`. Only the top eight seeds get to pull
  neighbours in, or everything ends up adjacent to everything.

Two adjustments on top:

- **Kind boost.** A thread note scores ×1.45, an NPC ×1.35, a rulebook
  paragraph ×0.9. What is happening now outranks what the book says.
- **Diversity cap.** At most three chunks per note, so one long page cannot fill
  the context window.

Chunks are indexed as `note + heading + body`, not body alone. Without that, an
NPC note whose text never repeats the NPC's name is unfindable by their name —
which is the common case in a real vault.

Section stubs (`_none yet_`) are dropped at index time. A two-word document
scores absurdly well on BM25 and crowds out real passages.

## The never-ending guarantee

Three mechanisms, each independently sufficient to keep the pool non-empty:

1. **Consequences.** `spawn_consequences` always returns at least one thread,
   drawn from success / failure / mixed tables. 55% of the time it mints a
   named NPC, and 30% of the time it drags in a faction that was not previously
   involved — so the cast and the map both keep growing.
2. **Heat.** Loud scenes raise heat; quiet ones leak it away. Heat is a leaky
   bucket, capped at 12 with proportional decay, so it plateaus instead of
   growing without bound over hundreds of scenes. When a faction crosses 5, it
   buys a retaliation thread — once per heat tier, so a hated corp escalates
   rather than spamming identical threads.
3. **Floor.** `world_tick` tops the pool back up to three open threads.

Scheduling is pressure-weighted: `tension + staleness + clock progress`, where
staleness grows with every beat a thread is ignored. Neglect a thread long
enough and it will demand the spotlight — which is what stops the campaign
collapsing into one storyline.

Scene types flow from the previous beat through a small transition table, biased
by thread kind, excluding the last two types used, with a pressure valve that
earns a downtime scene after four beats without one.

Spotlight goes to whichever runner has led fewest scenes. With three players
this matters: an unmanaged Director writes for the loudest character every time.
Over 200 scenes the split lands at 67/67/66.

## The Director's turn

```
pick thread     pressure-weighted draw over open threads
pick beat       flow from the last scene type
pick spotlight  the quietest runner
build spec      location, present NPCs, atmosphere, must-includes, withheld twist
retrieve        hybrid search, pinned to the NPCs and factions in the scene
narrate         LLM, or the offline template narrator
parse           <narration> <hooks> <tests> <state>
absorb          new NPCs, locations, heat, clock ticks, resolutions
persist         campaign.json (atomic) + scene note + thread note
world tick      off-screen clocks advance, consequences spawn
```

Every prompt carries a machine-readable `<<SPEC {...} SPEC>>` block. Real models
read it as unusually well-organised context; the offline narrator renders a
scene directly from it. That is why the same code path serves both, and why the
project plays with zero dependencies installed.

Output parsing tolerates sloppiness: missing tags fall back to treating
everything before the first list as prose, and a malformed state block is
treated as empty rather than crashing the session.

## State, in two places

`.srgm/campaign.json` is the machine-readable truth, written atomically through
a temp file so a crash mid-write cannot lose a campaign.

`vault/20-Campaign/` is the human-readable mirror. `sync_to_vault()` pushes
state into notes; retrieval reads those notes back. The engine only ever
rewrites its own `## Status`-style sections — `Note.upsert_section` replaces one
block and leaves the prose above it untouched, so a GM's hand-edits survive
indefinitely.

That round trip is the point: your edits are not annotations on the campaign,
they are inputs to it.

## Dice

SR5-style pools. Hits on 5–6. Glitch when half or more of the dice rolled show
1; critical glitch when that happens with zero hits. Limits cap hits. Edge does
three things at once — adds the Rule of Six, ignores the limit, and makes a
glitch impossible — so spending Edge is buying narrative safety, and the code
models all three together.

Extended tests lose a die per interval and stop early on a critical glitch: the
decker did not merely fail to crack the host, they tripped something.

## Failure behaviour

Every external dependency is optional and every failure is soft:

| Missing | Result |
|---|---|
| sentence-transformers | hashed vectors |
| Ollama / API unreachable | offline narrator, one warning, session continues |
| search index | scenes generate without vault context |
| Piper / any TTS | transcripts written as `.txt` |
| RVC model or binary | unconverted TTS audio plays |
| audio player | files still written to `.srgm/audio/` |
| pypdf | PDFs reported and skipped, other formats ingest |

A table should never lose a session to a missing package.
