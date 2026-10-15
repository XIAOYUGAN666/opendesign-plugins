"""Per-generation opaque-token verification using FastMCP's official seam."""

import hashlib
import secrets

from fastmcp.server.auth import AccessToken, TokenVerifier
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class GenerationTokenVerifier(TokenVerifier):
    def __init__(self, token: str, connector: str, generation: int):
        super().__init__()
        self._digest = hashlib.sha256(token.encode()).digest()
        self._identity = f"{connector}:{generation}"

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(
            hashlib.sha256(token.encode()).digest(), self._digest
        ):
            return None
        return AccessToken(token=token, client_id=self._identity, scopes=[])


class LoopbackRequestGuard:
    """Reject unsafe headers/paths before auth, routing, redirects or MCP."""

    def __init__(self, app: ASGIApp, port: int):
        self.app = app
        self.authority = f"127.0.0.1:{port}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        hosts = [v.decode("latin-1") for k, v in headers if k.lower() == b"host"]
        origins = [v.decode("latin-1") for k, v in headers if k.lower() == b"origin"]
        code = 0
        if hosts != [self.authority] or (
            origins and origins != [f"http://{self.authority}"]
        ):
            code = 403
        elif scope.get("query_string"):
            code = 400
        elif scope.get("path") != "/mcp":
            code = 404
        if code:
            await JSONResponse({"error": "requestRejected"}, status_code=code)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)
