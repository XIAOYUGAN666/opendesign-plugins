"""Persistent RFEM gRPC session with auto-reconnect."""

import logging
import threading
import time
from contextlib import contextmanager

from dlubal_mcp.core import rpc_deadline
from dlubal_mcp.core.client_application import client_application_kwargs

logger = logging.getLogger("dlubal_mcp.rfem")


class RFEMSession:
    """
    Per-call RFEM connection with a serialising lock.
    Each rfem_run_script() call opens a fresh connection and closes it on exit.
    """

    def __init__(
        self, api_key_name: str, api_key_value: str | None, url: str, port: int
    ):
        self._lock = threading.Lock()
        self._api_key_name = api_key_name
        self._api_key_value = api_key_value or ""
        self._url = url
        self._port = port

    @contextmanager
    def connection(self, timeout: float = 0.0):
        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            raise RuntimeError(
                "RFEM is busy executing another request. Try again when it finishes."
            )
        app = None
        try:
            for attempt in range(3):
                try:
                    from dlubal.api.rfem.application import Application

                    rpc_deadline.install(Application)
                    app = Application(
                        api_key_name=self._api_key_name,
                        api_key_value=self._api_key_value,
                        url=self._url,
                        port=self._port,
                        **client_application_kwargs(Application),
                    )
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(0.5)
            yield app
        finally:
            try:
                if app is not None:
                    app.close_connection()
            except Exception as exc:
                from dlubal_mcp.rfem.execution import ExecutionUncertain

                raise ExecutionUncertain(
                    "RFEM connection cleanup failed; execution outcome requires inspection."
                ) from exc
            finally:
                self._lock.release()
