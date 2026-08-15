# The storyteller's voice

Two stages, both local:

```
scene text
   │  strip markdown, split narration from dialogue, group into sentences
   ▼
TTS backend ──────────────> raw .wav
   │  piper | coqui/xtts | say | espeak
   ▼
RVC conversion (optional) ─> voiced .wav
   │  your .pth model, per character
   ▼
playback  (paplay | aplay | afplay | play | ffplay | mpv)
```

Stage one produces words. Stage two makes them belong to someone. Either can be
absent — the pipeline degrades instead of failing.

Check the whole chain at once:

```bash
srgm voices --test
```

```
TTS backend : piper
RVC         : ready (4 models)
Player      : paplay
Cast        :
narrator: en_US-lessac-medium
  Fathom: en_US-ryan-high -> rvc:fathom (pitch -2)
  Hollow: en_US-amy-medium -> rvc:hollow
```

## Choosing a TTS backend

`tts_backend = "auto"` picks the best one installed, in this order:

| Backend | Quality | Speed | Install |
|---|---|---|---|
| **piper** | very good | very fast | `pip install piper-tts`, then drop `.onnx` voices in `voices/piper/` |
| **coqui** (XTTS) | excellent, clones voices | slow | `pip install TTS`; put reference clips in `voices/samples/` |
| **say** | decent | fast | built into macOS |
| **espeak** | robotic | instant | `apt install espeak-ng` |
| **null** | — | — | writes `.txt` beside where the audio would go |

Piper is the recommendation: it is fast enough to keep up with a table and good
enough that nobody comments on it. Grab voices from the Piper voices release and
drop the `.onnx` (and its `.onnx.json`) into `voices/piper/`.

Synthesised audio is cached by text and voice, so replaying a recap or a
repeated NPC line is instant.

## RVC voice conversion

Point the engine at your models:

```toml
rvc_enabled   = true
rvc_model_dir = "voices/rvc"
rvc_cli = "rvc infer --model {model} --input {input} --output {output} --pitch {pitch}"
```

Placeholders: `{binary}` `{model}` `{input}` `{output}` `{pitch}` `{index}`.

The command is a template because RVC forks disagree about flags. Whatever your
converter's CLI looks like, write it here. `.index` files are found
automatically if they sit beside the `.pth`.

If a conversion fails, the unconverted TTS audio plays and a one-line warning is
printed. A broken voice model never silences the session.

## Casting

`voices/cast.json`:

```json
{
  "narrator": { "tts_voice": "en_US-lessac-medium" },
  "cast": {
    "Fathom": { "tts_voice": "en_US-ryan-high", "rvc_model": "fathom", "pitch": -2 },
    "Hollow": { "rvc_model": "hollow", "note": "smoke-ruined, was beautiful once" }
  },
  "pool": [
    { "rvc_model": "generic-male-1" },
    { "rvc_model": "generic-fem-1" }
  ]
}
```

Anyone not in `cast` is assigned from `pool` by a stable hash of their name — so
a walk-on NPC has a consistent voice from the moment they are introduced, and
you can promote them into `cast` later without them suddenly sounding different.

Edit this file between sessions. It is re-read on every launch.

## How dialogue gets its voice

Narration is spoken by the narrator. Lines of the form `Name: what they say` at
the start of a line are spoken by that character. Structural labels (`Hooks:`,
`Tests:`, `Summary:`) are recognised and left to the narrator.

`/say <npc> <line>` always speaks in that NPC's voice.

Long passages are split into sentence groups and rendered one group ahead of
playback on a worker thread, so the table hears the first line while the rest is
still being synthesised.

## Playback

The first available player is used: `paplay`, `aplay`, `afplay`, `play` (sox),
`ffplay`, `mpv`, `cvlc`. If none exists, audio files are still written under
`.srgm/audio/` and you can play them however you like.

## Troubleshooting

**Silence, and the backend says `null`** — no synthesiser found. Install one, or
accept text-only play; the `.txt` transcripts land in `.srgm/audio/raw/`.

**`no piper .onnx model found`** — the directory is empty. Any `.onnx` in
`voices/piper/` will be used if the configured voice name does not match.

**RVC says "binary not found"** — the first word of `rvc_cli` must be on your
`PATH`, or set `binary` explicitly and use `{binary}` in the template.

**It reads the asterisks aloud** — that would be a bug in the markdown stripper;
`clean_for_speech` handles bold, italics, headings, wikilinks, links, code and
bullets. Please report the input that broke it.

**Playback stutters between sentences** — increase the sentence group size, or
switch from XTTS to Piper. XTTS is not real-time on most hardware.
