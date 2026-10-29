"""Attest Connector source and frozen inputs without a release hash cycle.

The verified distribution retains project/lock inputs beside the module. Host
authenticates the artifact, including runtime/wheels, before passing its expected
build ID. No identity is accepted from a file inside an untrusted archive alone.
"""

import hashlib
import hmac
from pathlib import Path

from dlubal_rfem_mcp.core.catalog import canonical_json


def connector_build_manifest(source: Path | None = None) -> dict[str, str]:
    package = source or Path(__file__).resolve().parents[1]
    root = package.parent
    files = list(package.rglob("*.py")) + [root / "pyproject.toml", root / "uv.lock"]
    # Runtime archive and build inputs are immutable, non-secret JSON inputs.
    # release.json/signatures/timestamps are deliberately excluded.
    for name in ("runtime-lock.json", "release-input.json"):
        candidate = root / "packaging" / name
        if candidate.exists():
            files.append(candidate)
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }


def connector_build_id(source: Path | None = None) -> str:
    return (
        "sha256:"
        + hashlib.sha256(canonical_json(connector_build_manifest(source))).hexdigest()
    )


def verify_connector_build(expected: str) -> None:
    if not hmac.compare_digest(connector_build_id(), expected):
        raise ValueError("connectorBuildMismatch")
