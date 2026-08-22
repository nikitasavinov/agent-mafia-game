"""The deterministic rules engine.

Every function here is pure with respect to the outside world: it reads and
mutates a `GameState` and returns the events describing what happened. No
provider, database, or console is reachable from this module, which is what
makes a run reproducible from its seed alone.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from mafia_game.config import AppConfig

from .models import (
    GameEvent,
    GameState,
    GameStatus,
    Phase,
    PlayerState,
    Role,
    Team,
    Visibility,
)

ROLE_DISTRIBUTION: dict[int, tuple[int, int, int, int]] = {
    6: (1, 1, 1, 3),
    7: (2, 1, 1, 3),
    8: (2, 1, 1, 4),
    9: (2, 1, 1, 5),
    10: (3, 1, 1, 5),
    11: (3, 1, 1, 6),
    12: (3, 1, 1, 7),
}


@dataclass(frozen=True, slots=True)
class VoteTally:
    unique: bool
    winner: int | None
    tied: tuple[int | None, ...] = ()


def create_game(game_id: str, config: AppConfig, seed: int) -> tuple[GameState, list[GameEvent]]:
    mafia, detectives, doctors, civilians = ROLE_DISTRIBUTION[len(config.players)]
    roles = (
        [Role.MAFIA] * mafia
        + [Role.DETECTIVE] * detectives
        + [Role.DOCTOR] * doctors
        + [Role.CIVILIAN] * civilians
    )
    # Role assignment must be reproducible for replay; this is not security-sensitive.
    random.Random(seed).shuffle(roles)  # noqa: S311
    players = [
        PlayerState(
            seat=index,
            name=player.name,
            provider=player.provider,
            model=player.model,
            reasoning_effort=player.reasoning_effort,
            communication_style=player.communication_style,
            role=roles[index - 1],
        )
        for index, player in enumerate(config.players, start=1)
    ]
    state = GameState(game_id=game_id, seed=seed, players=players, phase=Phase.NIGHT_ONE)
    events = [
        GameEvent(
            type="game_started",
            message=f"Game {game_id} started with {len(players)} players.",
            data={
                "players": [
                    {"seat": player.seat, "name": player.name, "provider": player.provider.value}
                    for player in players
                ]
            },
            night=1,
        )
    ]
    events.extend(
        [
            GameEvent(
                type="role_assigned",
                visibility=Visibility.player(player.seat),
                message=f"{player.name} is {player.role.value} ({player.team.value}).",
                data={"seat": player.seat, "role": player.role.value, "team": player.team.value},
                night=1,
            )
            for player in players
        ]
    )
    mafia_players = [player for player in players if player.role is Role.MAFIA]
    events.append(
        GameEvent(
            type="mafia_roster",
            visibility=Visibility.MAFIA,
            message="Mafia team: "
            + ", ".join(f"#{player.seat} {player.name}" for player in mafia_players),
            data={"seats": [player.seat for player in mafia_players]},
            night=1,
        )
    )
    events.append(
        GameEvent(
            type="phase_started",
            message="Night 1: roles and Mafia teammates are established; no actions occur.",
            data={"phase": Phase.NIGHT_ONE.value},
            night=1,
        )
    )
    return state, events


def begin_day(state: GameState) -> list[GameEvent]:
    state.day += 1
    state.phase = Phase.DAY_DISCUSSION
    living = sorted(state.living_seats())
    if state.day_first_speaker is None:
        first = living[0]
    else:
        following = [seat for seat in living if seat > state.day_first_speaker]
        first = following[0] if following else living[0]
    state.day_first_speaker = first
    return [
        GameEvent(
            type="phase_started",
            message=f"Day {state.day} begins. #{first} {state.player(first).name} speaks first.",
            data={"phase": Phase.DAY_DISCUSSION.value, "first_speaker": first},
            day=state.day,
        )
    ]


def discussion_order(state: GameState) -> list[int]:
    living = sorted(state.living_seats())
    first = state.day_first_speaker
    if first not in living:
        raise ValueError("day first speaker must be living")
    index = living.index(first)
    return living[index:] + living[:index]


def record_speech(
    state: GameState,
    seat: int,
    text: str,
    *,
    lap: int | None = None,
    kind: str = "speech",
    visibility: Visibility = Visibility.PUBLIC,
) -> GameEvent:
    player = state.player(seat)
    passed = not text.strip()
    label = "passes" if passed else text.strip()
    return GameEvent(
        type=kind,
        visibility=visibility,
        message=f"#{seat} {player.name}: {label}",
        data={"seat": seat, "text": text.strip(), "passed": passed, "lap": lap},
        day=state.day or None,
        night=state.night,
    )


def legal_day_vote_targets(state: GameState, seat: int) -> list[int]:
    return [target for target in state.living_seats() if target != seat]


def legal_runoff_targets(seat: int, tied: list[int | None]) -> list[int]:
    return [target for target in tied if target is not None and target != seat]


def legal_mafia_targets(state: GameState) -> list[int]:
    return [player.seat for player in state.living() if player.role is not Role.MAFIA]


def legal_detective_targets(state: GameState, detective_seat: int) -> list[int]:
    return [
        player.seat
        for player in state.living()
        if player.seat != detective_seat and player.seat not in state.detective_investigated
    ]


def legal_doctor_targets(state: GameState) -> list[int]:
    return [seat for seat in state.living_seats() if seat != state.doctor_last_target]


def validate_target(
    target: int | None, legal_targets: list[int], *, allow_none: bool = True
) -> bool:
    return (target is None and allow_none) or target in legal_targets


def tabulate(ballots: dict[int, int | None]) -> VoteTally:
    if not ballots:
        return VoteTally(unique=True, winner=None)
    counts = Counter(ballots.values())
    top = max(counts.values())
    winners = sorted(
        (choice for choice, count in counts.items() if count == top),
        key=lambda choice: (choice is None, choice if choice is not None else 0),
    )
    if len(winners) == 1:
        return VoteTally(unique=True, winner=winners[0])
    return VoteTally(unique=False, winner=None, tied=tuple(winners))


def vote_revealed_event(
    state: GameState,
    ballots: dict[int, int | None],
    *,
    runoff: bool = False,
) -> GameEvent:
    rendered = []
    for seat, target in sorted(ballots.items()):
        choice = "No elimination" if target is None else f"#{target} {state.player(target).name}"
        rendered.append(f"#{seat} {state.player(seat).name} → {choice}")
    return GameEvent(
        type="runoff_votes_revealed" if runoff else "day_votes_revealed",
        message=("Runoff votes: " if runoff else "Day votes: ") + "; ".join(rendered),
        data={"ballots": {str(seat): target for seat, target in ballots.items()}},
        day=state.day,
    )


def runoff_started_event(state: GameState, tied: list[int | None]) -> GameEvent:
    labels = [
        "No elimination" if target is None else f"#{target} {state.player(target).name}"
        for target in tied
    ]
    state.phase = Phase.RUNOFF_DEFENSE
    return GameEvent(
        type="runoff_started",
        message="Runoff between: " + ", ".join(labels),
        data={"tied": tied},
        day=state.day,
    )


def resolve_day_choice(
    state: GameState, winner: int | None, *, runoff_tied: bool = False
) -> list[GameEvent]:
    state.phase = Phase.DAY_RESOLUTION
    if runoff_tied:
        events = [
            GameEvent(
                type="no_elimination",
                message="The runoff is tied. Nobody is eliminated.",
                day=state.day,
            )
        ]
    elif winner is None:
        events = [
            GameEvent(
                type="no_elimination",
                message="No elimination wins the vote. Nobody is eliminated.",
                day=state.day,
            )
        ]
    else:
        player = state.player(winner)
        if not player.alive:
            raise ValueError("cannot eliminate a dead player")
        player.alive = False
        events = [
            GameEvent(
                type="player_eliminated",
                message=f"#{winner} {player.name} is eliminated and was {player.role.value}.",
                data={"seat": winner, "role": player.role.value},
                day=state.day,
            )
        ]
    events.extend(check_win(state))
    return events


def begin_night(state: GameState) -> list[GameEvent]:
    state.night += 1
    state.phase = Phase.MAFIA_DISCUSSION
    return [
        GameEvent(
            type="phase_started",
            message=f"Night {state.night} begins.",
            data={"phase": Phase.MAFIA_DISCUSSION.value},
            night=state.night,
        )
    ]


def mafia_votes_event(state: GameState, ballots: dict[int, int | None]) -> GameEvent:
    rendered = []
    for seat, target in sorted(ballots.items()):
        choice = "No attack" if target is None else f"#{target} {state.player(target).name}"
        rendered.append(f"#{seat} {state.player(seat).name} → {choice}")
    return GameEvent(
        type="mafia_votes",
        visibility=Visibility.MAFIA,
        message="Mafia votes: " + "; ".join(rendered),
        data={"ballots": {str(seat): target for seat, target in ballots.items()}},
        night=state.night,
    )


def resolve_night(
    state: GameState,
    *,
    mafia_target: int | None,
    detective_seat: int | None,
    investigation_target: int | None,
    doctor_seat: int | None,
    protection_target: int | None,
) -> list[GameEvent]:
    state.phase = Phase.NIGHT_RESOLUTION
    events = [
        GameEvent(
            type="night_actions_locked",
            visibility=Visibility.INTERNAL,
            message="Night actions locked.",
            data={
                "mafia_target": mafia_target,
                "detective_seat": detective_seat,
                "investigation_target": investigation_target,
                "doctor_seat": doctor_seat,
                "protection_target": protection_target,
            },
            night=state.night,
        )
    ]

    mafia_choice = (
        "No attack"
        if mafia_target is None
        else f"#{mafia_target} {state.player(mafia_target).name}"
    )
    events.append(
        GameEvent(
            type="mafia_attack_selected",
            visibility=Visibility.OMNISCIENT,
            message=f"Mafia locked: {mafia_choice}.",
            data={"target": mafia_target},
            night=state.night,
        )
    )
    if detective_seat is not None:
        detective_choice = (
            "no action"
            if investigation_target is None
            else f"#{investigation_target} {state.player(investigation_target).name}"
        )
        events.append(
            GameEvent(
                type="detective_action",
                visibility=Visibility.OMNISCIENT,
                message=(
                    f"#{detective_seat} {state.player(detective_seat).name} "
                    f"investigates {detective_choice}."
                ),
                data={"seat": detective_seat, "target": investigation_target},
                night=state.night,
            )
        )
    if doctor_seat is not None:
        doctor_choice = (
            "no action"
            if protection_target is None
            else f"#{protection_target} {state.player(protection_target).name}"
        )
        events.append(
            GameEvent(
                type="doctor_action",
                visibility=Visibility.OMNISCIENT,
                message=(
                    f"#{doctor_seat} {state.player(doctor_seat).name} protects {doctor_choice}."
                ),
                data={"seat": doctor_seat, "target": protection_target},
                night=state.night,
            )
        )

    if investigation_target is not None and detective_seat is not None:
        state.detective_investigated.add(investigation_target)
        target = state.player(investigation_target)
        alignment = "Mafia" if target.role is Role.MAFIA else "Not Mafia"
        events.append(
            GameEvent(
                type="investigation_result",
                visibility=Visibility.player(detective_seat),
                message=f"Investigation result: #{target.seat} {target.name} is {alignment}.",
                data={"target": target.seat, "alignment": alignment},
                night=state.night,
            )
        )

    state.doctor_last_target = protection_target
    if mafia_target is not None and mafia_target != protection_target:
        victim = state.player(mafia_target)
        if not victim.alive or victim.role is Role.MAFIA:
            raise ValueError("illegal Mafia resolution target")
        victim.alive = False
        events.append(
            GameEvent(
                type="night_death",
                message=f"#{victim.seat} {victim.name} died and was {victim.role.value}.",
                data={"seat": victim.seat, "role": victim.role.value},
                night=state.night,
            )
        )
    else:
        events.append(
            GameEvent(
                type="no_night_death",
                message="Nobody died during the Night.",
                night=state.night,
            )
        )
    events.extend(check_win(state))
    return events


def check_win(state: GameState) -> list[GameEvent]:
    living = state.living()
    mafia = sum(player.role is Role.MAFIA for player in living)
    others = len(living) - mafia
    winner: Team | None = None
    if mafia == 0:
        winner = Team.TOWN
    elif mafia >= others:
        winner = Team.MAFIA
    if winner is None:
        return []
    state.winner = winner
    state.status = GameStatus.COMPLETED
    state.phase = Phase.ENDED
    return [
        GameEvent(
            type="game_ended",
            message=f"{winner.value.title()} wins the game.",
            data={"winner": winner.value},
            day=state.day or None,
            night=state.night,
        )
    ]


def abort_game(state: GameState, reason: str) -> GameEvent:
    state.status = GameStatus.ABORTED
    state.phase = Phase.ABORTED
    return GameEvent(
        type="game_aborted",
        message=f"Game aborted: {reason}",
        data={"reason": reason},
        day=state.day or None,
        night=state.night,
    )
