from pathlib import Path

import pytest

from mafia_game.cli.common import BUNDLED_CONFIG_PATH
from mafia_game.config import AppConfig, Provider, ReasoningEffort

OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"

FAST_OPENROUTER_MODELS = [
    "ibm-granite/granite-4.1-8b",
    "google/gemini-2.5-flash-lite",
    "openai/gpt-4.1-nano",
    "deepseek/deepseek-v4-flash",
    "qwen/qwen3.6-flash",
    "nex-agi/nex-n2-mini",
]
THEMED_OPENROUTER_MODELS = [
    *FAST_OPENROUTER_MODELS,
    "mistralai/mistral-small-2603",
    "meta-llama/llama-4-scout",
]
THEMED_OPENROUTER_STYLES = {
    "openrouter_but_everyone_angry.example.toml": (
        "angry, annoyed and passive-aggressive communication only"
    ),
    "openrouter_but_everyone_is_paranoid.example.toml": (
        "Treat every pause, adjective, vote, and coincidence as part of an enormous "
        "conspiracy. Connect everything with complete confidence."
    ),
    "openrouter_but_everyone_is_corporate.example.toml": (
        "Discuss suspicion as employee performance. Use feedback, KPIs, action items, and "
        "improvement plans while deciding who must be terminated."
    ),
    "openrouter_but_everyone_is_inspirational.example.toml": (
        "Turn betrayal, murder, and elimination into inspirational lessons about growth, "
        "resilience, and becoming your best self."
    ),
    "openrouter_but_everyone_is_exhausted.example.toml": (
        "Sound profoundly tired and irritated that murder keeps delaying bedtime. Solve the "
        "game efficiently while complaining about the effort."
    ),
    "openrouter_but_everyone_is_the_protagonist.example.toml": (
        "Treat the game as your personal story. Interpret every death as character development "
        "and every disagreement as another player's betrayal arc."
    ),
}

EXAMPLES = [
    ("openai.toml", {Provider.OPENAI_COMPATIBLE}, 0),
    ("openrouter.example.toml", {Provider.OPENAI_COMPATIBLE}, 0),
    ("openrouter_fast.example.toml", {Provider.OPENAI_COMPATIBLE}, 0),
    ("ollama.toml", {Provider.OPENAI_COMPATIBLE}, 0),
    ("mixed_local.example.toml", {Provider.OPENAI_COMPATIBLE}, 0),
    ("sonnet_vs_gpt.example.toml", {Provider.OPENAI_COMPATIBLE}, 0),
    ("human.example.toml", {Provider.OPENAI_COMPATIBLE, Provider.HUMAN}, 1),
    ("openrouter_one_of_each.example.toml", {Provider.OPENAI_COMPATIBLE}, 0),
]


@pytest.mark.parametrize(("filename", "providers", "human_count"), EXAMPLES)
def test_example_roster_is_valid_and_safe(
    filename: str,
    providers: set[Provider],
    human_count: int,
) -> None:
    path = Path(__file__).parents[2] / "examples" / filename

    config = AppConfig.load(path)
    autonomous_players = [
        player for player in config.players if player.provider is not Provider.HUMAN
    ]

    assert {player.provider for player in config.players} == providers
    assert len(config.players) == 6
    assert config.game.discussion_rounds == 2
    assert config.game.speech_word_limit == 100
    assert sum(player.provider is Provider.HUMAN for player in config.players) == human_count
    assert len({player.communication_style for player in autonomous_players}) == len(
        autonomous_players
    )
    assert all(player.communication_style for player in autonomous_players)
    assert all(player.api_key is None for player in config.players)


def test_mixed_local_roster_uses_the_requested_models() -> None:
    path = Path(__file__).parents[2] / "examples" / "mixed_local.example.toml"

    config = AppConfig.load(path)

    assert [(player.provider, player.model) for player in config.players] == [
        (Provider.OPENAI_COMPATIBLE, "anthropic/claude-sonnet-5"),
        (Provider.OPENAI_COMPATIBLE, "anthropic/claude-opus-5"),
        (Provider.OPENAI_COMPATIBLE, "openai/gpt-5.6-luna"),
        (Provider.OPENAI_COMPATIBLE, "openai/gpt-5.6-luna"),
        (Provider.OPENAI_COMPATIBLE, "openai/gpt-5.3-codex-spark"),
        (Provider.OPENAI_COMPATIBLE, "qwen3:1.7b"),
    ]
    assert config.players[-1].base_url == "http://localhost:11434/v1"
    assert config.players[-1].api_key is None
    assert config.players[-1].api_key_env is None
    assert all(player.api_key is None for player in config.players[:-1])
    assert all(player.api_key_env == OPENROUTER_KEY_ENV for player in config.players[:-1])
    assert [player.reasoning_effort for player in config.players] == [
        ReasoningEffort.MEDIUM,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.MEDIUM,
        ReasoningEffort.HIGH,
        None,
    ]


