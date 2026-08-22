"""Roster and provider configuration, validated on load.

A roster is rejected at parse time rather than mid-game: unknown keys, blank
names, credentials on providers that do not take them, and out-of-range player
counts all fail here. Inline API keys are held as `SecretStr` so they do not
leak into logs or stored config snapshots.
"""

from __future__ import annotations

import re
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


class Provider(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    HUMAN = "human"
    FAKE = "fake"


class ReasoningEffort(StrEnum):
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


ENVIRONMENT_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HUMAN_MODEL = "terminal"


def _is_provider(value: Any, *providers: Provider) -> bool:
    """Match a not-yet-parsed `provider` field, which may be an enum or its string."""
    return any(value == provider or value == provider.value for provider in providers)


def _stripped(value: str) -> str:
    """Normalize a required text field, rejecting whitespace-only input."""
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


def _stripped_or_none(value: str | None) -> str | None:
    """Normalize an optional text field: absent stays absent, blank is an error."""
    return None if value is None else _stripped(value)


def _normalized_base_url(value: str | None) -> str | None:
    """Normalize an endpoint URL, rejecting anything that could smuggle in state.

    Credentials, query strings, and fragments are refused rather than silently
    dropped: a roster that carries them is not the roster the author meant, and
    an embedded secret would end up in a stored config snapshot.
    """
    if value is None:
        return None
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("must not contain a query string or fragment")
    return value


def _normalized_variable_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not ENVIRONMENT_VARIABLE.fullmatch(value):
        raise ValueError("must be a valid environment variable name")
    return value


def _normalized_secret(value: SecretStr | None) -> SecretStr | None:
    if value is None:
        return None
    return SecretStr(_stripped(value.get_secret_value()))


class GameOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    seed: int | None = Field(default=None, ge=-1)
    default_model: str | None = Field(default=None, max_length=200)
    default_reasoning_effort: ReasoningEffort | None = None
    default_communication_style: str | None = Field(default=None, max_length=500)
    default_base_url: str | None = Field(default=None, max_length=2_048)
    default_api_key: SecretStr | None = None
    default_api_key_env: str | None = Field(default=None, max_length=128)
    max_concurrency: int = Field(default=4, ge=1, le=12)
    turn_timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    discussion_rounds: int = Field(default=3, ge=1, le=10)
    speech_word_limit: int = Field(default=220, ge=20, le=1_000)
    defense_word_limit: int = Field(default=120, ge=20, le=500)
    private_planning_word_limit: int = Field(default=120, ge=20, le=500)
    repair_attempts: int = Field(default=1, ge=0, le=1)
    max_stalemate_days: int = Field(default=3, ge=1, le=20)

    _strip_defaults = field_validator("default_model", "default_communication_style")(
        _stripped_or_none
    )
    _normalize_default_base_url = field_validator("default_base_url")(_normalized_base_url)
    _normalize_default_api_key = field_validator("default_api_key")(_normalized_secret)
    _normalize_default_api_key_env = field_validator("default_api_key_env")(
        _normalized_variable_name
    )

    @model_validator(mode="after")
    def validate_auth_defaults(self) -> Self:
        if self.default_api_key is not None and self.default_api_key_env is not None:
            raise ValueError("default_api_key and default_api_key_env are mutually exclusive")
        return self


class PlayerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=80)
    provider: Provider
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: ReasoningEffort | None = None
    communication_style: str | None = Field(default=None, max_length=500)
    base_url: str | None = Field(default=None, max_length=2_048)
    api_key: SecretStr | None = None
    api_key_env: str | None = Field(default=None, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def apply_human_model(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if not _is_provider(value.get("provider"), Provider.HUMAN) or "model" in value:
            return value
        resolved = dict(value)
        resolved["model"] = HUMAN_MODEL
        return resolved

    _strip_required = field_validator("name", "model")(_stripped)
    _strip_communication_style = field_validator("communication_style")(_stripped_or_none)
    _normalize_base_url = field_validator("base_url")(_normalized_base_url)
    _normalize_api_key = field_validator("api_key")(_normalized_secret)
    _normalize_api_key_env = field_validator("api_key_env")(_normalized_variable_name)

    @model_validator(mode="after")
    def validate_provider_options(self) -> Self:
        if self.reasoning_effort is not None and self.provider is not Provider.OPENAI_COMPATIBLE:
            raise ValueError("reasoning_effort is only supported for openai_compatible players")
        if self.api_key is not None and self.api_key_env is not None:
            raise ValueError("api_key and api_key_env are mutually exclusive")
        if self.provider is Provider.OPENAI_COMPATIBLE:
            if self.base_url is None:
                raise ValueError("base_url is required for openai_compatible players")
        elif self.base_url is not None or self.api_key is not None or self.api_key_env is not None:
            raise ValueError(
                "base_url, api_key, and api_key_env are only supported for "
                "openai_compatible players"
            )
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    game: GameOptions = Field(default_factory=GameOptions)
    players: list[PlayerConfig]

    @model_validator(mode="before")
    @classmethod
    def apply_player_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        game = value.get("game")
        if isinstance(game, GameOptions):
            default_model = game.default_model
            default_effort: ReasoningEffort | str | None = game.default_reasoning_effort
            default_style = game.default_communication_style
            default_base_url = game.default_base_url
            default_api_key = game.default_api_key
            default_api_key_env = game.default_api_key_env
        elif isinstance(game, dict):
            default_model = game.get("default_model")
            default_effort = game.get("default_reasoning_effort")
            default_style = game.get("default_communication_style")
            default_base_url = game.get("default_base_url")
            default_api_key = game.get("default_api_key")
            default_api_key_env = game.get("default_api_key_env")
        else:
            default_model = None
            default_effort = None
            default_style = None
            default_base_url = None
            default_api_key = None
            default_api_key_env = None
        players = value.get("players")
        if not isinstance(players, list):
            return value

        resolved_players: list[Any] = []
        for raw_player in players:
            if not isinstance(raw_player, dict):
                resolved_players.append(raw_player)
                continue
            player = dict(raw_player)
            provider = player.get("provider")
            is_human = _is_provider(provider, Provider.HUMAN)
            is_openai_compatible = _is_provider(provider, Provider.OPENAI_COMPATIBLE)
            if is_human:
                player.setdefault("model", HUMAN_MODEL)
            elif default_model is not None:
                player.setdefault("model", default_model)
            if default_effort is not None and is_openai_compatible:
                player.setdefault("reasoning_effort", default_effort)
            if default_style is not None and not is_human:
                player.setdefault("communication_style", default_style)
            if is_openai_compatible:
                if default_base_url is not None:
                    player.setdefault("base_url", default_base_url)
                if "api_key" not in player and "api_key_env" not in player:
                    if default_api_key is not None:
                        player["api_key"] = default_api_key
                    elif default_api_key_env is not None:
                        player["api_key_env"] = default_api_key_env
            resolved_players.append(player)

        resolved = dict(value)
        resolved["players"] = resolved_players
        return resolved

    @model_validator(mode="after")
    def validate_roster(self) -> Self:
        if not 6 <= len(self.players) <= 12:
            raise ValueError("a game requires 6–12 players")
        normalized = [player.name.casefold() for player in self.players]
        if len(normalized) != len(set(normalized)):
            raise ValueError("player names must be unique (case-insensitive)")
        if sum(player.provider is Provider.HUMAN for player in self.players) > 1:
            raise ValueError("a terminal game supports at most one human player")
        return self

    def with_communication_style(self, style: str) -> Self:
        """Return a validated copy with one style applied to every automated seat."""
        game = self.game.model_dump(mode="python")
        game["default_communication_style"] = style
        players: list[dict[str, Any]] = []
        for player in self.players:
            resolved = player.model_dump(mode="python")
            if player.provider is not Provider.HUMAN:
                resolved["communication_style"] = style
            players.append(resolved)
        return type(self).model_validate({"game": game, "players": players})

    @classmethod
    def load(cls, path: Path) -> Self:
        with path.open("rb") as handle:
            return cls.model_validate(tomllib.load(handle))
