from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mafia_game.agents import PlayerRequest, RequestKind, ResultStatus
from mafia_game.agents.providers.openai_compatible import OpenAICompatibleDriver
from mafia_game.config import PlayerConfig, Provider, ReasoningEffort


def api_player(
    *,
    api_key: str | None = None,
    api_key_env: str | None = "MAFIA_TEST_API_KEY",
    reasoning_effort: ReasoningEffort | None = None,
) -> PlayerConfig:
    return PlayerConfig(
        name="HTTP Agent",
        provider=Provider.OPENAI_COMPATIBLE,
        model="test-chat-model",
        base_url="https://models.example/v1/",
        api_key=api_key,
        api_key_env=api_key_env,
        reasoning_effort=reasoning_effort,
    )


def request(prompt: str, *, timeout: float = 2) -> PlayerRequest:
    return PlayerRequest(
        turn_id=prompt,
        seat=1,
        kind=RequestKind.SPEECH,
        prompt=prompt,
        timeout_seconds=timeout,
    )


@pytest.mark.asyncio
async def test_openai_compatible_chat_contract_and_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAFIA_TEST_API_KEY", "test-secret")
    payloads: list[dict[str, Any]] = []

    async def handle(raw_request: httpx.Request) -> httpx.Response:
        assert raw_request.url == "https://models.example/v1/chat/completions"
        assert raw_request.headers["authorization"] == "Bearer test-secret"
        payload = json.loads(raw_request.content)
        payloads.append(payload)
        turn = len(payloads)
        return httpx.Response(
            200,
            json={
                "id": f"completion-{turn}",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": f"answer {turn}"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )

    driver = OpenAICompatibleDriver(transport=httpx.MockTransport(handle))
    session = await driver.open(api_player(), Path())
    try:
        first = await session.respond(request("turn one"))
        second = await session.respond(request("turn two"))
    finally:
        await session.close()

    assert first.ok
    assert first.text == "answer 1"
    assert first.provider_request_id == "completion-1"
    assert first.usage["total_tokens"] == 12
    assert second.text == "answer 2"
    assert payloads[0] == {
        "model": "test-chat-model",
        "messages": [{"role": "user", "content": "turn one"}],
        "stream": False,
    }
    assert payloads[1]["messages"] == [
        {"role": "user", "content": "turn one"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user", "content": "turn two"},
    ]


@pytest.mark.asyncio
async def test_openai_compatible_sends_reasoning_effort() -> None:
    payloads: list[dict[str, Any]] = []

    async def handle(raw_request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(raw_request.content))
        return httpx.Response(
            200,
            json={
                "id": "reasoning-completion",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "answer"},
                    }
                ],
            },
        )

    driver = OpenAICompatibleDriver(transport=httpx.MockTransport(handle))
    session = await driver.open(
        api_player(api_key_env=None, reasoning_effort=ReasoningEffort.HIGH), Path()
    )
    try:
        result = await session.respond(request("think"))
    finally:
        await session.close()

    assert result.ok
    assert payloads[0]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_openai_compatible_timeout() -> None:
    async def handle(raw_request: httpx.Request) -> httpx.Response:
        del raw_request
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    driver = OpenAICompatibleDriver(transport=httpx.MockTransport(handle))
    session = await driver.open(api_player(api_key_env=None), Path())
    try:
        result = await session.respond(request("slow", timeout=0.01))
    finally:
        await session.close()

    assert result.status is ResultStatus.TIMEOUT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (402, ResultStatus.FATAL_ERROR),
        (429, ResultStatus.TRANSPORT_ERROR),
        (503, ResultStatus.TRANSPORT_ERROR),
    ],
)
async def test_openai_compatible_classifies_http_failures(
    status_code: int,
    expected: ResultStatus,
) -> None:
    async def handle(raw_request: httpx.Request) -> httpx.Response:
        del raw_request
        return httpx.Response(
            status_code,
            json={"error": {"message": "provider rejected the request"}},
        )

    driver = OpenAICompatibleDriver(transport=httpx.MockTransport(handle))
    session = await driver.open(api_player(api_key_env=None), Path())
    try:
        result = await session.respond(request("speak"))
    finally:
        await session.close()

    assert result.status is expected
    assert result.detail == f"HTTP {status_code}: provider rejected the request"


@pytest.mark.asyncio
async def test_openai_compatible_inline_api_key_is_used_but_not_exposed() -> None:
    inline_secret = "inline-test-secret"

    async def handle(raw_request: httpx.Request) -> httpx.Response:
        assert raw_request.headers["authorization"] == f"Bearer {inline_secret}"
        return httpx.Response(
            200,
            json={
                "id": "completion-inline",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "READY"},
                    }
                ],
            },
        )

    player = api_player(api_key=inline_secret, api_key_env=None)
    driver = OpenAICompatibleDriver(transport=httpx.MockTransport(handle))
    checks = await driver.preflight(player, Path(), live=False)
    session = await driver.open(player, Path())
    try:
        result = await session.respond(request("hello"))
    finally:
        await session.close()

    assert result.ok
    assert all(check.ok for check in checks)
    assert any(check.detail == "inline API key configured" for check in checks)
    assert inline_secret not in repr(checks)


@pytest.mark.asyncio
async def test_openai_compatible_preflight_does_not_expose_or_probe_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAFIA_TEST_API_KEY", raising=False)
    calls = 0

    async def handle(raw_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del raw_request
        calls += 1
        return httpx.Response(500)

    driver = OpenAICompatibleDriver(transport=httpx.MockTransport(handle))
    checks = await driver.preflight(api_player(), Path(), live=False)

    assert not all(check.ok for check in checks)
    assert any(check.detail == "$MAFIA_TEST_API_KEY is not set" for check in checks)
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.PoolTimeout("pool timed out"),
    ],
)
async def test_transport_timeouts_are_reported_as_timeouts(
    monkeypatch: pytest.MonkeyPatch,
    raised: httpx.TimeoutException,
) -> None:
    """httpx timeouts are HTTPErrors, not TimeoutErrors, and must not be misfiled.

    Reporting these as TRANSPORT_ERROR silently skews the failure breakdown in
    `mafia stats`.
    """
    monkeypatch.setenv("MAFIA_TEST_API_KEY", "test-secret")

    def handle(raw_request: httpx.Request) -> httpx.Response:
        del raw_request
        raise raised

    driver = OpenAICompatibleDriver(transport=httpx.MockTransport(handle))
    session = await driver.open(api_player(), Path())
    try:
        result = await session.respond(request("speak"))
    finally:
        await session.close()

    assert result.status is ResultStatus.TIMEOUT
