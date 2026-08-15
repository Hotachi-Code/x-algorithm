"""RVC voice conversion: turn the synthesiser's output into a character.

This is the second half of the voice pipeline. TTS gives you *words*; RVC
gives you *someone*. Point `rvc_model_dir` at your .pth models and the cast
sheet maps each NPC to one.

Several RVC CLIs exist with incompatible flags, so the command is a template
string you can override in `srgm.toml`:

    rvc_cli = "rvc infer --model {model} --input {input} --output {output} --pitch {pitch}"

Placeholders: {model} {input} {output} {pitch} {index}
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TEMPLATE = (
    "{binary} infer --model {model} --input {input} --output {output} --pitch {pitch}"
)


@dataclass
class RVCResult:
    path: Path | None
    model: str = ""
    converted: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None and self.path.is_file()


@dataclass
class RVCConverter:
    """Wraps whatever RVC command line you have installed."""

    model_dir: Path = Path("voices/rvc")
    command: str = DEFAULT_TEMPLATE
    binary: str = "rvc"
    enabled: bool = True
    default_pitch: int = 0
    timeout: int = 600
    _missing_warned: set[str] = field(default_factory=set, init=False, repr=False)

    # -- discovery ----------------------------------------------------------
    def available(self) -> bool:
        if not self.enabled:
            return False
        return shutil.which(self._binary_name()) is not None

    def _binary_name(self) -> str:
        # The template may itself start with the binary; honour whichever is set.
        if "{binary}" in self.command:
            return self.binary
        try:
            return shlex.split(self.command)[0]
        except (ValueError, IndexError):
            return self.binary

    def models(self) -> list[Path]:
        directory = Path(self.model_dir)
        if not directory.is_dir():
            return []
        return sorted(
            p for p in directory.rglob("*") if p.suffix.lower() in (".pth", ".onnx")
        )

    def resolve_model(self, name: str) -> Path | None:
        if not name:
            return None
        candidate = Path(name)
        if candidate.is_file():
            return candidate
        directory = Path(self.model_dir)
        for suffix in (".pth", ".onnx", ""):
            path = directory / f"{name}{suffix}"
            if path.is_file():
                return path
        for model in self.models():
            if model.stem.lower() == name.lower():
                return model
        return None

    def resolve_index(self, model: Path) -> Path | None:
        """RVC feature-index files usually sit beside the model."""
        for candidate in (
            model.with_suffix(".index"),
            model.parent / f"{model.stem}.index",
        ):
            if candidate.is_file():
                return candidate
        indexes = sorted(model.parent.glob("*.index"))
        return indexes[0] if indexes else None

    # -- conversion ---------------------------------------------------------
    def convert(
        self,
        source: Path,
        out_path: Path,
        model_name: str,
        *,
        pitch: int | None = None,
    ) -> RVCResult:
        """Run one conversion. Failures degrade to the unconverted audio."""
        if not self.enabled:
            return RVCResult(source, converted=False)
        if not source.is_file():
            return RVCResult(None, error=f"source audio missing: {source}")

        model = self.resolve_model(model_name)
        if model is None:
            if model_name and model_name not in self._missing_warned:
                self._missing_warned.add(model_name)
                print(f"  [voice] no RVC model named {model_name!r} in {self.model_dir}")
            return RVCResult(source, converted=False)

        if not self.available():
            return RVCResult(source, converted=False, error="rvc binary not found")

        index = self.resolve_index(model)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = self.command.format(
            binary=self.binary,
            model=str(model),
            input=str(source),
            output=str(out_path),
            pitch=str(self.default_pitch if pitch is None else pitch),
            index=str(index) if index else "",
        )
        try:
            proc = subprocess.run(
                shlex.split(rendered),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError:
            return RVCResult(source, converted=False, error="rvc binary not found")
        except subprocess.TimeoutExpired:
            return RVCResult(source, converted=False, error="rvc timed out")

        if proc.returncode != 0 or not out_path.is_file():
            err = proc.stderr.decode("utf-8", "replace")[-500:]
            return RVCResult(source, model=model.stem, converted=False, error=err)
        return RVCResult(out_path, model=model.stem, converted=True)
