"""Exact SDK regression for replacing component input data and native GH Undo."""

import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope="module")
def native_gh_values(tmp_path_factory):
    sdk = os.environ.get("RHINO_TEST_SDK_SYSTEM_DIR")
    native = os.environ.get("RHINO_TEST_NATIVE_NET48_DIR")
    if not sdk or not native:
        pytest.skip("requires explicit exact Rhino SDK and compiled net48 Bridge paths")
    sdk_root, native_root = Path(sdk), Path(native)
    compiler = (
        Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    )
    source = Path(__file__).parents[1] / "fixtures/native_gh_value_sdk.cs"
    output = tmp_path_factory.mktemp("native-gh-value-sdk") / "GhValueSdkProbe.exe"
    built = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            f"/out:{output}",
            f"/reference:{sdk_root / 'RhinoCommon.dll'}",
            f"/reference:{sdk_root / '../Plug-ins/Grasshopper/Grasshopper.dll'}",
            f"/reference:{sdk_root / '../Plug-ins/Grasshopper/GH_IO.dll'}",
            "/reference:System.Drawing.dll",
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
    assert report["baseline"] == [1]
    assert report["rhinoObjectCount"] == 0 and report["ghObjectCount"] == 1
    return report


def test_actual_set_replaces_default_and_previous_scalar(native_gh_values):
    assert native_gh_values["first"] == [5]
    assert native_gh_values["second"] == [7]


def test_actual_set_is_restored_by_single_official_undo(native_gh_values):
    assert native_gh_values["undone"] is True
    assert native_gh_values["restored"] == [5]


def test_actual_set_replaces_with_flat_list(native_gh_values):
    assert native_gh_values["list"] == [2, 3]


def test_actual_invalid_set_fails_and_preserves_previous_values(native_gh_values):
    assert native_gh_values["invalidError"]
    assert native_gh_values["afterInvalid"] == [2, 3]


def test_actual_partially_invalid_list_fails_without_partial_replacement(
    native_gh_values,
):
    assert native_gh_values["mixedError"]
    assert native_gh_values["afterMixed"] == [2, 3]


def test_actual_empty_list_clears_input(native_gh_values):
    assert native_gh_values["cleared"] == []


def test_actual_successful_set_marks_document_modified(native_gh_values):
    assert native_gh_values["successfulSetModified"] is True


def test_actual_failed_set_preserves_clean_document_flag(native_gh_values):
    assert native_gh_values["failedCleanSetModified"] is False


def test_actual_failed_set_preserves_dirty_document_flag(native_gh_values):
    assert native_gh_values["failedDirtySetModified"] is True
