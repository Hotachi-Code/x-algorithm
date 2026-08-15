"""The storyteller: text in, spoken audio out.

Handles the whole chain -- clean the markdown, split narration from character
dialogue, synthesise each segment in its own voice, run RVC over it, play it.
Long passages are synthesised sentence-group by sentence-group so the table
hears the first line while the rest is still rendering.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Iterator

from .cast import NARRATOR, VoiceCast, VoiceProfile
from .player import play_audio
from .rvc import RVCConverter
from .tts import TTSBackend, TTSResult, get_tts

# `Name: "line"` or `Name: line` at the start of a line -- the convention the
# Director's prompt nudges models toward.
SPEAKER_RE = re.compile(r"^\s*([A-Z][\w '’\-]{1,28}):\s+(.+)$")
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")

_MD_PATTERNS = (
    (re.compile(r"```.*?```", re.DOTALL), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),
    (re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]"), r"\2"),
    (re.compile(r"\[\[([^\]]+)\]\]"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE), ""),
    (re.compile(r"^\s{0,3}>\s?", re.MULTILINE), ""),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"\*([^*]+)\*"), r"\1"),
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"^\s*[-*•]\s+", re.MULTILINE), ""),
    (re.compile(r"[ \t]+"), " "),
    (re.compile(r"\n{3,}"), "\n\n"),
)


def clean_for_speech(text: str) -> str:
    """Strip markdown so the synthesiser does not read asterisks aloud."""
    for pattern, replacement in _MD_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip()


@dataclass
class Segment:
    text: str
    speaker: str = NARRATOR

    def is_narration(self) -> bool:
        return self.speaker == NARRATOR


def split_speakers(text: str) -> list[Segment]:
    """Break text into narration and per-character dialogue segments."""
    segments: list[Segment] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = SPEAKER_RE.match(line)
        if match and not _looks_like_label(match.group(1)):
            segments.append(Segment(text=match.group(2).strip(), speaker=match.group(1)))
        elif segments and segments[-1].is_narration():
            segments[-1].text = f"{segments[-1].text} {line.strip()}"
        else:
            segments.append(Segment(text=line.strip()))
    return [s for s in segments if s.text]


_LABELS = {"Hooks", "Tests", "Note", "Narration", "Location", "Present", "Thread",
           "Summary", "Status", "Stakes", "People", "Factions", "Warning", "Tip"}


def _looks_like_label(word: str) -> bool:
    return word in _LABELS


def group_sentences(text: str, max_chars: int = 320) -> list[str]:
    """Pack sentences into synthesiser-sized groups."""
    sentences = [s.strip() for s in SENTENCE_RE.split(text) if s.strip()]
    groups: list[str] = []
    buf: list[str] = []
    size = 0
    for sentence in sentences:
        if size and size + len(sentence) > max_chars:
            groups.append(" ".join(buf))
            buf, size = [], 0
        buf.append(sentence)
        size += len(sentence) + 1
    if buf:
        groups.append(" ".join(buf))
    return groups or ([text] if text.strip() else [])


@dataclass
class Narrator:
    """Speaks scenes and dialogue in the cast's voices."""

    tts: TTSBackend
    cast: VoiceCast
    rvc: RVCConverter | None = None
    out_dir: Path = Path(".srgm/audio")
    enabled: bool = True
    _counter: int = field(default=0, init=False)

    @classmethod
    def build(
        cls,
        *,
        tts_backend: str = "auto",
        piper_model_dir: Path = Path("voices/piper"),
        tts_voice: str = "",
        cast_path: Path = Path("voices/cast.json"),
        out_dir: Path = Path(".srgm/audio"),
        rvc_enabled: bool = False,
        rvc_cli: str = "",
        rvc_model_dir: Path = Path("voices/rvc"),
        enabled: bool = True,
    ) -> "Narrator":
        tts = get_tts(
            tts_backend,
            model_dir=piper_model_dir,
            voice=tts_voice,
            cache_dir=Path(out_dir) / "cache",
        )
        cast = VoiceCast.load(cast_path)
        if not cast.narrator.tts_voice and tts_voice:
            cast.narrator.tts_voice = tts_voice

        rvc = None
        if rvc_enabled:
            rvc = RVCConverter(model_dir=Path(rvc_model_dir), enabled=True)
            if rvc_cli:
                rvc.command = rvc_cli
        return cls(tts=tts, cast=cast, rvc=rvc, out_dir=Path(out_dir), enabled=enabled)

    # -- synthesis ----------------------------------------------------------
    def render(self, text: str, speaker: str = NARRATOR) -> TTSResult:
        """Synthesise one utterance, applying the speaker's voice and RVC."""
        profile: VoiceProfile = self.cast.voice_for(speaker)
        self._counter += 1
        stem = f"{int(time.time())}-{self._counter:04d}"
        raw_path = self.out_dir / "raw" / f"{stem}.wav"

        result = self.tts.synthesize(text, raw_path, profile.tts_voice)
        if not result.ok or self.rvc is None or not profile.rvc_model:
            return result

        converted_path = self.out_dir / "voiced" / f"{stem}-{profile.rvc_model}.wav"
        conversion = self.rvc.convert(
            result.path, converted_path, profile.rvc_model, pitch=profile.pitch
        )
        if conversion.ok and conversion.converted:
            return TTSResult(
                conversion.path, text, f"{result.backend}+rvc", profile.rvc_model
            )
        if conversion.error:
            print(f"  [voice] RVC failed for {speaker}: {conversion.error}")
        return result

    def speak(self, text: str, speaker: str = NARRATOR, *, blocking: bool = True) -> bool:
        """Render and play one utterance."""
        if not self.enabled:
            return False
        cleaned = clean_for_speech(text)
        if not cleaned:
            return False
        result = self.render(cleaned, speaker)
        if not result.ok:
            if result.error and self.tts.name == "null":
                return False
            return False
        return play_audio(result.path, blocking=blocking)

    def speak_passage(self, text: str, *, blocking: bool = True) -> int:
        """Speak a whole scene: narration in the narrator's voice, dialogue in character.

        Rendering runs one segment ahead of playback so there is no gap between
        sentences at the table.
        """
        if not self.enabled:
            return 0
        cleaned = clean_for_speech(text)
        if not cleaned:
            return 0

        jobs: list[tuple[str, str]] = []
        for segment in split_speakers(cleaned):
            for group in group_sentences(segment.text):
                jobs.append((group, segment.speaker))
        if not jobs:
            return 0

        queue: Queue = Queue()

        def produce() -> None:
            for job_text, speaker in jobs:
                try:
                    queue.put(self.render(job_text, speaker))
                except Exception as exc:  # a bad line must not kill the session
                    queue.put(TTSResult(None, job_text, "error", error=str(exc)))
            queue.put(None)

        worker = threading.Thread(target=produce, daemon=True)
        worker.start()

        played = 0
        while True:
            result = queue.get()
            if result is None:
                break
            if result.ok and play_audio(result.path, blocking=blocking):
                played += 1
        worker.join(timeout=5)
        return played

    # -- introspection ------------------------------------------------------
    def status(self) -> str:
        lines = [f"TTS backend : {self.tts.name}"]
        if self.rvc is not None:
            models = [m.stem for m in self.rvc.models()]
            lines.append(
                f"RVC         : {'ready' if self.rvc.available() else 'binary not found'}"
                f" ({len(models)} model{'s' if len(models) != 1 else ''})"
            )
        else:
            lines.append("RVC         : disabled")
        from .player import available_player

        lines.append(f"Player      : {available_player() or 'none found'}")
        lines.append(f"Cast        :\n{self.cast.describe()}")
        return "\n".join(lines)

    def iter_segments(self, text: str) -> Iterator[Segment]:
        yield from split_speakers(clean_for_speech(text))
