from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CENSUS = PROJECT_ROOT / "tests" / "fixtures" / "expected_source_census.json"


def _names(directory: Path, suffix: str) -> set[str]:
    return {path.name for path in directory.glob(f"*{suffix}") if path.is_file()}


def test_package_root_is_flat() -> None:
    assert (PROJECT_ROOT / "rhino_mcp" / "__init__.py").is_file()
    assert not (PROJECT_ROOT / "provider").exists()
    assert not (PROJECT_ROOT / "src").exists()


def test_exact_python_tool_module_allowlist() -> None:
    expected = json.loads(EXPECTED_CENSUS.read_text(encoding="utf-8"))
    actual = _names(PROJECT_ROOT / "rhino_mcp" / "tools", ".py") - {"__init__.py"}
    assert actual == set(expected["pythonToolModules"])


def test_exact_guidance_and_rhinoscript_documentation_inventory() -> None:
    expected = json.loads(EXPECTED_CENSUS.read_text(encoding="utf-8"))
    guides = _names(PROJECT_ROOT / "rhino_mcp" / "guides", ".md")
    static_docs = _names(PROJECT_ROOT / "rhino_mcp" / "static" / "source", ".py")
    assert guides == set(expected["guides"])
    assert static_docs == set(expected["rhinoScriptSourceModules"])


def test_exact_contract_schema_inventory() -> None:
    expected = json.loads(EXPECTED_CENSUS.read_text(encoding="utf-8"))
    commands = _names(PROJECT_ROOT.parent / "contracts" / "commands", ".json")
    responses = _names(PROJECT_ROOT.parent / "contracts" / "responses", ".json")
    assert commands == set(expected["commandSchemas"])
    assert responses == set(expected["responseSchemas"])
    assert (PROJECT_ROOT.parent / "contracts" / "protocol.json").is_file()
    assert (PROJECT_ROOT.parent / "contracts" / "common" / "definitions.json").is_file()


def test_fourteen_first_conversion_test_identities_are_present() -> None:
    expected = json.loads(EXPECTED_CENSUS.read_text(encoding="utf-8"))
    actual = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in PROJECT_ROOT.joinpath("tests").rglob("test_*.py")
    }
    assert set(expected["baselineTests"]) <= actual
    assert len(expected["baselineTests"]) == 14


def test_census_records_supported_inventory_and_conversion_floor() -> None:
    expected = json.loads(EXPECTED_CENSUS.read_text(encoding="utf-8"))
    assert expected["mcpTools"] == 70
    assert expected["mcpResources"] == 4
    assert expected["mcpPrompts"] == 2
    assert expected["pythonToolModuleCount"] == 41
    assert expected["nativeRoutineCount"] == 69
    assert expected["nativeCommandHandlerCount"] == 65
    assert expected["commandSchemaCount"] == 65
    assert expected["responseSchemaCount"] == len(expected["responseSchemas"]) == 18
    assert expected["baselineTestCount"] == 14
