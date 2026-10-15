from __future__ import annotations

import os
import tomllib
from pathlib import Path
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_NAMES = ("LICENSE", "COPYING", "NOTICE", "ORIGIN", "CHANGELOG")
FORBIDDEN_DIRECTORIES = {".git", "experiments"}
GENERATED_DIRECTORIES = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venvs",
    "venv",
    "build",
    "bin",
    "obj",
    "dist",
    "target",
}


def _native_assembly_identifier() -> str:
    native_projects = tuple((PROJECT_ROOT.parent / "native").glob("*.csproj"))
    assert len(native_projects) == 1
    root = ElementTree.parse(native_projects[0]).getroot()
    assembly_name = next(
        (
            item.text
            for item in root.iter()
            if item.tag.endswith("AssemblyName") and item.text
        ),
        None,
    )
    assert assembly_name
    return assembly_name.casefold()


def _project_paths():
    for current_root, directories, filenames in os.walk(PROJECT_ROOT):
        directories[:] = [
            name
            for name in directories
            if name not in GENERATED_DIRECTORIES and not name.endswith(".egg-info")
        ]
        for name in (*directories, *filenames):
            yield Path(current_root) / name


def _forbidden_content() -> tuple[str, ...]:
    old_import = "rhino" + "mcp"
    old_commands = tuple(
        "mcp" + suffix for suffix in ("start", "stop", "test", "version")
    )
    old_environment = tuple(
        "RHINO_MCP_" + suffix for suffix in ("HOST", "PORT", "ALLOW_REMOTE")
    )
    old_repository = "https://github.com/" + "jingcheng-chen/" + old_import + ".git"
    return (
        old_import,
        *old_commands,
        *old_environment,
        old_repository,
    )


def test_source_tree_contains_no_upstream_or_legacy_runtime_metadata() -> None:
    assert PROJECT_ROOT.is_dir()
    forbidden = _forbidden_content()
    # This native assembly identity is defined by this project, not an imported Python module or
    # upstream launcher name. Read it from the project file rather than duplicating that identity.
    native_assembly = _native_assembly_identifier()
    for path in _project_paths():
        if path.is_dir():
            assert path.name not in FORBIDDEN_DIRECTORIES
        if path.is_file():
            assert not any(
                path.name.upper().startswith(prefix) for prefix in FORBIDDEN_NAMES
            )
            if path.suffix.lower() in {".py", ".toml", ".json", ".md", ".yml", ".yaml"}:
                contents = path.read_text(encoding="utf-8").casefold()
                contents = contents.replace(native_assembly, "")
                for token in forbidden:
                    assert token.casefold() not in contents, path.relative_to(
                        PROJECT_ROOT
                    )


def test_managed_provider_has_no_legacy_launcher_or_update_guidance() -> None:
    managed_modules = (
        PROJECT_ROOT / "rhino_mcp" / "__init__.py",
        PROJECT_ROOT / "rhino_mcp" / "server.py",
    )
    legacy_commands = tuple("mcp" + suffix for suffix in ("start", "stop", "version"))
    forbidden = (
        "up" + "stream",
        "uv" + "x",
        *legacy_commands,
        str(1900 + 99),
        "package" + " manager",
    )

    for path in managed_modules:
        contents = path.read_text(encoding="utf-8").casefold()
        for token in forbidden:
            assert token not in contents, path.relative_to(PROJECT_ROOT)


def test_distribution_declares_no_public_console_script_or_source_metadata() -> None:
    project_file = PROJECT_ROOT / "pyproject.toml"
    assert project_file.is_file()
    metadata = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]
    assert metadata["name"] == "rhino-mcp-provider"
    assert metadata["version"] == "0.1.2"
    assert "scripts" not in metadata
    assert "readme" not in metadata
    assert "license" not in metadata
    assert "authors" not in metadata
    assert "urls" not in metadata
