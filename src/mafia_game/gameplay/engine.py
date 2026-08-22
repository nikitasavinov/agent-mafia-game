"""The top-level game loop.

`GameRunner` alternates Day and Night until the rules declare a winner, and owns
the failure path: interruptions, provider-wide failure, stalls, and infrastructure
errors abort the run, record why, and still close sessions and finalize the store.
"""

from __future__ import annotations

import asyncio

from mafia_game.agents import PlayerDriver
from mafia_game.config import AppConfig, Provider

from .errors import GameAbortError, GameStalledError
from .models import GameEvent, GameState, GameStatus
from .observer import GameObserver, NullObserver
from .phases import PhaseRunner
from .ports import GameStore
from .rules import abort_game, begin_day, begin_night, create_game
from .turns import TurnManager


class GameRunner:
    def __init__(
        self,
        config: AppConfig,
        state: GameState,
        initial_events: list[GameEvent],
        store: GameStore,
        *,
        observer: GameObserver | None = None,
        drivers: dict[Provider, PlayerDriver] | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.store = store
        self.observer = observer or NullObserver()
        self.events: list[GameEvent] = []
        self.initial_events = initial_events
        self.stalemate_days = 0
        self.turns = TurnManager(
            config,
            state,
            self.events,
            store.run_dir,
            self.observer,
            self._commit,
            drivers,
        )
        self.phases = PhaseRunner(config, state, self.turns, self.observer, self._commit)

    @classmethod
    def create(
        cls,
        config: AppConfig,
        store: GameStore,
        seed: int,
        *,
        observer: GameObserver | None = None,
        drivers: dict[Provider, PlayerDriver] | None = None,
    ) -> GameRunner:
        state, initial_events = create_game(store.game_id, config, seed)
        return cls(
            config,
            state,
            initial_events,
            store,
            observer=observer,
            drivers=drivers,
        )

    async def run(self) -> GameState:
        self._commit(self.initial_events, "setup")
        try:
            await self.turns.open_all()
            await self.observer.phase_boundary(self.state)
            self._commit(begin_day(self.state), "night-one-complete")
            await self.observer.phase_boundary(self.state)
            while self.state.status is GameStatus.ACTIVE:
                living_before_day = len(self.state.living())
                await self.phases.run_day()
                if self.state.status is not GameStatus.ACTIVE:
                    break
                self._commit(begin_night(self.state), f"night-{self.state.night}-start")
                await self.observer.phase_boundary(self.state)
                await self.phases.run_night()
                if self.state.status is not GameStatus.ACTIVE:
                    break
                if len(self.state.living()) == living_before_day:
                    self.stalemate_days += 1
                else:
                    self.stalemate_days = 0
                if self.stalemate_days >= self.config.game.max_stalemate_days:
                    raise GameStalledError(
                        "no player was eliminated for "
                        f"{self.stalemate_days} consecutive days "
                        f"(limit: {self.config.game.max_stalemate_days})"
                    )
                self._commit(begin_day(self.state), f"day-{self.state.day}-start")
                await self.observer.phase_boundary(self.state)
        except asyncio.CancelledError:
            self._abort_if_active("cancelled by operator")
            raise
        except KeyboardInterrupt:
            self._abort_if_active("cancelled by operator")
        except BaseException as error:
            root = unwrap_single_error(error)
            reason = (
                str(root)
                if isinstance(root, GameAbortError)
                else f"infrastructure failure: {describe_error(error)}"
            )
            self._abort_if_active(reason)
            raise
        finally:
            await self.turns.close_all()
            self.store.finish(self.state.status, self.state.winner)
        return self.state

    def _commit(self, events: list[GameEvent], transition_id: str | None = None) -> None:
        if not events:
            return
        self.store.append(events, transition_id)
        self.events.extend(events)
        self.observer.on_events(events)

    def _abort_if_active(self, reason: str) -> None:
        if self.state.status is not GameStatus.ACTIVE:
            return
        self._commit([abort_game(self.state, reason)], "abort")


def describe_error(error: BaseException) -> str:
    """Render an exception for an abort reason, seeing through TaskGroup wrappers.

    Concurrent phases run under `asyncio.TaskGroup`, which reports a single
    failing turn as an `ExceptionGroup`. Unwrapping it keeps the recorded abort
    reason pointing at the driver error that actually happened rather than at
    "unhandled errors in a TaskGroup".
    """
    error = unwrap_single_error(error)
    return f"{type(error).__name__}: {error}"


def unwrap_single_error(error: BaseException) -> BaseException:
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error
