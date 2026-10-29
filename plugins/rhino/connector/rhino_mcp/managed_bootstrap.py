"""Tauri-owned, bounded startup for the Rhino Streamable HTTP Connector."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Literal, NoReturn

import uvicorn
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
)
from pydantic import field_validator

from rhino_mcp.bridge import (
    BridgeClient,
    BridgeConfig,
    BridgeRejected,
    BridgeUnavailable,
)
from rhino_mcp.build_identity import CONNECTOR_BUILD_ID
from rhino_mcp.server import (
    ManagedTokenVerifier,
    RhinoConnectorServer,
    create_authenticated_http_app,
    create_mcp_server,
)
from rhino_mcp.validation import CONTRACTS_DIR, validate_contract_inventory

MAX_STARTUP_BYTES = 65_536
_MAX_U64 = (1 << 64) - 1
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")


class StartupConfigurationError(ValueError):
    """Safe, non-sensitive rejection of the managed startup contract."""

    category = "invalidStartup"

    def __init__(self) -> None:
        super().__init__("Managed Connector startup configuration was rejected.")


@dataclass(repr=False)
class ConnectorRuntimeContext:
    binding: dict[str, Any]
    mode: str
    connectorInstanceId: str
    generation: int
    bind: Any
    bearerToken: SecretStr = field(repr=False)
    connectorBuildId: str
    nativeContext: Any = field(repr=False)
    nativeInstanceId: str | None
    _contract_digest: str

    @property
    def contract_root(self) -> Path:
        return CONTRACTS_DIR

    @property
    def contract_digest(self) -> str:
        return self._contract_digest


class ExactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )


class ConfigurationV1(ExactModel):
    """Rhino 插件无需 API Key。请启动兼容版本的 Rhino 8 以建立连接。"""

    model_config = ConfigDict(
        title="Rhino",
        description="Rhino 插件无需 API Key。请启动兼容版本的 Rhino 8 以建立连接。",
    )


class BindSettings(ExactModel):
    host: Literal["127.0.0.1"]
    port: int = Field(strict=True, ge=1024, le=65535)
    path: Literal["/mcp"]


class NativeContext(ExactModel):
    host: Literal["127.0.0.1"]
    port: int = Field(strict=True, ge=1024, le=65535)
    token: SecretStr = Field(repr=False)
    # The authenticated native Bridge lease generation, independent of MCP resources.
    generation: int = Field(strict=True, ge=1, le=2**53 - 1)
    connectorBuildId: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contractDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("token")
    @classmethod
    def valid_token(cls, value: SecretStr) -> SecretStr:
        if not _TOKEN_PATTERN.fullmatch(value.get_secret_value()):
            raise ValueError("Invalid Bridge credential.")
        return value


class ConnectorExecutionStatusV1(ExactModel):
    schemaVersion: Literal[1] = 1
    kind: Literal["status"] = "status"
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: int = Field(strict=True, ge=1, le=2**53 - 1)
    sequence: int = Field(strict=True, ge=1, le=2**53 - 1)
    state: Literal["idle", "running", "unknown", "draining", "unverifiable"]
    inFlight: int = Field(strict=True, ge=0)


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("Non-JSON numeric constant.")


def _validated_contract_root(raw_root: Any) -> tuple[Path, str]:
    if not isinstance(raw_root, str) or not raw_root:
        raise StartupConfigurationError()
    try:
        if raw_root != os.path.normpath(raw_root):
            raise StartupConfigurationError()
        root = Path(raw_root)
        if not root.is_absolute():
            raise StartupConfigurationError()
        canonical_root = root.resolve(strict=True)
        if not canonical_root.is_dir() or str(canonical_root) != raw_root:
            raise StartupConfigurationError()
        validate_contract_inventory(canonical_root)
        protocol_path = (canonical_root / "protocol.json").resolve(strict=True)
        protocol_path.relative_to(canonical_root)
        if not protocol_path.is_file():
            raise StartupConfigurationError()
        contract_digest = (
            "sha256:" + hashlib.sha256(protocol_path.read_bytes()).hexdigest()
        )
    except StartupConfigurationError:
        raise
    except Exception:
        raise StartupConfigurationError() from None
    return canonical_root, contract_digest


def read_connector_envelope(stream: BinaryIO) -> ConnectorRuntimeContext:
    from rhino_mcp._connector_control import decode_bootstrap

    try:
        payload = stream.readline(MAX_STARTUP_BYTES + 2).removesuffix(b"\n")
        value = decode_bootstrap(payload)
        ConfigurationV1.model_validate(value["configuration"])
        if value["connectorBuildId"] != CONNECTOR_BUILD_ID or value["secrets"]:
            raise ValueError("invalidConnectorConfiguration")
        bearer = value["bearerToken"]
        if not _TOKEN_PATTERN.fullmatch(bearer):
            raise ValueError("invalidBearer")
        _, digest = _validated_contract_root(str(CONTRACTS_DIR))
        native = value["nativePeer"]
        native_instance = native["softwareInstanceId"] if native is not None else None
        if native is not None:
            native = dict(native)
            native.pop("softwareInstanceId")
            native = NativeContext.model_validate(native)
            if native.connectorBuildId != CONNECTOR_BUILD_ID or native.contractDigest != digest or native.port == value["bind"]["port"] or hmac.compare_digest(native.token.get_secret_value(), bearer):
                raise ValueError("invalidNativePeer")
        binding = value["binding"]
        return ConnectorRuntimeContext(binding=binding, mode="execution" if value["mode"] == "runtime" else value["mode"], connectorInstanceId=binding["connectorInstanceId"], generation=binding["generation"], bind=BindSettings.model_validate(value["bind"]), bearerToken=SecretStr(bearer), connectorBuildId=value["connectorBuildId"], nativeContext=native, nativeInstanceId=native_instance, _contract_digest=digest)
    except (KeyError, TypeError, ValueError, ValidationError, OSError):
        raise StartupConfigurationError() from None


def create_managed_server(
    envelope: ConnectorRuntimeContext,
) -> tuple[RhinoConnectorServer, BridgeClient | None]:
    """Metadata never constructs a Bridge transport; engineering connects lazily."""
    native = envelope.nativeContext
    bridge = None
    if native is not None:
        bridge = BridgeClient(
            BridgeConfig(
                endpoint=(native.host, native.port),
                timeout_seconds=15.0,
                generation=native.generation,
                token=native.token.get_secret_value(),
                connector_build_id=native.connectorBuildId,
                contract_digest=envelope.contract_digest,
                contract_root=envelope.contract_root,
            )
        )
    return create_mcp_server(bridge), bridge


def _bridge_from_connector_peer(envelope: ConnectorRuntimeContext, value: dict) -> BridgeClient:
    peer = dict(value)
    peer.pop("softwareInstanceId")
    native = NativeContext.model_validate(peer)
    if native.connectorBuildId != envelope.connectorBuildId or native.contractDigest != envelope.contract_digest or native.port == envelope.bind.port or hmac.compare_digest(native.token.get_secret_value(), envelope.bearerToken.get_secret_value()):
        raise StartupConfigurationError()
    return BridgeClient(BridgeConfig(endpoint=(native.host, native.port), timeout_seconds=15.0, generation=native.generation, token=native.token.get_secret_value(), connector_build_id=native.connectorBuildId, contract_digest=envelope.contract_digest, contract_root=envelope.contract_root))


async def _serve(envelope: ConnectorRuntimeContext) -> None:
    mcp_server, bridge_client = create_managed_server(envelope)
    from rhino_mcp._connector_channel import ConnectorChannel
    from rhino_mcp.software_connection import RhinoSoftwareConnection

    def emit(event):
        sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    native = envelope.nativeContext
    initial_peer = None if native is None else {"host":native.host,"port":native.port,"token":native.token.get_secret_value(),"generation":native.generation,"connectorBuildId":native.connectorBuildId,"contractDigest":native.contractDigest,"softwareInstanceId":envelope.nativeInstanceId}
    adapter = RhinoSoftwareConnection(bridge_client, peer_factory=lambda peer: _bridge_from_connector_peer(envelope, peer), peer_instance=envelope.nativeInstanceId, peer_changed=mcp_server.connector_bridge_slot.update, initial_peer=initial_peer)
    del initial_peer
    channel = ConnectorChannel(envelope.binding, adapter, emit)
    token_verifier = ManagedTokenVerifier(
        envelope.bearerToken.get_secret_value(),
        client_id=envelope.connectorInstanceId,
    )
    app = create_authenticated_http_app(
        mcp_server,
        token_verifier=token_verifier,
        port=envelope.bind.port,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=envelope.bind.host,
            port=envelope.bind.port,
            log_config=None,
            access_log=False,
            log_level="critical",
            proxy_headers=False,
            forwarded_allow_ips="",
            lifespan="on",
        )
    )
    previous_break_handler = None
    if hasattr(signal, "SIGBREAK"):
        # Tauri sends CTRL_BREAK_EVENT to Windows connector process groups. Uvicorn
        # handles SIGINT/SIGTERM, but SIGBREAK otherwise bypasses its graceful
        # shutdown path and leaves the connector alive until the force-kill timeout.
        previous_break_handler = signal.getsignal(signal.SIGBREAK)
        signal.signal(signal.SIGBREAK, lambda _signum, _frame: setattr(server, "should_exit", True))
    server_task = asyncio.create_task(server.serve())
    control_task = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15.0
    try:
        while not server.started:
            if server_task.done():
                await server_task
                raise StartupConfigurationError()
            if loop.time() >= deadline:
                raise StartupConfigurationError()
            await asyncio.sleep(0.01)

        ready = {"schemaVersion":3,"kind":"ready","binding":envelope.binding,"connectorBuildId":envelope.connectorBuildId,"port":envelope.bind.port}
        sys.stdout.write(json.dumps(ready, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        control_task = asyncio.create_task(channel.run_reader(sys.stdin.buffer))
        sequence = 0
        while not server_task.done():
            if control_task.done():
                await control_task
                server.should_exit = True
            sequence += 1
            state: Literal["idle", "running", "unknown"]
            current_bridge = mcp_server.connector_bridge_slot.bridge_client
            if current_bridge is None:
                state, in_flight = "idle", 0
            else:
                state, in_flight = current_bridge.lifecycle_snapshot()
            status = ConnectorExecutionStatusV1(
                connectorInstanceId=envelope.binding["connectorInstanceId"],
                generation=envelope.binding["generation"],
                sequence=sequence,
                state=state,
                inFlight=in_flight,
            )
            sys.stdout.write(status.model_dump_json() + "\n")
            sys.stdout.flush()
            await asyncio.wait({server_task}, timeout=0.25)
        await server_task
    finally:
        if control_task is not None and not control_task.done():
            control_task.cancel()
            try:
                await control_task
            except asyncio.CancelledError:
                pass
        if not server_task.done():
            server.should_exit = True
            try:
                await asyncio.wait_for(server_task, timeout=10)
            except Exception:
                pass
        token_verifier.clear()
        if previous_break_handler is not None:
            signal.signal(signal.SIGBREAK, previous_break_handler)


def _write_error(category: str) -> None:
    sys.stderr.write(
        json.dumps(
            {"schemaVersion": 1, "kind": "error", "category": category},
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stderr.flush()


def main() -> int:
    """Run only as a Tauri-launched module with one stdin startup document."""
    logging.disable(logging.CRITICAL)
    if len(sys.argv) != 1:
        _write_error("invalidStartup")
        return 2
    try:
        envelope = read_connector_envelope(sys.stdin.buffer)
        asyncio.run(_serve(envelope))
    except KeyboardInterrupt:
        # Match Uvicorn's CLI runner: Server.serve() restores and re-raises
        # captured signals after its graceful shutdown has completed.
        return 0
    except StartupConfigurationError:
        _write_error("invalidStartup")
        return 2
    except BridgeRejected:
        _write_error("bridgeRejected")
        return 3
    except BridgeUnavailable:
        _write_error("bridgeUnavailable")
        return 4
    except Exception:
        _write_error("startupFailed")
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
