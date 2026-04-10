from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator as AsyncIteratorAbc
import contextlib
import copy
import json
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

_LOCAL_WARMUP_RESPONSE_ID_PREFIX = "bridge_warmup_"


@dataclass(frozen=True)
class BridgeConfig:
    upstream_base_url: str
    upstream_api_key: str
    host: str = "127.0.0.1"
    port: int = 8765

    @property
    def local_base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"


def _strip_hop_by_hop_headers(headers: httpx.Headers) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in _HOP_BY_HOP_HEADERS:
            continue
        sanitized[name] = value
    return sanitized


def _build_upstream_headers(request_headers: Any, api_key: str) -> dict[str, str]:
    forwarded: dict[str, str] = {}
    for name, value in request_headers.items():
        lowered = name.lower()
        if lowered in _HOP_BY_HOP_HEADERS or lowered == "host" or lowered == "authorization":
            continue
        if lowered.startswith("sec-websocket-"):
            continue
        forwarded[name] = value
    forwarded["Authorization"] = f"Bearer {api_key}"
    return forwarded


def _ws_request_to_responses_payload(payload: dict[str, Any]) -> dict[str, Any]:
    request_type = payload.get("type")
    if request_type != "response.create":
        raise ValueError(f"Unsupported websocket request type: {request_type}")
    upstream_payload = dict(payload)
    upstream_payload.pop("type", None)
    return upstream_payload


def _is_local_warmup_request(payload: dict[str, Any]) -> bool:
    return (
        payload.get("generate") is False
        and payload.get("stream") is True
        and payload.get("previous_response_id") is None
    )


def _local_warmup_response_id() -> str:
    return f"{_LOCAL_WARMUP_RESPONSE_ID_PREFIX}{uuid.uuid4().hex}"


