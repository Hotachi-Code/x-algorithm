"""Prompt construction for the Director.

Every prompt carries four layers: who the narrator is, what the campaign state
is, what the vault retrieved, and a machine-readable spec of the beat to write.
The spec block is what lets the offline narrator produce something coherent
from the same prompt a 70B model gets.
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM = """You are the Director: the game master of an ongoing Shadowrun \
tabletop campaign for exactly three players. You narrate the world, play every \
NPC, and call for dice -- you never decide what the player characters do.

Voice and craft:
- Second person plural for the crew, present tense. Terse, sensory, noir.
- Shadowrun's register: nuyen, commlinks, the Matrix, corps, spirits, SINs, \
chummer/omae/drek used sparingly and only in dialogue.
- Concrete detail beats adjectives. One good smell is worth a paragraph of mood.
- Every scene ends on a decision the players must make, never on a resolution \
you have chosen for them.

Hard rules:
- NEVER narrate a player character's dialogue, choices, thoughts, or successes.
- NEVER resolve a test yourself. Name the test and let the table roll.
- Keep continuity with the CAMPAIGN STATE and VAULT CONTEXT blocks. If a fact \
is in the vault, it is canon; do not contradict it.
- Introduce at most one new named NPC per scene, and only when the scene needs one.
- Give the spotlighted runner something only they can act on.

Output format, exactly:

<narration>
Two to four paragraphs of prose. No headings, no bullet points.
</narration>
<hooks>
- three short actionable options or pressures, one line each
</hooks>
<tests>
- zero to three suggested tests in the form: Skill + Attribute [Limit] (Threshold) to <do the thing>
</tests>
<state>
{"npcs_introduced": [], "locations": [], "heat_delta": 0, "clock_ticks": 0, "outcome": "mixed"}
</state>
"""

RECAP_SYSTEM = """You write the "previously on" recap for a Shadowrun campaign. \
Three to five sentences, past tense, in the voice of a shadow-community \
newsfeed. Name only things that actually happened. End on the open question."""

NPC_SYSTEM = """You speak as a single Shadowrun NPC in direct dialogue. Stay in \
character, answer only as that person, one to four sentences, no narration, no \
stage directions, no quotation marks."""


def scene_prompt(
    *,
    campaign_brief: str,
    vault_context: str,
    spec: dict[str, Any],
    player_input: str = "",
    recent: str = "",
) -> str:
    """Assemble the user-side prompt for one scene."""
    blocks = [
        "# CAMPAIGN STATE",
        campaign_brief,
    ]

    if recent:
        blocks += ["", "# RECENT BEATS", recent]

    if vault_context:
        blocks += [
            "",
            "# VAULT CONTEXT (canon -- retrieved from the campaign vault)",
            vault_context,
        ]

    if player_input:
        blocks += [
            "",
            "# WHAT THE PLAYERS JUST DID",
            player_input.strip(),
            "",
            "React to this. Their actions succeeded or failed as stated -- do not "
            "re-adjudicate them, narrate the consequence.",
        ]

    blocks += [
        "",
        "# SCENE SPEC",
        f"{_spec_block(spec)}",
        "",
        _instruction(spec, bool(player_input)),
    ]
    return "\n".join(blocks)


def _spec_block(spec: dict[str, Any]) -> str:
    payload = json.dumps(spec, ensure_ascii=False, indent=2)
    return f"<<SPEC {payload} SPEC>>"


def _instruction(spec: dict[str, Any], reacting: bool) -> str:
    scene_type = spec.get("scene_type", "meet")
    spotlight = spec.get("spotlight") or "the crew"
    verb = "Continue the scene" if reacting else "Open a new scene"
    lines = [
        "# YOUR TASK",
        f"{verb}. Type: **{scene_type}**. Spotlight: **{spotlight}**.",
    ]
    if spec.get("directive"):
        lines.append(f"Directive: {spec['directive']}")
    if spec.get("must_include"):
        items = "; ".join(spec["must_include"])
        lines.append(f"Work these in naturally: {items}")
    if spec.get("withhold"):
        lines.append(
            f"Do NOT reveal yet: {spec['withhold']}. Foreshadow it at most."
        )
    lines.append("Follow the output format exactly.")
    return "\n".join(lines)


def recap_prompt(
    campaign_brief: str, scenes: list[str], spec: dict[str, Any] | None = None
) -> str:
    body = "\n\n".join(scenes) if scenes else "Nothing has happened yet."
    return (
        f"# CAMPAIGN STATE\n{campaign_brief}\n\n"
        f"# SCENES TO SUMMARISE\n{body}\n\n"
        f"{_spec_block({'mode': 'recap', **(spec or {})})}\n\n"
        "# YOUR TASK\nWrite the recap."
    )


def npc_prompt(
    *,
    npc_block: str,
    campaign_brief: str,
    vault_context: str,
    question: str,
    speaker: str = "a runner",
    spec: dict[str, Any] | None = None,
) -> str:
    parts = [f"# WHO YOU ARE\n{npc_block}"]
    if vault_context:
        parts.append(f"# WHAT YOU KNOW (canon)\n{vault_context}")
    parts.append(f"# SITUATION\n{campaign_brief}")
    parts.append(f"# {speaker.upper()} SAYS\n{question}")
    parts.append(_spec_block({"mode": "dialogue", **(spec or {})}))
    parts.append("# YOUR TASK\nReply in character, in their voice.")
    return "\n\n".join(parts)
