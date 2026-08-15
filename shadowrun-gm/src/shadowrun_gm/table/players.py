"""Pregenerated runners.

Three of them, because that is the table size this project is built for. They
cover the three jobs a Shadowrun crew cannot do without: someone who opens
doors, someone who opens systems, and someone who opens people. Swap them
freely -- these are a starting point, not a constraint.
"""

from __future__ import annotations

from ..engine.state import Runner

PREGENS: list[dict] = [
    {
        "name": "Ratchet",
        "archetype": "street samurai",
        "metatype": "ork",
        "concept": "Ex-Knight Errant, fired for the wrong kind of honesty",
        "attributes": {
            "body": 6, "agility": 6, "reaction": 5, "strength": 6,
            "willpower": 4, "logic": 2, "intuition": 4, "charisma": 2,
        },
        "skills": {
            "firearms": 6, "close combat": 4, "athletics": 4, "perception": 4,
            "intimidation": 3, "first aid": 2, "sneaking": 2,
        },
        "gear": [
            "Ares Predator VI with smartlink",
            "armor jacket (12)",
            "wired reflexes 1",
            "cyberarm (left, obvious)",
            "medkit rating 4",
            "fake SIN rating 3 (Marcus Dolan)",
        ],
        "contacts": ["Hollow"],
        "edge": 3,
        "nuyen": 4200,
    },
    {
        "name": "Static",
        "archetype": "decker",
        "metatype": "human",
        "concept": "Grew up in the Redmond squats, learned the Matrix before reading",
        "attributes": {
            "body": 3, "agility": 4, "reaction": 4, "strength": 2,
            "willpower": 5, "logic": 6, "intuition": 5, "charisma": 3,
        },
        "skills": {
            "hacking": 6, "computer": 5, "electronic warfare": 4, "software": 3,
            "perception": 3, "sneaking": 3, "hardware": 4,
        },
        "gear": [
            "Erika MCD-6 cyberdeck",
            "datajack",
            "cybereyes rating 3 (vision enhancement, flare compensation)",
            "armor vest (9)",
            "autopicker rating 4",
            "fake SIN rating 4 (Wen Li)",
        ],
        "contacts": ["Verdigris"],
        "edge": 4,
        "nuyen": 2800,
    },
    {
        "name": "Sundown",
        "archetype": "face",
        "metatype": "elf",
        "concept": "Corporate PR until the day she read her own press release",
        "attributes": {
            "body": 3, "agility": 4, "reaction": 4, "strength": 2,
            "willpower": 5, "logic": 4, "intuition": 5, "charisma": 7,
        },
        "skills": {
            "con": 6, "negotiation": 6, "etiquette": 5, "perception": 4,
            "leadership": 4, "pistols": 3, "impersonation": 4,
        },
        "gear": [
            "Colt America L36, concealed",
            "actioneer business clothes (8)",
            "tailored pheromones rating 2",
            "Transys Avalon commlink",
            "three fake SINs, rating 4, all with matching licences",
        ],
        "contacts": ["Mox"],
        "edge": 5,
        "nuyen": 6100,
    },
]


def make_runner(spec: dict, player: str = "") -> Runner:
    data = dict(spec)
    return Runner(
        name=data["name"],
        player=player,
        archetype=data.get("archetype", "street samurai"),
        metatype=data.get("metatype", "human"),
        concept=data.get("concept", ""),
        attributes=dict(data.get("attributes", {})),
        skills=dict(data.get("skills", {})),
        gear=list(data.get("gear", [])),
        contacts=list(data.get("contacts", [])),
        edge=int(data.get("edge", 3)),
        nuyen=int(data.get("nuyen", 0)),
    )


def pregen_runners(players: list[str] | None = None) -> list[Runner]:
    """Three ready-to-play runners, optionally assigned to named players."""
    players = players or []
    out = []
    for index, spec in enumerate(PREGENS):
        out.append(make_runner(spec, players[index] if index < len(players) else ""))
    return out


def parse_runner_specs(text: str) -> list[Runner]:
    """Parse `Name:archetype:metatype,Name:archetype` into runners.

    Anything omitted falls back to the matching pregen slot, so
    `--runners "Kite,Nine,Bellwether"` just renames the three pregens.
    """
    runners: list[Runner] = []
    entries = [e.strip() for e in text.split(",") if e.strip()]
    for index, entry in enumerate(entries):
        parts = [p.strip() for p in entry.split(":")]
        base = dict(PREGENS[index % len(PREGENS)])
        base["name"] = parts[0]
        if len(parts) > 1 and parts[1]:
            base["archetype"] = parts[1]
        if len(parts) > 2 and parts[2]:
            base["metatype"] = parts[2]
        base["contacts"] = []
        runners.append(make_runner(base, parts[3] if len(parts) > 3 else ""))
    return runners
