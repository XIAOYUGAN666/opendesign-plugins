"""Prebuild a deterministic native candidate and its generated root binding."""

from __future__ import annotations

import argparse
import tempfile
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import build_release as build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build.configure_plugin_root(args.plugin_root)
    build.validate_root_identity()
    if args.output.exists():
        raise build.ReleaseBuildError("The native candidate output must be fresh.")
    record, build_id = build._build_identity(build._runtime_lock())
    dotnet = build._dotnet_executable()
    with tempfile.TemporaryDirectory(
        prefix="rhino-native-candidate-", dir=build._safe_ascii_temp_root()
    ) as folder:
        workspace = Path(folder)
        outputs = build._build_native_outputs(workspace, dotnet, build_id)
        _, entries = build._native_output_records(outputs)
        probe = build._build_native_capability_probe(workspace, dotnet, build_id)
        capability = build._native_capability_digest(outputs, dotnet, probe)
        yak = build.build_yak_archive_bytes(
            entries, build._runtime_lock()["sourceDateEpoch"]
        )
        identity = build.validate_root_identity()
        binding = {
            "packageId": "rhino-mcp-bridge",
            "version": identity["version"],
            "relativePath": "native/" + build.YAK_NAME,
            "sha256": build.sha256_digest(yak),
            "contractVersion": 1,
            "contractDigest": build.sha256_digest(
                (build.PLUGIN_ROOT / "contracts/protocol.json").read_bytes()
            ),
            "capabilityDigest": capability,
            "pluginId": build.PLUGIN_GUID,
        }
        args.output.mkdir(parents=True)
        (args.output / build.YAK_NAME).write_bytes(yak)
        (args.output / "native-binding.json").write_bytes(
            build.canonical_json_bytes(binding)
        )
        (args.output / "connector-build.json").write_bytes(
            build.canonical_json_bytes(record)
        )
        (args.output / "connector-build-id.txt").write_text(
            build_id + "\n", encoding="ascii"
        )
    print("Native candidate and generated binding prepared.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("nativeCandidateBuildFailed") from None
