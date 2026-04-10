from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient

from codex_gateway.responses_bridge import (
    BridgeConfig,
    ResponsesBridge,
    _rewrite_previous_response_id,
    _ws_request_to_responses_payload,
)


def test_ws_request_to_responses_payload_drops_type() -> None:
    payload = _ws_request_to_responses_payload(
        {
            "type": "response.create",
            "model": "gpt-5.2-codex",
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        }
    )
    assert payload == {
        "model": "gpt-5.2-codex",
        "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        "stream": True,
    }


def test_ws_request_to_responses_payload_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unsupported websocket request type"):
        _ws_request_to_responses_payload({"type": "response.cancel"})


def test_bridge_websocket_relays_upstream_sse_events() -> None:
    requests: list[dict[str, object]] = []
    auth_headers: list[str | None] = []

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/responses":
                self.send_response(404)
                self.end_headers()
                return

            auth_headers.append(self.headers.get("Authorization"))
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0)
            requests.append(json.loads(body))

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"event: response.created\n")
            self.wfile.write(b'data: {"type":"response.created","response":{"id":"resp-1"}}\n\n')
            self.wfile.write(b"event: response.output_text.delta\n")
            self.wfile.write(b'data: {"type":"response.output_text.delta","delta":"hello"}\n\n')
            self.wfile.write(b"event: response.completed\n")
            self.wfile.write(
                b'data: {"type":"response.completed","response":{"id":"resp-1","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
            )

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bridge = ResponsesBridge(
            BridgeConfig(
                upstream_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                upstream_api_key="test-key",
            )
        )
        client = TestClient(bridge.app)

        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.2-codex",
                    "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
                    "stream": True,
                }
            )
            created = websocket.receive_json()
            delta = websocket.receive_json()
            completed = websocket.receive_json()

        assert created["type"] == "response.created"
        assert delta == {"type": "response.output_text.delta", "delta": "hello"}
        assert completed["type"] == "response.completed"
        assert requests == [
            {
                "model": "gpt-5.2-codex",
                "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
                "stream": True,
            }
        ]
        assert auth_headers == ["Bearer test-key"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_bridge_websocket_handles_codex_warmup_locally() -> None:
    requests: list[dict[str, object]] = []

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0)
            requests.append(json.loads(body))
            self.send_response(500)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bridge = ResponsesBridge(
            BridgeConfig(
                upstream_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                upstream_api_key="test-key",
            )
        )
        client = TestClient(bridge.app)

        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.2-codex",
                    "instructions": "",
                    "input": [],
                    "tools": [],
                    "tool_choice": "auto",
                    "parallel_tool_calls": True,
                    "reasoning": {"effort": "high", "summary": "detailed"},
                    "store": False,
                    "stream": True,
                    "include": ["reasoning.encrypted_content"],
                    "generate": False,
                }
            )
            created = websocket.receive_json()
            completed = websocket.receive_json()

        assert created["type"] == "response.created"
        assert completed["type"] == "response.completed"
        assert created["response"]["id"].startswith("bridge_warmup_")
        assert completed["response"]["id"] == created["response"]["id"]
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_bridge_websocket_rewrites_synthetic_previous_response_id() -> None:
    requests: list[dict[str, object]] = []

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/responses":
                self.send_response(404)
                self.end_headers()
                return

            body = self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0)
            requests.append(json.loads(body))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"event: response.created\n")
            self.wfile.write(b'data: {"type":"response.created","response":{"id":"resp-1"}}\n\n')
            self.wfile.write(b"event: response.completed\n")
            self.wfile.write(
                b'data: {"type":"response.completed","response":{"id":"resp-1","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
            )

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bridge = ResponsesBridge(
            BridgeConfig(
                upstream_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                upstream_api_key="test-key",
            )
        )
        client = TestClient(bridge.app)

        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.2-codex",
                    "instructions": "",
                    "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
                    "tools": [],
                    "tool_choice": "auto",
                    "parallel_tool_calls": True,
                    "reasoning": {"effort": "high", "summary": "detailed"},
                    "store": False,
                    "stream": True,
                    "include": ["reasoning.encrypted_content"],
                    "generate": False,
                }
            )
            warmup_created = websocket.receive_json()
            warmup_completed = websocket.receive_json()

            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.2-codex",
                    "instructions": "",
                    "previous_response_id": warmup_created["response"]["id"],
                    "input": [],
                    "tools": [],
                    "tool_choice": "auto",
                    "parallel_tool_calls": True,
                    "reasoning": {"effort": "high", "summary": "detailed"},
                    "store": False,
                    "stream": True,
                    "include": ["reasoning.encrypted_content"],
                }
            )
            created = websocket.receive_json()
            completed = websocket.receive_json()

        assert warmup_created["type"] == "response.created"
        assert warmup_completed["type"] == "response.completed"
        assert created["type"] == "response.created"
        assert completed["type"] == "response.completed"
        assert requests == [
            {
                "model": "gpt-5.2-codex",
                "instructions": "",
                "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
                "tools": [],
                "tool_choice": "auto",
                "parallel_tool_calls": True,
                "reasoning": {"effort": "high", "summary": "detailed"},
                "store": False,
                "stream": True,
                "include": ["reasoning.encrypted_content"],
            }
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_rewrite_previous_response_id_expands_real_response_context() -> None:
    prior_requests = {
        "resp-1": {
            "model": "gpt-5.2-codex",
            "instructions": "system",
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "tools": [{"type": "function", "name": "shell"}],
            "store": False,
            "stream": True,
        }
    }

    rewritten = _rewrite_previous_response_id(
        {
            "model": "gpt-5.2-codex",
            "previous_response_id": "resp-1",
            "input": [{"type": "function_call_output", "call_id": "call-1", "output": "ok"}],
            "stream": True,
        },
        prior_requests,
    )

    assert rewritten == {
        "model": "gpt-5.2-codex",
        "instructions": "system",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            {"type": "function_call_output", "call_id": "call-1", "output": "ok"},
        ],
        "tools": [{"type": "function", "name": "shell"}],
        "store": False,
        "stream": True,
    }


def test_bridge_models_proxies_response() -> None:
    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if not self.path.startswith("/v1/models"):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"gpt-5.2-codex"}]}')

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bridge = ResponsesBridge(
            BridgeConfig(
                upstream_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                upstream_api_key="test-key",
            )
        )
        client = TestClient(bridge.app)
        response = client.get("/v1/models", params={"client_version": "1.0.0"})
        assert response.status_code == 200
        assert response.json() == {"data": [{"id": "gpt-5.2-codex"}]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
