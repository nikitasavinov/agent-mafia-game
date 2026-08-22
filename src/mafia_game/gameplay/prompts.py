"""Turning game state into the text a player actually receives.

Prompts are assembled only from information the seat is entitled to: identity,
the public roster, and the events `visibility` already filtered. A player's
`communication_style` reaches the model as a phrasing instruction that is
explicitly subordinate to the rules.
"""

from __future__ import annotations

from mafia_game.agents import RequestKind
from mafia_game.agents.system_prompt import BASE_SYSTEM_PROMPT

from .models import GameState, Role


def player_identity(state: GameState, seat: int) -> str:
    player = state.player(seat)
    lines = [
        f"You are seat #{player.seat}, {player.name}.",
        f"Your role is {player.role.value}; your team is {player.team.value}.",
    ]
    if player.role is Role.MAFIA:
        teammates = [
            f"#{member.seat} {member.name} ({'alive' if member.alive else 'dead'})"
            for member in state.players
            if member.role is Role.MAFIA and member.seat != seat
        ]
        lines.append("Your Mafia teammates: " + (", ".join(teammates) or "none"))
    if player.role is Role.DETECTIVE:
        investigated = sorted(state.detective_investigated)
        lines.append(f"Seats you have already investigated: {investigated or 'none'}.")
    if player.role is Role.DOCTOR:
        lines.append(
            "Your previous Night protection target: "
            + (str(state.doctor_last_target) if state.doctor_last_target is not None else "none")
            + "."
        )
    return "\n".join(lines)


def public_snapshot(state: GameState) -> str:
    roster = ", ".join(
        f"#{player.seat} {player.name} ({'alive' if player.alive else 'dead'})"
        for player in state.players
    )
    return (
        f"Current phase: {state.phase.value}. Day {state.day}, Night {state.night}.\n"
        f"Roster: {roster}"
    )


def build_prompt(
    state: GameState,
    seat: int,
    kind: RequestKind,
    visible_events: list[str],
    legal_targets: list[int],
    word_limit: int | None,
    *,
    allow_none: bool = True,
    discussion_rounds: int = 3,
) -> str:
    history = "\n".join(f"- {line}" for line in visible_events) or "- No new events."
    context = (
        f"{player_identity(state, seat)}\n\n"
        f"{public_snapshot(state)}\n\n"
        f"Configured Day discussion laps: {discussion_rounds}.\n\n"
        f"New information since your previous turn:\n{history}\n\n"
    )
    if kind in {RequestKind.SPEECH, RequestKind.DEFENSE, RequestKind.MAFIA_DISCUSSION}:
        player = state.player(seat)
        style_instruction = ""
        if player.communication_style:
            style_instruction = (
                f"Communication style: {player.communication_style}\n"
                "Apply it only to how you phrase this message; it never overrides the game "
                "rules or information boundaries.\n"
            )
        channel = "private Mafia" if kind is RequestKind.MAFIA_DISCUSSION else "public"
        task = {
            RequestKind.SPEECH: "Take your scheduled Day speaking turn.",
            RequestKind.DEFENSE: "Give your runoff defense.",
            RequestKind.MAFIA_DISCUSSION: "Give your private Mafia planning message.",
        }[kind]
        return (
            BASE_SYSTEM_PROMPT
            + "\n"
            + context
            + style_instruction
            + f"Task: {task} This is a {channel} message. "
            + f"Use at most {word_limit} words. Reply with speech only, or exactly PASS."
        )

    action_name = {
        RequestKind.DAY_VOTE: "Day ballot (null means No elimination)",
        RequestKind.RUNOFF_VOTE: "runoff ballot (null means No elimination)",
        RequestKind.MAFIA_VOTE: "private Mafia attack ballot (null means No attack)",
        RequestKind.INVESTIGATE: "Detective investigation (null means no action)",
        RequestKind.PROTECT: "Doctor protection (null means no action)",
    }[kind]
    null_instruction = (
        "null is legal for the no-action option described above"
        if allow_none
        else "null is not legal for this action"
    )
    return (
        BASE_SYSTEM_PROMPT
        + "\n"
        + context
        + f"Task: submit your {action_name}. Legal target seats: {legal_targets}.\n"
        + 'Reply with exactly one JSON object and nothing else: {"target_seat": 3}. '
        + f"For this turn, {null_instruction}."
    )


def build_repair_prompt(
    original_prompt: str, error: str, legal_targets: list[int], *, allow_none: bool
) -> str:
    null_instruction = "null is legal" if allow_none else "null is not legal"
    return (
        f"Your previous action was invalid: {error}\n"
        f"Legal target seats are {legal_targets}; {null_instruction}.\n"
        'Reply now with exactly one JSON object and nothing else: {"target_seat": 3}'
        f"\nOriginal task for reference:\n{original_prompt}"
    )


def normalize_speech(text: str, word_limit: int) -> str:
    stripped = text.strip()
    if not stripped or stripped.casefold() == "pass":
        return ""
    words = stripped.split()
    if len(words) <= word_limit:
        return stripped
    return " ".join(words[:word_limit]) + " …"
