"""FastMCP app factory, product registry, and ASGI app builder."""

import json
import logging
import os

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from dlubal_mcp import __version__
from dlubal_mcp.core.auth import APIKeyAuthMiddleware
from dlubal_mcp.core.config import DlubalMCPSettings
from dlubal_mcp.core.off_loop import off_event_loop
from dlubal_mcp.core.operation_control import OperationControl
from dlubal_mcp.core.products import ProductRegistry, ProductState, api_port_open
from dlubal_mcp.core.rate_limit import RateLimitMiddleware

# Re-exported: callers and tests import the registry from here.
__all__ = ["ProductRegistry", "ProductState", "create_asgi_app"]

logger = logging.getLogger("dlubal_mcp")


# Per product: (package, config class, session class, display label).
_PRODUCT_MODULES = {
    "rfem": ("dlubal_mcp.rfem", "RFEMConfig", "RFEMSession", "RFEM"),
    "rstab": ("dlubal_mcp.rstab", "RSTABConfig", "RSTABSession", "RSTAB"),
    "rsection": (
        "dlubal_mcp.rsection",
        "RSECTIONConfig",
        "RSECTIONSession",
        "RSECTION",
    ),
}


def _register_product(mcp: FastMCP, registry: ProductRegistry, name: str) -> None:
    """Register one product's tools and state. Skipped when its API is absent."""
    import importlib

    package, config_class, session_class, label = _PRODUCT_MODULES[name]
    try:
        tools = importlib.import_module(f"{package}.tools")
        cfg = getattr(importlib.import_module(f"{package}.config"), config_class)()
        session_type = getattr(
            importlib.import_module(f"{package}.session"), session_class
        )
    except ImportError:
        registry.register(name, "")
        logger.debug("dlubal.api.%s not available — %s tools skipped.", name, label)
        return
    registry.register(
        name,
        cfg.exe_path,
        lambda: api_port_open(cfg.url, cfg.api_port),
        api_port=cfg.api_port,
    )
    session = session_type(cfg.api_key_name, cfg.api_key, cfg.url, cfg.api_port)
    registry.add_tools(
        tools.register_tools(mcp, session, cfg, control=registry.control)
    )
    logger.info("%s tools registered.", label)


def _build_mcp(settings: DlubalMCPSettings, registry: ProductRegistry) -> FastMCP:
    """Instantiate FastMCP and register tools for the enabled products. Mutates registry.

    DLUBAL_MCP_PRODUCTS limits the products (default: all). A process then serves
    only the products whose Dlubal API build it carries: a client of another
    build exchanges model data incorrectly, because protobuf field numbers move
    between builds.
    """
    enabled = settings.enabled_products()
    mcp = FastMCP("dlubal-mcp")
    for name in _PRODUCT_MODULES:
        if name in enabled:
            _register_product(mcp, registry, name)
        else:
            registry.register_disabled(name)
            logger.info(
                "%s disabled by DLUBAL_MCP_PRODUCTS.", _PRODUCT_MODULES[name][3]
            )

    # --- health_check MCP tool (AC6) ---
    @mcp.tool(
        description="Returns server health: installed products, running state, active tools, and auth status."
    )
    @off_event_loop
    def health_check() -> str:
        return json.dumps(registry.health_dict(settings.require_auth), indent=2)

    if not settings.allow_scripts:
        for product in ("rfem", "rstab", "rsection"):
            name = f"run_{product}_script"
            if name in registry._tools:
                mcp.remove_tool(name)
                registry._tools.remove(name)
    return mcp


def _warm_product_imports() -> None:
    """Import the Dlubal API packages on the MAIN thread before serving.

    Verified on RFEM 6.15 / fastmcp 2.14.7: without this, the FIRST
    run_*_script over stdio never returns. work() does `from dlubal.api import
    common, rfem` inside the execution worker thread, which reaches
    dlubal/api/common/table.py -> pandas -> numpy and stalls loading numpy's C
    extension, while the event-loop thread is blocked in Event.wait() waiting
    for that same worker. The call times out after 120 s and leaves the journal
    row 'running', which then blocks every later execution — the connector's
    included, since the journal is shared.

    The same import from a worker completes in ~3.5 s under streamable-http, so
    this is specific to the stdio server's blocked loop thread. Doing the import
    here, synchronously, on the main thread, costs that time once at startup and
    removes the failure entirely.
    """
    import importlib

    for module in (
        "dlubal.api.common",
        "dlubal.api.rfem",
        "dlubal.api.rfem.application",
        "dlubal.api.rstab",
        "dlubal.api.rstab.application",
        "dlubal.api.rsection",
        "dlubal.api.rsection.application",
    ):
        try:
            importlib.import_module(module)
        except Exception:
            # A product that is not installed is normal; _build_mcp already
            # skipped its tools. Never let warm-up stop the server starting.
            logger.debug("Import warm-up skipped: %s", module, exc_info=True)


