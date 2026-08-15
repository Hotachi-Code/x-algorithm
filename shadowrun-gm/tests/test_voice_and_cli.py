from pathlib import Path

from shadowrun_gm.cli import main
from shadowrun_gm.voice.cast import NARRATOR, VoiceCast, VoiceProfile, default_cast
from shadowrun_gm.voice.narrator import (
    Narrator,
    clean_for_speech,
    group_sentences,
    split_speakers,
)
from shadowrun_gm.voice.rvc import RVCConverter
from shadowrun_gm.voice.tts import NullTTS, get_tts


def test_clean_for_speech_strips_markdown_and_wikilinks():
    text = "**Rain.** The [[Crime Mall]] is [closed](http://x) and `dead`.\n\n## Hooks\n- go home"
    spoken = clean_for_speech(text)
    assert "**" not in spoken
    assert "[[" not in spoken
    assert "Crime Mall" in spoken
    assert "closed" in spoken
    assert "#" not in spoken


def test_wikilink_alias_speaks_the_alias():
    assert clean_for_speech("[[Mox|the fixer]] waits.") == "the fixer waits."


def test_split_speakers_separates_dialogue():
    text = "Rain on the window.\nHollow: You are late.\nShe does not look up."
    segments = split_speakers(text)
    speakers = [s.speaker for s in segments]
    assert "Hollow" in speakers
    assert segments[0].speaker == NARRATOR


def test_split_speakers_ignores_structural_labels():
    segments = split_speakers("Hooks: three of them")
    assert segments[0].speaker == NARRATOR


def test_group_sentences_packs_to_budget():
    text = " ".join(f"Sentence number {i} goes here." for i in range(20))
    groups = group_sentences(text, max_chars=100)
    assert len(groups) > 1
    assert all(len(g) <= 140 for g in groups)


def test_cast_assigns_stable_voices(tmp_path: Path):
    cast = VoiceCast(path=tmp_path / "cast.json")
    cast.pool = [VoiceProfile(tts_voice=f"v{i}") for i in range(4)]
    first = cast.voice_for("Kestrel")
    second = cast.voice_for("Kestrel")
    assert first is second
    assert cast.is_cast("Kestrel")


def test_cast_roundtrips_to_disk(tmp_path: Path):
    path = tmp_path / "cast.json"
    cast = default_cast(path, rvc_models=["hollow", "johnson"])
    cast.assign("Mox", VoiceProfile(tts_voice="en_US-amy-medium", rvc_model="mox", pitch=-2))
    cast.save()

    loaded = VoiceCast.load(path)
    assert loaded.profiles["Mox"].rvc_model == "mox"
    assert loaded.profiles["Mox"].pitch == -2
    assert len(loaded.pool) == 2


def test_null_tts_writes_a_transcript(tmp_path: Path):
    backend = NullTTS()
    result = backend.synthesize("The rain has not stopped.", tmp_path / "line.wav")
    assert result.ok is False
    assert (tmp_path / "line.txt").read_text(encoding="utf-8").startswith("The rain")


def test_get_tts_null_backend_is_selectable():
    assert get_tts("null").name == "null"


def test_rvc_degrades_when_no_model_exists(tmp_path: Path):
    source = tmp_path / "in.wav"
    source.write_bytes(b"RIFF")
    converter = RVCConverter(model_dir=tmp_path / "models", enabled=True)
    result = converter.convert(source, tmp_path / "out.wav", "nobody")
    assert result.converted is False
    assert result.path == source  # the unconverted audio still plays


def test_narrator_speak_is_safe_without_a_synthesiser(tmp_path: Path):
    narrator = Narrator.build(
        tts_backend="null",
        cast_path=tmp_path / "cast.json",
        out_dir=tmp_path / "audio",
    )
    assert narrator.speak("Testing one two.") is False
    assert narrator.speak_passage("Testing one two.") == 0
    assert "TTS backend" in narrator.status()


def test_cli_init_then_scene_then_status(tmp_path: Path, capsys):
    root = str(tmp_path)
    assert main(["--root", root, "init", "--campaign", "Test Sprawl"]) == 0
    assert (tmp_path / "srgm.toml").is_file()
    assert (tmp_path / "vault" / "00-System" / "House Rules.md").is_file()
    assert (tmp_path / "vault" / ".obsidian" / "graph.json").is_file()
    assert (tmp_path / "voices" / "cast.json").is_file()

    assert main([
        "--root", root, "--llm-backend", "local", "--embed-backend", "hashing",
        "-Q", "scene", "-n", "2",
    ]) == 0
    out = capsys.readouterr().out
    assert "===" in out

    assert main(["--root", root, "status"]) == 0
    assert "Test Sprawl" in capsys.readouterr().out


def test_cli_roll_is_deterministic_with_a_seed(capsys):
    assert main(["roll", "--seed", "42", "12", "3"]) == 0
    first = capsys.readouterr().out
    assert main(["roll", "--seed", "42", "12", "3"]) == 0
    assert capsys.readouterr().out == first


def test_cli_graph_reports_stats(tmp_path: Path, capsys):
    root = str(tmp_path)
    main(["--root", root, "init", "--campaign", "Graph Test"])
    assert main(["--root", root, "graph"]) == 0
    out = capsys.readouterr().out
    assert "Vault graph" in out
    assert "notes" in out
