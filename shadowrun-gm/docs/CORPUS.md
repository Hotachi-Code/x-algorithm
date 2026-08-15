# Feeding the corpus

The engine ships with no game text. It is a reader, and `corpus/` is what it
reads.

## What belongs there

| Source | Notes |
|---|---|
| Rulebooks and sourcebooks **you own** | PDF or EPUB. This is the bulk of it. |
| Catalyst's freely distributed material | Quick-start rules, free adventures, previews. |
| **Your own campaign notes** | The highest-value folder. Session logs, character sheets, homebrew, house rules, the NPC you invented last Tuesday. |
| Fan wikis and articles | Only where the licence permits keeping a local copy. |

You are responsible for having the right to use what you put here. Nothing is
downloaded for you and nothing is shipped with the project.

## Formats

| Extension | Requires |
|---|---|
| `.md` `.txt` | nothing |
| `.html` `.htm` `.xhtml` | nothing |
| `.epub` | nothing — read straight out of the zip container |
| `.json` | nothing — a list of `{heading, text}` or a `{heading: text}` map |
| `.pdf` | `pip install 'shadowrun-gm[ingest]'` (pypdf) |

Anything else is skipped silently. A file that fails to parse is reported and
the run continues — one bad PDF never stops an ingest.

## Suggested layout

Subfolders are free-form and preserved as metadata:

```
corpus/
  core/          core rules, companion volumes
  setting/       city and faction sourcebooks
  adventures/    published runs
  mine/          your notes -- put the good stuff here
```

`corpus/README.md` at the top level is skipped (it is this project's own
instructions). A `README.md` inside a subfolder is treated as content.

## What ingest does

```
srgm ingest
```

1. **Extract.** Text is pulled out and cleaned: soft hyphens removed, words
   de-hyphenated across line breaks, whitespace normalised.
2. **Structure.** Content is split on real headings — markdown `#`, `CHAPTER
   TWO`, numbered headings, and short Title Case lines with no terminal
   punctuation. PDFs keep their page numbers.
3. **Link.** Every entity in the lexicon is wrapped in a wikilink. Aliases
   resolve to one canonical note, so "Renraku" and "Renraku Computer Systems"
   are the same entity. Only capitalised entries link — that is what keeps
   "fixer" and "dwarf" out of the graph.
4. **Write.** One note per source file under `05-Sources/`, with frontmatter
   recording the origin, format, section count and word count.
5. **Stub.** Entities the corpus mentions but the vault lacks get a stub note
   in the right folder, ready for you to fill in.
6. **Propose.** Repeated capitalised names the lexicon has never heard of are
   collected into the vault note **Entity Proposals** for review.
7. **Index.** The hybrid search index is rebuilt.

Ingest is idempotent. Re-run it whenever you add material.

## Teaching it your names

The proposals note is the feedback loop. After ingesting a sourcebook you will
find a table of candidates:

| Candidate | Mentions |
|---|---|
| Kestrel Vance | 41 |
| The Obsidian Gate | 17 |

Add the ones that matter to `lexicon.json` in the project root:

```json
{
  "npc":   ["Kestrel Vance"],
  "place": [{"name": "The Obsidian Gate", "aliases": ["the Gate"]}]
}
```

Re-run `srgm ingest`. From now on every mention anywhere in the corpus links to
one note, and the Director can find it through the graph.

## Scale

A full shelf of sourcebooks produces tens of thousands of chunks. That is fine:
the index is SQLite, search is in-process, and a query over ~50k chunks with
the hashing embedder takes well under a second. With `[embed]` installed the
one-off embedding pass is the slow part; the query stays fast.

If ingest is slow, it is almost always PDF text extraction. Convert once to
markdown and keep that instead.

## Troubleshooting

**"no extractable text"** — a scanned PDF with no text layer. Run OCR on it
first; the engine does not do OCR.

**Everything ranks badly** — check `srgm graph`. A vault with no links is a
vault where two thirds of the retrieval signal is missing. Ingesting real
sourcebooks fixes it; so does writing a few NPC notes by hand.

**Too many junk stub notes** — run `srgm ingest --no-stubs`, or trim the
lexicon. Lowercase an entry to keep it searchable without making it a note.
