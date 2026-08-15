"""Command line entry point: `srgm`."""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .engine.director import Director
from .engine.state import Campaign
from .ingest.pipeline import ingest_corpus, reindex_vault
from .retrieval.search import Retriever
from .rules import DicePool, extended_test, opposed_test, roll
from .scaffold import scaffold_project
from .table.players import parse_runner_specs, pregen_runners
from .table.session import Session
from .vaultio import Vault, VaultGraph
from .voice.cast import default_cast
from .voice.narrator import Narrator


def _config(args: argparse.Namespace) -> Config:
    overrides = {}
    for key in ("llm_backend", "llm_model", "tts_backend", "embed_backend", "seed"):
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = value
    if getattr(args, "quiet_voice", False):
        overrides["speak_by_default"] = False
    return Config.load(Path(getattr(args, "root", ".")).resolve(), **overrides)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    created = scaffold_project(root, campaign_name=args.campaign)
    config = Config.load(root)

    runners = (
        parse_runner_specs(args.runners)
        if args.runners
        else pregen_runners(args.players.split(",") if args.players else None)
    )

    campaign = Campaign.load(config.state / "campaign.json") or Campaign(
        name=args.campaign
    )
    campaign.name = args.campaign
    director = Director.bootstrap(config, campaign=campaign)
    director.start_campaign(runners)
    director.sync_to_vault()

    cast_path = root / "voices" / "cast.json"
    if not cast_path.is_file():
        default_cast(cast_path).save()
        created.append(str(cast_path.relative_to(root)))

    print(f"Initialised {args.campaign} in {root}")
    for path in created:
        print(f"  + {path}")
    print(f"\nCrew: {', '.join(r.name for r in runners)}")
    print(f"Open threads: {len(campaign.open_threads())}")
    print(
        "\nNext:\n"
        "  1. drop books and notes you own into corpus/\n"
        "  2. srgm ingest\n"
        "  3. srgm play\n"
    )
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    config = _config(args)
    print(f"Ingesting {config.corpus} -> {config.vault}")
    report = ingest_corpus(
        config.corpus,
        config.vault,
        config.state / "index.sqlite",
        embed_backend=config.embed_backend,
        embed_model=config.embed_model,
        create_stubs=not args.no_stubs,
        progress=print if args.verbose else None,
    )
    print("\nIngest complete:")
    print(report.summary())
    if report.files_seen == 0:
        print(
            f"\nNothing found in {config.corpus}.\n"
            "Add .pdf / .epub / .md / .txt / .html files you own, then re-run.\n"
            "See docs/CORPUS.md for what to put there."
        )
    if report.proposals:
        print(f"\n{len(report.proposals)} new entity candidates -> "
              f"vault note 'Entity Proposals'")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    config = _config(args)
    count = reindex_vault(
        config.vault,
        config.state / "index.sqlite",
        embed_backend=config.embed_backend,
        embed_model=config.embed_model,
        progress=print if args.verbose else None,
    )
    print(f"Indexed {count} chunks from {config.vault}")
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    config = _config(args)
    if not (config.state / "campaign.json").exists():
        print("No campaign here yet. Run `srgm init` first.")
        return 1
    Session.build(config).run()
    return 0


def cmd_scene(args: argparse.Namespace) -> int:
    config = _config(args)
    director = Director.bootstrap(config)
    if not director.campaign.runners:
        print("No campaign here yet. Run `srgm init` first.")
        return 1

    for _ in range(max(1, args.count)):
        result = director.next_scene(
            player_input=args.input or "",
            scene_type=args.type,
            thread_id=args.thread or "",
        )
        print(f"\n=== [{result.scene.number}] {result.scene.type.upper()} "
              f"-- {result.scene.location} ===\n")
        print(result.render())
        if result.events:
            print("\n-- behind the screen --")
            for event in result.events:
                print(f"   * {event}")

    director.sync_to_vault()
    if args.speak:
        narrator = _narrator(config)
        narrator.speak_passage(director.campaign.scenes[-1].narration)
    return 0


def cmd_recap(args: argparse.Namespace) -> int:
    config = _config(args)
    director = Director.bootstrap(config)
    text = director.write_recap()
    print(text)
    if args.speak:
        _narrator(config).speak_passage(text)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config = _config(args)
    index = config.state / "index.sqlite"
    if not index.exists():
        print("No index yet. Run `srgm ingest` or `srgm reindex` first.")
        return 1
    retriever = Retriever.open(
        config.vault,
        index,
        embed_backend=config.embed_backend,
        embed_model=config.embed_model,
        graph_hops=config.graph_hops,
    )
    hits = retriever.search(args.query, k=args.k)
    if not hits:
        print("no matches")
        return 0
    for hit in hits:
        print(f"\n[{hit.score:5.3f}] {hit.chunk.label()}  "
              f"(lex {hit.lexical:.2f} vec {hit.vector:.2f} graph {hit.graph:.2f})")
        print("  " + hit.chunk.text[:400].replace("\n", "\n  "))
    return 0


