"""RFEM software connection checks use only the application Service API."""

import asyncio
from collections.abc import Callable

from dlubal_rfem_mcp._connector_control import AdapterConnectionResult
from dlubal_rfem_mcp._connector_process import running_instance
from dlubal_rfem_mcp.rfem.session import RFEMBusy, RFEMSession


class RFEMSoftwareConnection:
    def __init__(self, session: RFEMSession | None, *, software_running: Callable[[], str | None] | None = None):
        self._session = session
        self._software_running = software_running or (lambda: running_instance("RFEM6.exe"))
        self._pending: asyncio.Task[AdapterConnectionResult] | None = None

    def _check_service(self) -> AdapterConnectionResult:
        assert self._session is not None
        try:
            # RFEMSession validates the exact supported build with this Service
            # call while opening the session; a second call here would duplicate
            # the same quota-free probe without adding evidence.
            with self._session.connection(timeout=0):
                pass
            return AdapterConnectionResult("confirmed", "none")
        except RFEMBusy:
            return AdapterConnectionResult("deferred", "softwareBusy")
        except Exception:  # noqa: BLE001 - SDK errors are classified without exposing details.
            reason = self._session.restriction
            if reason == "authenticationFailed":
                return AdapterConnectionResult("blocked", "credentialInvalid")
            if reason == "unsupportedSoftwareVersion":
                return AdapterConnectionResult("blocked", "unsupportedVersion")
            return AdapterConnectionResult("transient", "transportUnavailable")

    async def ensure_connection(self, deadline_seconds: float) -> AdapterConnectionResult:
        if self._session is None:
            return AdapterConnectionResult("blocked", "credentialMissing")
        instance = self._software_running()
        if instance is None:
            return AdapterConnectionResult("blocked", "softwareNotRunning")
        if self._pending is not None and not self._pending.done():
            return AdapterConnectionResult("deferred", "softwareBusy")
        self._pending = asyncio.create_task(asyncio.to_thread(self._check_service))
        try:
            result = await asyncio.wait_for(asyncio.shield(self._pending), timeout=deadline_seconds)
            if result.outcome == "confirmed":
                if self._software_running() != instance:
                    return AdapterConnectionResult("unconfirmed", "unknown")
                return AdapterConnectionResult("confirmed", "none", instance)
            return result
        except TimeoutError:
            return AdapterConnectionResult("unconfirmed", "timeout")
