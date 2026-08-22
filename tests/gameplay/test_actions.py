from __future__ import annotations

import pytest

from mafia_game.gameplay.turns import parse_target


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"target_seat": 3}', 3),
        ('```json\n{"target_seat": null}\n```', None),
    ],
)
def test_parse_target(text: str, expected: int | None) -> None:
    target, error = parse_target(text, [2, 3], allow_none=True)
    assert error is None
    assert target == expected


@pytest.mark.parametrize(
    "text",
    [
        "I vote 3",
        '{"target_seat": 8}',
        '{"target_seat": true}',
        '{"target_seat": 3, "reason": "because"}',
        "```json\n{}\n``` trailing",
    ],
)
def test_rejects_malformed_or_illegal_target(text: str) -> None:
    _target, error = parse_target(text, [2, 3], allow_none=False)
    assert error is not None
