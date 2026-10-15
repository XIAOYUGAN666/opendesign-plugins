"""Authoritative, read-only fingerprint of the active RFEM model input state.

RFEM's ``ModelId.guid`` identifies a logical model, not one revision of that
model.  The public API does not expose a monotone revision counter.  Its
Python-gRPC exporter is the only bounded API surface whose purpose is to
recreate the complete model input.  We canonicalise the exported Python AST,
hash it locally and immediately delete the temporary export.

No exported model text, path, object name or exception message leaves this
module.  Callers receive only the algorithm id, RFEM exporter version and
SHA-256 digest.
"""

from __future__ import annotations

import ast
import hashlib
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

STATE_FINGERPRINT_ALGORITHM: Literal["rfem-python-grpc-ast-sha256-v1"] = (
    "rfem-python-grpc-ast-sha256-v1"
)
_MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


class RFEMStateFingerprint(BaseModel):
    """Opaque proof of one complete, versioned RFEM model-input snapshot."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["rfem-python-grpc-ast-sha256-v1"] = STATE_FINGERPRINT_ALGORITHM
    exporter_version: str = Field(min_length=1, max_length=40)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class StateFingerprintError(RuntimeError):
    """Safe boundary error; its message is a fixed code, never model data."""


def _canonical_ast(source: bytes) -> str:
    if not source or len(source) > _MAX_SNAPSHOT_BYTES:
        raise StateFingerprintError("snapshot_size_invalid")
    try:
        text = source.decode("utf-8-sig")
        tree = ast.parse(text, mode="exec")
    except (UnicodeDecodeError, SyntaxError, ValueError) as exc:
        raise StateFingerprintError("snapshot_parse_failed") from exc
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def capture_model_state(
    app, *, model_id, exporter_version: str
) -> RFEMStateFingerprint:
    """Export, canonicalise and hash one model without saving or changing it."""

    try:
        from dlubal.api import common

        with tempfile.TemporaryDirectory(
            prefix="dlubal-rfem-mcp-rfem-state-"
        ) as directory:
            snapshot_path = Path(directory) / "model_snapshot.py"
            app.export_to(
                filepath=str(snapshot_path),
                export_attributes=common.import_export.PythonGrpcExportAttributes(),
                model_id=model_id,
            )
            size = snapshot_path.stat().st_size
            if size <= 0 or size > _MAX_SNAPSHOT_BYTES:
                raise StateFingerprintError("snapshot_size_invalid")
            canonical = _canonical_ast(snapshot_path.read_bytes())
    except StateFingerprintError:
        raise
    except Exception as exc:
        raise StateFingerprintError("snapshot_export_failed") from exc

    digest = hashlib.sha256(
        f"{STATE_FINGERPRINT_ALGORITHM}\0{exporter_version}\0{canonical}".encode(),
    ).hexdigest()
    return RFEMStateFingerprint(
        exporter_version=exporter_version,
        sha256=digest,
    )
