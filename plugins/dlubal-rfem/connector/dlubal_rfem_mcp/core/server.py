"""RFEM-only FastMCP composition; discovery never opens an engineering session."""

import json

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware as MCPMiddleware
from starlette.middleware import Middleware

from dlubal_rfem_mcp import __version__
from dlubal_rfem_mcp.core.auth import GenerationTokenVerifier, LoopbackRequestGuard
from dlubal_rfem_mcp.core.operation_control import OperationControl
from dlubal_rfem_mcp.core.rate_limit import RateLimitMiddleware
from dlubal_rfem_mcp.rfem.config import RFEMConfig
from dlubal_rfem_mcp.rfem.session import RFEMSession

_UNSET_SESSION = object()


def run_stdio() -> None:
    import os
    from pathlib import Path

    from dlubal_rfem_mcp.rfem.tools import register_tools
    from dlubal_rfem_mcp.setup import prepared_build

    key = os.environ.pop("PLUGIN_RFEM_API_KEY", "")
    data = Path(os.environ["PLUGIN_DATA"])
    data.mkdir(parents=True, exist_ok=True)
    config = RFEMConfig(api_key=key, execution_state_file=str(data / "executions.sqlite3"))
    build = prepared_build(data)
    session = RFEMSession(key, config.url, config.api_port, expected_build=build) if key and build else None
    control = OperationControl()
    server = FastMCP("dlubal-rfem-mcp", mask_error_details=True)
    server.add_middleware(StdioEngineeringGate(session, build))
    register_tools(server, session, config, control=control)
    server.run(transport="stdio", show_banner=False)


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


class StdioEngineeringGate(MCPMiddleware):
    def __init__(self, session, build):
        self.session = session
        self.build = build

    async def on_call_tool(self, context, call_next):
        if self.session is None and context.message.name != "get_rfem_execution":
            raise ToolError("credentialMissing" if self.build else "softwarePreparationRequired")
        if self.session is not None and self.session.restriction and context.message.name not in {
            "get_rfem_execution", "get_rfem_application_info",
        }:
            raise ToolError(self.session.restriction)
        return await call_next(context)


def build_mcp(envelope, *, control=None, session=_UNSET_SESSION) -> FastMCP:
    mcp = FastMCP(
        "dlubal-rfem-mcp",
        mask_error_details=True,
        auth=GenerationTokenVerifier(
            envelope.bearerToken.get_secret_value(),
            envelope.connectorInstanceId,
            envelope.generation,
        ),
    )
    control = control or OperationControl()
    if envelope.mode == "observation":
        from pathlib import Path

        from dlubal_rfem_mcp.rfem.execution import ExecutionManager

        assert envelope.data is not None
        ExecutionManager(
            Path(envelope.data.stateDirectory) / "executions.sqlite3",
            control=control,
            readonly_existing=True,
        )
        mcp.add_middleware(EngineeringGate(envelope, None))
        return mcp
    from dlubal_rfem_mcp.rfem.tools import register_tools

    config = RFEMConfig.from_envelope(envelope)
    if session is _UNSET_SESSION:
        session = (
            RFEMSession(
                config.api_key.get_secret_value(),
                config.url,
                config.api_port,
            )
            if envelope.mode == "execution"
            else None
        )
    mcp.add_middleware(EngineeringGate(envelope, session))
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
            raise ToolError(session.describe_error(error, session.restriction or "softwareUnavailable")) from None
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


def create_asgi_app(envelope, *, control=None, session=_UNSET_SESSION):
    return build_mcp(envelope, control=control, session=session).http_app(
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
