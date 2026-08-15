"""Configuration: one dataclass, loaded from srgm.toml plus SRGM_* env vars.

Precedence is explicit argument > environment variable > config file > default,
so a table can override anything for one session without editing files.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAMES = ("srgm.toml", ".srgm.toml")


def _project_root(start: Path | None = None) -> Path:
    """Walk up from `start` looking for a config file or a vault/ directory."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if any((candidate / name).is_file() for name in DEFAULT_CONFIG_NAMES):
            return candidate
        if (candidate / "vault").is_dir() and (candidate / "corpus").is_dir():
            return candidate
    return here


@dataclass
class Config:
    """Everything the engine needs to know about this table's setup."""

    root: Path = field(default_factory=_project_root)

    # --- storage -----------------------------------------------------------
    vault_dir: Path = Path("vault")
    corpus_dir: Path = Path("corpus")
    state_dir: Path = Path(".srgm")

    # --- campaign ----------------------------------------------------------
    campaign: str = "Neon Requiem"
    edition: str = "5e"  # 5e | 6e  -- affects Edge and limit handling
    players: int = 3
    heat_decay_per_scene: float = 0.15

    # --- narration ---------------------------------------------------------
    llm_backend: str = "local"  # local | ollama | anthropic
    llm_model: str = "llama3.1:8b"
    llm_base_url: str = "http://localhost:11434"
    llm_temperature: float = 0.85
    llm_max_tokens: int = 900

    # --- retrieval ---------------------------------------------------------
    embed_backend: str = "auto"  # auto | sentence-transformers | hashing | none
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_k: int = 12
    graph_hops: int = 1

    # --- voice -------------------------------------------------------------
    tts_backend: str = "auto"  # auto | piper | coqui | espeak | say | null
    tts_voice: str = "en_US-lessac-medium"
    piper_model_dir: Path = Path("voices/piper")
    rvc_enabled: bool = False
    rvc_cli: str = "rvc"
    rvc_model_dir: Path = Path("voices/rvc")
    audio_out_dir: Path = Path(".srgm/audio")
    speak_by_default: bool = True

    # --- misc --------------------------------------------------------------
    seed: int | None = None

    # -- derived paths ------------------------------------------------------
    def path(self, attr: str) -> Path:
        """Resolve a configured relative path against the project root."""
        value = getattr(self, attr)
        p = Path(value)
        return p if p.is_absolute() else (self.root / p)

    @property
    def vault(self) -> Path:
        return self.path("vault_dir")

    @property
    def corpus(self) -> Path:
        return self.path("corpus_dir")

    @property
    def state(self) -> Path:
        return self.path("state_dir")

    @property
    def audio(self) -> Path:
        return self.path("audio_out_dir")

    # -- loading ------------------------------------------------------------
    @classmethod
    def load(cls, root: Path | None = None, **overrides: Any) -> "Config":
        root = (root or _project_root()).resolve()
        data: dict[str, Any] = {}

        for name in DEFAULT_CONFIG_NAMES:
            cfg_file = root / name
            if cfg_file.is_file():
                with cfg_file.open("rb") as fh:
                    parsed = tomllib.load(fh)
                # Accept both flat keys and [srgm] / [tool.srgm] tables.
                data.update(parsed.get("srgm", parsed))
                data.update(parsed.get("tool", {}).get("srgm", {}))
                break

        typed = {f.name: f for f in fields(cls)}
        for key, value in _env_overrides().items():
            if key in typed:
                data[key] = value

        for key, value in overrides.items():
            if value is not None:
                data[key] = value

        clean: dict[str, Any] = {"root": root}
        for key, value in data.items():
            spec = typed.get(key)
            if spec is None:
                continue
            clean[key] = _coerce(value, spec.type)
        return cls(**clean)


def _env_overrides() -> dict[str, str]:
    prefix = "SRGM_"
    return {
        key[len(prefix):].lower(): value
        for key, value in os.environ.items()
        if key.startswith(prefix) and value != ""
    }


def _coerce(value: Any, target: Any) -> Any:
    """Coerce TOML/env strings into the annotated field type."""
    text = str(target)
    if "Path" in text:
        return Path(value)
    if "bool" in text:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if "int" in text and "None" in text:
        return None if value in ("", "none", None) else int(value)
    if "int" in text:
        return int(value)
    if "float" in text:
        return float(value)
    return value
