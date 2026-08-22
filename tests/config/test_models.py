from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mafia_game.config import (
    AppConfig,
    GameOptions,
    PlayerConfig,
    Provider,
    ReasoningEffort,
)


def test_loads_toml_config(tmp_path: Path) -> None:
    config_path = tmp_path / "game.toml"
    players = "\n".join(
        (
            f'[[players]]\nname = "P{seat}"\nprovider = "fake"\nmodel = "m"\n'
            'communication_style = "Speak plainly."'
        )
        for seat in range(1, 7)
    )
    config_path.write_text(
        f"[game]\nseed = 9\ndiscussion_rounds = 2\n{players}\n", encoding="utf-8"
    )

    config = AppConfig.load(config_path)

    assert config.game.seed == 9
    assert config.game.discussion_rounds == 2
    assert len(config.players) == 6
    assert config.players[0].provider is Provider.FAKE
    assert config.players[0].communication_style == "Speak plainly."


@pytest.mark.parametrize("discussion_rounds", [0, 11])
def test_rejects_out_of_range_discussion_rounds(discussion_rounds: int) -> None:
    with pytest.raises(ValidationError):
        AppConfig(
            game={"discussion_rounds": discussion_rounds},
            players=[
                PlayerConfig(name=f"P{seat}", provider=Provider.FAKE, model="m")
                for seat in range(1, 7)
            ],
        )


@pytest.mark.parametrize("max_stalemate_days", [0, 21])
def test_rejects_out_of_range_stalemate_limit(max_stalemate_days: int) -> None:
    with pytest.raises(ValidationError):
        GameOptions(max_stalemate_days=max_stalemate_days)


def test_game_default_fills_missing_player_model_and_allows_override(tmp_path: Path) -> None:
    config_path = tmp_path / "game.toml"
    players = "\n".join(
        (
            f'[[players]]\nname = "P{seat}"\nprovider = "fake"'
            + ('\nmodel = "override-model"' if seat == 6 else "")
        )
        for seat in range(1, 7)
    )
    config_path.write_text(
        '[game]\ndefault_model = "shared-model"\n' + players + "\n",
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert [player.model for player in config.players[:5]] == ["shared-model"] * 5
    assert config.players[5].model == "override-model"


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_removed_product_login_provider_is_unknown(provider: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "players": [
                    {"name": f"P{seat}", "provider": provider, "model": "m"} for seat in range(1, 7)
                ]
            }
        )


