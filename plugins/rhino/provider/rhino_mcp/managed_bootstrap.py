"""Tauri-owned, bounded startup for the Rhino Streamable HTTP Provider."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, BinaryIO, Literal, NoReturn

import uvicorn
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    ValidationError,
)
from pydantic import field_validator, model_validator

from rhino_mcp.bridge import (
    BridgeClient,
    BridgeConfig,
    BridgeRejected,
    BridgeUnavailable,
)
from rhino_mcp.build_identity import PROVIDER_BUILD_ID
from rhino_mcp.server import (
    ManagedTokenVerifier,
    RhinoProviderServer,
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
        super().__init__("Managed Provider startup configuration was rejected.")


class ExactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
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
    providerBuildId: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    contractDigest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("token")
    @classmethod
    def valid_token(cls, value: SecretStr) -> SecretStr:
        if not _TOKEN_PATTERN.fullmatch(value.get_secret_value()):
            raise ValueError("Invalid Bridge credential.")
        return value


class ConfigurationV1(ExactModel):
    """Rhino has no ordinary configuration or business secret slot."""


class DataSettings(ExactModel):
    stateDirectory: str = Field(repr=False, min_length=1, max_length=32767)

    @field_validator("stateDirectory")
    @classmethod
    def local_directory(cls, value: str) -> str:
        from pathlib import PureWindowsPath

        path = PureWindowsPath(value)
        if (
            not path.is_absolute()
            or len(path.drive) != 2
            or path.drive[1] != ":"
            or ".." in path.parts
            or ":" in value[2:]
            or any(ord(char) < 32 for char in value)
            or path.name in ("", ".")
        ):
            raise ValueError("Invalid data path.")
        return value


class SoftwareSettings(ExactModel):
    safeIdentity: str = Field(pattern=r"^si_[a-zA-Z0-9_-]{1,128}$")
    build: Literal["8.26.25349.19001"]


class StartupEnvelopeV2(ExactModel):
    """Exact anonymous-stdin contract; native credentials never become config."""

    schemaVersion: Literal[2]
    mode: Literal["metadata", "execution"]
    connectorInstanceId: str = Field(pattern=r"^ci_[0-9a-f]{32}$")
    # Host MCP resource generation; echoed by Provider ready and lifecycle frames.
    generation: int = Field(strict=True, ge=1, le=2**53 - 1)
    bind: BindSettings
    bearerToken: SecretStr = Field(repr=False)
    providerBuildId: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    configuration: ConfigurationV1 | None = None
    data: DataSettings | None = None
    software: SoftwareSettings | None = None
    nativeContext: NativeContext | None = Field(default=None, repr=False)
    _contract_digest: str = PrivateAttr(default="")

    @field_validator("schemaVersion", mode="before")
    @classmethod
    def integer_schema(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Invalid schema version.")
        return value

    @field_validator("bearerToken")
    @classmethod
    def valid_bearer(cls, value: SecretStr) -> SecretStr:
        if not _TOKEN_PATTERN.fullmatch(value.get_secret_value()):
            raise ValueError("Invalid bearer.")
        return value

    @model_validator(mode="after")
    def fixed_identity(self) -> StartupEnvelopeV2:
        if self.providerBuildId != PROVIDER_BUILD_ID:
            raise ValueError("Provider build identity mismatch.")
        engineering = (self.configuration, self.data, self.software, self.nativeContext)
        if self.mode == "metadata" and any(value is not None for value in engineering):
            raise ValueError("Metadata cannot carry engineering state.")
        if self.mode == "execution" and any(value is None for value in engineering):
            raise ValueError("Engineering context is required.")
        native = self.nativeContext
        if native is not None:
            if (
                native.providerBuildId != self.providerBuildId
                or native.port == self.bind.port
                or hmac.compare_digest(
                    native.token.get_secret_value(), self.bearerToken.get_secret_value()
                )
            ):
                raise ValueError("Native binding mismatch.")
        return self

    @property
    def contract_root(self) -> Path:
        return CONTRACTS_DIR

    @property
    def contract_digest(self) -> str:
        return self._contract_digest


StartupEnvelope = StartupEnvelopeV2


class ProviderReadyV2(ExactModel):
    schemaVersion: Literal[2] = 2
    kind: Literal["ready"] = "ready"
    mode: Literal["metadata", "execution"]
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: int = Field(strict=True, ge=1, le=2**53 - 1)
    providerBuildId: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    port: int = Field(strict=True, ge=1024, le=65535)


class ProviderLifecycleStatusV1(ExactModel):
    schemaVersion: Literal[1] = 1
    kind: Literal["status"] = "status"
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: int = Field(strict=True, ge=1, le=2**53 - 1)
    sequence: int = Field(strict=True, ge=1, le=2**53 - 1)
    state: Literal["idle", "running", "unknown", "draining", "unverifiable"]
    inFlight: int = Field(strict=True, ge=0)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, member in pairs:
        if key in value:
            raise ValueError("Duplicate JSON member.")
        value[key] = member
    return value


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


def parse_startup_envelope(payload: bytes) -> StartupEnvelope:
    """Parse and validate one strict UTF-8 JSON startup document."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_STARTUP_BYTES:
        raise StartupConfigurationError()
    try:
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(value, dict):
            raise ValueError("Startup input must be a JSON object.")
        _, digest = _validated_contract_root(str(CONTRACTS_DIR))
        envelope = StartupEnvelope.model_validate(value)
        if (
            envelope.nativeContext is not None
            and envelope.nativeContext.contractDigest != digest
        ):
            raise StartupConfigurationError()
        object.__setattr__(envelope, "_contract_digest", digest)
    except StartupConfigurationError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        raise StartupConfigurationError() from None
    return envelope


