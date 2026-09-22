"""Small dependency-free client for the accepted Team0 runtime bindings."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class ApiError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail or code

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status in RETRYABLE_STATUSES


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Mapping[str, Any]
    headers: Mapping[str, str]


class Team0ApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        read_timeout_seconds: float = 5.0,
        write_timeout_seconds: float = 5.0,
        host_name: str = "custom-agent",
        opener=urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._read_timeout = read_timeout_seconds
        self._write_timeout = write_timeout_seconds
        self._host_name = host_name.strip() or "custom-agent"
        self._opener = opener

    def create_understanding_read(
        self, *, query: str, idempotency_key: str
    ) -> Mapping[str, Any]:
        """Use the same server-owned Living Understanding projection as every MCP host."""

        response = self._request(
            "POST",
            "/mcp",
            body={
                "jsonrpc": "2.0",
                "id": idempotency_key,
                "method": "tools/call",
                "params": {
                    "name": "team0_living_understanding",
                    "arguments": {
                        "query": query[:8000],
                        "idempotency_key": idempotency_key,
                    },
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {
                            "name": self._host_name[:160],
                            "version": "1.0",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "team0_living_understanding",
            },
            timeout=self._read_timeout,
        )
        result = response.body.get("result")
        if not isinstance(result, Mapping) or result.get("isError") is True:
            raise ApiError("protocol.invalid_understanding_result", status=502)
        structured = result.get("structuredContent")
        if not isinstance(structured, Mapping) or not isinstance(
            structured.get("context"), str
        ):
            raise ApiError("protocol.invalid_understanding_result", status=502)
        return {
            "id": structured.get("read_id") or "unknown",
            "status": structured.get("status") or "unknown",
            "retrieval_health": structured.get("retrieval_health") or "unknown",
            "projection_scope": structured.get("projection_scope") or "unknown",
            "as_of": structured.get("as_of"),
            "content_digest": structured.get("content_digest"),
            "context": structured["context"],
        }

    def get_agent_runtime_binding(self) -> Mapping[str, Any]:
        capabilities = self._request(
            "GET", "/capabilities", timeout=self._read_timeout
        ).body
        binding = capabilities.get("agent_runtime")
        if not isinstance(binding, Mapping):
            raise ApiError(
                "runtime.binding_unavailable",
                status=409,
                detail="This Team0 key is not bound to conversation learning.",
            )
        source_id = binding.get("contribution_source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ApiError("protocol.invalid_runtime_binding", status=502)
        return binding

    def ingest_event(
        self, *, event: Mapping[str, Any], idempotency_key: str
    ) -> Mapping[str, Any]:
        return self._request(
            "POST",
            "/wm/events",
            body=event,
            headers={"Idempotency-Key": idempotency_key},
            timeout=self._write_timeout,
        ).body

    def complete_action(self, *, action_id: str, idempotency_key: str) -> Mapping[str, Any]:
        encoded_id = quote(action_id, safe="")
        current = self._request(
            "GET", f"/action-items/{encoded_id}", timeout=self._read_timeout
        )
        if current.body.get("status") == "completed":
            return current.body
        etag = current.headers.get("etag")
        if not etag:
            raise ApiError("protocol.missing_etag", status=500)
        return self._request(
            "PATCH",
            f"/action-items/{encoded_id}",
            body={"status": "completed"},
            headers={
                "Content-Type": "application/merge-patch+json",
                "Idempotency-Key": idempotency_key,
                "If-Match": etag,
            },
            timeout=self._write_timeout,
        ).body

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float,
    ) -> ApiResponse:
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "team0-agent-runtime/0.1.1",
        }
        request_headers.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(
            f"{self._base_url}/{path.lstrip('/')}",
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                raw = response.read()
                status = int(getattr(response, "status", response.getcode()))
                response_headers = {
                    str(key).lower(): str(value) for key, value in response.headers.items()
                }
        except HTTPError as error:
            raw = error.read()
            problem = _json_object(raw)
            raise ApiError(
                str(problem.get("code") or problem.get("type") or "http.error"),
                status=error.code,
                detail=str(problem.get("detail") or problem.get("title") or error.reason),
            ) from None
        except (URLError, TimeoutError, socket.timeout) as error:
            raise ApiError("dependency.unavailable", detail=str(error)) from None
        if status < 200 or status >= 300:
            # Keep the transport status and a bounded, non-secret detail.  MCP errors are
            # JSON-RPC envelopes, so do not discard their message behind a generic HTTP error.
            try:
                problem = _json_object(raw)
            except ApiError:
                problem = {}
            nested = problem.get("error")
            if isinstance(nested, Mapping):
                problem = nested
            detail = problem.get("detail") or problem.get("message") or problem.get("title")
            raise ApiError(
                "http.error",
                status=status,
                detail=str(detail)[:500] if detail else f"HTTP {status}",
            )
        return ApiResponse(status, _json_object(raw), response_headers)


def _json_object(raw: bytes) -> Mapping[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApiError("protocol.invalid_json", status=502) from error
    if not isinstance(parsed, Mapping):
        raise ApiError("protocol.invalid_json", status=502)
    return parsed
