"""Internal synthetic connector for Host connection and MCP contract tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import socket
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from types import SimpleNamespace

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier
from mcp.types import Tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
)


class ExactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )


class Bind(ExactModel):
    host: Literal["127.0.0.1"]
    port: int = Field(ge=1024, le=65535)
    path: Literal["/mcp"]


class ConfigurationV1(ExactModel):
    """Explicit synthetic lifecycle state for production Host integration tests."""

    lifecycleTestState: Literal["unknown", "idle", "running"] = "unknown"


class ToolPreviewV1(ExactModel):
    """Complete official MCP Tool declarations, with fixed normalization."""

    normalizationVersion: Literal[1]
    tools: list[Tool]


class ConnectorExecutionStatusV1(ExactModel):
    schemaVersion: Literal[1] = 1
    kind: Literal["status"] = "status"
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: int = Field(ge=1, le=2**53 - 1)
    sequence: int = Field(ge=1, le=2**53 - 1)
    state: Literal["unknown", "idle", "running"]
    inFlight: int = Field(ge=0)


class SourceFileDigestV1(ExactModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RiskReviewV1(ExactModel):
    """Pending synthetic evidence only; never a permission or review approval."""

    schemaVersion: Literal[1]
    status: Literal["pending-review/internal-fixture-only"]
    engineeringAcceptance: Literal["not-performed"]
    previewDigest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    connectorBuildId: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    sourceDigest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    sourceFiles: list[SourceFileDigestV1]


class EchoResult(ExactModel):
    connectorInstanceId: str
    value: str


class WriteResult(ExactModel):
    written: bool


class GenerationTokenVerifier(TokenVerifier):
    def __init__(self, token: str, connector: str):
        super().__init__()
        self._digest = hashlib.sha256(token.encode()).digest()
        self._connector = connector

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(
            hashlib.sha256(token.encode()).digest(), self._digest
        ):
            return None
        return AccessToken(token=token, client_id=self._connector, scopes=[])


def build_mcp(
    connector_id: str,
    state_root: Path | None,
    bearer_token: str | None = None,
    *,
    mode: Literal["metadata", "runtime"] = "runtime",
) -> FastMCP:
    server = FastMCP(
        "fixture-engineering",
        mask_error_details=True,
        auth=GenerationTokenVerifier(bearer_token, connector_id)
        if bearer_token
        else None,
        strict_input_validation=True,
    )

    def require_execution():
        if mode != "runtime":
            raise ValueError("metadataOnly")

    @server.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    def echo(value: str) -> EchoResult:
        """Echo a value with this isolated connector's identity."""
        require_execution()
        return EchoResult(connectorInstanceId=connector_id, value=value)

    @server.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    def synthetic_write(value: str) -> WriteResult:
        """Write an approved synthetic value to the fixture's fixed state file."""
        require_execution()
        state_root.mkdir(parents=True, exist_ok=True)
        (state_root / "synthetic-state.json").write_text(
            json.dumps({"value": value}), encoding="utf-8"
        )
        return WriteResult(written=True)

    return server


def connector_build_id(package_root: Path) -> str:
    records = [
        {
            "path": path.relative_to(package_root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(package_root.rglob("*.py"))
    ]
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def read_connector_envelope(stream):
    from fixture_engineering._connector_control import decode_bootstrap

    raw = stream.readline(64 * 1024 + 2).removesuffix(b"\n")
    value = decode_bootstrap(raw)
    if value["secrets"] or value["nativePeer"] is not None:
        raise ValueError("invalidFixtureConfiguration")
    binding = value["binding"]
    if value["mode"] not in {"metadata", "runtime"}:
        raise ValueError("invalidFixtureMode")
    return SimpleNamespace(
        binding=binding,
        connectorInstanceId=binding["connectorInstanceId"],
        generation=binding["generation"],
        mode=value["mode"],
        bind=Bind.model_validate(value["bind"]),
        bearerToken=SecretStr(value["bearerToken"]),
        connectorBuildId=value["connectorBuildId"],
        dataDirectory=value["dataDirectory"],
        configuration=ConfigurationV1.model_validate(value["configuration"])
        if value["mode"] == "runtime"
        else None,
    )


async def serve(envelope, ready_stream):
    import uvicorn

    if (
        sys.version_info[:3] != (3, 12, 10)
        or version("fastmcp") != "4.0.3"
        or version("mcp") != "2.2.0"
    ):
        raise ValueError("runtimeVersionMismatch")
    if connector_build_id(Path(__file__).parent) != envelope.connectorBuildId:
        raise ValueError("connectorBuildMismatch")
    app = build_mcp(
        envelope.connectorInstanceId,
        Path(envelope.dataDirectory) if envelope.dataDirectory else None,
        envelope.bearerToken.get_secret_value(),
        mode=envelope.mode,
    ).http_app(
        path="/mcp",
        transport="streamable-http",
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=[f"127.0.0.1:{envelope.bind.port}"],
        allowed_origins=[],
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((envelope.bind.host, envelope.bind.port))
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
        from fixture_engineering._connector_channel import ConnectorChannel
        from fixture_engineering._connector_control import AdapterConnectionResult

        class Adapter:
            async def ensure_connection(self, _deadline):
                return AdapterConnectionResult("confirmed", "none", "si_fixture")

        def emit(event):
            ready_stream.write(json.dumps(event, separators=(",", ":")) + "\n")
            ready_stream.flush()

        channel = ConnectorChannel(envelope.binding, Adapter(), emit)

        async def notify_ready():
            while not server.started:
                if server.should_exit:
                    raise ValueError("providerStartFailed")
                await asyncio.sleep(0.01)
            ready_stream.write(json.dumps({"schemaVersion":3,"kind":"ready","binding":envelope.binding,"connectorBuildId":envelope.connectorBuildId,"port":envelope.bind.port}, separators=(",", ":")) + "\n")
            ready_stream.flush()
            sequence = 0
            while not server.should_exit:
                sequence += 1
                state = envelope.configuration.lifecycleTestState if envelope.configuration else "unknown"
                ready_stream.write(ConnectorExecutionStatusV1(
                    connectorInstanceId=envelope.connectorInstanceId,
                    generation=envelope.generation, sequence=sequence,
                    state=state, inFlight=1 if state == "running" else 0,
                ).model_dump_json() + "\n")
                ready_stream.flush()
                await asyncio.sleep(0.25)

        async def read_controls():
            try:
                await channel.run_reader(sys.stdin.buffer)
            finally:
                server.should_exit = True

        async with asyncio.TaskGroup() as group:
            group.create_task(server.serve(sockets=[sock]))
            group.create_task(notify_ready())
            group.create_task(read_controls())
    finally:
        sock.close()


def main():
    try:
        envelope = read_connector_envelope(sys.stdin.buffer)
        asyncio.run(serve(envelope, sys.stdout))
    except (Exception, KeyboardInterrupt):
        # No envelope values, machine paths, or bearer tokens in diagnostics.
        print("fixtureStartFailed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
