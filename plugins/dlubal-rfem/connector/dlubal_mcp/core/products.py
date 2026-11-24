"""Product registry: which Dlubal applications this server serves and their state.

`health_check` is built from here. Two facts it reports were wrong through 1.0.17
and are the reason this module exists:

- RSECTION 1 installs ``RSECTION.exe`` under ``Software\\Dlubal\\RSECTION``. The
  server looked for ``RSECTION1.exe`` / ``RSECTION1`` and reported RSECTION as not
  installed while ``run_rsection_script`` worked against it.
- ``running`` was a process-name match, which drifted from reality with that same
  naming. It is now whether the product's API port accepts a TCP connection.
"""

import importlib.metadata
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from dlubal_mcp import __version__
from dlubal_mcp.core.operation_control import OperationControl

PRODUCTS = ("rfem", "rstab", "rsection")

# health_check can be polled; a closed port costs up to PORT_PROBE_TIMEOUT, so
# the answer is reused briefly instead of being re-probed on every call.
RUNNING_CACHE_SECONDS = 2.0
PORT_PROBE_TIMEOUT = 0.5


def api_port_open(host: str, port: int, timeout: float = PORT_PROBE_TIMEOUT) -> bool:
    """True when something accepts a TCP connection on the product's API port.

    Deliberately NOT a gRPC session: opening one takes the product session lock,
    which a running script holds, so a busy product would read as stopped.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def client_version() -> str | None:
    """Installed Dlubal API client version, or None when it is not installed."""
    try:
        return importlib.metadata.version("dlubal.api")
    except importlib.metadata.PackageNotFoundError:
        return None


@dataclass
class ProductState:
    name: str
    installed: bool
    disabled: bool = False
    api_port: int | None = None
    _is_running_fn: Callable[[], bool] | None = field(default=None, repr=False)
    _cached: tuple[float, bool] | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        if self.disabled or self._is_running_fn is None:
            return False
        now = time.monotonic()
        if self._cached and now - self._cached[0] < RUNNING_CACHE_SECONDS:
            return self._cached[1]
        value = bool(self._is_running_fn())
        self._cached = (now, value)
        return value


class ProductRegistry:
    def __init__(self, control: OperationControl | None = None):
        self._products: dict[str, ProductState] = {}
        self._tools: list[str] = []
        # Injected by the app builders in server.py, which is where operation
        # control is wired (and replaced in tests).
        self.control = control if control is not None else OperationControl()

    def register(
        self,
        name: str,
        exe_path: str,
        is_running_fn: Callable[[], bool] | None = None,
        api_port: int | None = None,
    ) -> ProductState:
        state = ProductState(
            name=name,
            installed=bool(exe_path) and os.path.exists(exe_path),
            api_port=api_port,
            _is_running_fn=is_running_fn,
        )
        self._products[name] = state
        return state

    def register_disabled(self, name: str) -> ProductState:
        """A product excluded by DLUBAL_MCP_PRODUCTS: no tools, no probing."""
        state = ProductState(name=name, installed=False, disabled=True)
        self._products[name] = state
        return state

    def add_tools(self, names: list[str]) -> None:
        self._tools.extend(names)

    def health_dict(self, require_auth: bool) -> dict:
        products = {}
        for name, state in self._products.items():
            if state.disabled:
                products[name] = {"disabled": True}
                continue
            entry = {"installed": state.installed, "running": state.running}
            if state.api_port is not None:
                entry["api_port"] = state.api_port
            products[name] = entry
        return {
            "version": __version__,
            "client_version": client_version(),
            "products": products,
            "tools": list(self._tools),
            "auth_required": require_auth,
        }
