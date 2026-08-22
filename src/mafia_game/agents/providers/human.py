"""Terminal driver for a human player.

Presents the same requests an AI seat receives, then validates the reply against
the same legal-target list, so a person cannot make a move the engine would
reject. Blocking input runs off the event loop in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mafia_game.config import PlayerConfig

from ..contracts import (
    DriverCheck,
    DriverSession,
    PlayerRequest,
    PlayerResult,
    RequestKind,
    ResultStatus,
)

InputFunction = Callable[[str], str]

SPEECH_TITLES = {
    RequestKind.SPEECH: ("YOUR TURN", "Speak publicly to the table"),
    RequestKind.DEFENSE: ("YOUR DEFENSE", "Answer the runoff accusations publicly"),
    RequestKind.MAFIA_DISCUSSION: (
        "MAFIA · PRIVATE",
        "Plan with your living Mafia teammates",
    ),
}

ACTION_TITLES = {
    RequestKind.DAY_VOTE: "DAY BALLOT",
    RequestKind.RUNOFF_VOTE: "RUNOFF BALLOT",
    RequestKind.MAFIA_VOTE: "MAFIA ATTACK · PRIVATE",
    RequestKind.INVESTIGATE: "DETECTIVE INVESTIGATION · PRIVATE",
    RequestKind.PROTECT: "DOCTOR PROTECTION · PRIVATE",
    RequestKind.REPAIR: "CORRECT YOUR CHOICE",
}

NO_ACTION_LABELS = {
    RequestKind.DAY_VOTE: "No elimination",
    RequestKind.RUNOFF_VOTE: "No elimination",
    RequestKind.MAFIA_VOTE: "No attack",
    RequestKind.INVESTIGATE: "Skip investigation",
    RequestKind.PROTECT: "Skip protection",
    RequestKind.REPAIR: "No action",
}


class HumanSession:
    def __init__(
        self,
        console: Console,
        player_names: dict[int, str],
        input_function: InputFunction,
    ) -> None:
        self.console = console
        self.player_names = player_names
        self.input_function = input_function

    async def respond(self, request: PlayerRequest) -> PlayerResult:
        try:
            return await asyncio.to_thread(self._respond_sync, request)
        except EOFError:
            return PlayerResult(
                status=ResultStatus.CANCELLED,
                detail="terminal input closed",
            )

    def _respond_sync(self, request: PlayerRequest) -> PlayerResult:
        if request.kind is RequestKind.PROBE:
            return self._success(request, "READY")
        if request.kind in SPEECH_TITLES:
            return self._speech(request)
        if request.kind in ACTION_TITLES:
            return self._action(request)
        return PlayerResult(
            status=ResultStatus.TRANSPORT_ERROR,
            detail=f"unsupported human turn kind: {request.kind.value}",
        )

    def _speech(self, request: PlayerRequest) -> PlayerResult:
        title, description = SPEECH_TITLES[request.kind]
        limit = request.word_limit or 0
        instructions = Text(description + ".", style="bold white")
        instructions.append(
            f"\nUse one line and at most {limit} words. Enter PASS to stay silent.",
            style="dim",
        )
        self.console.print(
            Panel(
                instructions,
                title=Text(title, style="bold bright_yellow"),
                title_align="left",
                border_style="bright_yellow",
                padding=(0, 2),
            )
        )
        while True:
            value = self.input_function("[bold bright_yellow]You ›[/bold bright_yellow] ").strip()
            if not value or value.casefold() == "pass":
                return self._success(request, "PASS")
            words = len(value.split())
            if not limit or words <= limit:
                return self._success(request, value)
            self.console.print(
                Text(
                    f"That is {words} words; shorten it to {limit} or fewer.",
                    style="bold red",
                )
            )

    def _action(self, request: PlayerRequest) -> PlayerResult:
        table = Table(
            box=box.SIMPLE,
            show_header=False,
            padding=(0, 1),
            border_style="dim",
        )
        table.add_column(justify="right", style="bold bright_yellow")
        table.add_column()
        for seat in request.legal_targets:
            table.add_row(f"#{seat}", Text(self.player_names.get(seat, f"Seat {seat}")))
        if request.allow_none:
            table.add_row("#0", Text(NO_ACTION_LABELS[request.kind], style="italic dim"))
        self.console.print(
            Panel(
                table,
                title=Text(ACTION_TITLES[request.kind], style="bold bright_yellow"),
                subtitle=Text("Enter a seat number or player name", style="dim"),
                title_align="left",
                border_style="bright_yellow",
                padding=(0, 2),
            )
        )

        choices = [f"#{seat}" for seat in request.legal_targets]
        if request.allow_none:
            choices.append("#0")
        choice_hint = ", ".join(choices)
        while True:
            raw_value = self.input_function(
                f"[bold bright_yellow]Your choice ({choice_hint}) ›[/bold bright_yellow] "
            ).strip()
            valid, target = self._parse_target(raw_value, request)
            if valid:
                text = json.dumps({"target_seat": target})
                return self._success(request, text)
            legal = ", ".join(f"#{seat}" for seat in request.legal_targets)
            if request.allow_none:
                legal = f"{legal}, or #0" if legal else "#0"
            self.console.print(Text(f"Choose one of: {legal}.", style="bold red"))

    def _parse_target(
        self,
        value: str,
        request: PlayerRequest,
    ) -> tuple[bool, int | None]:
        folded = value.casefold()
        if request.allow_none and folded in {
            "",
            "0",
            "#0",
            "none",
            "no",
            "pass",
            "skip",
        }:
            return True, None
        for seat in request.legal_targets:
            name = self.player_names.get(seat)
            if name is not None and folded == name.casefold():
                return True, seat
        candidate = value.removeprefix("#").split(maxsplit=1)[0] if value else ""
        try:
            seat = int(candidate)
        except ValueError:
            return False, None
        return (True, seat) if seat in request.legal_targets else (False, None)

    @staticmethod
    def _success(request: PlayerRequest, text: str) -> PlayerResult:
        return PlayerResult(
            status=ResultStatus.SUCCESS,
            text=text,
            raw=text,
            provider_request_id=f"human:{request.turn_id}",
        )

    async def close(self) -> None:
        return None


class HumanDriver:
    def __init__(
        self,
        console: Console | None = None,
        *,
        player_names: dict[int, str] | None = None,
        input_function: InputFunction | None = None,
        interactive: bool | None = None,
    ) -> None:
        self.console = console or Console()
        self.player_names = dict(player_names or {})
        self.input_function = input_function or self.console.input
        self.interactive = (
            interactive
            if interactive is not None
            else input_function is not None or sys.stdin.isatty()
        )

    async def preflight(
        self,
        player: PlayerConfig,
        workspace: Path,
        *,
        live: bool = False,
    ) -> list[DriverCheck]:
        del player, workspace, live
        return [
            DriverCheck(
                name="interactive terminal",
                ok=self.interactive,
                detail=(
                    "ready for keyboard input"
                    if self.interactive
                    else "stdin is not an interactive terminal"
                ),
            )
        ]

    async def open(self, player: PlayerConfig, workspace: Path) -> DriverSession:
        del player, workspace
        if not self.interactive:
            raise RuntimeError("human players require an interactive terminal")
        return HumanSession(self.console, self.player_names, self.input_function)
