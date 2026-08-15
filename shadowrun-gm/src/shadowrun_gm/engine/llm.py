"""Narration backends.

Three of them, same interface:

  local      no network, no dependencies -- a template narrator driven by the
             scene spec. Not as good as a model, but it plays, and it means
             the project works the second it is cloned.
  ollama     any local model you have pulled. The recommended setup.
  anthropic  the Claude API, if you would rather rent the prose.

The Director always embeds a machine-readable spec block in the prompt, so the
local backend can render a scene from structure while the model backends just
read it as unusually well-organised context.
"""

from __future__ import annotations

import json
import os
import random
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

SPEC_OPEN = "<<SPEC"
SPEC_RE = re.compile(r"<<SPEC\s*(\{.*?\})\s*SPEC>>", re.DOTALL)


class LLM(Protocol):
    name: str

    def complete(self, system: str, prompt: str, *, max_tokens: int = 900) -> str: ...


def _cap(text: str) -> str:
    """Capitalise the first letter and leave the rest of the string alone.

    `str.capitalize()` lowercases everything after the first character, which
    turns "Salish" into "salish" -- unusable for prose full of proper nouns.
    """
    text = text.strip()
    return text[:1].upper() + text[1:] if text else text


def extract_spec(prompt: str) -> dict[str, Any]:
    match = SPEC_RE.search(prompt)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


# --------------------------------------------------------------------------
# local template narrator
# --------------------------------------------------------------------------

_OPENERS = {
    "meet": [
        "The meet is set for {location}. {weather}.",
        "{location}. You get there early, because you always get there early.",
        "They picked {location}. That choice tells you something already.",
    ],
    "legwork": [
        "Legwork means hours, and the hours start at {location}.",
        "You start pulling threads at {location}. {sound}.",
        "Nothing about {location} wants to give up an answer for free.",
    ],
    "infiltration": [
        "{location}. Two ways in, and one of them is a bad idea.",
        "The approach to {location} is quiet. Too quiet is a cliche until it happens to you.",
        "You are inside the perimeter of {location} and the clock is already running.",
    ],
    "matrix": [
        "The host resolves around you, sculpted and humming.",
        "You go hot. The grid unfolds, and something on the far side notices.",
        "Icons drift past like fish. One of them is not a fish.",
    ],
    "social": [
        "{location}, and everyone in the room is performing something.",
        "The conversation starts three moves before anyone says a word.",
        "You are being read as fast as you are reading them.",
    ],
    "combat": [
        "It goes loud. It always goes loud eventually.",
        "The first shot is somebody else's. After that it is everyone's.",
        "Cover, angles, seconds. The world compresses down to those three things.",
    ],
    "chase": [
        "You are moving before you have finished deciding to move.",
        "{location} blurs. Behind you, something is keeping pace.",
        "The route is bad and the alternative is worse.",
    ],
    "downtime": [
        "For a day and a half, nothing tries to kill anyone. It is unsettling.",
        "The safehouse smells of {smell}. Nobody has opened a window.",
        "Downtime. The kind where everyone pretends to rest.",
    ],
    "twist": [
        "And then the shape of the job changes.",
        "The detail that did not fit finally fits, and you wish it did not.",
        "Everything you were told was true. It was just not all of it.",
    ],
    "fallout": [
        "After. The part nobody plans for.",
        "The job is over. What the job did is not.",
        "You count what you still have. It takes less time than it used to.",
    ],
}

_DIALOGUE_OPENERS = {
    "hostile": [
        "You have got a lot of nerve opening with that.",
        "I should have walked when I saw your faces.",
        "Say the next part carefully.",
    ],
    "wary": [
        "That depends entirely on who is asking, and you have not told me.",
        "I have heard the question before. I did not like the people asking it then either.",
        "Careful. That is the kind of question that gets logged.",
    ],
    "transactional": [
        "Everything is available. The question is what you are paying with.",
        "I deal in specifics, chummer. Bring me one.",
        "You are asking for free what I usually charge for.",
    ],
    "friendly": [
        "For you? Fine. But this is a favour, and I count favours.",
        "You are lucky I like you. Most people get the short version.",
        "All right. Sit down, this takes a minute.",
    ],
    "loyal": [
        "You did not have to ask twice. You never do.",
        "Whatever you need. You know that.",
        "I have been waiting for you to ask me that.",
    ],
}

_HOOK_TEMPLATES = [
    "Someone here knows more than they are saying: {npc}.",
    "There is a way to make this easier, and it costs something: {stake}.",
    "The clock is running -- {clock}.",
    "{faction} has an interest in how this ends.",
    "A door nobody mentioned. It is unlocked.",
]

