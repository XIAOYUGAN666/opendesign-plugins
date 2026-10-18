from __future__ import annotations

import tempfile
import shutil
import subprocess
import sys
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_built_wheel_contains_only_flat_python_package_and_distribution_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_file = PROJECT_ROOT / "pyproject.toml"
    assert project_file.is_file()

    from build import ProjectBuilder
    from build.env import DefaultIsolatedEnv

    output = tmp_path / "wheelhouse"
    output.mkdir()
    # Windows may use a non-ASCII user-profile temp path. The isolated pip
    # backend emits paths through build's UTF-8 subprocess reader, so use an
    # ASCII temp root for the build environment while keeping wheel output under
    # pytest's temporary directory.
    with TemporaryDirectory(
        prefix="rhino-wheel-build-", dir=PROJECT_ROOT.parent
    ) as build_temp:
        for name in ("TEMP", "TMP", "TMPDIR"):
            monkeypatch.setenv(name, build_temp)
        monkeypatch.setenv("PYTHONUTF8", "1")
        monkeypatch.setattr(tempfile, "tempdir", build_temp)
        build_environment = Path(build_temp) / "backend-env"
        with DefaultIsolatedEnv(path=str(build_environment)) as environment:
            builder = ProjectBuilder.from_isolated_env(environment, str(PROJECT_ROOT))
            environment.install(builder.build_system_requires)
            environment.install(builder.get_requires_for_build("wheel"))
            wheel_path = builder.build("wheel", str(output))

    with ZipFile(wheel_path) as wheel:
        members = {name for name in wheel.namelist() if not name.endswith("/")}

    assert any(name.startswith("rhino_mcp/") for name in members)
    version = json.loads((PROJECT_ROOT.parent / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert any(name.startswith(f"rhino_mcp_provider-{version}.dist-info/") for name in members)
    assert all(
        not name.startswith(("native/", "contracts/", "scripts/", "tests/"))
        for name in members
    )
    assert "rhino_mcp/guides/overview.md" in members
    assert "rhino_mcp/static/rhinoscriptsyntax.py" in members
    assert "rhino_mcp/static/source/curve.py" in members

    uv = shutil.which("uv")
    assert uv is not None, (
        "uv is required to install the wheel into the isolated test venv"
    )
    with TemporaryDirectory(
        prefix="rhino-wheel-resources-", dir=PROJECT_ROOT.parent
    ) as install_temp:
        install_target = Path(install_temp) / "site-packages"
        install_result = subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--target",
                str(install_target),
                str(wheel_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert install_result.returncode == 0, install_result.stderr
        probe = f"""
import sys
sys.path.insert(0, {str(install_target)!r})
import asyncio
from importlib.resources import files
import rhino_mcp
from rhino_mcp.guidance import TOPICS, modeling_guidance
from rhino_mcp.static.rhinoscriptsyntax import rhinoscriptsyntax_json

root = files("rhino_mcp")
assert all(root.joinpath("guides", f"{{topic}}.md").is_file() for topic in TOPICS)
source_modules = {{
    resource.name.removesuffix(".py")
    for resource in root.joinpath("static", "source").iterdir()
    if resource.name.endswith(".py")
}}
assert len(source_modules) == 27
assert "curve" in source_modules
assert all(modeling_guidance(topic)["content"].strip() for topic in TOPICS)
assert {{module["ModuleName"] for module in rhinoscriptsyntax_json}} == source_modules - {{"compat"}}
assert len(asyncio.run(rhino_mcp.mcp.list_tools())) == 70
"""
        probe_result = subprocess.run(
            [sys.executable, "-I", "-c", probe],
            cwd=install_temp,
            check=False,
            capture_output=True,
            text=True,
        )
        assert probe_result.returncode == 0, probe_result.stderr
