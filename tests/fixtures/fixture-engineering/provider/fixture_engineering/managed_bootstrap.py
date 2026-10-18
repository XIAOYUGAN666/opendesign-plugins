"""Internal fixture's single typed startup authority and real MCP tool source.

No vendor software, engineering credentials, local import command, or Stable trust.
Schema V2 is fixture-declared; production Host V2 routing is a later task.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import socket
import sys
from importlib.metadata import version
from pathlib import Path, PureWindowsPath
from typing import Literal

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier
from mcp.types import Tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
    TypeAdapter,
)


class ExactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )


class Bind(ExactModel):
    host: Literal["127.0.0.1"]
    port: int = Field(ge=1024, le=65535)
    path: Literal["/mcp"]


class Data(ExactModel):
    stateDirectory: str = Field(min_length=1, max_length=32767, repr=False)

    @field_validator("stateDirectory")
    @classmethod
    def local_directory(cls, value: str) -> str:
        path = PureWindowsPath(value)
        if (
            not path.is_absolute()
            or len(path.drive) != 2
            or path.drive[1] != ":"
            or ".." in path.parts
            or ":" in value[2:]
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("invalidDataPath")
        return value


class Software(ExactModel):
    safeIdentity: str = Field(pattern=r"^si_[a-zA-Z0-9_-]{1,128}$")
    build: Literal["synthetic-1"]


class StartupEnvelopeV2(ExactModel):
    schemaVersion: Literal[2]
    mode: Literal["metadata", "execution", "observation"]
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: int = Field(ge=1, le=2**53 - 1)
    bind: Bind
    bearerToken: SecretStr = Field(repr=False)
    providerBuildId: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    data: Data | None = None
    software: Software | None = None
    configuration: ConfigurationV1 | None = None

    @model_validator(mode="after")
    def execution_requires_binding(self):
        if self.mode == "execution" and (self.data is None or self.software is None):
            raise ValueError("executionBindingRequired")
        if self.mode == "metadata" and (
            self.data is not None or self.software is not None
        ):
            raise ValueError("metadataBindingForbidden")
        if self.mode == "observation" and (
            self.data is None or self.software is not None or self.configuration is not None
        ):
            raise ValueError("observationDataOnlyRequired")
        return self

    @field_validator("schemaVersion", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("invalidSchemaVersion")
        return value

    @field_validator("bearerToken")
    @classmethod
    def bounded_token(cls, value: SecretStr) -> SecretStr:
        plain = value.get_secret_value()
        if (
            not 32 <= len(plain) <= 512
            or not plain.isascii()
            or not all(c.isalnum() or c in "_-" for c in plain)
        ):
            raise ValueError("invalidBearer")
        return value


class ConfigurationV1(ExactModel):
    """Explicit synthetic lifecycle state for production Host integration tests."""

    lifecycleTestState: Literal["unknown", "idle", "running"] = "unknown"


class ReadyV2(ExactModel):
    """Only the current ready frame; no background execution protocol."""

    schemaVersion: Literal[2]
    kind: Literal["ready"]
    mode: Literal["metadata", "execution", "observation"]
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: int = Field(ge=1, le=2**53 - 1)
    providerBuildId: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    port: int = Field(ge=1024, le=65535)


class ToolPreviewV1(ExactModel):
    """Complete official MCP Tool declarations, with fixed normalization."""

    normalizationVersion: Literal[1]
    tools: list[Tool]


class ProviderLifecycleStatusV1(ExactModel):
    schemaVersion: Literal[1] = 1
    kind: Literal["status"] = "status"
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: int = Field(ge=1, le=2**53 - 1)
    sequence: int = Field(ge=1, le=2**53 - 1)
    state: Literal["unknown", "idle", "running"]
    inFlight: int = Field(ge=0)


def lifecycle_schema():
    return TypeAdapter(ReadyV2 | ProviderLifecycleStatusV1).json_schema()


class SourceFileDigestV1(ExactModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RiskReviewV1(ExactModel):
    """Pending synthetic evidence only; never a permission or review approval."""

    schemaVersion: Literal[1]
    status: Literal["pending-review/internal-fixture-only"]
    engineeringAcceptance: Literal["not-performed"]
    previewDigest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    providerBuildId: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
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
    mode: Literal["metadata", "execution", "observation"] = "execution",
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
        if mode != "execution":
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


def provider_build_id(package_root: Path) -> str:
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


def read_envelope(stream) -> StartupEnvelopeV2:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicateKey")
            result[key] = value
        return result

    raw = stream.read(64 * 1024 + 1)
    if not raw or len(raw) > 64 * 1024:
        raise ValueError("invalidStartupEnvelope")
    return StartupEnvelopeV2.model_validate(
        json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    )


async def serve(envelope: StartupEnvelopeV2, ready_stream):
    import uvicorn

    if (
        sys.version_info[:3] != (3, 12, 10)
        or version("fastmcp") != "4.0.3"
        or version("mcp") != "2.2.0"
    ):
        raise ValueError("runtimeVersionMismatch")
    if provider_build_id(Path(__file__).parent) != envelope.providerBuildId:
        raise ValueError("providerBuildMismatch")
    app = build_mcp(
        envelope.connectorInstanceId,
        Path(envelope.data.stateDirectory) if envelope.data else None,
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

        async def notify_ready():
            while not server.started:
                if server.should_exit:
                    raise ValueError("providerStartFailed")
                await asyncio.sleep(0.01)
            ready_stream.write(
                ReadyV2(
                    schemaVersion=2,
                    kind="ready",
                    mode=envelope.mode,
                    connectorInstanceId=envelope.connectorInstanceId,
                    generation=envelope.generation,
                    providerBuildId=envelope.providerBuildId,
                    port=envelope.bind.port,
                ).model_dump_json()
                + "\n"
            )
            ready_stream.flush()
            sequence = 0
            while not server.should_exit:
                sequence += 1
                state = envelope.configuration.lifecycleTestState if envelope.configuration else "unknown"
                if envelope.mode == "observation":
                    try:
                        marker = Path(envelope.data.stateDirectory) / "observation-state.json"
                        value = json.loads(marker.read_text(encoding="utf-8"))
                        state = "idle" if value == {"state": "idle"} else "unknown"
                    except (OSError, ValueError):
                        state = "unknown"
                ready_stream.write(ProviderLifecycleStatusV1(
                    connectorInstanceId=envelope.connectorInstanceId,
                    generation=envelope.generation, sequence=sequence,
                    state=state, inFlight=1 if state == "running" else 0,
                ).model_dump_json() + "\n")
                ready_stream.flush()
                await asyncio.sleep(0.25)

        async with asyncio.TaskGroup() as group:
            group.create_task(server.serve(sockets=[sock]))
            group.create_task(notify_ready())
    finally:
        sock.close()


def main():
    try:
        envelope = read_envelope(sys.stdin.buffer)
        sys.stdin.close()
        asyncio.run(serve(envelope, sys.stdout))
    except (Exception, KeyboardInterrupt):
        # No envelope values, machine paths, or bearer tokens in diagnostics.
        print("fixtureStartFailed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
