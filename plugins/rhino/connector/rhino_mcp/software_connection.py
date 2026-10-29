"""Rhino connection checks use only the authenticated bridge probe."""

import asyncio
import hashlib
import json
from collections.abc import Callable

from rhino_mcp._connector_control import AdapterConnectionResult
from rhino_mcp._connector_process import running_instance
from rhino_mcp.bridge import BridgeBusy, BridgeClient, BridgeRejected, BridgeUnavailable


class RhinoSoftwareConnection:
    def __init__(self, bridge: BridgeClient | None, *, software_running: Callable[[], str | None] | None = None, peer_factory: Callable[[dict], BridgeClient] | None = None, peer_instance: str | None = None, peer_changed: Callable[[BridgeClient | None], None] | None = None, initial_peer: dict | None = None):
        self._bridge = bridge
        self._software_running = software_running or (lambda: running_instance("Rhino.exe"))
        self._pending: asyncio.Task[AdapterConnectionResult] | None = None
        self._peer_revision = 0
        self._peer_factory = peer_factory
        self._peer_instance = peer_instance
        self._peer_changed = peer_changed
        self._peer_fingerprint = self._fingerprint(initial_peer) if initial_peer is not None else None

    @staticmethod
    def _fingerprint(peer: dict) -> bytes:
        return hashlib.sha256(json.dumps(peer, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).digest()

    def set_bridge(self, bridge: BridgeClient | None) -> None:
        if self._bridge is not bridge:
            self._bridge = bridge
            self._peer_instance = None
            self._peer_revision += 1

    def update_native_peer(self, peer: dict | None) -> str | None:
        previous_instance = self._peer_instance
        if peer is None:
            self._peer_instance = None
            self._peer_fingerprint = None
            self.set_bridge(None)
            if self._peer_changed is not None:
                self._peer_changed(None)
            return previous_instance
        if self._peer_factory is None:
            raise ValueError("nativePeerUnsupported")
        fingerprint = self._fingerprint(peer)
        if fingerprint == self._peer_fingerprint:
            return None
        bridge = self._peer_factory(peer)
        self.set_bridge(bridge)
        self._peer_instance = peer["softwareInstanceId"]
        self._peer_fingerprint = fingerprint
        if self._peer_changed is not None:
            self._peer_changed(bridge)
        return previous_instance

    def _check_bridge(self, bridge: BridgeClient, timeout_seconds: float) -> AdapterConnectionResult:
        try:
            bridge.confirm_connection(timeout_seconds)
            return AdapterConnectionResult("confirmed", "none")
        except BridgeBusy:
            return AdapterConnectionResult("deferred", "softwareBusy")
        except BridgeRejected:
            return AdapterConnectionResult("blocked", "trustFailure")
        except BridgeUnavailable:
            return AdapterConnectionResult("transient", "transportUnavailable")

    async def ensure_connection(self, deadline_seconds: float) -> AdapterConnectionResult:
        instance = self._software_running()
        if instance is None:
            return AdapterConnectionResult("blocked", "softwareNotRunning")
        if self._bridge is None:
            return AdapterConnectionResult("blocked", "nativeComponentUnavailable")
        if self._peer_instance is not None and self._peer_instance != instance:
            return AdapterConnectionResult("unconfirmed", "unknown")
        if self._pending is not None and not self._pending.done():
            return AdapterConnectionResult("deferred", "softwareBusy")
        revision = self._peer_revision
        self._pending = asyncio.create_task(asyncio.to_thread(self._check_bridge, self._bridge, deadline_seconds))
        try:
            result = await asyncio.wait_for(asyncio.shield(self._pending), timeout=deadline_seconds)
            if revision != self._peer_revision or self._software_running() != instance:
                return AdapterConnectionResult("unconfirmed", "unknown")
            if result.outcome == "confirmed":
                return AdapterConnectionResult("confirmed", "none", instance)
            return result
        except TimeoutError:
            return AdapterConnectionResult("unconfirmed", "timeout")
