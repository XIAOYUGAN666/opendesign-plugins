"""Managed stdin bootstrap. Stdout contains one bounded lifecycle message."""

import asyncio
import json
import socket
import sys
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import BinaryIO

from pydantic import SecretStr, ValidationError

from dlubal_rfem_mcp.core.config import (
    BindSettings,
    ConfigurationV1,
    DataSettings,
)
from dlubal_rfem_mcp.core.operation_control import OperationControl

MAX_ENVELOPE_BYTES = 64 * 1024


class BootstrapError(ValueError):
    pass


@dataclass(repr=False)
class ConnectorRuntimeContext:
    binding: dict
    mode: str
    connectorInstanceId: str
    generation: int
    bind: BindSettings
    bearerToken: SecretStr = field(repr=False)
    connectorBuildId: str
    configuration: ConfigurationV1 | None = field(repr=False)
    data: DataSettings | None = field(repr=False)
    software: None = None


def read_connector_envelope(stream: BinaryIO) -> ConnectorRuntimeContext:
    from dlubal_rfem_mcp._connector_control import decode_bootstrap

    try:
        raw = stream.readline(MAX_ENVELOPE_BYTES + 2).removesuffix(b"\n")
        value = decode_bootstrap(raw)
        binding = value["binding"]
        bearer = value["bearerToken"]
        if not bearer.isascii() or not all(c.isalnum() or c in "_-" for c in bearer):
            raise ValueError("invalidBearer")
        if value["nativePeer"] is not None:
            raise ValueError("invalidNativePeer")
        if value["configuration"] != {}:
            raise ValueError("invalidConfiguration")
        slots = value["secrets"]
        if len({slot["fieldId"] for slot in slots}) != len(slots) or any(slot["fieldId"] != "dlubal-api-key" for slot in slots):
            raise ValueError("invalidSecrets")
        if slots and slots[0]["value"] == bearer:
            raise ValueError("credentialsMustDiffer")
        configuration = ConfigurationV1.model_validate({"dlubal-api-key": slots[0]["value"]}) if slots else None
        data_directory = value.get("dataDirectory")
        mode = "execution" if value["mode"] == "runtime" else value["mode"]
        if mode in {"execution", "observation"} and not data_directory:
            raise ValueError("missingExecutionStateDirectory")
        data = DataSettings.model_validate({"stateDirectory": data_directory}) if data_directory else None
        return ConnectorRuntimeContext(binding=binding, mode=mode, connectorInstanceId=binding["connectorInstanceId"], generation=binding["generation"], bind=BindSettings.model_validate(value["bind"]), bearerToken=SecretStr(bearer), connectorBuildId=value["connectorBuildId"], configuration=configuration, data=data)
    except (KeyError, TypeError, ValueError, ValidationError, OSError):
        raise BootstrapError("invalidStartupEnvelope") from None


def verify_runtime() -> None:
    for package, required in (
        ("fastmcp", "4.0.3"),
        ("mcp", "2.2.0"),
        ("dlubal.api", "2.14.13"),
    ):
        if version(package) != required:
            raise BootstrapError("runtimeVersionMismatch")
    if sys.version_info[:3] != (3, 12, 10):
        raise BootstrapError("runtimeVersionMismatch")


async def _serve_owned(envelope: ConnectorRuntimeContext, ready_stream) -> None:
    import uvicorn

    from dlubal_rfem_mcp.core.identity import verify_connector_build
    from dlubal_rfem_mcp.core.server import create_asgi_app

    verify_runtime()
    verify_connector_build(envelope.connectorBuildId)
    control = OperationControl()
    from dlubal_rfem_mcp._connector_channel import ConnectorChannel
    from dlubal_rfem_mcp.rfem.config import RFEMConfig
    from dlubal_rfem_mcp.rfem.session import RFEMSession
    from dlubal_rfem_mcp.rfem.software_connection import RFEMSoftwareConnection

    rfem_config = RFEMConfig.from_envelope(envelope)
    session = RFEMSession(rfem_config.api_key.get_secret_value(), rfem_config.url, rfem_config.api_port) if envelope.configuration is not None else None
    app = create_asgi_app(envelope, control=control, session=session)

    def emit(event):
        ready_stream.write(json.dumps(event, separators=(",", ":")) + "\n")
        ready_stream.flush()

    channel = ConnectorChannel(envelope.binding, RFEMSoftwareConnection(session), emit)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((envelope.bind.host, envelope.bind.port))
        config = uvicorn.Config(
            app,
            host=envelope.bind.host,
            port=envelope.bind.port,
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)

        async def notify_ready():
            while not server.started:
                if server.should_exit:
                    raise BootstrapError("connectorStartFailed")
                await asyncio.sleep(0.01)
            ready = json.dumps({"schemaVersion":3,"kind":"ready","binding":envelope.binding,"connectorBuildId":envelope.connectorBuildId,"port":envelope.bind.port}, separators=(",", ":"))
            ready_stream.write(ready + "\n")
            ready_stream.flush()
            sequence = 0
            while not server.should_exit:
                sequence += 1
                frame = control.execution_status(envelope.binding, sequence).model_dump_json()
                if len(frame.encode("utf-8")) > 4096:
                    raise BootstrapError("invalidLifecycleFrame")
                ready_stream.write(frame + "\n")
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


async def serve(envelope: ConnectorRuntimeContext, ready_stream) -> None:
    from dlubal_rfem_mcp.rfem.execution import worker_lock

    if envelope.mode == "metadata":
        await _serve_owned(envelope, ready_stream)
        return
    assert envelope.data is not None
    database = Path(envelope.data.stateDirectory) / "executions.sqlite3"
    if envelope.mode == "execution":
        database.parent.mkdir(parents=True, exist_ok=True)
    # A distinct lifetime lock protects startup ownership; operation workers
    # retain their original per-operation lock and journal semantics.
    with worker_lock(Path(str(database) + ".connector")):
        await _serve_owned(envelope, ready_stream)


def main() -> None:
    from dlubal_rfem_mcp.core.logging_config import setup_logging

    ready_stream = sys.stdout
    setup_logging()
    try:
        envelope = read_connector_envelope(sys.stdin.buffer)
        asyncio.run(serve(envelope, ready_stream))
    except (Exception, KeyboardInterrupt):
        import logging

        logging.getLogger(__name__).exception("connectorStartFailed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
