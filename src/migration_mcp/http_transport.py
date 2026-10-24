"""Authenticated Streamable HTTP app factory for hosted MCP deployments."""

from __future__ import annotations

import hmac
import json
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

class BearerTokenMiddleware:
    """Require a configured static bearer token for MCP HTTP requests."""

    def __init__(self, app: ASGIApp, token: str, *, mcp_path: str = "/mcp") -> None:
        if len(token) < 32:
            raise ValueError("MIGRATION_MCP_HTTP_TOKEN must be at least 32 characters long.")
        self.app = app
        self.token = token
        self.mcp_path = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == "/healthz" and scope.get("method") == "GET":
            await _send_json(send, 200, {"status": "ok"})
            return
        if path == self.mcp_path:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            scheme, _, supplied = authorization.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                supplied.encode("latin-1"), self.token.encode("utf-8")
            ):
                await _send_json(
                    send,
                    401,
                    {"error": "unauthorized"},
                    extra_headers=[(b"www-authenticate", b"Bearer")],
                )
                return
        await self.app(scope, receive, send)


async def _send_json(
    send: Send,
    status: int,
    payload: dict[str, Any],
    *,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        *(extra_headers or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def create_http_app(mcp_server: Any, token: str, allowed_hosts: list[str], allowed_origins: list[str] | None = None) -> ASGIApp:
    """Create stateless Streamable HTTP MCP app with host validation and bearer auth."""
    if not allowed_hosts:
        raise ValueError("Set MIGRATION_MCP_ALLOWED_HOSTS to the allowed host header(s).")
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins or [],
        ),
    )
    return BearerTokenMiddleware(mcp_app, token, mcp_path="/mcp")


__all__ = ["BearerTokenMiddleware", "create_http_app"]
