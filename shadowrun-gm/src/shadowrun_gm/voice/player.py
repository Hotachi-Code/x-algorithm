"""Audio playback, using whatever player the machine already has."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Ordered by how quietly they behave when told to play one file and exit.
PLAYERS: tuple[tuple[str, list[str]], ...] = (
    ("paplay", []),
    ("aplay", ["-q"]),
    ("afplay", []),
    ("play", ["-q"]),                                  # sox
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
    ("mpv", ["--no-video", "--really-quiet"]),
    ("cvlc", ["--play-and-exit", "--intf", "dummy"]),
)


def playback_command(path: Path) -> list[str] | None:
    for binary, args in PLAYERS:
        if shutil.which(binary):
            return [binary, *args, str(path)]
    return None


def play_audio(path: Path | None, *, blocking: bool = True) -> bool:
    """Play a file. Returns False if there is nothing to play it with."""
    if path is None or not Path(path).is_file():
        return False
    cmd = playback_command(Path(path))
    if cmd is None:
        return False
    try:
        if blocking:
            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
            )
        else:
            subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def available_player() -> str:
    for binary, _ in PLAYERS:
        if shutil.which(binary):
            return binary
    return ""
