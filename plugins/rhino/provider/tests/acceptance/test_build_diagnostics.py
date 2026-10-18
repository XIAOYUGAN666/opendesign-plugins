"""Build diagnostics identify a fixed phase without emitting commands or output."""

import subprocess

import pytest

from scripts import build_plugin, build_release


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        ("_build_native_outputs", "nativeBuildFailed"),
        ("_build_native_capability_probe", "nativeProbeBuildFailed"),
    ],
)
def test_native_command_failure_reports_only_its_actual_phase(
    tmp_path, monkeypatch, function, expected
):
    private = "private-command-and-output-must-not-be-printed"
    monkeypatch.setattr(
        build_release.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [private], 1, stdout=private, stderr=private
        ),
    )
    with pytest.raises(build_release.ReleaseBuildError) as captured:
        getattr(build_release, function)(tmp_path, "dotnet", "sha256:" + "a" * 64)
    assert build_plugin.failure_code(captured.value) == expected
    assert private not in build_plugin.failure_code(captured.value)


def test_native_binding_failure_has_a_fixed_category():
    error = build_release.ReleaseBuildError(
        "The built native package differs from the root binding."
    )
    assert build_plugin.failure_code(error) == "nativeBindingMismatch"


def test_unrecognized_failure_does_not_emit_exception_values():
    assert (
        build_plugin.failure_code(RuntimeError("private-data")) == "providerStageFailed"
    )
