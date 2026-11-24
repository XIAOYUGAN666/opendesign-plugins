"""Pydantic settings for the Dlubal MCP server."""

import configparser
import glob
import ipaddress
import logging
import os
import sys
from pathlib import Path

PROGRAM_ROOTS = (r"C:\Program Files\Dlubal", r"C:\Program Files (x86)\Dlubal")


def _registry_application_path(registry_key: str) -> str | None:
    """HKCU\\Software\\Dlubal\\<registry_key> → ApplicationPath, when it exists."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Dlubal\{registry_key}"
        ) as key:
            app_path, _ = winreg.QueryValueEx(key, "ApplicationPath")
    except (ImportError, OSError):
        logging.getLogger("dlubal_mcp").debug(
            "Dlubal registry path unavailable", exc_info=True
        )
        return None
    return app_path if app_path and os.path.exists(app_path) else None


def find_dlubal_exe(
    dir_prefix: str,
    exe_names: str | tuple[str, ...],
    registry_keys: str | tuple[str, ...],
    roots: tuple[str, ...] = PROGRAM_ROOTS,
) -> str | None:
    """Discover a Dlubal product executable via Windows registry then filesystem scan.

    Mirrors the Go findRFEMBinDir() logic so manual (non-DCC) usage resolves
    versioned install paths (e.g. 'RFEM 6.14') without requiring RFEM_EXE_PATH.
    Several executable names and registry keys may be given, tried in order: a
    product's spelling can differ from what an older release assumed (RSECTION 1
    installs RSECTION.exe under key RSECTION, not RSECTION1.exe under RSECTION1).
    Returns None on non-Windows or when the product is not found.
    """
    if sys.platform != "win32":
        return None
    exe_names = (exe_names,) if isinstance(exe_names, str) else exe_names
    registry_keys = (
        (registry_keys,) if isinstance(registry_keys, str) else registry_keys
    )

    # 1. Windows registry.
    for registry_key in registry_keys:
        if found := _registry_application_path(registry_key):
            return found

    # 2. Filesystem scan.
    return scan_program_roots(dir_prefix, exe_names, roots)


def scan_program_roots(
    dir_prefix: str, exe_names: tuple[str, ...], roots: tuple[str, ...]
) -> str | None:
    """Newest `<root>/<dir_prefix>*/bin/<exe>` for the first name found.

    Reverse alphabetical order of the zero-padded version directories is newest
    first ("RSECTION 1.15" before "RSECTION 1.14").
    """
    for exe_name in exe_names:
        for base in roots:
            pattern = os.path.join(base, f"{dir_prefix}*", "bin", exe_name)
            matches = sorted(glob.glob(pattern), reverse=True)
            if matches:
                return matches[0]
    return None


from pydantic_settings import BaseSettings, SettingsConfigDict


def load_api_key_from_ini(key_name: str = "default") -> str | None:
    """Load a Dlubal gRPC API key from config.ini. Returns None if not found."""
    config_ini_path = os.environ.get("DLUBAL_CONFIG_INI_PATH", "dlubal/api")

    candidates: list[Path] = [Path(config_ini_path) / "config.ini"]
    for parent in Path(__file__).parents:
        candidates.append(parent / config_ini_path / "config.ini")
    candidates.append(Path(sys.executable).parent / config_ini_path / "config.ini")

    for candidate in candidates:
        if candidate.exists():
            cfg = configparser.ConfigParser()
            cfg.read(candidate)
            if "api_keys" in cfg and key_name in cfg["api_keys"]:
                return cfg["api_keys"][key_name] or None

    return None


class DlubalMCPSettings(BaseSettings):
    """Unified server settings. All fields are configurable via DLUBAL_MCP_* env vars."""

    model_config = SettingsConfigDict(
        env_prefix="DLUBAL_MCP_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "127.0.0.1"
    port: int = 7130
    transport: str = "streamable-http"
    cors_origins: str = "http://localhost:3001,http://localhost:3100"

    # Inbound auth (clients authenticating TO this server)
    require_auth: bool = True
    allowed_api_keys: str = ""
    read_api_keys: str = ""
    write_api_keys: str = ""
    allow_scripts: bool = False
    rate_limit_rpm: int = 30

    # Products this process serves, comma-separated subset of rfem,rstab,rsection.
    # Empty = all. Lets a launcher run one process per Dlubal API build, each
    # serving only the products of its own build.
    products: str = ""

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    def enabled_products(self) -> set[str]:
        """Parse `products`. An unknown name fails loudly rather than silently
        serving nothing: a launcher that asked for a product must learn at
        startup that it will not get it."""
        known = {"rfem", "rstab", "rsection"}
        requested = {
            name.strip().lower() for name in self.products.split(",") if name.strip()
        }
        if not requested:
            return set(known)
        unknown = requested - known
        if unknown:
            raise ValueError(
                f"DLUBAL_MCP_PRODUCTS contains unknown product(s) {sorted(unknown)}; "
                f"accepted values: {', '.join(sorted(known))}"
            )
        return requested

    def get_allowed_keys(self) -> set[str]:
        """Parse allowed_api_keys into a set."""
        keys: set[str] = set()
        env_keys = self.allowed_api_keys or os.environ.get("ALLOWED_API_KEYS", "")
        if env_keys:
            keys.update(k.strip() for k in env_keys.split(",") if k.strip())
        keys.update(
            k.strip()
            for k in f"{self.read_api_keys},{self.write_api_keys}".split(",")
            if k.strip()
        )
        return keys

    def token_permissions(self) -> dict[str, str]:
        permissions = {key: "admin" for key in self.get_allowed_keys()}
        for value, permission in (
            (self.write_api_keys, "write"),
            (self.read_api_keys, "read"),
        ):
            for key in value.split(","):
                if key.strip():
                    permissions[key.strip()] = permission
        return permissions

    def validate_http_security(self) -> set[str]:
        """Validate before loading product tools; never include secrets in errors."""
        try:
            loopback = ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError(
                "MCP must bind to a numeric loopback address (127.0.0.1 or ::1)"
            )
        if not self.require_auth:
            raise ValueError("MCP authentication cannot be disabled")
        keys = self.get_allowed_keys()
        if not keys or any(
            len(key) < 32
            or len(key) > 512
            or not key.isascii()
            or any(c.isspace() for c in key)
            for key in keys
        ):
            raise ValueError(
                "Set DLUBAL_MCP_ALLOWED_API_KEYS to separate random access tokens of 32 to 512 ASCII characters"
            )
        rfem_keys = {
            os.environ.get(name)
            for name in (
                "DLUBAL_API_KEY",
                "RFEM_API_KEY",
                "RSTAB_API_KEY",
                "RSECTION_API_KEY",
            )
        }
        if keys.intersection(rfem_keys):
            raise ValueError(
                "MCP access tokens must differ from Dlubal application API keys"
            )
        if self.rate_limit_rpm < 1:
            raise ValueError("MCP rate limit must be positive")
        return keys
