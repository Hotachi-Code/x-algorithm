"""Text to speech, all of it local.

Backends are probed in order of quality: Piper (fast, excellent, offline),
Coqui/XTTS (slower, supports voice cloning), then the OS speech synthesiser as
a last resort. Every backend writes a WAV file so the RVC stage downstream
always has something to convert.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class TTSResult:
    path: Path | None
    text: str
    backend: str
    voice: str = ""
    cached: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None and self.path.is_file()


class TTSBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def synthesize(self, text: str, out_path: Path, voice: str = "") -> TTSResult: ...


def _run(cmd: list[str], stdin: bytes | None = None, timeout: int = 300) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stderr.decode("utf-8", "replace")[-800:]
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out after {timeout}s"


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------

@dataclass
class PiperTTS:
    """Piper: the recommended backend. One .onnx model per voice."""

    model_dir: Path = Path("voices/piper")
    default_voice: str = "en_US-lessac-medium"
    binary: str = "piper"
    name: str = field(default="piper", init=False)

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def resolve_model(self, voice: str = "") -> Path | None:
        voice = voice or self.default_voice
        candidate = Path(voice)
        if candidate.is_file():
            return candidate
        directory = Path(self.model_dir)
        for name in (f"{voice}.onnx", voice):
            path = directory / name
            if path.is_file():
                return path
        # Fall back to any model in the directory, so a fresh install with one
        # downloaded voice just works regardless of what it is called.
        models = sorted(directory.glob("*.onnx")) if directory.is_dir() else []
        return models[0] if models else None

    def synthesize(self, text: str, out_path: Path, voice: str = "") -> TTSResult:
        model = self.resolve_model(voice)
        if model is None:
            return TTSResult(
                None, text, self.name, voice,
                error=f"no piper .onnx model found in {self.model_dir}",
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        code, err = _run(
            [self.binary, "--model", str(model), "--output_file", str(out_path)],
            stdin=text.encode("utf-8"),
        )
        if code != 0:
            return TTSResult(None, text, self.name, voice, error=err)
        return TTSResult(out_path, text, self.name, str(model.stem))


@dataclass
class CoquiTTS:
    """Coqui TTS / XTTS, invoked through its CLI. Supports speaker cloning."""

    model: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    speaker_wav_dir: Path = Path("voices/samples")
    language: str = "en"
    binary: str = "tts"
    name: str = field(default="coqui", init=False)

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def synthesize(self, text: str, out_path: Path, voice: str = "") -> TTSResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.binary,
            "--text", text,
            "--model_name", self.model,
            "--out_path", str(out_path),
        ]
        sample = self._speaker_sample(voice)
        if sample is not None:
            cmd += ["--speaker_wav", str(sample), "--language_idx", self.language]
        code, err = _run(cmd, timeout=600)
        if code != 0:
            return TTSResult(None, text, self.name, voice, error=err)
        return TTSResult(out_path, text, self.name, voice)

    def _speaker_sample(self, voice: str) -> Path | None:
        if not voice:
            return None
        candidate = Path(voice)
        if candidate.is_file():
            return candidate
        for suffix in (".wav", ".flac", ".mp3"):
            path = Path(self.speaker_wav_dir) / f"{voice}{suffix}"
            if path.is_file():
                return path
        return None


@dataclass
class EspeakTTS:
    """espeak-ng. Robotic, but it is on nearly every Linux box already."""

    binary: str = "espeak-ng"
    rate: int = 155
    name: str = field(default="espeak", init=False)

    def available(self) -> bool:
        return shutil.which(self.binary) is not None or shutil.which("espeak") is not None

    def synthesize(self, text: str, out_path: Path, voice: str = "") -> TTSResult:
        binary = self.binary if shutil.which(self.binary) else "espeak"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [binary, "-s", str(self.rate), "-w", str(out_path)]
        if voice:
            cmd += ["-v", voice]
        cmd.append(text)
        code, err = _run(cmd)
        if code != 0:
            return TTSResult(None, text, self.name, voice, error=err)
        return TTSResult(out_path, text, self.name, voice)


@dataclass
class SayTTS:
    """macOS `say`. Writes AIFF, converted to WAV when afconvert is present."""

    name: str = field(default="say", init=False)

    def available(self) -> bool:
        return shutil.which("say") is not None

    def synthesize(self, text: str, out_path: Path, voice: str = "") -> TTSResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        aiff = out_path.with_suffix(".aiff")
        cmd = ["say", "-o", str(aiff)]
        if voice:
            cmd += ["-v", voice]
        cmd.append(text)
        code, err = _run(cmd)
        if code != 0:
            return TTSResult(None, text, self.name, voice, error=err)
        if shutil.which("afconvert"):
            convert, cerr = _run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff), str(out_path)]
            )
            if convert == 0:
                aiff.unlink(missing_ok=True)
                return TTSResult(out_path, text, self.name, voice)
            return TTSResult(aiff, text, self.name, voice, error=cerr)
        return TTSResult(aiff, text, self.name, voice)


@dataclass
class NullTTS:
    """Writes the narration to a .txt beside where the audio would have gone.

    Keeps the session loop identical whether or not a synthesiser exists.
    """

    name: str = field(default="null", init=False)

    def available(self) -> bool:
        return True

    def synthesize(self, text: str, out_path: Path, voice: str = "") -> TTSResult:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        transcript = out_path.with_suffix(".txt")
        transcript.write_text(text, encoding="utf-8")
        return TTSResult(None, text, self.name, voice, error="no TTS backend installed")


# --------------------------------------------------------------------------
# caching wrapper + factory
# --------------------------------------------------------------------------

@dataclass
class CachingTTS:
    """Skip synthesis when the same text and voice have been rendered before.

    Recaps and repeated NPC lines get replayed a lot at a real table.
    """

    inner: TTSBackend
    cache_dir: Path
    name: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.name = self.inner.name

    def available(self) -> bool:
        return self.inner.available()

    def synthesize(self, text: str, out_path: Path, voice: str = "") -> TTSResult:
        key = hashlib.blake2b(
            f"{self.inner.name}|{voice}|{text}".encode("utf-8"), digest_size=12
        ).hexdigest()
        cached = Path(self.cache_dir) / f"{key}.wav"
        if cached.is_file():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached, out_path)
            return TTSResult(out_path, text, self.inner.name, voice, cached=True)

        result = self.inner.synthesize(text, out_path, voice)
        if result.ok and result.path is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(result.path, cached)
            except OSError:
                pass
        return result


def probe_backends(
    model_dir: Path = Path("voices/piper"), default_voice: str = ""
) -> list[TTSBackend]:
    return [
        PiperTTS(model_dir=model_dir, default_voice=default_voice or "en_US-lessac-medium"),
        CoquiTTS(),
        SayTTS(),
        EspeakTTS(),
    ]


def get_tts(
    backend: str = "auto",
    *,
    model_dir: Path = Path("voices/piper"),
    voice: str = "",
    cache_dir: Path | None = None,
) -> TTSBackend:
    """Pick a synthesiser. `auto` takes the best one actually installed."""
    backend = (backend or "auto").lower()
    candidates = probe_backends(model_dir, voice)
    by_name = {b.name: b for b in candidates}

    chosen: TTSBackend
    if backend in ("null", "off", "none", "text"):
        chosen = NullTTS()
    elif backend == "auto":
        chosen = next((b for b in candidates if b.available()), NullTTS())
    else:
        picked = by_name.get(backend)
        if picked is None:
            raise ValueError(f"unknown tts backend: {backend!r}")
        if not picked.available():
            print(f"  [voice] {backend} is not installed; falling back")
            chosen = next((b for b in candidates if b.available()), NullTTS())
        else:
            chosen = picked

    if cache_dir is not None and not isinstance(chosen, NullTTS):
        return CachingTTS(chosen, Path(cache_dir))
    return chosen


def temp_wav(prefix: str = "srgm") -> Path:
    handle, name = tempfile.mkstemp(prefix=f"{prefix}-", suffix=".wav")
    os.close(handle)
    return Path(name)