def test_openrouter_roster_uses_one_endpoint_for_a_model_mix() -> None:
    path = Path(__file__).parents[2] / "examples" / "openrouter.example.toml"

    config = AppConfig.load(path)

    assert [player.model for player in config.players] == [
        "anthropic/claude-sonnet-5",
        "openai/gpt-5.6-luna",
        "google/gemini-3.7-flash",
        "deepseek/deepseek-v4-pro-0813",
        "qwen/qwen3.8-27b",
        "moonshotai/kimi-k2.5",
    ]
    assert {player.base_url for player in config.players} == {"https://openrouter.ai/api/v1"}
    assert config.game.default_api_key is None
    assert config.game.default_api_key_env == OPENROUTER_KEY_ENV
    assert {player.api_key_env for player in config.players} == {OPENROUTER_KEY_ENV}


def test_openrouter_fast_roster_uses_budget_models_without_forced_reasoning() -> None:
    path = Path(__file__).parents[2] / "examples" / "openrouter_fast.example.toml"

    config = AppConfig.load(path)

    assert [player.model for player in config.players] == FAST_OPENROUTER_MODELS
    assert {player.base_url for player in config.players} == {"https://openrouter.ai/api/v1"}
    assert all(player.reasoning_effort is None for player in config.players)
    assert config.game.default_api_key is None
    assert config.game.default_api_key_env == OPENROUTER_KEY_ENV


def test_human_roster_uses_openrouter_for_every_ai_seat() -> None:
    path = Path(__file__).parents[2] / "examples" / "human.example.toml"

    config = AppConfig.load(path)
    autonomous_players = [
        player for player in config.players if player.provider is not Provider.HUMAN
    ]

    assert [player.model for player in autonomous_players] == FAST_OPENROUTER_MODELS[:5]
    assert {player.base_url for player in autonomous_players} == {"https://openrouter.ai/api/v1"}
    assert config.game.default_api_key is None
    assert config.game.default_api_key_env == OPENROUTER_KEY_ENV


def test_bundled_default_is_the_budget_openrouter_roster() -> None:
    config = AppConfig.load(BUNDLED_CONFIG_PATH)
    example = AppConfig.load(
        Path(__file__).parents[2] / "examples" / "openrouter_fast.example.toml"
    )

    assert config == example
    assert [player.model for player in config.players] == FAST_OPENROUTER_MODELS
    assert {player.base_url for player in config.players} == {"https://openrouter.ai/api/v1"}
    assert {player.api_key_env for player in config.players} == {OPENROUTER_KEY_ENV}
    assert all(player.api_key is None for player in config.players)


@pytest.mark.parametrize(("filename", "style"), THEMED_OPENROUTER_STYLES.items())
def test_themed_openrouter_rosters_share_one_style_across_a_budget_cast(
    filename: str,
    style: str,
) -> None:
    path = Path(__file__).parents[2] / "examples" / filename

    config = AppConfig.load(path)

    assert [player.model for player in config.players] == THEMED_OPENROUTER_MODELS
    assert {player.provider for player in config.players} == {Provider.OPENAI_COMPATIBLE}
    assert {player.base_url for player in config.players} == {"https://openrouter.ai/api/v1"}
    assert config.game.discussion_rounds == 2
    assert config.game.speech_word_limit == 100
    assert config.game.default_communication_style == style
    assert {player.communication_style for player in config.players} == {style}
    assert all(player.reasoning_effort is None for player in config.players)
    assert config.game.default_api_key is None
    assert config.game.default_api_key_env == OPENROUTER_KEY_ENV
    assert {player.api_key_env for player in config.players} == {OPENROUTER_KEY_ENV}


def test_one_of_each_roster_takes_one_style_from_every_themed_table() -> None:
    path = Path(__file__).parents[2] / "examples" / "openrouter_one_of_each.example.toml"

    config = AppConfig.load(path)

    assert [player.model for player in config.players] == FAST_OPENROUTER_MODELS
    assert [player.communication_style for player in config.players] == list(
        THEMED_OPENROUTER_STYLES.values()
    )
    assert {player.base_url for player in config.players} == {"https://openrouter.ai/api/v1"}
    assert config.game.default_communication_style is None
    assert config.game.default_api_key_env == OPENROUTER_KEY_ENV


def test_sonnet_vs_gpt_roster_uses_two_hostile_model_camps() -> None:
    path = Path(__file__).parents[2] / "examples" / "sonnet_vs_gpt.example.toml"

    config = AppConfig.load(path)

    assert [(player.provider, player.model) for player in config.players] == [
        (Provider.OPENAI_COMPATIBLE, "anthropic/claude-sonnet-5"),
        (Provider.OPENAI_COMPATIBLE, "anthropic/claude-sonnet-5"),
        (Provider.OPENAI_COMPATIBLE, "anthropic/claude-sonnet-5"),
        (Provider.OPENAI_COMPATIBLE, "openai/gpt-5.6-luna"),
        (Provider.OPENAI_COMPATIBLE, "openai/gpt-5.6-luna"),
        (Provider.OPENAI_COMPATIBLE, "openai/gpt-5.6-luna"),
    ]
    assert {player.base_url for player in config.players} == {"https://openrouter.ai/api/v1"}
    assert all(player.reasoning_effort is ReasoningEffort.MEDIUM for player in config.players)
    styles = [(player.communication_style or "").casefold() for player in config.players]
    assert all("gpt" in style for style in styles[:3])
    assert all("sonnet" in style for style in styles[3:])
