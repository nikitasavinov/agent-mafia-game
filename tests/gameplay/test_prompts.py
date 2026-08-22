from __future__ import annotations

from collections.abc import Callable

from mafia_game.agents import RequestKind
from mafia_game.config import AppConfig
from mafia_game.gameplay.prompts import build_prompt
from mafia_game.gameplay.rules import create_game


def test_communication_style_applies_only_to_free_form_messages(
    config_factory: Callable[..., AppConfig],
) -> None:
    config = config_factory()
    config.players[0].communication_style = "Speak like a caveman."
    state, _ = create_game("styled", config, seed=3)

    speech = build_prompt(
        state,
        seat=1,
        kind=RequestKind.SPEECH,
        visible_events=[],
        legal_targets=[],
        word_limit=40,
        discussion_rounds=2,
    )
    ballot = build_prompt(
        state,
        seat=1,
        kind=RequestKind.DAY_VOTE,
        visible_events=[],
        legal_targets=[2, 3, 4, 5, 6],
        word_limit=None,
    )

    assert "Communication style: Speak like a caveman." in speech
    assert "Configured Day discussion laps: 2." in speech
    assert "always identify another player with both seat and name" in speech
    assert "Speak like a caveman." not in ballot
