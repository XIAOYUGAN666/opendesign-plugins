"""Verify an unsigned review artifact. Signature verification remains Host-owned."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dlubal_rfem_mcp.core.catalog import canonical_json
from scripts.build_plugin import extract_verified_zip

EXPECTED_TOOLS = {
    "health_check",
    "get_rfem_application_info",
    "inspect_rfem_api",
    "write_rfem_model",
    "calculate_rfem_model",
    "run_rfem_script",
    "get_rfem_execution",
    "read_rfem_results",
}


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def verify(archive: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(root.parents[2] / "scripts/verify_artifact.py"),
            str(archive),
            str(archive.parent / "unsigned-receipt.json"),
            str(root.parents[2] / "schemas"),
            "--unsigned",
        ],
        check=True,
        capture_output=True,
    )
    if not archive.is_file() or archive.stat().st_size > 1024 * 1024 * 1024:
        raise ValueError("invalidArtifact")
    with tempfile.TemporaryDirectory(prefix="rfem-plugin-verify-") as temporary:
        root = Path(temporary)
        extract_verified_zip(archive, root)
        release = json.loads((root / "release.json").read_text("utf-8"))
        files_bytes = (root / "files.json").read_bytes()
        files = json.loads(files_bytes)
        if release["filesDigest"] != digest(files_bytes) or files["schemaVersion"] != 1:
            raise ValueError("filesDigestMismatch")
        expected = {"files.json", "release.json"}
        for item in files["files"]:
            path = root.joinpath(*item["path"].split("/"))
            if item["path"] in expected or not path.is_file():
                raise ValueError("invalidFileInventory")
            content = path.read_bytes()
            if len(content) != item["byteLength"] or digest(content) != item["sha256"]:
                raise ValueError("fileDigestMismatch")
            expected.add(item["path"])
        actual = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        }
        if actual != expected:
            raise ValueError("undeclaredFile")
        preview = json.loads((root / "tool-preview.json").read_text("utf-8"))
        if release["toolCatalogDigest"] != digest(canonical_json(preview)):
            raise ValueError("toolCatalogDigestMismatch")
        names = {tool["name"] for tool in preview["tools"]}
        if (
            preview["normalizationVersion"] != 1
            or names != EXPECTED_TOOLS
            or len(preview["tools"]) != 8
        ):
            raise ValueError("incompleteToolCatalog")
        if release["sbomDigest"] != digest((root / "sbom.spdx.json").read_bytes()):
            raise ValueError("sbomDigestMismatch")
        python = root / "runtime/python/python.exe"
        environment = {
            name: os.environ[name]
            for name in ("SystemRoot", "WINDIR", "TEMP", "TMP")
            if name in os.environ
        }
        probe = subprocess.run(
            [
                python,
                "-I",
                "-s",
                "-c",
                "import importlib.metadata as m,json,platform,sys; print(json.dumps({'executable':sys.executable,'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,'architecture':platform.machine(),'fastmcp':m.version('fastmcp'),'mcp':m.version('mcp'),'dlubal.api':m.version('dlubal.api')}))",
            ],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        runtime = json.loads(probe.stdout)
        if (
            runtime["isolated"] != 1
            or runtime["architecture"].upper() != "AMD64"
            or runtime["fastmcp"] != "4.0.3"
            or runtime["mcp"] != "2.2.0"
            or runtime["dlubal.api"] != "2.14.13"
            or not Path(runtime["executable"]).is_relative_to(root)
        ):
            raise ValueError("runtimeIsolationMismatch")
        return {
            "schemaVersion": 1,
            "archiveDigest": digest(archive.read_bytes()),
            "release": release,
            "runtime": runtime,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.artifact.resolve()), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - CLI errors cannot expose artifact paths or raw commands
        raise SystemExit("artifactVerificationFailed") from None
