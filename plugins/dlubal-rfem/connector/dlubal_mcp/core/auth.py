"""API key authentication middleware."""

import hashlib
import json
import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("dlubal_mcp.auth")

_PUBLIC_PATHS = frozenset({"/health", "/favicon.ico"})


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self, app, allowed_keys: set[str], require_auth: bool = True, permissions=None
    ):
        super().__init__(app)
        if not require_auth or not allowed_keys:
            raise ValueError("MCP authentication requires configured access tokens")
        self.permissions = {
            hashlib.sha256(key.encode()).hexdigest(): value
            for key, value in (permissions or {}).items()
        }
        self.allowed_key_hashes = {
            hashlib.sha256(key.encode()).digest() for key in allowed_keys
        }

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        api_key = self._extract_key(request)
        if not api_key:
            logger.debug("MCP request rejected: missing bearer token")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Missing Authorization header. Use: Authorization: Bearer <key>"
                },
            )

        key_hash = hashlib.sha256(api_key.encode()).digest()
        if not any(
            secrets.compare_digest(key_hash, allowed)
            for allowed in self.allowed_key_hashes
        ):
            logger.debug("MCP request rejected: invalid bearer token")
            return JSONResponse(status_code=403, content={"error": "Invalid API key."})

        request.state.api_key = key_hash.hex()
        permission = self.permissions.get(key_hash.hex(), "admin")
        if request.method == "POST":
            if request.url.path == "/control" and permission != "admin":
                return JSONResponse(
                    {"error": "admin_permission_required"}, status_code=403
                )
            if request.url.path.startswith("/mcp") and permission != "admin":
                # FastMCP accepts HTTP and legacy SSE message submissions. Check both
                # before dispatch; tool descriptions are not an authorization boundary.
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        return JSONResponse(
                            {"error": "request_too_large"}, status_code=413
                        )
                try:
                    message = json.loads(data)
                    if not isinstance(message, dict):
                        raise TypeError("batch requests are not supported")
                    if message.get("method") == "tools/call":
                        tool = message.get("params", {}).get("name")
                        reads = {
                            "health_check",
                            "get_rfem_application_info",
                            "get_rstab_application_info",
                            "get_rsection_application_info",
                            "read_rfem_results",
                            "get_rfem_execution",
                            "get_rstab_execution",
                            "get_rsection_execution",
                            "inspect_rfem_api",
                            "inspect_rstab_api",
                            "inspect_rsection_api",
                        }
                        writes = {"write_rfem_model", "calculate_rfem_model"}
                        if tool not in reads and not (
                            permission == "write" and tool in writes
                        ):
                            return JSONResponse(
                                {"error": "tool_permission_denied"}, status_code=403
                            )
                except (ValueError, TypeError, AttributeError):
                    return JSONResponse({"error": "invalid_request"}, status_code=400)
                # BaseHTTPMiddleware replays a cached body to the downstream app.
                request._body = bytes(data)
        return await call_next(request)

    @staticmethod
    def _extract_key(request: Request) -> str | None:
        headers = request.headers.getlist("authorization")
        if len(headers) != 1:
            return None
        parts = headers[0].split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        return None
