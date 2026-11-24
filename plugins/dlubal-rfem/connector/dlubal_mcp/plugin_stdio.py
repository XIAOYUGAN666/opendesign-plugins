"""OpenDesign's local process entry using the official RFEM MCP registration."""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Literal

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from pydantic import BaseModel, ConfigDict, Field

from dlubal_mcp.core.config import DlubalMCPSettings
from dlubal_mcp.core.logging_config import setup_logging
from dlubal_mcp.core.product_execution import read_application_info
from dlubal_mcp.core.server import build_stdio_mcp
from dlubal_mcp.rfem.session import RFEMSession
from dlubal_mcp.sdk import activate_sdk, service_version_matches
from dlubal_mcp.setup import bound_installation


class ConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    address: Literal["127.0.0.1", "localhost"] = Field(
        "127.0.0.1", title="RFEM API 地址"
    )
    port: int = Field(9000, ge=1, le=65535, title="RFEM API 端口")


def read_connection_settings() -> ConnectionSettings:
    return ConnectionSettings.model_validate_json(
        os.environ.get("PLUGIN_CONFIGURATION", "{}")
    )


class ConnectionGate(Middleware):
    def __init__(
        self,
        metadata: bool,
        build: str | None,
        key: str,
        connection: ConnectionSettings,
    ):
        self.metadata = metadata
        self.build = build
        self.key = key
        self.connection = connection
        self.verified = False
        self._verification = asyncio.Lock()

    async def on_call_tool(self, context, call_next):
        if self.metadata:
            raise ToolError("metadataOnly")
        if context.message.name in {"health_check", "get_rfem_execution"}:
            return await call_next(context)
        if self.build is None:
            raise ToolError("softwarePreparationRequired")
        if context.message.name == "inspect_rfem_api":
            return await call_next(context)
        if not self.key:
            raise ToolError("credentialMissing")
        if context.message.name != "get_rfem_application_info" and not self.verified:
            await self._verify_endpoint()
        result = await call_next(context)
        if context.message.name == "get_rfem_application_info":
            self._verified_version(result.structured_content)
        return result

    async def _verify_endpoint(self) -> None:
        async with self._verification:
            if self.verified:
                return
            session = RFEMSession(
                "", self.key, self.connection.address, self.connection.port
            )
            try:
                info = await asyncio.to_thread(read_application_info, session, "rfem")
            except Exception:  # noqa: BLE001 - external SDK errors stay at this boundary
                raise ToolError("connectionFailed") from None
            self._verified_version(info)

    def _verified_version(self, info) -> None:
        if (
            self.build is None
            or not info
            or not service_version_matches(self.build, info.get("version", ""))
        ):
            self.verified = False
            raise ToolError("unsupportedSoftwareVersion")
        self.verified = True


class _StdioOutput:
    """Keep the SDK's text messages off the MCP byte stream."""

    def __init__(self, protocol, diagnostics):
        self.buffer = protocol.buffer
        self._diagnostics = diagnostics

    def __getattr__(self, name):
        return getattr(self._diagnostics, name)


def build_plugin_server(data: Path, *, metadata: bool = False):
    connection = read_connection_settings()
    key = "" if metadata else os.environ.get("PLUGIN_RFEM_API_KEY", "")
    binding = None if metadata else bound_installation(data)
    if binding is not None:
        activate_sdk(data)
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "DLUBAL_MCP_PRODUCTS": "rfem",
            "RFEM_URL": connection.address,
            "RFEM_API_PORT": str(connection.port),
            "RFEM_EXE_PATH": binding["executable"] if binding else "",
            # A supplied value and an empty name select the SDK's value-only path.
            # The gate keeps metadata and missing-key calls from reaching the SDK.
            "RFEM_API_KEY": key or "unavailable",
            "RFEM_API_KEY_NAME": "",
            "RFEM_EXECUTION_STATE_FILE": str(data / "executions.sqlite3"),
        }
    )
    settings = DlubalMCPSettings(transport="stdio")
    setup_logging(
        level=settings.log_level, json_output=settings.log_json, stream=sys.stderr
    )
    server = build_stdio_mcp(settings)
    server.add_middleware(
        ConnectionGate(metadata, binding["build"] if binding else None, key, connection)
    )
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", action="store_true")
    args = parser.parse_args()
    data = Path(os.environ["PLUGIN_DATA"])
    sys.stdout = _StdioOutput(sys.stdout, sys.stderr)  # type: ignore[assignment]
    build_plugin_server(data, metadata=args.metadata).run(
        transport="stdio", show_banner=False
    )


if __name__ == "__main__":
    main()
