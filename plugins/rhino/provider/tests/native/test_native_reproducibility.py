"""Opt-in SDK build check; uses a disposable Git fixture and no Rhino process."""

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from scripts import build_release as release


pytestmark = pytest.mark.skipif(
    os.environ.get("NATIVE_REPRODUCIBILITY_CHECK") != "1",
    reason="Explicit two-build SDK reproducibility check",
)


def test_release_resolves_the_recorded_sdk():
    expected = json.loads((release.PLUGIN_ROOT / "global.json").read_bytes())["sdk"][
        "version"
    ]
    actual = release._run(
        [release._dotnet_executable(), "--version"], cwd=release.PLUGIN_ROOT
    )
    assert actual.strip() == expected


def test_release_uses_recorded_inputs(tmp_path, monkeypatch):
    repository = tmp_path / "src"
    native = repository / "native"
    shutil.copytree(
        release.NATIVE_ROOT,
        native,
        ignore=shutil.ignore_patterns("bin", "obj", "tests"),
    )
    shutil.copytree(release.PLUGIN_ROOT / "contracts", repository / "contracts")
    shutil.copyfile(release.PLUGIN_ROOT / "global.json", repository / "global.json")

    def git(*arguments):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Native build fixture",
                "-c",
                "user.email=fixture@example.invalid",
                *arguments,
            ],
            cwd=repository,
        ).strip()

    git("init", "-q", "--initial-branch=main")
    git(
        "remote",
        "add",
        "origin",
        "https://github.com/XIAOYUGAN666/opendesign-plugins.git",
    )
    note = repository / "fixture-context.txt"
    note.write_text("First build context\n", encoding="utf-8")
    git("add", "--", "native", "contracts", "global.json", "fixture-context.txt")
    git("commit", "-q", "-m", "test: first isolated build context")
    monkeypatch.setattr(release, "PLUGIN_ROOT", repository)
    monkeypatch.setattr(release, "NATIVE_ROOT", native)
    dotnet = shutil.which("dotnet")
    assert dotnet

    def build(name):
        workspace = tmp_path / name
        workspace.mkdir()
        outputs = release._build_native_outputs(workspace, dotnet, "sha256:" + "a" * 64)
        return {
            f"{target}/{file}": hashlib.sha256(data).hexdigest()
            for target, path in outputs.items()
            for file, data in release.native_package_files(path, target).items()
        }

    before = build("a")
    first_revision = git("rev-parse", "HEAD")
    note.write_text(
        "Second build context; compiled source is unchanged\n", encoding="utf-8"
    )
    git("add", "--", "fixture-context.txt")
    git("commit", "-q", "-m", "test: change only isolated checkout context")
    assert git("rev-parse", "HEAD") != first_revision
    assert not git("diff", "HEAD^", "HEAD", "--", "native", "contracts")
    assert build("b") == before
