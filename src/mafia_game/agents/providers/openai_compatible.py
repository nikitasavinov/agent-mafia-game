"""Driver for any OpenAI-compatible chat-completions endpoint.

Covers the OpenAI API itself, local Ollama, and anything else exposing the same
route. The session keeps its own message history, since these endpoints are
stateless between calls; see `OpenAICompatibleSession.respond` for the cost that
implies over a long game.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx

from mafia_game.config import PlayerConfig

from ..contracts import (
    DriverCheck,
    DriverSession,
    PlayerRequest,
    PlayerResult,
    RequestKind,
    ResultStatus,
)


class OpenAICompatibleSession:
    def __init__(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        model: str,
        reasoning_effort: str | None,
    ) -> None:
        self.client = client
        self.endpoint = endpoint
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.messages: list[dict[str, str]] = []

    async def respond(self, request: PlayerRequest) -> PlayerResult:
        user_message = {"role": "user", "content": request.prompt}
        messages = [*self.messages, user_message]
        request_payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if self.reasoning_effort is not None:
            # Chat Completions uses the top-level OpenAI parameter. OpenRouter
            # accepts the same field as shorthand for `reasoning.effort`.
            request_payload["reasoning_effort"] = self.reasoning_effort
        try:
            async with asyncio.timeout(request.timeout_seconds):
                response = await self.client.post(
                    self.endpoint,
                    json=request_payload,
                    timeout=request.timeout_seconds,
                )
        # httpx.TimeoutException is an httpx.HTTPError, not a TimeoutError, so it
        # must be caught first or every read timeout is misfiled as a transport error.
        except (TimeoutError, httpx.TimeoutException):
            return PlayerResult(
                status=ResultStatus.TIMEOUT,
                detail="OpenAI-compatible request timed out",
            )
        except httpx.HTTPError as error:
            return PlayerResult(status=ResultStatus.TRANSPORT_ERROR, detail=str(error))

        if not response.is_success:
            return PlayerResult(
                status=_http_result_status(response.status_code),
                raw=response.text,
                provider_request_id=response.headers.get("x-request-id"),
                detail=_http_error_detail(response),
            )

        try:
            payload: Any = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            text = _message_text(message)
            refusal = message.get("refusal") if isinstance(message, dict) else None
            finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        except (KeyError, IndexError, TypeError, ValueError) as error:
            return PlayerResult(
                status=ResultStatus.TRANSPORT_ERROR,
                raw=response.text,
                provider_request_id=response.headers.get("x-request-id"),
                detail=f"Malformed OpenAI-compatible response: {error}",
            )

        if not text and isinstance(refusal, str):
            text = refusal
        status = (
            ResultStatus.REFUSAL
            if finish_reason == "content_filter" or (isinstance(refusal, str) and bool(refusal))
            else ResultStatus.SUCCESS
        )
        # The transcript grows for the whole game and is resent every turn: these
        # endpoints are stateless, and a player reasoning about earlier Days needs
        # its own prior turns. Cost therefore grows quadratically in game length,
        # which is acceptable for a 6-12 player game and is the documented limit.
        self.messages.extend([user_message, {"role": "assistant", "content": text}])
        raw_usage = payload.get("usage") if isinstance(payload, dict) else None
        response_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(response_id, str):
            response_id = response.headers.get("x-request-id")
        return PlayerResult(
            status=status,
            text=text,
            raw=text,
            provider_request_id=response_id,
            usage=dict(raw_usage) if isinstance(raw_usage, dict) else {},
            detail=None if status is ResultStatus.SUCCESS else str(finish_reason or "refusal"),
        )

    async def close(self) -> None:
        await self.client.aclose()


class OpenAICompatibleDriver:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def preflight(
        self, player: PlayerConfig, workspace: Path, *, live: bool = False
    ) -> list[DriverCheck]:
        del workspace
        if player.base_url is None:
            raise ValueError("OpenAI-compatible players require a base URL")
        checks = [
            DriverCheck(
                name="OpenAI-compatible endpoint",
                ok=True,
                detail=player.base_url,
            )
        ]
        if player.api_key is not None:
            checks.append(
                DriverCheck(
                    name="OpenAI-compatible auth",
                    ok=True,
                    detail="inline API key configured",
                )
            )
        elif player.api_key_env is None:
            checks.append(
                DriverCheck(
                    name="OpenAI-compatible auth",
                    ok=True,
                    detail="keyless endpoint configured",
                )
            )
        else:
            configured = bool(os.getenv(player.api_key_env, "").strip())
            checks.append(
                DriverCheck(
                    name="OpenAI-compatible auth",
                    ok=configured,
                    detail=(
                        f"${player.api_key_env} is set"
                        if configured
                        else f"${player.api_key_env} is not set"
                    ),
                )
            )

        if live and all(check.ok for check in checks):
            session: DriverSession | None = None
            try:
                session = await self.open(player, Path())
                probe = await session.respond(
                    PlayerRequest(
                        turn_id="doctor-probe",
                        seat=0,
                        kind=RequestKind.PROBE,
                        prompt="Reply with exactly READY.",
                        timeout_seconds=60,
                    )
                )
                checks.append(
                    DriverCheck(
                        name=f"OpenAI-compatible model {player.model}",
                        ok=probe.ok,
                        detail=probe.text.strip() if probe.ok else (probe.detail or "probe failed"),
                    )
                )
            except Exception as error:
                checks.append(
                    DriverCheck(
                        name=f"OpenAI-compatible model {player.model}",
                        ok=False,
                        detail=str(error),
                    )
                )
            finally:
                if session is not None:
                    await session.close()
        return checks

    async def open(self, player: PlayerConfig, workspace: Path) -> DriverSession:
        del workspace
        if player.base_url is None:
            raise ValueError("OpenAI-compatible players require a base URL")
        headers = {"Content-Type": "application/json"}
        if player.api_key is not None:
            headers["Authorization"] = f"Bearer {player.api_key.get_secret_value()}"
        elif player.api_key_env is not None:
            api_key = os.getenv(player.api_key_env, "").strip()
            if not api_key:
                raise RuntimeError(f"API key environment variable ${player.api_key_env} is not set")
            headers["Authorization"] = f"Bearer {api_key}"
        client = httpx.AsyncClient(headers=headers, transport=self.transport)
        return OpenAICompatibleSession(
            client=client,
            endpoint=f"{player.base_url}/chat/completions",
            model=player.model,
            reasoning_effort=(
                player.reasoning_effort.value if player.reasoning_effort is not None else None
            ),
        )


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        raise TypeError("choice.message must be an object")
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "".join(parts)
    raise TypeError("choice.message.content must be text")


def _http_error_detail(response: httpx.Response) -> str:
    message = ""
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
        elif isinstance(error, str):
            message = error
    except ValueError:
        message = response.text.strip()
    suffix = f": {message[:500]}" if message else ""
    return f"HTTP {response.status_code}{suffix}"


def _http_result_status(status_code: int) -> ResultStatus:
    """Separate permanent request/account errors from failures worth retrying."""
    retryable_client_errors = {408, 409, 425, 429}
    if 400 <= status_code < 500 and status_code not in retryable_client_errors:
        return ResultStatus.FATAL_ERROR
    return ResultStatus.TRANSPORT_ERROR