_TEST_TEMPLATES = {
    "meet": ["Negotiation + Charisma (3) to read the real offer under the stated one"],
    "legwork": ["Etiquette + Charisma (2) to get the door opened",
                "Perception + Intuition (3) to notice the watcher"],
    "infiltration": ["Sneaking + Agility (3) past the patrol",
                     "Locksmith + Agility [Physical] (4, 1 minute) on the maglock"],
    "matrix": ["Hacking + Logic [Attack] vs. Firewall + Willpower to place a mark",
               "Computer + Logic [Data Processing] (3) for Matrix Perception"],
    "social": ["Con + Charisma opposed by Willpower + Intuition"],
    "combat": ["Reaction + Intuition for Initiative, +1D6 if wired",
               "Agility + Firearms [Accuracy] vs. Reaction + Intuition"],
    "chase": ["Reaction + Intuition (3) to keep the lead",
              "Running + Strength (2) over the gap"],
    "downtime": ["Logic + Skill extended test for the long project"],
    "twist": ["Intuition + Logic (3) to piece it together before it lands"],
    "fallout": ["Composure (Charisma + Willpower) (3) to hold it together"],
}


@dataclass
class LocalLLM:
    """A deterministic-ish narrator built from the scene spec and tables.

    It writes serviceable noir. It will not surprise you the way a real model
    does, but it keeps the table moving when the GPU is busy or absent.
    """

    name: str = "local"
    rng: random.Random = field(default_factory=random.Random)

    def complete(self, system: str, prompt: str, *, max_tokens: int = 900) -> str:
        spec = extract_spec(prompt)
        if not spec:
            return self._generic(prompt)

        mode = str(spec.get("mode", "scene"))
        if mode == "dialogue":
            return self._dialogue(spec)
        if mode == "recap":
            return self._recap(spec)

        scene_type = str(spec.get("scene_type", "meet"))
        atmosphere = dict(spec.get("atmosphere", {}) or {})
        location = spec.get("location") or "an address you were given twenty minutes ago"

        template = self.rng.choice(_OPENERS.get(scene_type, _OPENERS["meet"]))
        used = {key for key in ("weather", "sound", "smell") if f"{{{key}}}" in template}
        opener = _cap(
            template.format(
                location=location,
                weather=_cap(atmosphere.get("weather", "the rain has not stopped in days")),
                sound=_cap(atmosphere.get("sound", "the block hums")),
                smell=_cap(atmosphere.get("smell", "wet concrete")),
            )
        )
        paragraphs = [opener]

        # Do not repeat whatever the opener already spent.
        sensory = [
            value
            for key, value in atmosphere.items()
            if value and key in ("detail", "crowd", "sound", "smell") and key not in used
        ]
        if sensory:
            paragraphs.append(_cap(self._sentence_join(sensory[:3])) + ".")

        thread = spec.get("thread") or {}
        # The premise is restated only when the thread is new to the table.
        if thread.get("summary") and int(spec.get("thread_beat", 0)) == 0:
            paragraphs.append(_cap(str(thread["summary"])))

        present = spec.get("present") or []
        if present:
            who = " and ".join(present[:2])
            verb = "are" if len(present[:2]) > 1 else "is"
            paragraphs.append(
                f"{who} {verb} already here, and the room arranges itself around that."
            )

        spotlight = spec.get("spotlight")
        if spotlight:
            paragraphs.append(
                f"{spotlight} clocks it first -- whatever *it* turns out to be."
            )

        hooks = self._hooks(spec)
        tests = _TEST_TEMPLATES.get(scene_type, [])

        parts = ["\n\n".join(paragraphs), "", "**What do you do?**"]
        if hooks:
            parts += ["", "HOOKS:"] + [f"- {_cap(h)}" for h in hooks]
        if tests:
            parts += ["", "TESTS:"] + [f"- {t}" for t in tests]
        return "\n".join(parts)

    def _hooks(self, spec: dict[str, Any]) -> list[str]:
        thread = spec.get("thread") or {}
        npcs = spec.get("present") or thread.get("npcs") or []
        factions = thread.get("factions") or []
        pool = []
        for template in _HOOK_TEMPLATES:
            try:
                pool.append(
                    template.format(
                        npc=npcs[0] if npcs else "the one who keeps checking the door",
                        stake=thread.get("stakes") or "a favour you cannot afford",
                        clock=(thread.get("clock") or "the deadline"),
                        faction=factions[0] if factions else "somebody with a budget",
                    )
                )
            except (IndexError, KeyError):
                continue
        self.rng.shuffle(pool)
        return pool[:3]

    @staticmethod
    def _sentence_join(items: list[str]) -> str:
        items = [i.rstrip(".") for i in items if i]
        if len(items) <= 1:
            return items[0] if items else ""
        return ", ".join(items[:-1]) + f", and {items[-1]}"

    def _dialogue(self, spec: dict[str, Any]) -> str:
        """An in-character line, built from the NPC's attitude and agenda."""
        name = str(spec.get("name", "They"))
        attitude = str(spec.get("attitude", "transactional"))
        wants = str(spec.get("wants", "")).strip()
        role = str(spec.get("role", "contact"))

        opener = self.rng.choice(_DIALOGUE_OPENERS.get(attitude, _DIALOGUE_OPENERS["transactional"]))
        lines = [opener]
        if wants:
            lines.append(
                self.rng.choice(
                    [
                        f"What I want is simple enough: {wants}. Help with that and we can talk about the rest.",
                        f"You want to know what I am doing here? {_cap(wants)}. That is the whole of it.",
                        f"Everything I do comes back to one thing -- {wants}. Keep that in mind.",
                    ]
                )
            )
        lines.append(
            self.rng.choice(
                [
                    "Ask me something specific and I will give you something specific.",
                    f"I am a {role}, omae. I trade. That is the arrangement.",
                    "That is as much as I am giving away standing up.",
                ]
            )
        )
        return " ".join(lines)

    def _recap(self, spec: dict[str, Any]) -> str:
        beats = [str(b) for b in (spec.get("beats") or []) if str(b).strip()]
        campaign = str(spec.get("campaign", "the crew"))
        open_threads = [str(t) for t in (spec.get("open_threads") or [])]

        if not beats:
            return f"{campaign} has not started yet. The city is waiting."

        body = ". Then ".join(_cap(b) for b in beats[-3:])
        tail = (
            f" Still open: {'; '.join(open_threads[:3])}."
            if open_threads
            else " Nothing is settled."
        )
        return f"Previously on {campaign}: {body}.{tail} What happens next is up to the crew."

    def _generic(self, prompt: str) -> str:
        return (
            "The scene holds for a moment, waiting on you.\n\n"
            "**What do you do?**\n\n"
            f"_(local narrator: no scene spec found in the prompt)_\n"
        )


