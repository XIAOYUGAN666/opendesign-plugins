# server.py
import functools
import hmac
import inspect
import logging
import os
import re
import threading
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any, Callable

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import (
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.shared.exceptions import MCPError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, InputRequiredResult, ToolAnnotations
from starlette.applications import Starlette
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.types import Receive, Scope, Send

from rhino_mcp.bridge import (
    BridgeClient,
    BridgeConfig,
    BridgeProtocolError,
    BridgeResultUnknown,
    BridgeUnavailable,
    RhinoCommandError,
)
from rhino_mcp.guidance import SERVER_INSTRUCTIONS
from rhino_mcp.static.rhinoscriptsyntax import rhinoscriptsyntax_json

# Opt-in perception: when enabled, every mutating command carries an
# `include_delta` flag on the envelope, and the plugin attaches a `_delta` block
# (created_ids / deleted_ids / count_before / count_after) to the result so a
# client can see what changed without re-querying. Off by default, so responses
# are byte-identical unless explicitly turned on.
RHINO_PERCEPTION = os.getenv("RHINO_MCP_PERCEPTION", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Envelope metadata the plugin injects into a mutating command's result when
# perception is on. These are cross-cutting, not part of any single command's
# result contract, so they are stripped before post-flight validation; the
# full result, these keys included, is still returned to the caller.
PERCEPTION_RESULT_KEYS = ("_delta", "_health")
RHINO_DEBUG = os.getenv("RHINO_MCP_DEBUG", "").lower() in ("1", "true", "yes")
RHINO_LOG_LEVEL = os.getenv("RHINO_MCP_LOG_LEVEL", "DEBUG" if RHINO_DEBUG else "INFO")

# Configure logging
log_level = getattr(logging, RHINO_LOG_LEVEL.upper(), logging.INFO)
logging.basicConfig(
    level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("rhino_mcp_connector.server")
logger.setLevel(log_level)

if RHINO_DEBUG:
    logger.info("Debug mode enabled")

CAPABILITIES_COMMAND = "describe_capabilities"

# The Host binds Connector and Bridge to the signed build/contract identity.
# Check actual command and dry-run capabilities here. Legacy version
# thresholds do not describe this independently versioned managed package.
# Commands added after describe_capabilities (0.3.2) existed, so a plugin too
# old to report its command table is still refused with the right advice.
COMMANDS_SINCE: dict[str, str] = {
    "create_planar_region": "0.4.0",
}
PLUGIN_UPDATE_ADVICE = (
    "Install the matching Rhino plug-in from the Plugin Manager, then restart "
    "Rhino to activate the update."
)
SERVER_UPDATE_ADVICE = "Apply Connector updates through the plugin manager."
_VERSION_PREFIX = re.compile(r"\s*v?(\d+(?:\.\d+)*)")
_UNKNOWN_COMMAND_ANSWER = re.compile(r"^Unknown command( type)?\b")


def server_version() -> str | None:
    """This package's installed version, or None outside an installed package."""
    try:
        return _pkg_version("rhino-mcp-connector")
    except PackageNotFoundError:
        return None


class ManagedTokenVerifier(TokenVerifier):
    """Verify one short-lived bearer supplied by the trusted Tauri host."""

    def __init__(self, token: str, *, client_id: str) -> None:
        try:
            encoded = token.encode("ascii")
        except (AttributeError, UnicodeEncodeError) as exc:
            raise ValueError("Connector bearer credential is invalid.") from exc
        if not 32 <= len(encoded) <= 256:
            raise ValueError("Connector bearer credential is invalid.")
        self._token = bytearray(encoded)
        self._client_id = client_id

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            candidate = token.encode("ascii")
        except (AttributeError, UnicodeEncodeError):
            return None
        if not self._token or not hmac.compare_digest(bytes(self._token), candidate):
            return None
        return AccessToken(
            token=token,
            client_id=self._client_id,
            scopes=["rhino:invoke"],
        )

    def clear(self) -> None:
        """Best-effort overwrite the in-memory bearer after server shutdown."""
        self._token[:] = b"\x00" * len(self._token)
        self._token.clear()


class _RequireMcpAuthMiddleware:
    """Apply the SDK bearer gate to the sole MCP route, not ASGI lifespan."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._mcp_auth = RequireAuthMiddleware(
            app,
            required_scopes=["rhino:invoke"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            await self._mcp_auth(scope, receive, send)
            return
        await self._app(scope, receive, send)


def create_authenticated_http_app(
    server: MCPServer,
    *,
    token_verifier: ManagedTokenVerifier,
    port: int,
) -> Starlette:
    """Build the one-route, exact-loopback Streamable HTTP application."""
    if not isinstance(server, MCPServer):
        raise TypeError("Authenticated MCP app requires an MCPServer.")
    if not isinstance(token_verifier, ManagedTokenVerifier):
        raise TypeError("Authenticated MCP app requires a managed token verifier.")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError("MCP port is outside the supported range.")

    authority = f"127.0.0.1:{port}"
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        host="127.0.0.1",
        max_sessions=1,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[authority],
            allowed_origins=[f"http://{authority}"],
        ),
    )
    # Starlette applies middleware in reverse add order. Authentication must
    # populate scope before the auth-context and required-scope middlewares run.
    app.add_middleware(_RequireMcpAuthMiddleware)
    app.add_middleware(AuthContextMiddleware)
    app.add_middleware(
        AuthenticationMiddleware,
        backend=BearerAuthBackend(token_verifier),
    )
    return app


def parse_version(text) -> tuple | None:
    """The leading dotted integers, retaining nonzero revisions, or None.

    "0.4.0", "0.4.0.0" and "0.4.0+abc" all read as (0, 4, 0); "unknown" as None.
    """
    match = _VERSION_PREFIX.match(str(text)) if text else None
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 3 and parts[-1] == 0:
        parts.pop()
    return tuple(parts + [0] * (3 - len(parts)))


def version_skew_report(plugin_version) -> dict[str, Any]:
    """Compare the plugin's reported version with this server's.

    Returns server_version, plugin_matches_server (None when either side is
    unknown) and update_advice naming the older side, or None when they match.
    """
    server = server_version()
    report: dict[str, Any] = {
        "server_version": server,
        "plugin_matches_server": None,
        "update_advice": None,
    }
    plugin, mine = parse_version(plugin_version), parse_version(server)
    if plugin is None or mine is None:
        return report
    report["plugin_matches_server"] = plugin == mine
    if plugin < mine:
        report["update_advice"] = (
            f"The Rhino plugin ({plugin_version}) is older than this server "
            f"({server}). {PLUGIN_UPDATE_ADVICE}"
        )
    elif plugin > mine:
        report["update_advice"] = (
            f"This server ({server}) is older than the Rhino plugin "
            f"({plugin_version}). {SERVER_UPDATE_ADVICE}"
        )
    return report


def unsupported_command_message(command_type: str, plugin_version: str, since) -> str:
    """Actionable text for a command the connected plugin cannot run."""
    added = f", which was added in plugin {since}" if since else ""
    return (
        f"The connected rhino_mcp plugin ({plugin_version}) does not support "
        f"'{command_type}'{added}; this server is {server_version() or 'unknown'}. "
        f"{PLUGIN_UPDATE_ADVICE}"
    )


class UnsupportedCommandError(Exception):
    """The connected plugin cannot run this command; the socket itself is fine."""


def rhino_startup_error_message(
    prefix: str = "Rhino Bridge is not connected",
) -> str:
    """Give a safe recovery hint without exposing local endpoint details."""
    return (
        f"{prefix}. Open Rhino, confirm the Rhino plug-in is active, then retry "
        "from the plugin manager."
    )


class RhinoConnection:
    """Apply Rhino command compatibility checks around one injected BridgeClient."""

    def __init__(self, bridge: BridgeClient | BridgeConfig) -> None:
        if isinstance(bridge, BridgeConfig):
            # Kept for direct protocol tests and small internal adapters. Managed
            # Connector startup always constructs the BridgeClient before lifespan.
            bridge = BridgeClient(bridge)
        if not isinstance(bridge, BridgeClient):
            raise TypeError("RhinoConnection requires a validated BridgeClient.")
        self._bridge = bridge
        self._capabilities: dict[str, Any] | None = None
        self._dry_run_commands: set[str] | None = None
        self._capabilities_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self._bridge.is_connected

    def connect(self) -> bool:
        self._bridge.connect()
        return True

    def disconnect(self) -> None:
        self._bridge.disconnect()
        with self._capabilities_lock:
            self._capabilities = None
            self._dry_run_commands = None

    def send_command(
        self,
        command_type: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        command_params = dict(params or {})
        if command_type != CAPABILITIES_COMMAND:
            self._check_plugin_compatibility(command_type, command_params)

        envelope_fields: dict[str, bool] = {}
        if RHINO_PERCEPTION:
            envelope_fields = {"include_delta": True, "include_health": True}
        try:
            result = self._bridge.call(
                command_type,
                command_params,
                envelope_fields=envelope_fields or None,
            )
        except RhinoCommandError as exc:
            if _UNKNOWN_COMMAND_ANSWER.match(str(exc)):
                raise UnsupportedCommandError(
                    unsupported_command_message(
                        command_type,
                        self._plugin_version_text(),
                        COMMANDS_SINCE.get(command_type),
                    )
                ) from exc
            raise
        return result

    def _check_plugin_compatibility(
        self, command_type: str, params: Mapping[str, Any]
    ) -> None:
        capabilities = self._plugin_capabilities()
        version_text = self._plugin_version_text()
        since = COMMANDS_SINCE.get(command_type)

        if capabilities == {}:
            if since:
                raise UnsupportedCommandError(
                    unsupported_command_message(command_type, version_text, since)
                )
        elif capabilities:
            advertised = {
                entry.get("name")
                for entry in capabilities.get("commands", [])
                if isinstance(entry, dict)
            }
            if advertised and command_type not in advertised:
                raise UnsupportedCommandError(
                    unsupported_command_message(command_type, version_text, since)
                )

        if params.get("dry_run"):
            self._require_dry_run_support(command_type, capabilities)

    def _require_dry_run_support(
        self, command_type: str, capabilities: Mapping[str, Any] | None
    ) -> None:
        if capabilities and command_type in (self._dry_run_commands or set()):
            return
        raise UnsupportedCommandError(
            "Rhino Bridge does not report dry_run support for "
            f"'{command_type}', so the preview was not sent. Update the Rhino plug-in "
            "or retry without dry_run."
        )

    def _plugin_capabilities(self) -> dict[str, Any]:
        with self._capabilities_lock:
            if self._capabilities is None:
                try:
                    answer = self._bridge.call(CAPABILITIES_COMMAND, {})
                except RhinoCommandError as exc:
                    if _UNKNOWN_COMMAND_ANSWER.match(str(exc)):
                        answer = {}
                    else:
                        raise
                if not isinstance(answer, dict):
                    raise BridgeProtocolError(
                        "Rhino Bridge returned invalid capability data."
                    )
                self._capabilities = answer
                self._dry_run_commands = {
                    entry["name"]
                    for entry in answer.get("commands", [])
                    if isinstance(entry, dict)
                    and isinstance(entry.get("name"), str)
                    and entry.get("supports_dry_run") is True
                }
                advice = version_skew_report(answer.get("version"))["update_advice"]
                if advice:
                    logger.warning("Version skew: %s", advice)
            return self._capabilities

    def _plugin_version_text(self) -> str:
        if self._capabilities is None:
            return "version not read"
        if not self._capabilities:
            return "older than 0.3.2"
        return str(self._capabilities.get("version") or "unknown version")


def _check_request_dispatch_state(ctx: Context | None):
    # Only actual MCP requests have a lifespan context. Direct Python helpers
    # retain their own exception behavior.
    if ctx is None:
        return
    try:
        lifespan = ctx.request_context.lifespan_context
    except ValueError:
        return
    if not isinstance(lifespan, ConnectorLifespanContext):
        return
    if lifespan.bridge_client is None:
        raise ToolError("Engineering calls are unavailable in metadata mode.")
    if lifespan.bridge_client.lifecycle_snapshot()[0] == "unknown":
        raise ToolError(
            "A previous Rhino result remains unknown; further dispatch is blocked. Do not retry automatically."
        )


def _reporting_errors(fn):
    """Re-raise a tool's failure as ToolError so its message reaches the client.

    MCP SDK 2.x reports any other exception to the client as only
    "Error executing tool <name>". The Connector's error text is the agent's recovery
    path (open Rhino, confirm the plug-in is active, fix a parameter, retry after a dropped
    connection), so it has to survive the trip. Only the callable handed to the
    SDK is wrapped; the module-level tool function keeps raising its own types.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except (ToolError, MCPError):
                raise
            except Exception as e:
                raise ToolError(str(e)) from e

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ToolError, MCPError):
            raise
        except Exception as e:
            raise ToolError(str(e)) from e

    return wrapper


class RhinoConnectorServer(MCPServer):
    """MCPServer whose tools report their real error messages to the client."""

    connector_bridge_slot: "ConnectorBridgeSlot"

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> CallToolResult | InputRequiredResult:
        # MCPServer supplies the official request Context here even when the
        # registered function has no Context parameter. Direct Python calls
        # without a request retain the SDK's existing behavior.
        _check_request_dispatch_state(context)
        result = await super().call_tool(name, arguments, context)
        _check_request_dispatch_state(context)
        return result

    def add_tool(self, fn, *args, **kwargs):
        return super().add_tool(_reporting_errors(fn), *args, **kwargs)


# Create the MCP server with lifespan support
# ============================================================================
# MCP Resources - Browsable RhinoScript Documentation
# ============================================================================


def resource_list_modules() -> str:
    """
    List all RhinoScript modules with function counts.
    Browse this to discover what's available.
    """
    lines = ["# RhinoScript Modules\n"]
    lines.append("| Module | Functions |")
    lines.append("|--------|-----------|")

    for module in sorted(rhinoscriptsyntax_json, key=lambda m: m["ModuleName"]):
        name = module["ModuleName"]
        count = len(module["functions"])
        lines.append(f"| {name} | {count} |")

    lines.append("\n\nUse `rhinoscript://module/<name>` to browse a specific module.")
    return "\n".join(lines)


def resource_get_module(module_name: str) -> str:
    """
    Get all functions in a specific module with signatures.
    """
    for module in rhinoscriptsyntax_json:
        if module["ModuleName"].lower() == module_name.lower():
            lines = [f"# RhinoScript Module: {module['ModuleName']}\n"]
            lines.append(f"Total functions: {len(module['functions'])}\n")

            for func in module["functions"]:
                sig = func.get("Signature", func["Name"] + "()")
                desc = func.get("Description", "")[:100]
                lines.append(f"## {func['Name']}")
                lines.append(f"```python\nrs.{sig}\n```")
                lines.append(f"{desc}\n")

            return "\n".join(lines)

    available = ", ".join(sorted(m["ModuleName"] for m in rhinoscriptsyntax_json))
    return f"Module '{module_name}' not found.\n\nAvailable modules: {available}"


def resource_get_function(function_name: str) -> str:
    """
    Get complete documentation for a specific function.
    """
    for module in rhinoscriptsyntax_json:
        for func in module["functions"]:
            if func["Name"].lower() == function_name.lower():
                lines = [f"# {func['Name']}\n"]
                lines.append(f"**Module:** {module['ModuleName']}\n")

                sig = func.get("Signature", func["Name"] + "()")
                lines.append(f"## Signature\n```python\nrs.{sig}\n```\n")

                if func.get("Description"):
                    lines.append(f"## Description\n{func['Description']}\n")

                if func.get("ArgumentDesc"):
                    lines.append(f"## Parameters\n{func['ArgumentDesc']}\n")

                if func.get("Returns"):
                    lines.append(f"## Returns\n{func['Returns']}\n")

                if func.get("Example"):
                    examples = func["Example"]
                    if isinstance(examples, list):
                        example_code = "\n".join(examples)
                    else:
                        example_code = examples
                    lines.append(f"## Example\n```python\n{example_code}\n```\n")

                return "\n".join(lines)

    return f"Function '{function_name}' not found. Use search_rhinoscript_functions() to find functions."


_NO_PENDING_BRIDGE = object()


class ConnectorBridgeSlot:
    """Share a changing native peer without interrupting an engineering call."""

    def __init__(self, bridge: BridgeClient | None):
        self._lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._bridge = bridge
        self._connection = RhinoConnection(bridge) if bridge is not None else None
        self._pending = _NO_PENDING_BRIDGE
        self._retired: list[BridgeClient] = []

    @property
    def bridge_client(self) -> BridgeClient | None:
        return self._bridge

    def update(self, bridge: BridgeClient | None) -> None:
        with self._pending_lock:
            self._pending = bridge
        if self._lock.acquire(blocking=False):
            try:
                self._apply_pending()
            finally:
                self._lock.release()

    def _apply_pending(self) -> None:
        if self._bridge is not None and self._bridge.lifecycle_snapshot()[0] == "unknown":
            return
        with self._pending_lock:
            pending = self._pending
            self._pending = _NO_PENDING_BRIDGE
        if pending is _NO_PENDING_BRIDGE:
            return
        if pending is not None and not isinstance(pending, BridgeClient):
            raise TypeError("Invalid native bridge.")
        if self._bridge is not None and self._bridge is not pending:
            self._retired.append(self._bridge)
        self._bridge = pending
        self._connection = RhinoConnection(pending) if pending is not None else None

    def send_command(self, command_type, params=None):
        with self._lock:
            self._apply_pending()
            if self._bridge is not None and self._bridge.lifecycle_snapshot()[0] == "unknown":
                raise BridgeResultUnknown("A previous Rhino result remains unknown; further dispatch is blocked.")
            if self._connection is None:
                raise BridgeUnavailable(rhino_startup_error_message())
            return self._connection.send_command(command_type, params)

    def close(self) -> None:
        with self._pending_lock:
            pending = self._pending
        bridges = [self._bridge, *self._retired]
        if isinstance(pending, BridgeClient):
            bridges.append(pending)
        for bridge in bridges:
            if bridge is not None:
                bridge.disconnect()


@dataclass(frozen=True)
class ConnectorLifespanContext:
    """Per-server-process dependencies made available to every MCP request."""

    bridge_slot: ConnectorBridgeSlot

    @property
    def bridge_client(self) -> BridgeClient | None:
        return self.bridge_slot.bridge_client

    @property
    def command_client(self):
        return self.bridge_slot if self.bridge_slot.bridge_client is not None else None


def get_rhino_connection(ctx: Context | None = None) -> ConnectorBridgeSlot:
    """Resolve the command client from MCP's request-scoped lifespan context."""
    if ctx is None:
        raise BridgeUnavailable(rhino_startup_error_message())
    try:
        lifespan_context = ctx.request_context.lifespan_context
    except ValueError as exc:
        raise BridgeUnavailable(rhino_startup_error_message()) from exc
    if not isinstance(lifespan_context, ConnectorLifespanContext):
        raise BridgeUnavailable(rhino_startup_error_message())
    if lifespan_context.command_client is None:
        raise BridgeUnavailable(rhino_startup_error_message())
    return lifespan_context.command_client


ToolRegistration = tuple[str, Callable[..., Any]]

_TOOL_REGISTRATION_SOURCES: tuple[tuple[str, str], ...] = (
    ("analyze_objects", "rhino_mcp.tools.analyze_objects"),
    ("boolean_difference", "rhino_mcp.tools.boolean_operations"),
    ("boolean_intersection", "rhino_mcp.tools.boolean_operations"),
    ("boolean_union", "rhino_mcp.tools.boolean_operations"),
    ("capture_viewport", "rhino_mcp.tools.capture_viewport"),
    ("create_layer", "rhino_mcp.tools.create_layer"),
    ("create_object", "rhino_mcp.tools.create_object"),
    ("create_objects", "rhino_mcp.tools.create_objects"),
    ("create_planar_region", "rhino_mcp.tools.create_planar_region"),
    ("delete_layer", "rhino_mcp.tools.delete_layer"),
    ("delete_object", "rhino_mcp.tools.delete_object"),
    ("describe_capabilities", "rhino_mcp.tools.describe_capabilities"),
    (
        "execute_rhinocommon_csharp_code",
        "rhino_mcp.tools.execute_rhinocommon_csharp_code",
    ),
    (
        "execute_rhinoscript_python_code",
        "rhino_mcp.tools.execute_rhinoscript_python_code",
    ),
    ("extrude_curve", "rhino_mcp.tools.advanced_geometry"),
    ("get_commands", "rhino_mcp.tools.get_commands"),
    ("get_document_summary", "rhino_mcp.tools.get_document_summary"),
    ("get_modeling_guidance", "rhino_mcp.tools.get_modeling_guidance"),
    ("get_module_functions", "rhino_mcp.tools.rhinoscript_docs"),
    ("get_object_attributes", "rhino_mcp.tools.object_attributes"),
    ("get_object_info", "rhino_mcp.tools.get_object_info"),
    ("get_objects", "rhino_mcp.tools.get_objects"),
    ("get_or_set_current_layer", "rhino_mcp.tools.get_or_set_current_layer"),
    ("get_rhinoscript_docs", "rhino_mcp.tools.rhinoscript_docs"),
    ("get_selected_objects_info", "rhino_mcp.tools.get_selected_objects_info"),
    ("gh_add_component", "rhino_mcp.tools.grasshopper_components"),
    ("gh_batch_get_component_type_info", "rhino_mcp.tools.grasshopper_catalog"),
    ("gh_batch_search_components", "rhino_mcp.tools.grasshopper_catalog"),
    ("gh_build_graph", "rhino_mcp.tools.grasshopper_build"),
    ("gh_capture_preview", "rhino_mcp.tools.grasshopper_preview"),
    ("gh_clear_canvas", "rhino_mcp.tools.grasshopper_components"),
    ("gh_clear_graph", "rhino_mcp.tools.grasshopper_graph"),
    ("gh_connect_components", "rhino_mcp.tools.grasshopper_connections"),
    ("gh_create_document", "rhino_mcp.tools.grasshopper_document"),
    ("gh_delete_component", "rhino_mcp.tools.grasshopper_components"),
    ("gh_disconnect_components", "rhino_mcp.tools.grasshopper_connections"),
    ("gh_expire_solution", "rhino_mcp.tools.grasshopper_solution"),
    ("gh_get_available_components", "rhino_mcp.tools.grasshopper_catalog"),
    ("gh_get_canvas_state", "rhino_mcp.tools.grasshopper_document"),
    ("gh_get_component_info", "rhino_mcp.tools.grasshopper_components"),
    ("gh_get_component_type_info", "rhino_mcp.tools.grasshopper_catalog"),
    ("gh_get_document_info", "rhino_mcp.tools.grasshopper_document"),
    ("gh_get_graph", "rhino_mcp.tools.grasshopper_graph"),
    ("gh_get_parameter_value", "rhino_mcp.tools.grasshopper_parameters"),
    ("gh_layout_components", "rhino_mcp.tools.grasshopper_components"),
    ("gh_list_component_categories", "rhino_mcp.tools.grasshopper_catalog"),
    ("gh_list_components", "rhino_mcp.tools.grasshopper_components"),
    ("gh_mutate_graph", "rhino_mcp.tools.grasshopper_mutation"),
    ("gh_run_solution", "rhino_mcp.tools.grasshopper_solution"),
    ("gh_search_components", "rhino_mcp.tools.grasshopper_catalog"),
    ("gh_set_parameter_value", "rhino_mcp.tools.grasshopper_parameters"),
    ("gh_update_component", "rhino_mcp.tools.grasshopper_components"),
    ("intersect_curves", "rhino_mcp.tools.curve_operations"),
    ("list_rhinoscript_modules", "rhino_mcp.tools.rhinoscript_docs"),
    ("loft", "rhino_mcp.tools.advanced_geometry"),
    ("measure_objects", "rhino_mcp.tools.measure_objects"),
    ("modify_object", "rhino_mcp.tools.modify_object"),
    ("modify_objects", "rhino_mcp.tools.modify_objects"),
    ("offset_curve", "rhino_mcp.tools.advanced_geometry"),
    ("pipe", "rhino_mcp.tools.advanced_geometry"),
    ("project_curve", "rhino_mcp.tools.curve_operations"),
    ("redo", "rhino_mcp.tools.undo"),
    ("run_command", "rhino_mcp.tools.run_command"),
    ("search_rhinoscript_functions", "rhino_mcp.tools.rhinoscript_docs"),
    ("section_profile", "rhino_mcp.tools.section_profile"),
    ("select_objects", "rhino_mcp.tools.select_objects"),
    ("split_curve", "rhino_mcp.tools.curve_operations"),
    ("sweep1", "rhino_mcp.tools.advanced_geometry"),
    ("undo", "rhino_mcp.tools.undo"),
    ("update_object_attributes", "rhino_mcp.tools.object_attributes"),
)


def _load_tool_registrations() -> tuple[ToolRegistration, ...]:
    registrations = tuple(
        (name, getattr(import_module(module_name), name))
        for name, module_name in _TOOL_REGISTRATION_SOURCES
    )
    if len(registrations) != 70:
        raise RuntimeError("The explicit MCP tool registration table is incomplete.")
    return registrations


TOOL_REGISTRATIONS = _load_tool_registrations()

READ_ONLY_TOOL_NAMES = frozenset(
    {
        "analyze_objects",
        "capture_viewport",
        "describe_capabilities",
        "get_commands",
        "get_document_summary",
        "get_modeling_guidance",
        "get_module_functions",
        "get_object_attributes",
        "get_object_info",
        "get_objects",
        "get_rhinoscript_docs",
        "get_selected_objects_info",
        "gh_batch_get_component_type_info",
        "gh_batch_search_components",
        "gh_get_available_components",
        "gh_get_canvas_state",
        "gh_get_component_info",
        "gh_get_component_type_info",
        "gh_get_document_info",
        "gh_get_graph",
        "gh_get_parameter_value",
        "gh_list_component_categories",
        "gh_list_components",
        "gh_search_components",
        "list_rhinoscript_modules",
        "measure_objects",
        "search_rhinoscript_functions",
        "section_profile",
    }
)
DESTRUCTIVE_TOOL_NAMES = frozenset(
    {
        "boolean_difference",
        "boolean_intersection",
        "boolean_union",
        "delete_layer",
        "delete_object",
        "execute_rhinocommon_csharp_code",
        "execute_rhinoscript_python_code",
        "gh_clear_canvas",
        "gh_clear_graph",
        "gh_delete_component",
        "gh_mutate_graph",
        "redo",
        "run_command",
        "undo",
    }
)
OPEN_WORLD_TOOL_NAMES = frozenset(
    {
        "execute_rhinocommon_csharp_code",
        "execute_rhinoscript_python_code",
        "run_command",
    }
)

RESOURCE_REGISTRATIONS: tuple[tuple[str, Callable[..., Any], str | None], ...] = (
    ("rhinoscript://modules", resource_list_modules, "text/markdown"),
    ("rhinoscript://module/{module_name}", resource_get_module, "text/markdown"),
    (
        "rhinoscript://function/{function_name}",
        resource_get_function,
        "text/markdown",
    ),
    (
        "rhino://guidance/{topic}",
        getattr(
            import_module("rhino_mcp.tools.get_modeling_guidance"),
            "modeling_guidance_resource",
        ),
        "text/markdown",
    ),
)
PROMPT_REGISTRATIONS: tuple[tuple[str, Callable[..., Any]], ...] = (
    (
        "asset_general_strategy",
        getattr(
            import_module("rhino_mcp.prompts.assert_general_strategy"),
            "asset_general_strategy",
        ),
    ),
    (
        "rhinoscript_workflow",
        getattr(
            import_module("rhino_mcp.prompts.assert_general_strategy"),
            "rhinoscript_workflow",
        ),
    ),
)


def _tool_annotations(name: str) -> ToolAnnotations:
    read_only = name in READ_ONLY_TOOL_NAMES
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=name in DESTRUCTIVE_TOOL_NAMES,
        idempotent_hint=read_only,
        open_world_hint=name in OPEN_WORLD_TOOL_NAMES,
    )


def create_mcp_server(bridge_client: BridgeClient | None = None) -> RhinoConnectorServer:
    """Build one Connector server whose Bridge dependency is owned by its lifespan."""
    if bridge_client is not None and not isinstance(bridge_client, BridgeClient):
        raise TypeError("create_mcp_server requires a validated BridgeClient.")

    bridge_slot = ConnectorBridgeSlot(bridge_client)

    @asynccontextmanager
    async def server_lifespan(
        _server: MCPServer,
    ) -> AsyncIterator[ConnectorLifespanContext]:
        logger.info("Rhino MCP Connector starting up")
        try:
            yield ConnectorLifespanContext(bridge_slot=bridge_slot)
        finally:
            bridge_slot.close()
            logger.info("Rhino MCP Connector shut down")

    server = RhinoConnectorServer(
        "Rhino MCP",
        lifespan=server_lifespan,
        instructions=SERVER_INSTRUCTIONS,
        version=server_version() or "0+unknown",
    )
    server.connector_bridge_slot = bridge_slot
    registered_names: set[str] = set()
    for name, tool in TOOL_REGISTRATIONS:
        if name in registered_names or name != tool.__name__:
            raise RuntimeError("The explicit MCP tool registration table is invalid.")
        registered_names.add(name)
        server.add_tool(tool, name=name, annotations=_tool_annotations(name))
    if len(registered_names) != 70:
        raise RuntimeError("The explicit MCP tool registration table is incomplete.")

    for uri, resource, mime_type in RESOURCE_REGISTRATIONS:
        server.resource(uri, mime_type=mime_type)(resource)
    for name, prompt in PROMPT_REGISTRATIONS:
        server.prompt(name=name)(prompt)
    return server


# Source-level consumers and tests use this discovery-only instance. Managed
# startup builds a fresh server with the validated process-owned BridgeClient.
mcp = create_mcp_server()
