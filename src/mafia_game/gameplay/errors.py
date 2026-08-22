"""Expected runtime conditions that stop a game safely."""


class GameAbortError(RuntimeError):
    """A gameplay/runtime condition that should abort rather than be ignored."""


class ProviderRejectedError(GameAbortError):
    """A provider rejected a request in a way retrying cannot fix."""


class AgentFailureLimitError(GameAbortError):
    """Too many model requests failed without an intervening success."""


class GameStalledError(GameAbortError):
    """No player has been eliminated for the configured number of Days."""
