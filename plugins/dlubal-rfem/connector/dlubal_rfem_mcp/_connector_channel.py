"""Generated control-channel correlation; software policy lives in the adapter."""
import asyncio
import threading
from contextlib import suppress

from ._connector_control import AdapterConnectionResult, decode_control, validate_event


class ConnectorChannel:
    def __init__(self, binding, adapter, emit):
        self._binding = binding
        self._adapter = adapter
        self._emit = emit
        self._sequence = 0
        self._active = None
        self._task = None
        self._peer_revision = 0

    async def accept(self, frame):
        command = decode_control(frame)
        if command["binding"] != self._binding:
            raise ValueError("staleBinding")
        if command["kind"] == "nativePeerChanged":
            if command["peerRevision"] <= self._peer_revision:
                raise ValueError("staleNativePeer")
            self._adapter.update_native_peer(command["peer"])
            self._peer_revision = command["peerRevision"]
            return
        operation = command["operationId"]
        if command["kind"] == "cancelConnectionCheck":
            if self._active == operation and self._task is not None:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
                self._active = None
                self._task = None
            return
        if self._task is not None and not self._task.done():
            raise ValueError("connectionCheckInFlight")
        self._active = operation
        self._task = asyncio.create_task(self._check(operation, command["deadlineMs"] / 1000))

    async def _check(self, operation, deadline):
        try:
            result = await self._adapter.ensure_connection(deadline)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - emit a fixed error category without exception details.
            result = AdapterConnectionResult("unconfirmed", "unknown")
        if self._active != operation:
            return
        event = validate_event({"schemaVersion": 1, "kind": "connectionCheckCompleted", "operationId": operation, "binding": self._binding, "sequence": self._sequence + 1, "outcome": result.outcome, "reason": result.reason, "softwareInstanceId": result.software_instance_id})
        self._emit(event)
        self._sequence += 1
        self._active = None

    def publish_change(self, outcome, reason, software_instance_id=None):
        event = validate_event({"schemaVersion": 1, "kind": "softwareConnectionChanged", "binding": self._binding, "sequence": self._sequence + 1, "outcome": outcome, "reason": reason, "softwareInstanceId": software_instance_id})
        self._emit(event)
        self._sequence += 1

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            self._active = None

    async def run_reader(self, stream):
        """Read bounded lines without blocking the event loop or executor shutdown."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=16)
        stopped = threading.Event()

        def read_frames():
            try:
                while not stopped.is_set():
                    frame = stream.readline(16 * 1024 + 1)
                    if stopped.is_set() or loop.is_closed():
                        return
                    future = asyncio.run_coroutine_threadsafe(queue.put(frame), loop)
                    future.result()
                    if not frame:
                        return
            except (OSError, RuntimeError):
                return

        threading.Thread(target=read_frames, name="connector-control-reader", daemon=True).start()
        try:
            while True:
                frame = await queue.get()
                if not frame:
                    return
                if len(frame) > 16 * 1024 or not frame.endswith(b"\n"):
                    raise ValueError("invalidFrame")
                await self.accept(frame[:-1])
        finally:
            stopped.set()
            await self.close()
