from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.text import Text

from mafia_game.agents import RequestKind
from mafia_game.config import Provider
from mafia_game.gameplay import RichGameObserver
from mafia_game.gameplay.models import (
    GameEvent,
    GameState,
    PlayerState,
    Role,
)


def observer_output() -> tuple[RichGameObserver, StringIO]:
    stream = StringIO()
    console = Console(
        file=stream,
        force_terminal=True,
        color_system="standard",
        width=90,
    )
    observer = RichGameObserver(
        console,
        player_names={1: "Ada", 2: "Babbage", 3: "Curie"},
    )
    return observer, stream


def plain_output(stream: StringIO) -> str:
    return Text.from_ansi(stream.getvalue()).plain


def state_for_human_view(*, human_role: Role = Role.CIVILIAN) -> GameState:
    return GameState(
        game_id="human-view",
        seed=1,
        players=[
            PlayerState(
                seat=1,
                name="You",
                provider=Provider.HUMAN,
                model="terminal",
                role=human_role,
            ),
            PlayerState(
                seat=2,
                name="Babbage",
                provider=Provider.FAKE,
                model="fake",
                role=Role.MAFIA,
            ),
            PlayerState(
                seat=3,
                name="Curie",
                provider=Provider.FAKE,
                model="fake",
                role=Role.DETECTIVE,
            ),
        ],
    )


def test_speech_uses_colored_card_and_treats_model_text_as_plain_text() -> None:
    observer, stream = observer_output()

    observer.render_event(
        GameEvent(
            type="speech",
            message="#1 Ada: I suspect #2. [red]Literal markup[/red]",
            data={
                "seat": 1,
                "text": "I suspect #2. [red]Literal markup[/red]",
                "passed": False,
                "lap": 1,
            },
            day=1,
        )
    )

    raw = stream.getvalue()
    plain = plain_output(stream)
    assert "\x1b[" in raw
    assert "╭─ #1 Ada" in plain
    assert "ALIVE  3/3" in plain
    assert "#1 Ada  •  #2 Babbage  •  #3 Curie" in plain
    assert "I suspect #2 Babbage. [red]Literal markup[/red]" in plain


def test_discussion_lap_uses_configured_total() -> None:
    observer, stream = observer_output()

    observer.render_event(
        GameEvent(
            type="discussion_lap_started",
            message="Day 1, discussion lap 1.",
            data={"lap": 1, "total_laps": 2},
            day=1,
        )
    )

    assert "DISCUSSION LAP 1 / 2" in plain_output(stream)


def test_expands_bare_seats_without_duplicating_existing_names() -> None:
    observer, stream = observer_output()

    observer.render_event(
        GameEvent(
            type="speech",
            message="#3 Curie: Compare #1, #2 Babbage, and #3's records.",
            data={
                "seat": 3,
                "text": "Compare #1, #2 Babbage, and #3's records.",
                "passed": False,
                "lap": 2,
            },
            day=2,
        )
    )

    plain = plain_output(stream)
    assert "Compare #1 Ada, #2 Babbage, and #3 Curie's records." in plain
    assert "#2 Babbage Babbage" not in plain


def test_vote_card_includes_ballots_and_aggregate_tally() -> None:
    observer, stream = observer_output()

    observer.render_event(
        GameEvent(
            type="day_votes_revealed",
            message="Day votes",
            data={"ballots": {"1": 3, "2": 3, "3": 2}},
            day=2,
        )
    )

    plain = plain_output(stream)
    assert "DAY 2  ·  BALLOTS" in plain
    assert "#1 Ada → #3 Curie" in plain
    assert "#3 Curie × 2" in plain
    assert "#2 Babbage × 1" in plain


def test_abstaining_ballots_use_the_label_that_matches_the_phase() -> None:
    """A Mafia ballot for nobody is "No attack"; only a Day ballot elects nobody."""
    observer, stream = observer_output()

    observer.render_event(
        GameEvent(
            type="day_votes_revealed",
            message="Day votes",
            data={"ballots": {"1": None, "2": 3}},
            day=2,
        )
    )
    observer.render_event(
        GameEvent(
            type="mafia_votes",
            message="Mafia votes",
            data={"ballots": {"2": None}},
            night=2,
        )
    )

    plain = plain_output(stream)
    assert "#1 Ada → No elimination" in plain
    assert "#2 Babbage → No attack" in plain


