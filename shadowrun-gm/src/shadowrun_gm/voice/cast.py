"""Casting: which voice belongs to whom.

The cast sheet lives at `voices/cast.json` and is plain JSON so you can edit it
between sessions. Anyone not cast gets assigned a voice deterministically from
their name -- so the same NPC always sounds the same, even before you have
bothered to cast them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

NARRATOR = "__narrator__"


@dataclass
class VoiceProfile:
    """One voice: a TTS voice, an optional RVC model, and a pitch shift."""

    tts_voice: str = ""
    rvc_model: str = ""
    pitch: int = 0
    rate: int = 0
    note: str = ""

    def key(self) -> str:
        return f"{self.tts_voice}|{self.rvc_model}|{self.pitch}"


@dataclass
class VoiceCast:
    path: Path
    profiles: dict[str, VoiceProfile] = field(default_factory=dict)
    pool: list[VoiceProfile] = field(default_factory=list)
    narrator: VoiceProfile = field(default_factory=VoiceProfile)

    # -- io -----------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "VoiceCast":
        path = Path(path)
        cast = cls(path=path)
        if not path.is_file():
            return cast
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cast

        cast.narrator = VoiceProfile(**data.get("narrator", {}))
        cast.pool = [VoiceProfile(**p) for p in data.get("pool", [])]
        cast.profiles = {
            name: VoiceProfile(**profile)
            for name, profile in data.get("cast", {}).items()
        }
        return cast

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_comment": (
                "Voice casting. 'narrator' is the storyteller; 'cast' maps NPC "
                "names to voices; 'pool' is drawn from for anyone uncast. "
                "tts_voice is a Piper model name or an XTTS speaker sample; "
                "rvc_model is a .pth in your RVC model directory."
            ),
            "narrator": asdict(self.narrator),
            "cast": {name: asdict(p) for name, p in sorted(self.profiles.items())},
            "pool": [asdict(p) for p in self.pool],
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return self.path

    # -- casting ------------------------------------------------------------
    def assign(self, name: str, profile: VoiceProfile) -> None:
        self.profiles[name] = profile

    def voice_for(self, name: str | None) -> VoiceProfile:
        """Look up a voice, auto-casting from the pool if there is no entry."""
        if not name or name == NARRATOR:
            return self.narrator
        existing = self.profiles.get(name)
        if existing is not None:
            return existing
        if not self.pool:
            return self.narrator
        # Stable hash -> the same NPC keeps the same voice across sessions.
        digest = hashlib.blake2b(name.encode("utf-8"), digest_size=4).digest()
        index = int.from_bytes(digest, "big") % len(self.pool)
        chosen = self.pool[index]
        self.profiles[name] = chosen
        return chosen

    def is_cast(self, name: str) -> bool:
        return name in self.profiles

    def describe(self) -> str:
        lines = [f"narrator: {self.narrator.tts_voice or '(default)'}"]
        if self.narrator.rvc_model:
            lines[0] += f" -> rvc:{self.narrator.rvc_model}"
        for name, profile in sorted(self.profiles.items()):
            entry = f"  {name}: {profile.tts_voice or '(default)'}"
            if profile.rvc_model:
                entry += f" -> rvc:{profile.rvc_model}"
            if profile.pitch:
                entry += f" (pitch {profile.pitch:+d})"
            lines.append(entry)
        if not self.profiles:
            lines.append("  (nobody cast yet)")
        return "\n".join(lines)


def default_cast(path: Path, rvc_models: list[str] | None = None) -> VoiceCast:
    """Build a starter cast sheet, using whatever RVC models are on disk."""
    cast = VoiceCast(path=Path(path))
    cast.narrator = VoiceProfile(
        tts_voice="en_US-lessac-medium",
        note="The storyteller. Low and unhurried works best.",
    )
    models = rvc_models or []
    if models:
        cast.pool = [
            VoiceProfile(rvc_model=model, pitch=0, note=f"auto-pooled from {model}")
            for model in models
        ]
    else:
        cast.pool = [
            VoiceProfile(tts_voice="en_US-ryan-high", pitch=0, note="placeholder"),
            VoiceProfile(tts_voice="en_GB-alba-medium", pitch=0, note="placeholder"),
            VoiceProfile(tts_voice="en_US-amy-medium", pitch=0, note="placeholder"),
        ]
    return cast
