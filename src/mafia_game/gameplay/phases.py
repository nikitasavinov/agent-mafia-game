"""Sequencing of a single Day and Night.

`PhaseRunner` decides who is asked what and in which order; it never decides
outcomes. Every batch of simultaneous choices — Day ballots, runoff ballots,
Night actions — goes through `_collect_choices`, which is the one place that
knows how a human seat differs from an automated one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from mafia_game.agents import RequestKind
from mafia_game.config import AppConfig, Provider

from .models import GameEvent, GameState, Phase, Role, Visibility
from .observer import GameObserver
from .ports import CommitEvents
from .rules import (
    discussion_order,
    legal_day_vote_targets,
    legal_detective_targets,
    legal_doctor_targets,
    legal_mafia_targets,
    legal_runoff_targets,
    mafia_votes_event,
    record_speech,
    resolve_day_choice,
    resolve_night,
    runoff_started_event,
    tabulate,
    vote_revealed_event,
)
from .turns import TurnManager


@dataclass(frozen=True, slots=True)
class _ActionRequest:
    """One pending choice: which seat is asked, for what, and what is legal."""

    seat: int
    kind: RequestKind
    legal_targets: list[int]
    allow_none: bool


class PhaseRunner:
    def __init__(
        self,
        config: AppConfig,
        state: GameState,
        turns: TurnManager,
        observer: GameObserver,
        commit: CommitEvents,
    ) -> None:
        self.config = config
        self.state = state
        self.turns = turns
        self.observer = observer
        self.commit = commit

    async def run_day(self) -> None:
        order = discussion_order(self.state)
        discussion_rounds = self.config.game.discussion_rounds
        for lap in range(1, discussion_rounds + 1):
            self.commit(
                [
                    GameEvent(
                        type="discussion_lap_started",
                        message=f"Day {self.state.day}, discussion lap {lap}.",
                        data={"lap": lap, "total_laps": discussion_rounds},
                        day=self.state.day,
                    )
                ],
                f"day-{self.state.day}-lap-{lap}-start",
            )
            all_passed = True
            for seat in order:
                speech = await self.turns.get_speech(
                    seat,
                    RequestKind.SPEECH,
                    self.config.game.speech_word_limit,
                )
                if speech:
                    all_passed = False
                self.commit(
                    [record_speech(self.state, seat, speech, lap=lap)],
                    f"day-{self.state.day}-lap-{lap}-seat-{seat}",
                )
            if all_passed:
                self.commit(
                    [
                        GameEvent(
                            type="discussion_ended_early",
                            message="Every living player passed; discussion ends early.",
                            day=self.state.day,
                        )
                    ]
                )
                break

        self.state.phase = Phase.DAY_VOTE
        self.commit(
            [
                GameEvent(
                    type="phase_started",
                    message=f"Day {self.state.day} voting begins.",
                    data={"phase": Phase.DAY_VOTE.value},
                    day=self.state.day,
                )
            ]
        )
        await self.observer.phase_boundary(self.state)
        ballots = await self._collect_day_ballots(runoff=None)
        tally = tabulate(ballots)
        self.commit([vote_revealed_event(self.state, ballots)])
        if tally.unique:
            self.commit(resolve_day_choice(self.state, tally.winner))
            return

        tied = list(tally.tied)
        self.commit([runoff_started_event(self.state, tied)])
        await self.observer.phase_boundary(self.state)
        for seat in sorted(target for target in tied if target is not None):
            defense = await self.turns.get_speech(
                seat,
                RequestKind.DEFENSE,
                self.config.game.defense_word_limit,
            )
            self.commit([record_speech(self.state, seat, defense, kind="runoff_defense")])

        self.state.phase = Phase.RUNOFF_VOTE
        self.commit(
            [
                GameEvent(
                    type="phase_started",
                    message="Runoff voting begins.",
                    data={"phase": Phase.RUNOFF_VOTE.value},
                    day=self.state.day,
                )
            ]
        )
        await self.observer.phase_boundary(self.state)
        runoff_ballots = await self._collect_day_ballots(runoff=tied)
        runoff_tally = tabulate(runoff_ballots)
        self.commit([vote_revealed_event(self.state, runoff_ballots, runoff=True)])
        if runoff_tally.unique:
            self.commit(resolve_day_choice(self.state, runoff_tally.winner))
        else:
            self.commit(resolve_day_choice(self.state, None, runoff_tied=True))

    async def _collect_day_ballots(self, runoff: list[int | None] | None) -> dict[int, int | None]:
        if runoff is None:
            requests = [
                _ActionRequest(
                    seat=seat,
                    kind=RequestKind.DAY_VOTE,
                    legal_targets=legal_day_vote_targets(self.state, seat),
                    allow_none=True,
                )
                for seat in self.state.living_seats()
            ]
        else:
            requests = [
                _ActionRequest(
                    seat=seat,
                    kind=RequestKind.RUNOFF_VOTE,
                    legal_targets=legal_runoff_targets(seat, runoff),
                    allow_none=None in runoff,
                )
                for seat in self.state.living_seats()
            ]
        return await self._collect_choices(requests)

    async def _collect_choices(self, requests: Sequence[_ActionRequest]) -> dict[int, int | None]:
        """Resolve simultaneous choices, returning the target each seat picked.

        Human seats are polled first and one at a time: a person cannot answer
        two prompts at once, and must never be racing an automated seat for
        their own terminal.

        The rest run under a `TaskGroup`, not bare `create_task` or `gather`: if
        one seat's turn fails, the others must be cancelled and awaited before
        this phase unwinds. Leaving them running would let a turn commit events
        after the game had already aborted.
        """
        choices: dict[int, int | None] = {}
        concurrent: list[_ActionRequest] = []
        for request in requests:
            if self._is_human(request.seat):
                choices[request.seat] = await self._ask(request)
            else:
                concurrent.append(request)
        async with asyncio.TaskGroup() as group:
            tasks = [
                (request.seat, group.create_task(self._ask(request))) for request in concurrent
            ]
        choices.update({seat: task.result() for seat, task in tasks})
        return choices

    async def _ask(self, request: _ActionRequest) -> int | None:
        return await self.turns.get_action(
            request.seat,
            request.kind,
            request.legal_targets,
            allow_none=request.allow_none,
        )

    def _is_human(self, seat: int) -> bool:
        return self.turns.players[seat].config.provider is Provider.HUMAN

    async def run_night(self) -> None:
        mafia_seats = [player.seat for player in self.state.living_role(Role.MAFIA)]
        for seat in mafia_seats:
            speech = await self.turns.get_speech(
                seat,
                RequestKind.MAFIA_DISCUSSION,
                self.config.game.private_planning_word_limit,
            )
            self.commit(
                [
                    record_speech(
                        self.state,
                        seat,
                        speech,
                        kind="mafia_discussion",
                        visibility=Visibility.MAFIA,
                    )
                ]
            )

        self.state.phase = Phase.NIGHT_ACTIONS
        self.commit(
            [
                GameEvent(
                    type="phase_started",
                    message=f"Night {self.state.night} actions are being selected.",
                    data={"phase": Phase.NIGHT_ACTIONS.value},
                    night=self.state.night,
                )
            ]
        )
        await self.observer.phase_boundary(self.state)

        detectives = self.state.living_role(Role.DETECTIVE)
        detective_seat = detectives[0].seat if detectives else None
        doctors = self.state.living_role(Role.DOCTOR)
        doctor_seat = doctors[0].seat if doctors else None

        mafia_targets = legal_mafia_targets(self.state)
        requests = [
            _ActionRequest(
                seat=seat,
                kind=RequestKind.MAFIA_VOTE,
                legal_targets=mafia_targets,
                allow_none=True,
            )
            for seat in mafia_seats
        ]
        if detective_seat is not None:
            requests.append(
                _ActionRequest(
                    seat=detective_seat,
                    kind=RequestKind.INVESTIGATE,
                    legal_targets=legal_detective_targets(self.state, detective_seat),
                    allow_none=True,
                )
            )
        if doctor_seat is not None:
            requests.append(
                _ActionRequest(
                    seat=doctor_seat,
                    kind=RequestKind.PROTECT,
                    legal_targets=legal_doctor_targets(self.state),
                    allow_none=True,
                )
            )

        # Roles are exclusive, so one seat contributes at most one Night choice
        # and the seat number is enough to identify each result.
        choices = await self._collect_choices(requests)
        mafia_ballots = {seat: choices[seat] for seat in mafia_seats}
        self.commit([mafia_votes_event(self.state, mafia_ballots)])
        mafia_tally = tabulate(mafia_ballots)
        self.commit(
            resolve_night(
                self.state,
                mafia_target=mafia_tally.winner if mafia_tally.unique else None,
                detective_seat=detective_seat,
                investigation_target=(
                    choices.get(detective_seat) if detective_seat is not None else None
                ),
                doctor_seat=doctor_seat,
                protection_target=(choices.get(doctor_seat) if doctor_seat is not None else None),
            )
        )
