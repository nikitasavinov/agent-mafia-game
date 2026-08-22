from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from mafia_game.config import AppConfig
from mafia_game.gameplay import Audience, GameEvent, ReplayView, Role, Visibility
from mafia_game.gameplay.rules import create_game
from mafia_game.gameplay.visibility import visible_for_replay, visible_to_player


def test_private_views_do_not_leak(config_factory: Callable[..., AppConfig]) -> None:
    state, events = create_game("g", config_factory(), seed=3)
    mafia = state.living_role(Role.MAFIA)[0]
    town = next(player for player in state.players if player.role is not Role.MAFIA)
    mafia_event = next(event for event in events if event.visibility == Visibility.MAFIA)
    town_role_event = next(
        event for event in events if event.visibility == Visibility.player(town.seat)
    )

    assert visible_to_player(mafia_event, state, mafia.seat)
    assert not visible_to_player(mafia_event, state, town.seat)
    assert visible_to_player(town_role_event, state, town.seat)
    assert not visible_to_player(town_role_event, state, mafia.seat)


def test_replay_views_filter_internal_and_private() -> None:
    public = GameEvent(type="x", message="public")
    private = GameEvent(type="x", visibility=Visibility.MAFIA, message="secret")
    internal = GameEvent(type="x", visibility=Visibility.INTERNAL, message="transport")
    assert visible_for_replay(public, ReplayView.PUBLIC)
    assert not visible_for_replay(private, ReplayView.PUBLIC)
    assert visible_for_replay(private, ReplayView.OMNISCIENT)
    assert not visible_for_replay(internal, ReplayView.OMNISCIENT)


def test_omniscient_action_never_reaches_players(
    config_factory: Callable[..., AppConfig],
) -> None:
    state, _ = create_game("g", config_factory(), seed=3)
    event = GameEvent(type="doctor_action", visibility=Visibility.OMNISCIENT, message="secret")
    assert all(not visible_to_player(event, state, player.seat) for player in state.players)


@pytest.mark.parametrize(
    "visibility",
    [
        Visibility.PUBLIC,
        Visibility.MAFIA,
        Visibility.OMNISCIENT,
        Visibility.INTERNAL,
        Visibility.player(4),
    ],
)
def test_visibility_round_trips_through_its_stored_form(visibility: Visibility) -> None:
    assert Visibility.parse(str(visibility)) == visibility


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("public", Visibility.PUBLIC),
        ("mafia", Visibility.MAFIA),
        ("omniscient", Visibility.OMNISCIENT),
        ("internal", Visibility.INTERNAL),
        ("player:12", Visibility.player(12)),
    ],
)
def test_stored_runs_keep_their_original_spelling(stored: str, expected: Visibility) -> None:
    """Runs recorded before `Visibility` existed must still load and re-serialize."""
    event = GameEvent.model_validate({"type": "t", "visibility": stored, "message": "m"})
    assert event.visibility == expected
    assert event.model_dump(mode="json")["visibility"] == stored


@pytest.mark.parametrize("stored", ["spectator", "player", "player:x", "public:1", "player:-1"])
def test_unknown_visibility_is_rejected_rather_than_silently_hidden(stored: str) -> None:
    """A misspelled audience used to read as 'nobody may see this'; now it fails loudly."""
    with pytest.raises(ValidationError):
        GameEvent.model_validate({"type": "t", "visibility": stored, "message": "m"})


def test_player_visibility_requires_a_seat() -> None:
    with pytest.raises(ValueError, match="seat is required"):
        Visibility(Audience.PLAYER)
    with pytest.raises(ValueError, match="forbidden otherwise"):
        Visibility(Audience.PUBLIC, seat=2)
