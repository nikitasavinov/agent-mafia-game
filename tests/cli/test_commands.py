from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mafia_game.cli import app
from mafia_game.cli.common import BUNDLED_CONFIG_PATH, resolve_config_path, resolve_seed
from mafia_game.runs import RunDatabase


def test_cli_registers_launch_commands() -> None:
    runner = CliRunner()

    root_help = runner.invoke(app, ["--help"])
    assert root_help.exit_code == 0, root_help.output
    for command in ("doctor", "play", "replay", "runs", "stats"):
        assert command in root_help.output
    assert "simulate" not in root_help.output

    runs_help = runner.invoke(app, ["runs", "--help"])
    assert runs_help.exit_code == 0, runs_help.output
    for command in ("list", "search"):
        assert command in runs_help.output
    assert "export" not in runs_help.output
    assert "import" not in runs_help.output

    staged = runner.invoke(app, ["simulate", "--help"])
    assert staged.exit_code == 2


def write_fake_config(path: Path) -> None:
    players = "\n".join(
        f'[[players]]\nname = "P{seat}"\nprovider = "fake"\nmodel = "fake"' for seat in range(1, 7)
    )
    path.write_text(
        "[game]\nseed = 27\nturn_timeout_seconds = 1\n"
        "speech_word_limit = 30\ndefense_word_limit = 20\n"
        f"private_planning_word_limit = 20\n{players}\n",
        encoding="utf-8",
    )


def test_cli_play_and_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "game.toml"
    database = tmp_path / "mafia.db"
    style = "Argue like a deeply offended chess commentator."
    write_fake_config(config)
    runner = CliRunner()

    play = runner.invoke(
        app,
        [
            "play",
            str(config),
            "--database",
            str(database),
            "--communication-style",
            style,
        ],
    )
    assert play.exit_code == 0, play.output
    with RunDatabase(database) as run_database:
        records = run_database.list_runs()
        stored_config = run_database.load_metadata(records[0].game_id).config
    assert len(records) == 1
    assert {player.communication_style for player in stored_config.players} == {style}

    delays: list[float] = []
    monkeypatch.setattr("mafia_game.runs.replay.sleep", delays.append)
    replay = runner.invoke(
        app,
        [
            "replay",
            records[0].game_id,
            "--database",
            str(database),
            "--view",
            "public",
            "--delay",
            "0.25",
        ],
    )
    assert replay.exit_code == 0, replay.output
    assert "Mafia Replay" in replay.output
    assert "ROLES REVEALED" not in replay.output
    assert "Mafia team:" not in replay.output
    assert delays and set(delays) == {0.25}

    omniscient = runner.invoke(
        app,
        [
            "replay",
            records[0].game_id,
            "--database",
            str(database),
            "--view",
            "omniscient",
        ],
    )
    assert omniscient.exit_code == 0, omniscient.output
    assert "ROLES REVEALED" in omniscient.output
    assert "MAFIA" in omniscient.output
    assert "DETECTIVE" in omniscient.output

    listed = runner.invoke(app, ["runs", "list", "--database", str(database)])
    assert listed.exit_code == 0, listed.output
    assert records[0].game_id in listed.output

    searched = runner.invoke(app, ["runs", "search", "compare votes", "--database", str(database)])
    assert searched.exit_code == 0, searched.output
    assert records[0].game_id in searched.output


def test_random_seed_sentinel_resolves_and_fixed_seeds_do_not(monkeypatch) -> None:
    monkeypatch.setattr("mafia_game.cli.common.secrets.randbits", lambda _bits: 8675309)

    assert resolve_seed(None, -1) == 8675309
    assert resolve_seed(-1, 27) == 8675309
    assert resolve_seed(None, None) == 8675309
    assert resolve_seed(None, 0) == 0
    assert resolve_seed(42, -1) == 42


def test_config_resolution_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_root = tmp_path / "config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))

    explicit = tmp_path / "explicit.toml"
    configured = tmp_path / "configured.toml"
    monkeypatch.setenv("MAFIA_CONFIG", str(configured))
    assert resolve_config_path(explicit) == explicit
    assert resolve_config_path(None) == configured

    monkeypatch.delenv("MAFIA_CONFIG")
    project = tmp_path / "mafia.toml"
    project.touch()
    assert resolve_config_path(None) == project

    project.unlink()
    user = config_root / "mafia" / "config.toml"
    user.parent.mkdir(parents=True)
    user.touch()
    assert resolve_config_path(None) == user

    user.unlink()
    assert resolve_config_path(None) == BUNDLED_CONFIG_PATH


def test_failed_play_preflight_points_to_doctor(tmp_path: Path) -> None:
    config = tmp_path / "missing-key.toml"
    players = "\n".join(
        f'[[players]]\nname = "P{seat}"\nprovider = "openai_compatible"' for seat in range(1, 7)
    )
    config.write_text(
        '[game]\ndefault_model = "test-model"\n'
        'default_base_url = "https://models.example/v1"\n'
        'default_api_key_env = "MISSING_TEST_API_KEY"\n' + players + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["play", str(config)])

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert "For detailed diagnostics" in output
    assert "mafia doctor" in output
    assert "missing-key.toml --live" in output


def test_play_rejects_blank_communication_style(tmp_path: Path) -> None:
    config = tmp_path / "game.toml"
    write_fake_config(config)

    result = CliRunner().invoke(
        app,
        ["play", str(config), "--communication-style", "   "],
    )

    assert result.exit_code == 2
    assert "Invalid communication style" in result.output
    assert "must not be blank" in result.output
