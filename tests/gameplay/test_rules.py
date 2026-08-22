from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import pytest

from mafia_game.config import AppConfig
from mafia_game.gameplay import GameStatus, Phase, Role, Team, Visibility
from mafia_game.gameplay.rules import (
    ROLE_DISTRIBUTION,
    begin_day,
    check_win,
    create_game,
    discussion_order,
    legal_detective_targets,
    legal_doctor_targets,
    resolve_day_choice,
    resolve_night,
    tabulate,
)


@pytest.mark.parametrize("count", range(6, 13))
def test_role_distribution(count: int, config_factory: Callable[..., AppConfig]) -> None:
    state, _events = create_game("g", config_factory(count), seed=100)
    counts = Counter(player.role for player in state.players)
    mafia, detective, doctor, civilians = ROLE_DISTRIBUTION[count]
    assert counts == {
        Role.MAFIA: mafia,
        Role.DETECTIVE: detective,
        Role.DOCTOR: doctor,
        Role.CIVILIAN: civilians,
    }


def test_seeded_assignment_is_reproducible(config_factory: Callable[..., AppConfig]) -> None:
    left, _ = create_game("left", config_factory(), seed=812)
    right, _ = create_game("right", config_factory(), seed=812)
    assert [player.role for player in left.players] == [player.role for player in right.players]


def test_speaker_rotates_after_previous_first_even_if_dead(
    config_factory: Callable[..., AppConfig],
) -> None:
    state, _ = create_game("g", config_factory(), seed=1)
    begin_day(state)
    assert state.day_first_speaker == 1
    assert discussion_order(state) == [1, 2, 3, 4, 5, 6]
    state.player(1).alive = False
    begin_day(state)
    assert state.day_first_speaker == 2
    assert discussion_order(state) == [2, 3, 4, 5, 6]


def test_vote_tally_handles_unique_none_and_tie() -> None:
    no_elimination = tabulate({1: None, 2: None, 3: 1})
    assert no_elimination.unique
    assert no_elimination.winner is None

    tied = tabulate({1: 2, 2: 1, 3: None})
    assert not tied.unique
    assert set(tied.tied) == {1, 2, None}


def test_day_elimination_reveals_role_and_can_end_game(
    config_factory: Callable[..., AppConfig],
) -> None:
    state, _ = create_game("g", config_factory(), seed=5)
    mafia_seat = state.living_role(Role.MAFIA)[0].seat
    events = resolve_day_choice(state, mafia_seat)
    assert not state.player(mafia_seat).alive
    assert state.status is GameStatus.COMPLETED
    assert state.winner is Team.TOWN
    assert any(event.type == "player_eliminated" for event in events)


def test_doctor_save_and_detective_result_are_simultaneous(
    config_factory: Callable[..., AppConfig],
) -> None:
    state, _ = create_game("g", config_factory(), seed=2)
    detective = state.living_role(Role.DETECTIVE)[0]
    doctor = state.living_role(Role.DOCTOR)[0]
    target = next(player for player in state.living() if player.role is Role.CIVILIAN)
    mafia = state.living_role(Role.MAFIA)[0]

    events = resolve_night(
        state,
        mafia_target=target.seat,
        detective_seat=detective.seat,
        investigation_target=mafia.seat,
        doctor_seat=doctor.seat,
        protection_target=target.seat,
    )

    assert target.alive
    assert state.doctor_last_target == target.seat
    assert mafia.seat in state.detective_investigated
    assert any(event.type == "no_night_death" for event in events)
    result = next(event for event in events if event.type == "investigation_result")
    assert result.visibility == Visibility.player(detective.seat)
    assert result.data["alignment"] == "Mafia"
    assert target.seat not in legal_doctor_targets(state)
    assert mafia.seat not in legal_detective_targets(state, detective.seat)


def test_mafia_wins_at_parity(config_factory: Callable[..., AppConfig]) -> None:
    state, _ = create_game("g", config_factory(7), seed=4)
    town = [player for player in state.players if player.role is not Role.MAFIA]
    for player in town[2:]:
        player.alive = False
    events = check_win(state)
    assert state.phase is Phase.ENDED
    assert state.winner is Team.MAFIA
    assert events[-1].type == "game_ended"