def test_default_communication_style_fills_missing_players_and_allows_override(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "game.toml"
    players = "\n".join(
        (
            f'[[players]]\nname = "P{seat}"\nprovider = "fake"'
            + ('\ncommunication_style = "Speak like yourself."' if seat == 6 else "")
        )
        for seat in range(1, 7)
    )
    config_path.write_text(
        '[game]\ndefault_model = "fake-model"\n'
        'default_communication_style = "Speak with theatrical confidence."\n' + players + "\n",
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert [player.communication_style for player in config.players[:5]] == [
        "Speak with theatrical confidence."
    ] * 5
    assert config.players[5].communication_style == "Speak like yourself."


def test_rejects_blank_default_communication_style() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        AppConfig(
            game={"default_communication_style": "   "},
            players=[
                PlayerConfig(name=f"P{seat}", provider=Provider.FAKE, model="m")
                for seat in range(1, 7)
            ],
        )


def test_default_model_also_applies_to_openai_compatible_players(tmp_path: Path) -> None:
    config_path = tmp_path / "game.toml"
    players = "\n".join(
        (
            f'[[players]]\nname = "P{seat}"\nprovider = "openai_compatible"\n'
            'base_url = "http://localhost:1234/v1"'
        )
        for seat in range(1, 7)
    )
    config_path.write_text(
        '[game]\ndefault_model = "shared-api-model"\n'
        'default_reasoning_effort = "medium"\n' + players + "\n",
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert all(player.model == "shared-api-model" for player in config.players)
    assert all(player.reasoning_effort is ReasoningEffort.MEDIUM for player in config.players)


def test_openai_compatible_defaults_fill_endpoint_and_auth(tmp_path: Path) -> None:
    config_path = tmp_path / "game.toml"
    players = "\n".join(
        f'[[players]]\nname = "P{seat}"\nprovider = "openai_compatible"' for seat in range(1, 7)
    )
    config_path.write_text(
        '[game]\ndefault_model = "shared-api-model"\n'
        'default_base_url = "https://models.example/v1/"\n'
        'default_api_key_env = "MODELS_API_KEY"\n' + players + "\n",
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert all(player.base_url == "https://models.example/v1" for player in config.players)
    assert all(player.api_key_env == "MODELS_API_KEY" for player in config.players)


def test_openai_compatible_defaults_fill_and_mask_inline_auth(tmp_path: Path) -> None:
    config_path = tmp_path / "game.toml"
    inline_secret = "shared-inline-secret"
    players = "\n".join(
        f'[[players]]\nname = "P{seat}"\nprovider = "openai_compatible"' for seat in range(1, 7)
    )
    config_path.write_text(
        '[game]\ndefault_model = "shared-api-model"\n'
        'default_base_url = "https://models.example/v1"\n'
        f'default_api_key = "{inline_secret}"\n' + players + "\n",
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert config.game.default_api_key is not None
    assert config.game.default_api_key.get_secret_value() == inline_secret
    assert all(player.api_key is not None for player in config.players)
    assert {
        player.api_key.get_secret_value() for player in config.players if player.api_key is not None
    } == {inline_secret}
    assert all(player.api_key_env is None for player in config.players)
    assert inline_secret not in config.model_dump_json()


def test_player_auth_can_override_inline_auth_default(tmp_path: Path) -> None:
    config_path = tmp_path / "game.toml"
    other_players = "\n".join(
        f'[[players]]\nname = "P{seat}"\nprovider = "openai_compatible"' for seat in range(2, 7)
    )
    config_path.write_text(
        '[game]\ndefault_model = "shared-api-model"\n'
        'default_base_url = "https://models.example/v1"\n'
        'default_api_key = "shared-inline-secret"\n'
        '[[players]]\nname = "P1"\nprovider = "openai_compatible"\n'
        'api_key_env = "LOCAL_API_KEY"\n' + other_players + "\n",
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert config.players[0].api_key is None
    assert config.players[0].api_key_env == "LOCAL_API_KEY"
    assert all(player.api_key is not None for player in config.players[1:])


def test_rejects_multiple_default_auth_sources() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        GameOptions(
            default_api_key="shared-inline-secret",
            default_api_key_env="MODELS_API_KEY",
        )


def test_openai_compatible_player_can_override_endpoint_and_auth_defaults(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "game.toml"
    other_players = "\n".join(
        f'[[players]]\nname = "P{seat}"\nprovider = "openai_compatible"' for seat in range(2, 7)
    )
    config_path.write_text(
        '[game]\ndefault_model = "shared-api-model"\n'
        'default_base_url = "https://models.example/v1"\n'
        'default_api_key_env = "MODELS_API_KEY"\n'
        '[[players]]\nname = "P1"\nprovider = "openai_compatible"\n'
        'base_url = "http://localhost:1234/v1"\napi_key_env = "LOCAL_API_KEY"\n'
        + other_players
        + "\n",
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert config.players[0].base_url == "http://localhost:1234/v1"
    assert config.players[0].api_key_env == "LOCAL_API_KEY"
    assert all(player.base_url == "https://models.example/v1" for player in config.players[1:])
    assert all(player.api_key_env == "MODELS_API_KEY" for player in config.players[1:])


def test_rejects_reasoning_effort_for_unsupported_provider() -> None:
    with pytest.raises(ValidationError, match="only supported for openai_compatible"):
        PlayerConfig(
            name="Fake",
            provider=Provider.FAKE,
            model="m",
            reasoning_effort=ReasoningEffort.LOW,
        )


def test_accepts_reasoning_effort_for_openai_compatible_provider() -> None:
    player = PlayerConfig(
        name="Reasoner",
        provider=Provider.OPENAI_COMPATIBLE,
        model="reasoning-model",
        reasoning_effort=ReasoningEffort.MINIMAL,
        base_url="https://models.example/v1",
    )

    assert player.reasoning_effort is ReasoningEffort.MINIMAL


def test_rejects_blank_communication_style() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        PlayerConfig(
            name="Player",
            provider=Provider.FAKE,
            model="m",
            communication_style="   ",
        )


def test_human_player_defaults_to_terminal_model() -> None:
    player = PlayerConfig(name="Nikita", provider=Provider.HUMAN)

    assert player.model == "terminal"


def test_game_defaults_do_not_apply_model_or_style_to_human_player(tmp_path: Path) -> None:
    config_path = tmp_path / "game.toml"
    automated_players = "\n".join(
        f'[[players]]\nname = "P{seat}"\nprovider = "fake"' for seat in range(2, 7)
    )
    config_path.write_text(
        '[game]\ndefault_model = "shared-model"\n'
        'default_communication_style = "Speak like a robot."\n'
        '[[players]]\nname = "Nikita"\nprovider = "human"\n' + automated_players + "\n",
        encoding="utf-8",
    )

    config = AppConfig.load(config_path)

    assert config.players[0].model == "terminal"
    assert config.players[0].communication_style is None
    assert all(player.model == "shared-model" for player in config.players[1:])


def test_rejects_more_than_one_human_player() -> None:
    with pytest.raises(ValidationError, match="at most one human"):
        AppConfig(
            players=[
                PlayerConfig(name="Human 1", provider=Provider.HUMAN),
                PlayerConfig(name="Human 2", provider=Provider.HUMAN),
                *[
                    PlayerConfig(name=f"P{seat}", provider=Provider.FAKE, model="m")
                    for seat in range(3, 7)
                ],
            ]
        )


def test_accepts_openai_compatible_player() -> None:
    player = PlayerConfig(
        name="HTTP",
        provider=Provider.OPENAI_COMPATIBLE,
        model="some-model",
        base_url="http://localhost:1234/v1/",
    )

    assert player.base_url == "http://localhost:1234/v1"
    assert player.api_key is None
    assert player.api_key_env is None


def test_accepts_and_masks_inline_api_key() -> None:
    player = PlayerConfig(
        name="HTTP",
        provider=Provider.OPENAI_COMPATIBLE,
        model="some-model",
        base_url="https://models.example/v1",
        api_key="literal-secret",
    )

    assert player.api_key is not None
    assert player.api_key.get_secret_value() == "literal-secret"
    serialized = player.model_dump_json()
    assert "literal-secret" not in serialized
    assert "**********" in serialized


def test_rejects_multiple_openai_compatible_auth_sources() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        PlayerConfig(
            name="HTTP",
            provider=Provider.OPENAI_COMPATIBLE,
            model="some-model",
            base_url="https://models.example/v1",
            api_key="literal-secret",
            api_key_env="MODELS_API_KEY",
        )


def test_inline_api_key_is_hidden_in_validation_errors() -> None:
    inline_secret = "never-print-this-secret"

    with pytest.raises(ValidationError) as captured:
        PlayerConfig(
            name=" ",
            provider=Provider.OPENAI_COMPATIBLE,
            model="some-model",
            base_url="https://models.example/v1",
            api_key=inline_secret,
        )

    assert inline_secret not in str(captured.value)


def test_requires_openai_compatible_base_url() -> None:
    with pytest.raises(ValidationError, match="base_url is required"):
        PlayerConfig(
            name="HTTP",
            provider=Provider.OPENAI_COMPATIBLE,
            model="some-model",
        )


def test_rejects_api_options_for_other_providers() -> None:
    with pytest.raises(ValidationError, match="only supported for openai_compatible"):
        PlayerConfig(
            name="Fake",
            provider=Provider.FAKE,
            model="some-model",
            base_url="https://models.example/v1",
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "models.example/v1",
        "ftp://models.example/v1",
        "https://secret@models.example/v1",
        "https://models.example/v1?api_key=secret",
    ],
)
def test_rejects_unsafe_openai_compatible_base_url(base_url: str) -> None:
    with pytest.raises(ValidationError):
        PlayerConfig(
            name="HTTP",
            provider=Provider.OPENAI_COMPATIBLE,
            model="some-model",
            base_url=base_url,
        )


def test_accepts_random_seed_sentinel() -> None:
    config = AppConfig(
        game={"seed": -1},
        players=[
            PlayerConfig(name=f"P{seat}", provider=Provider.FAKE, model="m") for seat in range(1, 7)
        ],
    )

    assert config.game.seed == -1


def test_rejects_seed_below_random_sentinel() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to -1"):
        AppConfig(
            game={"seed": -2},
            players=[
                PlayerConfig(name=f"P{seat}", provider=Provider.FAKE, model="m")
                for seat in range(1, 7)
            ],
        )


@pytest.mark.parametrize("count", [0, 5, 13])
def test_rejects_invalid_roster_size(count: int) -> None:
    with pytest.raises(ValidationError, match="6–12"):
        AppConfig(
            players=[
                PlayerConfig(name=f"P{seat}", provider=Provider.FAKE, model="m")
                for seat in range(count)
            ]
        )


def test_rejects_duplicate_names() -> None:
    with pytest.raises(ValidationError, match="unique"):
        AppConfig(
            players=[PlayerConfig(name="Same", provider=Provider.FAKE, model="m") for _ in range(6)]
        )
