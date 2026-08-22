"""Rich rendering of a live game.

`RichGameObserver` is a read-only view: it is handed events that have already
been committed and never influences the engine. When a human occupies a seat it
is constructed with that `perspective_seat`, and then it must enforce the same
privacy rules as the prompts do — hence the visibility checks below.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Callable
from typing import TypeVar

from rich import box
from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from mafia_game.agents import RequestKind

from .event_payloads import optional_int
from .models import Audience, GameEvent, GameState, Phase, Role, Team

EnumT = TypeVar("EnumT", Role, Team)

SEAT_STYLES = (
    "bright_cyan",
    "bright_yellow",
    "bright_green",
    "bright_blue",
    "bright_magenta",
    "bright_red",
    "cyan",
    "yellow",
    "green",
    "blue",
    "magenta",
    "red",
)

ROLE_STYLES: dict[Role, str] = {
    Role.CIVILIAN: "white",
    Role.DETECTIVE: "bright_blue",
    Role.DOCTOR: "bright_green",
    Role.MAFIA: "bright_magenta",
}

TEAM_STYLES: dict[Team, str] = {
    Team.TOWN: "bright_cyan",
    Team.MAFIA: "bright_magenta",
}

PHASE_LABELS = {
    Phase.NIGHT_ONE.value: "Setup",
    Phase.DAY_DISCUSSION.value: "Discussion",
    Phase.DAY_VOTE.value: "Vote",
    Phase.RUNOFF_DEFENSE.value: "Runoff defense",
    Phase.RUNOFF_VOTE.value: "Runoff vote",
    Phase.MAFIA_DISCUSSION.value: "Mafia council",
    Phase.NIGHT_ACTIONS.value: "Night actions",
    Phase.NIGHT_RESOLUTION.value: "Night resolution",
}

ACTION_LABELS = {
    RequestKind.SPEECH: "composing a public statement",
    RequestKind.DEFENSE: "preparing a defense",
    RequestKind.MAFIA_DISCUSSION: "planning privately",
    RequestKind.DAY_VOTE: "choosing a ballot",
    RequestKind.RUNOFF_VOTE: "choosing a runoff ballot",
    RequestKind.MAFIA_VOTE: "choosing a target",
    RequestKind.INVESTIGATE: "choosing an investigation",
    RequestKind.PROTECT: "choosing a protection",
    RequestKind.REPAIR: "correcting an invalid response",
    RequestKind.PROBE: "answering a health check",
}


def _coerce(enum_type: type[EnumT], value: str) -> EnumT | None:
    """Read a persisted enum spelling, tolerating values from a future schema."""
    try:
        return enum_type(value)
    except ValueError:
        return None


def _literal_replacer(label: str) -> Callable[[re.Match[str]], str]:
    def replace(_match: re.Match[str]) -> str:
        return label

    return replace


class RichGameObserver:
    def __init__(
        self,
        console: Console | None = None,
        *,
        step: bool = False,
        player_names: dict[int, str] | None = None,
        perspective_seat: int | None = None,
    ) -> None:
        self.console = console or Console()
        self.step = step
        self.perspective_seat = perspective_seat
        self.player_names = dict(player_names or {})
        self.player_roles: dict[int, Role] = {}
        self.alive_seats = set(self.player_names)

    def show_roster(self, state: GameState, *, title: str | None = None) -> None:
        self.player_names.update({player.seat: player.name for player in state.players})
        if self.perspective_seat is None:
            self.player_roles.update({player.seat: player.role for player in state.players})
        else:
            perspective_player = state.player(self.perspective_seat)
            self.player_roles[self.perspective_seat] = perspective_player.role
        self.alive_seats = {player.seat for player in state.players if player.alive}

        table = Table(
            title=title or f"MAFIA  ·  {state.game_id}",
            title_style="bold bright_cyan",
            box=box.ROUNDED,
            border_style="cyan",
            header_style="bold white",
            padding=(0, 1),
        )
        table.add_column("Seat", justify="right", no_wrap=True)
        table.add_column("Player", no_wrap=True)
        table.add_column("Provider", style="dim")
        table.add_column("Model", style="cyan")
        table.add_column("Effort", style="bright_blue")
        table.add_column("Role")
        for player in state.players:
            seat_style = self._seat_style(player.seat)
            is_viewer = player.seat == self.perspective_seat
            role_visible = self.perspective_seat is None or is_viewer
            table.add_row(
                Text(f"#{player.seat}", style=f"bold {seat_style}"),
                Text(
                    player.name + ("  ·  YOU" if is_viewer else ""),
                    style=f"bold {seat_style}",
                ),
                player.provider.value,
                player.model,
                player.reasoning_effort.value if player.reasoning_effort is not None else "—",
                (
                    Text(
                        player.role.value.upper(),
                        style=f"bold {ROLE_STYLES[player.role]}",
                    )
                    if role_visible
                    else Text("HIDDEN", style="italic dim")
                ),
            )
        self.console.line()
        self.console.print(table)
        self.console.line()

    def on_events(self, events: list[GameEvent]) -> None:
        for event in events:
            if not self._event_visible(event):
                continue
            self.render_event(event)

    def on_thinking(self, state: GameState, seat: int, kind: RequestKind) -> None:
        if not self._activity_visible(seat, kind):
            return
        player = state.player(seat)
        self.player_names[seat] = player.name
        line = Text("  …  ", style="dim")
        line.append_text(self._player_label(seat))
        line.append(f" is {ACTION_LABELS[kind]}…", style="dim")
        self.console.print(line)

    def on_locked(self, state: GameState, seat: int, kind: RequestKind) -> None:
        if not self._activity_visible(seat, kind):
            return
        player = state.player(seat)
        self.player_names[seat] = player.name
        line = Text("  ✓  ", style="bold green")
        line.append_text(self._player_label(seat))
        line.append(f" locked {self._action_noun(kind)}", style="dim")
        line.append(
            "  ·  choice recorded" if seat == self.perspective_seat else "  ·  choice hidden",
            style="italic dim",
        )
        self.console.print(line)

    def _event_visible(self, event: GameEvent) -> bool:
        visibility = event.visibility
        if visibility.audience is Audience.INTERNAL:
            return False
        if self.perspective_seat is None:
            return True
        match visibility.audience:
            case Audience.PUBLIC:
                return True
            case Audience.PLAYER:
                return visibility.seat == self.perspective_seat
            case Audience.MAFIA:
                return self._viewer_is_living_mafia()
            case Audience.OMNISCIENT | Audience.INTERNAL:
                return False

    def _activity_visible(self, seat: int, kind: RequestKind) -> bool:
        if self.perspective_seat is None or seat == self.perspective_seat:
            return True
        if kind in {
            RequestKind.SPEECH,
            RequestKind.DEFENSE,
            RequestKind.DAY_VOTE,
            RequestKind.RUNOFF_VOTE,
        }:
            return True
        return (
            kind in {RequestKind.MAFIA_DISCUSSION, RequestKind.MAFIA_VOTE}
            and self._viewer_is_living_mafia()
        )

    def _viewer_is_living_mafia(self) -> bool:
        """Report whether the watching seat is Mafia and still in the game.

        A dead Mafia player stops receiving the team's private traffic, so this
        deliberately checks both role and survival.
        """
        if self.perspective_seat is None:
            return False
        return (
            self.player_roles.get(self.perspective_seat) is Role.MAFIA
            and self.perspective_seat in self.alive_seats
        )

    async def phase_boundary(self, state: GameState) -> None:
        if not self.step:
            return
        label = PHASE_LABELS.get(state.phase.value, state.phase.value.replace("_", " ").title())
        await asyncio.to_thread(
            self.console.input,
            f"[bold cyan]{label}[/bold cyan] [dim]· press Enter to continue…[/dim] ",
        )

    def render_event(self, event: GameEvent) -> None:
        if event.type == "phase_started":
            self._render_phase(event)
        elif event.type == "discussion_lap_started":
            self._render_discussion_lap(event)
        elif event.type in {"speech", "runoff_defense", "mafia_discussion"}:
            self._render_speech(event)
        elif event.type in {"day_votes_revealed", "runoff_votes_revealed", "mafia_votes"}:
            self._render_votes(event)
        elif event.type == "role_assigned":
            self._render_role(event)
        elif event.type == "mafia_roster":
            self._render_small_panel(event, "MAFIA  ·  PRIVATE", "bright_magenta")
        elif event.type == "runoff_started":
            self._render_small_panel(event, "RUNOFF", "bright_yellow")
        elif event.type in {"player_eliminated", "night_death"}:
            self._mark_dead(event)
            title = "ELIMINATED" if event.type == "player_eliminated" else "NIGHT DEATH"
            self._render_resolution(event, title, "bright_red")
        elif event.type in {"no_elimination", "no_night_death"}:
            self._render_resolution(event, "NO DEATH", "bright_green")
        elif event.type == "game_ended":
            self._render_game_over(event)
        elif event.type == "game_aborted":
            self._render_resolution(event, "GAME ABORTED", "bright_red")
        elif event.type == "game_started":
            self._remember_players(event)
            self.console.print(self._styled_text(event.message, base_style="dim"), justify="center")
        elif event.visibility.audience is Audience.MAFIA:
            self._render_tagged(event, "MAFIA", "bright_magenta")
        elif event.visibility.audience is Audience.OMNISCIENT:
            self._render_tagged(event, "OMNISCIENT", "bright_cyan")
        elif event.visibility.seat is not None:
            self._render_tagged(event, f"PRIVATE #{event.visibility.seat}", "bright_yellow")
        else:
            self.console.print(self._styled_text(event.message))

    def _render_phase(self, event: GameEvent) -> None:
        phase = str(event.data.get("phase", ""))
        phase_label = PHASE_LABELS.get(phase, phase.replace("_", " ").title())
        is_day = phase.startswith(("day", "runoff"))
        if not phase:
            is_day = event.day is not None
        number = event.day if is_day else event.night
        period = "DAY" if is_day else "NIGHT"
        color = "bright_cyan" if is_day else "bright_magenta"

        title = Text()
        title.append(f"{period} {number or '—'}", style=f"bold {color}")
        if phase_label:
            title.append("  ·  ", style="dim")
            title.append(phase_label.upper(), style="bold white")

        self.console.line()
        self.console.print(
            Panel(
                self._styled_text(event.message, base_style="bold"),
                title=title,
                title_align="left",
                border_style=color,
                padding=(0, 2),
            )
        )
        self.console.line()

    def _render_discussion_lap(self, event: GameEvent) -> None:
        lap = optional_int(event.data.get("lap"))
        total_laps = optional_int(event.data.get("total_laps")) or 3
        label = Text()
        label.append(f"DAY {event.day or '—'}", style="bold bright_cyan")
        label.append("  ·  ", style="dim")
        label.append(f"DISCUSSION LAP {lap or '—'} / {total_laps}", style="bold white")
        self.console.print(Rule(label, style="cyan", align="left"))
        self.console.line()

    def _render_speech(self, event: GameEvent) -> None:
        seat = optional_int(event.data.get("seat"))
        if seat is None:
            self.console.print(self._styled_text(event.message))
            return

        passed = bool(event.data.get("passed", False))
        text = event.data.get("text")
        speech = text if isinstance(text, str) else self._message_body(event.message)
        if passed or not speech.strip():
            self.console.print(self._alive_line())
            line = Text("  ")
            line.append_text(self._player_label(seat))
            line.append(" passes.", style="italic dim")
            self.console.print(line)
            self.console.line()
            return

        title = Text()
        border_style = self._seat_style(seat)
        if event.type == "mafia_discussion":
            title.append("MAFIA PRIVATE  ·  ", style="bold bright_magenta")
            border_style = "bright_magenta"
        elif event.type == "runoff_defense":
            title.append("DEFENSE  ·  ", style="bold bright_yellow")
            border_style = "bright_yellow"
        title.append_text(self._player_label(seat))

        self.console.print(
            Panel(
                Group(self._alive_line(), Text(""), self._styled_text(speech)),
                title=title,
                title_align="left",
                border_style=border_style,
                padding=(0, 1),
            )
        )
        self.console.line()

    def _render_votes(self, event: GameEvent) -> None:
        raw_ballots = event.data.get("ballots")
        if not isinstance(raw_ballots, dict):
            self.console.print(self._styled_text(event.message, base_style="bold"))
            return

        ballots: list[tuple[int, int | None]] = []
        for raw_voter, raw_target in raw_ballots.items():
            voter = optional_int(raw_voter)
            target = optional_int(raw_target)
            if voter is not None and (target is not None or raw_target is None):
                ballots.append((voter, target))
        ballots.sort()

        # The engine calls an abstaining Mafia ballot "No attack"; only a Day
        # ballot elects nobody. Rendering both the same way would misreport the
        # Night to a Mafia viewer.
        is_mafia = event.type == "mafia_votes"
        no_target_label = "No attack" if is_mafia else "No elimination"

        table = Table.grid(padding=(0, 1))
        table.add_column(justify="right")
        table.add_column(justify="center", style="dim")
        table.add_column()
        for voter, target in ballots:
            table.add_row(
                self._player_label(voter),
                Text("→", style="dim"),
                self._choice_label(target, no_target_label),
            )

        tally = Counter(target for _voter, target in ballots)
        tally_line = Text("TALLY  ", style="bold dim")
        for index, (target, count) in enumerate(
            sorted(tally.items(), key=lambda item: (-item[1], item[0] is None, item[0] or 0))
        ):
            if index:
                tally_line.append("   ", style="dim")
            tally_line.append_text(self._choice_label(target, no_target_label))
            tally_line.append(f" × {count}", style="bold white")

        if is_mafia:
            heading = f"NIGHT {event.night or '—'}  ·  MAFIA BALLOTS"
            color = "bright_magenta"
        elif event.type == "runoff_votes_revealed":
            heading = f"DAY {event.day or '—'}  ·  RUNOFF BALLOTS"
            color = "bright_yellow"
        else:
            heading = f"DAY {event.day or '—'}  ·  BALLOTS"
            color = "bright_yellow"

        content = Group(table, Text(""), tally_line)
        self.console.print(
            Panel(
                content,
                title=Text(heading, style=f"bold {color}"),
                title_align="left",
                border_style=color,
                padding=(0, 2),
                expand=False,
            ),
            justify="center",
        )
        self.console.line()

    def _render_role(self, event: GameEvent) -> None:
        seat = optional_int(event.data.get("seat"))
        raw_role = str(event.data.get("role", "unknown"))
        raw_team = str(event.data.get("team", "unknown"))
        if seat is None:
            self._render_tagged(event, "PRIVATE", "bright_yellow")
            return

        role = _coerce(Role, raw_role)
        team = _coerce(Team, raw_team)
        if role is not None:
            self.player_roles[seat] = role
        self.alive_seats.add(seat)

        role_style = ROLE_STYLES[role] if role is not None else "white"
        team_style = TEAM_STYLES[team] if team is not None else "white"
        player_label = self._player_label(seat)
        player_width = max(
            cell_len(player_label.plain),
            *(cell_len(f"#{known_seat} {name}") for known_seat, name in self.player_names.items()),
        )
        role_width = max(len(raw_role), *(len(known_role.value) for known_role in Role))
        line = Text("PRIVATE  ", style="bold bright_yellow")
        line.append_text(player_label)
        line.append(" " * (player_width - cell_len(player_label.plain)))
        line.append("  ·  ", style="dim")
        line.append(raw_role.upper().ljust(role_width), style=f"bold {role_style}")
        line.append("  /  ", style="dim")
        line.append(raw_team.upper(), style=f"bold {team_style}")
        self.console.print(line)

    def _render_small_panel(self, event: GameEvent, title: str, color: str) -> None:
        self.console.print(
            Panel(
                self._styled_text(event.message),
                title=Text(title, style=f"bold {color}"),
                title_align="left",
                border_style=color,
                padding=(0, 1),
                expand=False,
            )
        )
        self.console.line()

    def _render_resolution(self, event: GameEvent, title: str, color: str) -> None:
        self.console.line()
        self.console.print(
            Panel(
                self._styled_text(event.message, base_style="bold"),
                title=Text(title, style=f"bold {color}"),
                border_style=color,
                padding=(0, 3),
                expand=False,
            ),
            justify="center",
        )
        self.console.line()

    def _render_game_over(self, event: GameEvent) -> None:
        winner = str(event.data.get("winner", "")).lower()
        color = "bright_magenta" if winner == Team.MAFIA.value else "bright_cyan"
        label = f"{winner.upper()} WINS" if winner else event.message.upper()
        self.console.line()
        self.console.print(
            Panel(
                Text(label, style=f"bold {color}", justify="center"),
                title=Text("GAME OVER", style="bold white"),
                border_style=color,
                padding=(1, 8),
                expand=False,
            ),
            justify="center",
        )
        self.console.line()

    def _render_tagged(self, event: GameEvent, tag: str, color: str) -> None:
        line = Text(f"{tag:<12}", style=f"bold {color}")
        line.append_text(self._styled_text(event.message))
        self.console.print(line)

    def _styled_text(self, value: str, *, base_style: str = "") -> Text:
        value = self._expand_player_labels(value)
        result = Text(value, style=base_style)
        for seat, name in self.player_names.items():
            pattern = rf"(?<!\w)#{seat}(?:\s+{re.escape(name)})?"
            for match in re.finditer(pattern, value):
                result.stylize(f"bold {self._seat_style(seat)}", match.start(), match.end())
        for role, style in ROLE_STYLES.items():
            for match in re.finditer(rf"\b{role.value}\b", value, flags=re.IGNORECASE):
                result.stylize(f"bold {style}", match.start(), match.end())
        return result

    def _expand_player_labels(self, value: str) -> str:
        for seat, name in sorted(self.player_names.items(), reverse=True):
            bare_seat = re.compile(rf"(?<!\w)#{seat}(?!\d)(?!\s+{re.escape(name)}(?:\b|$))")
            label = f"#{seat} {name}"
            value = bare_seat.sub(_literal_replacer(label), value)
        return value

    def _player_label(self, seat: int) -> Text:
        name = self.player_names.get(seat)
        label = f"#{seat} {name}" if name else f"#{seat}"
        return Text(label, style=f"bold {self._seat_style(seat)}")

    def _alive_line(self) -> Text:
        total = len(self.player_names)
        living = sorted(self.alive_seats)
        line = Text(f"ALIVE  {len(living)}/{total or len(living)}  ", style="bold green")
        for index, seat in enumerate(living):
            if index:
                line.append("  •  ", style="dim")
            line.append_text(self._player_label(seat))
        return line

    def _remember_players(self, event: GameEvent) -> None:
        players = event.data.get("players")
        if not isinstance(players, list):
            return
        for player in players:
            if not isinstance(player, dict):
                continue
            seat = optional_int(player.get("seat"))
            name = player.get("name")
            if seat is None or not isinstance(name, str):
                continue
            self.player_names[seat] = name
            self.alive_seats.add(seat)

    def _mark_dead(self, event: GameEvent) -> None:
        seat = optional_int(event.data.get("seat"))
        if seat is not None:
            self.alive_seats.discard(seat)

    def _choice_label(self, target: int | None, no_target_label: str) -> Text:
        if target is None:
            return Text(no_target_label, style="italic dim")
        return self._player_label(target)

    @staticmethod
    def _message_body(message: str) -> str:
        _separator, found, body = message.partition(": ")
        return body if found else message

    @staticmethod
    def _seat_style(seat: int) -> str:
        return SEAT_STYLES[(seat - 1) % len(SEAT_STYLES)]

    @staticmethod
    def _action_noun(kind: RequestKind) -> str:
        if kind in {RequestKind.DAY_VOTE, RequestKind.RUNOFF_VOTE}:
            return "their ballot"
        if kind is RequestKind.MAFIA_VOTE:
            return "their attack ballot"
        if kind is RequestKind.INVESTIGATE:
            return "their investigation"
        if kind is RequestKind.PROTECT:
            return "their protection"
        return "their response"
