from mafia_game.agents import default_drivers
from mafia_game.config import Provider


def test_default_registry_contains_only_live_integrations() -> None:
    drivers = default_drivers()

    assert set(drivers) == {
        Provider.OPENAI_COMPATIBLE,
        Provider.HUMAN,
        Provider.FAKE,
    }