def read_startup_envelope(stream: BinaryIO) -> StartupEnvelope:
    """Read at most 64 KiB plus one sentinel byte from the closed stdin pipe."""
    try:
        payload = stream.read(MAX_STARTUP_BYTES + 1)
    except Exception:
        raise StartupConfigurationError() from None
    if not isinstance(payload, bytes) or len(payload) > MAX_STARTUP_BYTES:
        raise StartupConfigurationError()
    return parse_startup_envelope(payload)


def _ready_record(envelope: StartupEnvelope) -> dict[str, Any]:
    return ProviderReadyV2(
        mode=envelope.mode,
        connectorInstanceId=envelope.connectorInstanceId,
        generation=envelope.generation,
        providerBuildId=envelope.providerBuildId,
        port=envelope.bind.port,
    ).model_dump()


def create_managed_server(
    envelope: StartupEnvelope,
) -> tuple[RhinoProviderServer, BridgeClient | None]:
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
                provider_build_id=native.providerBuildId,
                contract_digest=envelope.contract_digest,
                contract_root=envelope.contract_root,
            )
        )
    return create_mcp_server(bridge), bridge


async def _serve(envelope: StartupEnvelope) -> None:
    mcp_server, bridge_client = create_managed_server(envelope)
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
    server_task = asyncio.create_task(server.serve())
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

        sys.stdout.write(
            json.dumps(_ready_record(envelope), separators=(",", ":")) + "\n"
        )
        sys.stdout.flush()
        sequence = 0
        while not server_task.done():
            sequence += 1
            state: Literal["idle", "running", "unknown"]
            if bridge_client is None:
                state, in_flight = "idle", 0
            else:
                state, in_flight = bridge_client.lifecycle_snapshot()
            status = ProviderLifecycleStatusV1(
                connectorInstanceId=envelope.connectorInstanceId,
                generation=envelope.generation,
                sequence=sequence,
                state=state,
                inFlight=in_flight,
            )
            sys.stdout.write(status.model_dump_json() + "\n")
            sys.stdout.flush()
            await asyncio.wait({server_task}, timeout=0.25)
        await server_task
    finally:
        if not server_task.done():
            server.should_exit = True
            try:
                await asyncio.wait_for(server_task, timeout=10)
            except Exception:
                pass
        token_verifier.clear()


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
        envelope = read_startup_envelope(sys.stdin.buffer)
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
