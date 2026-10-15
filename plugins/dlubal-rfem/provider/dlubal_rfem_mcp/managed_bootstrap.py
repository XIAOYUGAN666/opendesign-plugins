"""Managed stdin bootstrap. Stdout contains one bounded lifecycle message."""

import asyncio
import json
import socket
import sys
from importlib.metadata import version
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

from dlubal_rfem_mcp.core.config import ProviderReadyV2, StartupEnvelope
from dlubal_rfem_mcp.core.operation_control import OperationControl

MAX_ENVELOPE_BYTES = 64 * 1024


class BootstrapError(ValueError):
    pass


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicateKey")
        result[key] = value
    return result


def read_envelope(stream: BinaryIO) -> StartupEnvelope:
    try:
        raw = stream.read(MAX_ENVELOPE_BYTES + 1)
        if not raw or len(raw) > MAX_ENVELOPE_BYTES:
            raise ValueError("invalidSize")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        return StartupEnvelope.model_validate(value)
    except (ValueError, UnicodeError, ValidationError, OSError):
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


async def _serve_owned(envelope: StartupEnvelope, ready_stream) -> None:
    import uvicorn

    from dlubal_rfem_mcp.core.identity import verify_provider_build
    from dlubal_rfem_mcp.core.server import create_asgi_app

    verify_runtime()
    verify_provider_build(envelope.providerBuildId)
    control = OperationControl()
    app = create_asgi_app(envelope, control=control)
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
                    raise BootstrapError("providerStartFailed")
                await asyncio.sleep(0.01)
            ready = ProviderReadyV2(
                mode=envelope.mode,
                connectorInstanceId=envelope.connectorInstanceId,
                generation=envelope.generation,
                providerBuildId=envelope.providerBuildId,
                port=envelope.bind.port,
            )
            ready_stream.write(ready.model_dump_json() + "\n")
            ready_stream.flush()
            sequence = 0
            while not server.should_exit:
                sequence += 1
                frame = control.lifecycle(envelope, sequence).model_dump_json()
                if len(frame.encode("utf-8")) > 4096:
                    raise BootstrapError("invalidLifecycleFrame")
                ready_stream.write(frame + "\n")
                ready_stream.flush()
                await asyncio.sleep(0.25)

        async with asyncio.TaskGroup() as group:
            group.create_task(server.serve(sockets=[sock]))
            group.create_task(notify_ready())
    finally:
        sock.close()


async def serve(envelope: StartupEnvelope, ready_stream) -> None:
    from dlubal_rfem_mcp.rfem.execution import worker_lock

    if envelope.mode == "metadata":
        await _serve_owned(envelope, ready_stream)
        return
    assert envelope.data is not None
    database = Path(envelope.data.stateDirectory) / "executions.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    # A distinct lifetime lock protects startup ownership; operation workers
    # retain their original per-operation lock and journal semantics.
    with worker_lock(Path(str(database) + ".provider")):
        await _serve_owned(envelope, ready_stream)


def main() -> None:
    from dlubal_rfem_mcp.core.logging_config import setup_logging

    ready_stream = sys.stdout
    setup_logging()
    try:
        envelope = read_envelope(sys.stdin.buffer)
        sys.stdin.close()
        asyncio.run(serve(envelope, ready_stream))
    except (Exception, KeyboardInterrupt):
        import logging

        logging.getLogger(__name__).exception("providerStartFailed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
