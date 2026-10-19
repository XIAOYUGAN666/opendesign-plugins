"""Opt-in actual SDK checks of component ports, without a GH canvas or solution."""

import json
import os
from pathlib import Path
import subprocess
import pytest


@pytest.fixture(scope="module")
def native_gh_metadata(tmp_path_factory):
    sdk = os.environ.get("RHINO_TEST_SDK_SYSTEM_DIR")
    native = os.environ.get("RHINO_TEST_NATIVE_NET48_DIR")
    if not sdk or not native:
        pytest.skip("requires explicit exact Rhino SDK and compiled net48 Bridge paths")
    sdk_root, native_root = Path(sdk), Path(native)
    assert (sdk_root / "RhinoCommon.dll").is_file()
    assert (native_root / "RhinoMcpBridge.rhp").is_file()
    compiler = (
        Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    assert compiler.is_file()
    source = Path(__file__).parents[1] / "fixtures/native_gh_metadata_sdk.cs"
    output = (
        tmp_path_factory.mktemp("native-gh-metadata-sdk") / "GhMetadataSdkProbe.exe"
    )
    built = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            f"/out:{output}",
            f"/reference:{sdk_root / 'RhinoCommon.dll'}",
            "/reference:System.Web.Extensions.dll",
            str(source),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    probe = subprocess.run(
        [str(output), str(sdk_root), str(native_root)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    lines = [
        line.removeprefix("RESULT_JSON=")
        for line in probe.stdout.splitlines()
        if line.startswith("RESULT_JSON=")
    ]
    assert len(lines) == 1
    report = json.loads(lines[0])
    assert report["softwareBuild"] == "8.26.25349.19001"
    return {case["id"]: json.loads(case["json"]) for case in report["cases"]}


@pytest.mark.parametrize(
    ("identity", "inputs", "outputs"),
    [
        ("a0d62394-a118-422d-abb3-6af115c75b25", 2, 1),
        ("57da07bd-ecab-415d-9d86-af36d7073abc", 1, 1),
        ("59e0b89a-e487-49f8-bab8-b5bab16be14c", 1, 1),
    ],
)
def test_actual_component_type_metadata_reports_stable_port_indices(
    native_gh_metadata, identity, inputs, outputs
):
    info = native_gh_metadata[identity]
    assert info["guid"] == identity
    assert len(info["inputs"]) == inputs
    assert len(info["outputs"]) == outputs
    assert [port["index"] for port in info["inputs"]] == list(range(inputs))
    assert [port["index"] for port in info["outputs"]] == list(range(outputs))
