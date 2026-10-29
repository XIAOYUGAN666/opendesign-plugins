"""Archive bytes are stable for identical package inputs and source epoch."""

from __future__ import annotations

import hashlib

from build_release import deterministic_zip_bytes, release_tree_bytes


def test_normalized_zip_is_byte_identical_for_identical_inputs() -> None:
    files = {
        "runtime/python/python.exe": b"python-runtime",
        "rhino_mcp/__init__.py": b"connector-source",
        "manifest.json": b"canonical-manifest",
    }

    first = deterministic_zip_bytes(files, source_date_epoch=1_750_000_000)
    second = deterministic_zip_bytes(
        dict(reversed(list(files.items()))), source_date_epoch=1_750_000_000
    )

    assert first == second
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()


def test_release_tree_omits_generated_python_bytecode(tmp_path) -> None:
    (tmp_path / "rhino_mcp" / "__pycache__").mkdir(parents=True)
    (tmp_path / "rhino_mcp" / "__pycache__" / "server.cpython-312.pyc").write_bytes(
        b"pyc"
    )
    (tmp_path / "rhino_mcp" / "server.py").write_bytes(b"source")

    assert release_tree_bytes(tmp_path) == {"rhino_mcp/server.py": b"source"}