# --------------------------------------------------------------------------
# HTTP backends (stdlib only)
# --------------------------------------------------------------------------

def _post_json(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass
class OllamaLLM:
    model: str = "llama3.1:8b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.85
    name: str = field(default="ollama", init=False)

    def complete(self, system: str, prompt: str, *, max_tokens: int = 900) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": max_tokens,
            },
        }
        try:
            data = _post_json(f"{self.base_url.rstrip('/')}/api/generate", payload, {})
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise RuntimeError(f"ollama request failed: {exc}") from exc
        return str(data.get("response", "")).strip()

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self.base_url.rstrip('/')}/api/tags", timeout=3
            ) as response:
                return response.status == 200
        except Exception:
            return False


@dataclass
class AnthropicLLM:
    model: str = "claude-sonnet-4-5"
    api_key: str = ""
    temperature: float = 0.9
    name: str = field(default="anthropic", init=False)

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def complete(self, system: str, prompt: str, *, max_tokens: int = 900) -> str:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            data = _post_json("https://api.anthropic.com/v1/messages", payload, headers)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise RuntimeError(f"anthropic request failed: {exc}") from exc
        blocks = data.get("content", [])
        return "".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        ).strip()


@dataclass
class FallbackLLM:
    """Wraps a primary backend and drops to local narration if it fails.

    A dead Ollama process should degrade the prose, not end the session.
    """

    primary: LLM
    backup: LLM
    name: str = field(default="", init=False)
    warned: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.name = f"{self.primary.name}->{self.backup.name}"

    def complete(self, system: str, prompt: str, *, max_tokens: int = 900) -> str:
        try:
            text = self.primary.complete(system, prompt, max_tokens=max_tokens)
            if text.strip():
                return text
        except Exception as exc:
            if not self.warned:
                print(f"  [narrator] {self.primary.name} unavailable ({exc}); using local")
                self.warned = True
        return self.backup.complete(system, prompt, max_tokens=max_tokens)


def get_llm(
    backend: str = "local",
    *,
    model: str = "",
    base_url: str = "http://localhost:11434",
    temperature: float = 0.85,
    seed: int | None = None,
) -> LLM:
    local = LocalLLM(rng=random.Random(seed))
    backend = (backend or "local").lower()

    if backend in ("local", "none", "offline"):
        return local
    if backend == "ollama":
        return FallbackLLM(
            OllamaLLM(
                model=model or "llama3.1:8b",
                base_url=base_url,
                temperature=temperature,
            ),
            local,
        )
    if backend in ("anthropic", "claude"):
        try:
            return FallbackLLM(
                AnthropicLLM(
                    model=model or "claude-sonnet-4-5", temperature=temperature
                ),
                local,
            )
        except RuntimeError as exc:
            print(f"  [narrator] {exc}; using local narrator")
            return local
    raise ValueError(f"unknown llm backend: {backend!r}")
