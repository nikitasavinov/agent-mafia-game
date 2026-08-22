from __future__ import annotations

from collections import Counter

from hypothesis import given
from hypothesis import strategies as st

from mafia_game.gameplay.rules import tabulate


@given(
    st.lists(
        st.one_of(st.none(), st.integers(min_value=1, max_value=12)),
        min_size=1,
        max_size=12,
    )
)
def test_plurality_result_matches_counts(choices: list[int | None]) -> None:
    ballots = dict(enumerate(choices, start=1))
    tally = tabulate(ballots)
    counts = Counter(choices)
    top = max(counts.values())
    expected = {choice for choice, count in counts.items() if count == top}
    if tally.unique:
        assert expected == {tally.winner}
    else:
        assert set(tally.tied) == expected