def _rewrite_previous_response_id(
    payload: dict[str, Any],
    prior_requests: dict[str, dict[str, Any]],
    prior_response_items: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    previous_response_id = payload.get("previous_response_id")
    if not isinstance(previous_response_id, str):
        return copy.deepcopy(payload)

    base_request = prior_requests.get(previous_response_id)
    if base_request is None:
        if previous_response_id.startswith(_LOCAL_WARMUP_RESPONSE_ID_PREFIX):
            raise ValueError(f"Unknown synthetic previous_response_id: {previous_response_id}")
        return copy.deepcopy(payload)

    rewritten = copy.deepcopy(base_request)
    rewritten.pop("generate", None)
    for key, value in payload.items():
        if key in {"input", "previous_response_id"}:
            continue
        rewritten[key] = copy.deepcopy(value)

    base_input = base_request.get("input")
    current_input = payload.get("input")
    if isinstance(base_input, list) and isinstance(current_input, list):
        rewritten["input"] = (
            copy.deepcopy(base_input)
            + _matching_function_call_items(
                current_input,
                prior_response_items.get(previous_response_id, []),
            )
            + copy.deepcopy(current_input)
        )
    elif isinstance(base_input, list):
        rewritten["input"] = copy.deepcopy(base_input)
    elif current_input is not None:
        rewritten["input"] = copy.deepcopy(current_input)

    rewritten.pop("previous_response_id", None)

    return rewritten


def _matching_function_call_items(
    current_input: list[dict[str, Any]],
    prior_response_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    call_ids = {
        call_id
        for item in current_input
        if item.get("type") == "function_call_output"
        and isinstance(call_id := item.get("call_id"), str)
    }
    matched_items: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    for item in prior_response_items:
        call_id = item.get("call_id")
        if (
            item.get("type") == "function_call"
            and isinstance(call_id, str)
            and call_id in call_ids
            and call_id not in seen_call_ids
        ):
            matched_items.append(copy.deepcopy(item))
            seen_call_ids.add(call_id)
    return matched_items


class ResponsesBridge:
    def __init__(self, config: BridgeConfig) -> None:
        self._config = config
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._startup_error: Exception | None = None
        self._shutdown_complete = threading.Event()
        self._app = self._build_app()

    @property
    def app(self) -> Starlette:
        return self._app

    @property
    def local_base_url(self) -> str:
        return self._config.local_base_url

    def start(self) -> None:
        if self._thread is not None:
            return

        self._startup_error = None
        self._started.clear()
        self._shutdown_complete.clear()
        self._thread = threading.Thread(target=self._run_server, name="responses-bridge", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=10):
            raise RuntimeError("responses bridge did not start within 10 seconds")
        if self._startup_error is not None:
            raise RuntimeError("responses bridge failed to start") from self._startup_error

    def stop(self) -> None:
        if self._server is None or self._thread is None:
            return
        self._server.should_exit = True
        self._shutdown_complete.wait(timeout=10)
        self._thread.join(timeout=10)
        self._thread = None
        self._server = None

    def _run_server(self) -> None:
        config = uvicorn.Config(
            self._app,
            host=self._config.host,
            port=self._config.port,
            log_level="warning",
            lifespan="on",
        )
        server = uvicorn.Server(config)
        self._server = server
        try:
            asyncio.run(server.serve())
        except Exception as exc:
            self._startup_error = exc
            self._started.set()
            logger.exception("responses bridge crashed")
        finally:
            self._shutdown_complete.set()

    def _build_app(self) -> Starlette:
        @asynccontextmanager
        async def lifespan(_: Starlette) -> AsyncIteratorAbc[None]:
            self._started.set()
            yield

        async def health(_: Request) -> Response:
            return JSONResponse({"ok": True})

        async def models(request: Request) -> Response:
            url = f"{self._config.upstream_base_url}/models"
            params = list(request.query_params.multi_items())
            headers = _build_upstream_headers(request.headers, self._config.upstream_api_key)
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                upstream = await client.get(url, params=params, headers=headers)
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=_strip_hop_by_hop_headers(upstream.headers),
            )

        async def responses(request: Request) -> Response:
            body = await request.body()
            url = f"{self._config.upstream_base_url}/responses"
            headers = _build_upstream_headers(request.headers, self._config.upstream_api_key)
            if "text/event-stream" in request.headers.get("accept", ""):
                client = httpx.AsyncClient(timeout=None, follow_redirects=True)
                upstream = await client.send(
                    client.build_request("POST", url, content=body, headers=headers),
                    stream=True,
                )

                async def stream_body() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in upstream.aiter_raw():
                            yield chunk
                    finally:
                        await upstream.aclose()
                        await client.aclose()

                return StreamingResponse(
                    stream_body(),
                    status_code=upstream.status_code,
                    headers=_strip_hop_by_hop_headers(upstream.headers),
                )

            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                upstream = await client.post(url, content=body, headers=headers)
                return Response(
                    content=upstream.content,
                    status_code=upstream.status_code,
                    headers=_strip_hop_by_hop_headers(upstream.headers),
                )

        async def responses_ws(websocket: WebSocket) -> None:
            await websocket.accept()
            prior_requests: dict[str, dict[str, Any]] = {}
            prior_response_items: dict[str, list[dict[str, Any]]] = {}
            try:
                while True:
                    request_text = await websocket.receive_text()
                    payload = json.loads(request_text)
                    upstream_payload = _ws_request_to_responses_payload(payload)
                    if _is_local_warmup_request(upstream_payload):
                        response_id = _local_warmup_response_id()
                        prior_requests[response_id] = copy.deepcopy(upstream_payload)
                        prior_response_items[response_id] = []
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "response.created",
                                    "response": {
                                        "id": response_id,
                                    },
                                }
                            )
                        )
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "response.completed",
                                    "response": {
                                        "id": response_id,
                                    },
                                }
                            )
                        )
                        continue
                    upstream_payload = _rewrite_previous_response_id(
                        upstream_payload,
                        prior_requests,
                        prior_response_items,
                    )
                    await self._stream_upstream_sse(
                        websocket,
                        upstream_payload,
                        prior_requests,
                        prior_response_items,
                    )
            except WebSocketDisconnect:
                return
            except ValueError as exc:
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "status": 400,
                                "error": {
                                    "type": "invalid_request_error",
                                    "message": str(exc),
                                },
                            }
                        )
                    )
                    await websocket.close()
            except Exception as exc:
                logger.exception("responses websocket bridge failed")
                error_payload = {
                    "type": "error",
                    "status": 500,
                    "error": {
                        "type": "server_error",
                        "message": str(exc),
                    },
                }
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.send_text(json.dumps(error_payload))
                    await websocket.close()

        return Starlette(
            lifespan=lifespan,
            routes=[
                Route("/health", health, methods=["GET"]),
                Route("/v1/models", models, methods=["GET"]),
                Route("/v1/responses", responses, methods=["POST"]),
                WebSocketRoute("/v1/responses", responses_ws),
            ]
        )

    async def _stream_upstream_sse(
        self,
        websocket: WebSocket,
        payload: dict[str, Any],
        prior_requests: dict[str, dict[str, Any]],
        prior_response_items: dict[str, list[dict[str, Any]]],
    ) -> None:
        upstream_payload = dict(payload)
        upstream_payload["stream"] = True
        url = f"{self._config.upstream_base_url}/responses"
        headers = _build_upstream_headers(websocket.headers, self._config.upstream_api_key)
        headers["Accept"] = "text/event-stream"
        headers["Content-Type"] = "application/json"
        response_id: str | None = None

        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            async with client.stream("POST", url, headers=headers, json=upstream_payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    try:
                        detail = json.loads(body.decode("utf-8"))
                    except Exception:
                        detail = {"message": body.decode("utf-8", errors="replace")}
                    logger.warning(
                        "upstream responses request failed status=%s previous_response_id=%s input_items=%s detail=%s",
                        response.status_code,
                        payload.get("previous_response_id"),
                        len(payload.get("input", [])) if isinstance(payload.get("input"), list) else None,
                        detail,
                    )
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "status": response.status_code,
                                "error": {
                                    "type": detail.get("type", "upstream_error")
                                    if isinstance(detail, dict)
                                    else "upstream_error",
                                    "message": detail.get("message", body.decode("utf-8", errors="replace"))
                                    if isinstance(detail, dict)
                                    else body.decode("utf-8", errors="replace"),
                                },
                            }
                        )
                    )
                    return

                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        data = "\n".join(data_lines).strip()
                        data_lines.clear()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            event = None
                        if isinstance(event, dict) and event.get("type") == "response.created":
                            response_obj = event.get("response")
                            if isinstance(response_obj, dict):
                                created_response_id = response_obj.get("id")
                                if isinstance(created_response_id, str):
                                    response_id = created_response_id
                                    prior_requests[response_id] = copy.deepcopy(payload)
                                    prior_response_items.setdefault(response_id, [])
                        if isinstance(event, dict) and event.get("type") == "response.output_item.done":
                            item = event.get("item")
                            if isinstance(item, dict):
                                item_response_id = event.get("response_id")
                                if not isinstance(item_response_id, str):
                                    item_response_id = response_id
                                if isinstance(item_response_id, str):
                                    prior_response_items.setdefault(item_response_id, []).append(
                                        copy.deepcopy(item)
                                    )
                        await websocket.send_text(data)
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
