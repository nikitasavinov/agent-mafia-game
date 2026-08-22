"""Concrete drivers, one per provider.

Each adapts an HTTP endpoint, a terminal prompt, or a deterministic test fake
to the single contract in `agents.contracts`.
"""

from .fake import FakeDriver
from .human import HumanDriver, HumanSession
from .openai_compatible import OpenAICompatibleDriver

__all__ = [
    "FakeDriver",
    "HumanDriver",
    "HumanSession",
    "OpenAICompatibleDriver",
]