def cmd_roll(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    expression = " ".join(args.expression).lower()
    if " vs " in expression:
        left, right = expression.split(" vs ", 1)
        print(opposed_test(int(left), int(right), rng=rng).describe())
        return 0
    if " x " in expression:
        pool, threshold = expression.split(" x ", 1)
        result = extended_test(int(pool), int(threshold), rng=rng)
        print(result.describe())
        return 0

    parts = expression.split()
    pool = int(parts[0])
    threshold = int(parts[1]) if len(parts) > 1 else 0
    result = roll(DicePool(pool, args.limit), edge=args.edge, rng=rng)
    print(result.dice)
    line = result.describe()
    if threshold:
        line += f" -- {'SUCCESS' if result.hits >= threshold else 'FAILURE'}"
    print(line)
    return 0


def cmd_voices(args: argparse.Namespace) -> int:
    config = _config(args)
    narrator = _narrator(config)
    print(narrator.status())
    if args.test:
        line = args.test if isinstance(args.test, str) else (
            "The rain has not stopped in nine days. Neither has the job."
        )
        print(f"\nSpeaking: {line}")
        if not narrator.speak(line):
            print("  (no audio produced -- check the backend and player above)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _config(args)
    campaign = Campaign.load(config.state / "campaign.json")
    print(f"shadowrun-gm {__version__}")
    print(f"  root      {config.root}")
    print(f"  vault     {config.vault}  ({_count_md(config.vault)} notes)")
    print(f"  corpus    {config.corpus}")
    print(f"  narrator  {config.llm_backend} ({config.llm_model})")
    print(f"  voice     {config.tts_backend}, rvc={'on' if config.rvc_enabled else 'off'}")
    index = config.state / "index.sqlite"
    print(f"  index     {'present' if index.exists() else 'not built'}")
    if campaign is None:
        print("\n  no campaign yet -- run `srgm init`")
        return 0
    print(f"\n  {campaign.status_line()}")
    for runner in campaign.runners:
        print(f"    {runner.summary()}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    config = _config(args)
    director = Director.bootstrap(config)
    count = director.sync_to_vault()
    print(f"Wrote {count} notes to {config.vault}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    config = _config(args)
    graph = VaultGraph.build(Vault(config.vault))
    stats = graph.stats()
    print("Vault graph")
    for key, value in stats.items():
        print(f"  {key:<12} {value}")
    print("\nMost connected notes:")
    for title, degree in graph.hubs(12):
        print(f"  {degree:>4}  {title}")
    broken = graph.broken_links()
    if broken:
        print(f"\nPromised but not written ({len(broken)}):")
        for target, sources in broken[:15]:
            print(f"  {target}  <- {', '.join(sources[:3])}")
    return 0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _narrator(config: Config) -> Narrator:
    return Narrator.build(
        tts_backend=config.tts_backend,
        piper_model_dir=config.path("piper_model_dir"),
        tts_voice=config.tts_voice,
        cast_path=config.root / "voices" / "cast.json",
        out_dir=config.audio,
        rvc_enabled=config.rvc_enabled,
        rvc_cli=config.rvc_cli,
        rvc_model_dir=config.path("rvc_model_dir"),
    )


def _count_md(path: Path) -> int:
    return len(list(path.rglob("*.md"))) if path.is_dir() else 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="srgm",
        description="A never-ending Shadowrun campaign for three players, "
                    "backed by an Obsidian vault and a local voice.",
    )
    parser.add_argument("--version", action="version", version=f"shadowrun-gm {__version__}")
    parser.add_argument("--root", default=".", help="project root (default: cwd)")
    parser.add_argument("--llm-backend", dest="llm_backend",
                        choices=["local", "ollama", "anthropic"])
    parser.add_argument("--llm-model", dest="llm_model")
    parser.add_argument("--tts-backend", dest="tts_backend",
                        choices=["auto", "piper", "coqui", "espeak", "say", "null"])
    parser.add_argument("--embed-backend", dest="embed_backend",
                        choices=["auto", "sentence-transformers", "hashing", "none"])
    parser.add_argument("--seed", type=int, help="deterministic run")
    parser.add_argument("-Q", "--quiet-voice", action="store_true",
                        help="do not speak, even if a synthesiser is available")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold a project and start a campaign")
    p.add_argument("--campaign", default="Neon Requiem")
    p.add_argument("--runners", help="Name:archetype:metatype,... (overrides pregens)")
    p.add_argument("--players", help="player names, comma separated")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="turn corpus/ into linked vault notes")
    p.add_argument("--no-stubs", action="store_true",
                   help="do not auto-create notes for discovered entities")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("reindex", help="rebuild the search index from the vault")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("play", help="run the interactive table")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("scene", help="generate scenes without the interactive loop")
    p.add_argument("--type", help="scene type")
    p.add_argument("--thread", help="thread id or title to focus")
    p.add_argument("--input", help="what the players just did")
    p.add_argument("-n", "--count", type=int, default=1)
    p.add_argument("--speak", action="store_true")
    p.set_defaults(func=cmd_scene)

    p = sub.add_parser("recap", help="write the 'previously on'")
    p.add_argument("--speak", action="store_true")
    p.set_defaults(func=cmd_recap)

    p = sub.add_parser("search", help="query the vault index")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=8)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("roll", help="roll Shadowrun dice")
    p.add_argument("expression", nargs="+", help="'12 3' | '9 vs 7' | '10 x 8'")
    p.add_argument("--edge", action="store_true")
    p.add_argument("--limit", type=int)
    # SUPPRESS so that `srgm --seed 1 roll ...` is not clobbered by the default.
    p.add_argument("--seed", type=int, default=argparse.SUPPRESS)
    p.set_defaults(func=cmd_roll)

    p = sub.add_parser("voices", help="show and test the voice pipeline")
    p.add_argument("--test", nargs="?", const=True, default=False,
                   help="speak a test line")
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("status", help="show project and campaign state")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("sync", help="rewrite vault notes from campaign state")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("graph", help="inspect the vault link graph")
    p.set_defaults(func=cmd_graph)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
