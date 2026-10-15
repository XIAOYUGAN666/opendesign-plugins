"""RFEM-only FastMCP composition; discovery never opens an engineering session."""

import json

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware as MCPMiddleware
from starlette.middleware import Middleware

from dlubal_rfem_mcp import __version__
from dlubal_rfem_mcp.core.auth import GenerationTokenVerifier, LoopbackRequestGuard
from dlubal_rfem_mcp.core.config import StartupEnvelope
from dlubal_rfem_mcp.core.operation_control import OperationControl
from dlubal_rfem_mcp.core.rate_limit import RateLimitMiddleware
from dlubal_rfem_mcp.rfem.config import RFEMConfig
from dlubal_rfem_mcp.rfem.session import RFEMSession


class EngineeringGate(MCPMiddleware):
    def __init__(self, envelope, session):
        self.envelope = envelope
        self.session = session

    async def on_call_tool(self, context, call_next):
        if self.envelope.mode != "execution" or self.envelope.configuration is None:
            raise ToolError("credentialMissing")
        if context.message.name not in {"health_check", "get_rfem_application_info"}:
            if self.session.restriction:
                raise ToolError(self.session.restriction)
            if (
                context.message.name in {"get_rfem_execution", "inspect_rfem_api"}
                and not self.session.verified
            ):
                raise ToolError("connectionUnverified")
        return await call_next(context)


def build_mcp(envelope: StartupEnvelope, *, control=None) -> FastMCP:
    from dlubal_rfem_mcp.rfem.tools import register_tools

    mcp = FastMCP(
        "dlubal-rfem-mcp",
        mask_error_details=True,
        auth=GenerationTokenVerifier(
            envelope.bearerToken.get_secret_value(),
            envelope.connectorInstanceId,
            envelope.generation,
        ),
    )
    config = RFEMConfig.from_envelope(envelope)
    session = (
        RFEMSession(
            config.api_key.get_secret_value(),
            config.url,
            config.api_port,
            expected_build=envelope.software.build if envelope.software else None,
        )
        if envelope.mode == "execution"
        else None
    )
    mcp.add_middleware(EngineeringGate(envelope, session))
    control = control or OperationControl()
    names = register_tools(mcp, session, config, control=control)

    @mcp.tool(
        description="Returns server health: installed products, running state, active tools, and auth status."
    )
    def health_check() -> str:
        if session is None:
            raise ToolError("credentialMissing")
        running = False
        try:
            with control.reading(), session.connection() as app:
                app.get_application_info()
            running = True
        except Exception as error:
            raise ToolError(session.restriction or "softwareUnavailable") from error
        return json.dumps(
            {
                "version": __version__,
                "products": {"rfem": {"installed": True, "running": running}},
                "tools": [*names, "health_check"],
                "auth_required": True,
            },
            indent=2,
        )

    return mcp


def create_asgi_app(envelope: StartupEnvelope, *, control=None):
    return build_mcp(envelope, control=control).http_app(
        path="/mcp",
        transport="streamable-http",
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=[f"127.0.0.1:{envelope.bind.port}"],
        allowed_origins=[],
        middleware=[
            Middleware(LoopbackRequestGuard, port=envelope.bind.port),
            Middleware(RateLimitMiddleware, requests_per_minute=120),
        ],
    )
