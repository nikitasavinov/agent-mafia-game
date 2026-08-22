"""The default provider-to-driver mapping.

Kept separate from the drivers themselves so tests can swap in fakes without
importing a concrete transport.
"""

from __future__ import annotations

from mafia_game.config import Provider

from .contracts import PlayerDriver
from .providers import FakeDriver, HumanDriver, OpenAICompatibleDriver


def default_drivers() -> dict[Provider, PlayerDriver]:
    return {
        Provider.OPENAI_COMPATIBLE: OpenAICompatibleDriver(),
        Provider.HUMAN: HumanDriver(),
        Provider.FAKE: FakeDriver(),
    }