def test_private_role_reveals_align_player_and_role_columns() -> None:
    observer, stream = observer_output()

    for seat, role, team in (
        (1, "civilian", "town"),
        (2, "mafia", "mafia"),
        (3, "detective", "town"),
    ):
        observer.render_event(
            GameEvent(
                type="role_assigned",
                visibility=f"player:{seat}",
                message="role assigned",
                data={"seat": seat, "role": role, "team": team},
            )
        )

    lines = [line for line in plain_output(stream).splitlines() if line.startswith("PRIVATE")]
    assert lines == [
        "PRIVATE  #1 Ada      ·  CIVILIAN   /  TOWN",
        "PRIVATE  #2 Babbage  ·  MAFIA      /  MAFIA",
        "PRIVATE  #3 Curie    ·  DETECTIVE  /  TOWN",
    ]


def test_phase_banner_has_period_and_human_readable_phase() -> None:
    observer, stream = observer_output()

    observer.render_event(
        GameEvent(
            type="phase_started",
            message="Night 3 actions are being selected.",
            data={"phase": "night_actions"},
            night=3,
        )
    )

    plain = plain_output(stream)
    assert "NIGHT 3  ·  NIGHT ACTIONS" in plain
    assert "Night 3 actions are being selected." in plain


def test_eliminated_player_disappears_from_next_turn_alive_strip() -> None:
    observer, stream = observer_output()
    observer.render_event(
        GameEvent(
            type="player_eliminated",
            message="#2 Babbage is eliminated and was civilian.",
            data={"seat": 2, "role": "civilian"},
            day=1,
        )
    )
    output_before_turn = len(stream.getvalue())

    observer.render_event(
        GameEvent(
            type="speech",
            message="#3 Curie: Two of us remain.",
            data={"seat": 3, "text": "Two of us remain.", "passed": False, "lap": 1},
            day=2,
        )
    )

    turn_output = Text.from_ansi(stream.getvalue()[output_before_turn:]).plain
    assert "ALIVE  2/3" in turn_output
    assert "#1 Ada  •  #3 Curie" in turn_output
    assert "#2 Babbage" not in turn_output


def test_human_roster_reveals_only_the_viewers_role() -> None:
    observer, stream = observer_output()
    observer.perspective_seat = 1

    observer.show_roster(state_for_human_view())

    plain = plain_output(stream)
    assert "You  ·  YOU" in plain
    assert plain.count("HIDDEN") == 2
    assert "CIVILIAN" in plain


def test_human_view_filters_other_players_private_events_and_night_activity() -> None:
    observer, stream = observer_output()
    observer.perspective_seat = 1
    state = state_for_human_view()
    observer.show_roster(state)
    stream.seek(0)
    stream.truncate(0)

    observer.on_events(
        [
            GameEvent(type="note", message="PUBLIC_MARKER"),
            GameEvent(
                type="note",
                visibility="player:1",
                message="MY_PRIVATE_MARKER",
            ),
            GameEvent(
                type="note",
                visibility="player:2",
                message="OTHER_PRIVATE_MARKER",
            ),
            GameEvent(type="note", visibility="mafia", message="MAFIA_MARKER"),
            GameEvent(
                type="note",
                visibility="omniscient",
                message="OMNISCIENT_MARKER",
            ),
        ]
    )
    observer.on_thinking(state, seat=2, kind=RequestKind.INVESTIGATE)
    observer.on_thinking(state, seat=2, kind=RequestKind.SPEECH)

    plain = plain_output(stream)
    assert "PUBLIC_MARKER" in plain
    assert "MY_PRIVATE_MARKER" in plain
    assert "OTHER_PRIVATE_MARKER" not in plain
    assert "MAFIA_MARKER" not in plain
    assert "OMNISCIENT_MARKER" not in plain
    assert "composing a public statement" in plain
    assert "choosing an investigation" not in plain


def test_human_mafia_view_receives_team_events() -> None:
    observer, stream = observer_output()
    observer.perspective_seat = 1
    observer.show_roster(state_for_human_view(human_role=Role.MAFIA))
    stream.seek(0)
    stream.truncate(0)

    observer.on_events([GameEvent(type="note", visibility="mafia", message="TEAM_MARKER")])

    assert "TEAM_MARKER" in plain_output(stream)


def test_dead_human_mafia_stops_receiving_team_events() -> None:
    observer, stream = observer_output()
    observer.perspective_seat = 1
    state = state_for_human_view(human_role=Role.MAFIA)
    observer.show_roster(state)
    stream.seek(0)
    stream.truncate(0)

    observer.on_events(
        [
            GameEvent(
                type="player_eliminated",
                message="#1 You died and was mafia.",
                data={"seat": 1, "role": "mafia"},
            ),
            GameEvent(type="note", visibility="mafia", message="AFTER_DEATH_MARKER"),
        ]
    )
    observer.on_thinking(state, seat=2, kind=RequestKind.MAFIA_DISCUSSION)

    plain = plain_output(stream)
    assert "AFTER_DEATH_MARKER" not in plain
    assert "planning privately" not in plain