def build_stdio_mcp(settings: DlubalMCPSettings) -> FastMCP:
    """Build the FastMCP server for the stdio transport.

    Deliberately the whole of the stdio path's server construction: it shares
    _build_mcp with create_asgi_app — so the two transports cannot drift apart
    on tool names, arguments or return shapes — and adds nothing else. No
    middleware, no extra routes, no validate_http_security.

    require_auth is pinned False because it is reported to callers through
    health_check, and on stdio "authentication required" would be a lie: the
    client owns the process. Nothing on this path consults it for enforcement.

    allow_scripts defaults ON here and only here. The flag exists so an OPERATOR
    can withhold free-form Python from remote callers; on stdio the caller is the
    user who started the process and could run the same Python directly. An
    explicit DLUBAL_MCP_ALLOW_SCRIPTS still wins, so the choice stays theirs. The
    HTTP default in core/config.py is deliberately untouched: the Dlubal
    Connector sets that variable explicitly for CALC, and that contract must not
    move underneath it.
    """
    settings.require_auth = False
    if not any(name.upper() == "DLUBAL_MCP_ALLOW_SCRIPTS" for name in os.environ):
        settings.allow_scripts = True
    registry = ProductRegistry(OperationControl())
    mcp = _build_mcp(settings, registry)
    _warm_product_imports()
    return mcp


def create_asgi_app(settings: DlubalMCPSettings):
    """
    Build the full ASGI application.

    Routes (served at DLUBAL_MCP_PORT, default 7130):
      GET  /health     — public health endpoint, no auth
      POST /mcp        — streamable-http MCP transport (default)
      GET  /mcp        — streamable-http SSE stream
      GET  /mcp/sse    — legacy SSE transport fallback
      POST /mcp/messages/ — legacy SSE message posting

    Middleware (applied to all routes except /health):
      APIKeyAuthMiddleware → RateLimitMiddleware
    """
    allowed_keys = settings.validate_http_security()
    registry = ProductRegistry(OperationControl())
    mcp = _build_mcp(settings, registry)

    # Public health endpoint — added directly to FastMCP's route list so it lives
    # inside the same Starlette app (no lifespan/mount complexity).
    async def health_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(
            {"status": "ok", "version": __version__, "auth_required": True}
        )

    async def auth_endpoint(request: Request) -> JSONResponse:
        return JSONResponse(
            {"authenticated": True, "version": __version__, "auth_required": True}
        )

    mcp._additional_http_routes.append(
        Route("/health", health_endpoint, methods=["GET"])
    )
    mcp._additional_http_routes.append(
        Route("/auth-check", auth_endpoint, methods=["GET"])
    )

    async def control_endpoint(request: Request) -> JSONResponse:
        try:
            if request.method == "GET":
                registry.control.reconcile()
                return JSONResponse(registry.control.snapshot())
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 4096:
                    return JSONResponse({"error": "Request too large"}, status_code=413)
            command = json.loads(body)
            if not isinstance(command, dict):
                raise TypeError("Invalid control command")
            if command.get("action") == "prepare_stop":
                registry.control.reconcile()
                result = registry.control.prepare_stop(
                    allow_unresolved=command.get("allow_unresolved") in (True, "1")
                )
                return JSONResponse(
                    result, status_code=200 if result["state"] == "draining" else 409
                )
            if command.get("action") == "resume":
                return JSONResponse(registry.control.resume(command.get("lease")))
            raise ValueError("Unknown control action")
        except (ValueError, TypeError):
            return JSONResponse({"error": "Invalid control request"}, status_code=400)
        except Exception:
            logger.exception("Operation control unavailable")
            return JSONResponse(
                {"error": "Operation state unavailable"}, status_code=503
            )

    mcp._additional_http_routes.append(
        Route("/control", control_endpoint, methods=["GET", "POST"])
    )

    # SSE fallback transport at /mcp/sse (legacy clients).
    # FastMCP's SSE app has routes at /sse (GET) and /messages/ (POST).
    # Mounting at /mcp exposes them at /mcp/sse and /mcp/messages/.
    sse_sub = mcp.http_app(transport="sse")
    mcp._additional_http_routes.append(Mount("/mcp", app=sse_sub))

    # Build and return the main ASGI app with auth + rate-limit middleware.
    return mcp.http_app(
        transport=settings.transport,
        middleware=[
            Middleware(
                APIKeyAuthMiddleware,
                allowed_keys=allowed_keys,
                permissions=settings.token_permissions(),
                require_auth=settings.require_auth,
            ),
            Middleware(
                RateLimitMiddleware, requests_per_minute=settings.rate_limit_rpm
            ),
        ],
    )
