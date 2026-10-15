"""Keep the native Bridge dispatch set aligned with Provider contracts."""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = PROJECT_ROOT.parent
CONTRACTS_ROOT = PLUGIN_ROOT / "contracts"
PROVIDER_ROOT = PROJECT_ROOT / "rhino_mcp"
FUNCTIONS_ROOT = PLUGIN_ROOT / "native" / "Functions"
SERVER_PATH = PROVIDER_ROOT / "server.py"
MCP_COMMAND_ATTRIBUTE = re.compile(
    r'\[McpCommand\(\s*"(?P<name>[a-z_][a-z0-9_]*)"(?P<arguments>[^\]]*)\)\]'
)


def _named_assignment(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return node.value
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                return node.value
    raise AssertionError(f"Provider server has no {name} declaration.")


def _provider_tool_functions() -> list[tuple[str, ast.FunctionDef]]:
    server_tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"))
    registrations = ast.literal_eval(
        _named_assignment(server_tree, "_TOOL_REGISTRATION_SOURCES")
    )
    functions: list[tuple[str, ast.FunctionDef]] = []
    for tool_name, module_name in registrations:
        module_path = PROJECT_ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
        module_tree = ast.parse(module_path.read_text(encoding="utf-8"))
        function = next(
            (
                node
                for node in module_tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == tool_name
            ),
            None,
        )
        assert isinstance(function, ast.FunctionDef), (
            f"Registered Provider tool {tool_name!r} has no function in {module_path}."
        )
        functions.append((tool_name, function))
    return functions


def _bridge_commands(function: ast.FunctionDef) -> set[str]:
    commands: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        function_name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
            if isinstance(node.func, ast.Name)
            else None
        )
        if function_name == "send_command" and node.args:
            index = 0
        elif function_name == "send_grasshopper_command" and len(node.args) > 1:
            index = 1
        else:
            continue
        command = node.args[index]
        if isinstance(command, ast.Constant) and isinstance(command.value, str):
            commands.add(command.value)
    return commands


def _provider_tool_sets(name: str) -> set[str]:
    tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"))
    value = _named_assignment(tree, name)
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
    assert value.func.id == "frozenset"
    return set(ast.literal_eval(value.args[0]))


def _native_attributes() -> list[tuple[str, str]]:
    if not FUNCTIONS_ROOT.is_dir():
        return []
    attributes: list[tuple[str, str]] = []
    for source_path in sorted(FUNCTIONS_ROOT.rglob("*.cs")):
        source = source_path.read_text(encoding="utf-8")
        attributes.extend(
            (match.group("name"), match.group("arguments"))
            for match in MCP_COMMAND_ATTRIBUTE.finditer(source)
        )
    return attributes


def test_protocol_schema_provider_and_native_dispatch_names_match() -> None:
    protocol = json.loads(
        (CONTRACTS_ROOT / "protocol.json").read_text(encoding="utf-8")
    )
    protocol_commands = set(protocol["$defs"]["command"]["properties"]["type"]["enum"])
    schema_commands = {
        path.stem for path in (CONTRACTS_ROOT / "commands").glob("*.json")
    }

    tool_commands: dict[str, set[str]] = defaultdict(set)
    for tool_name, function in _provider_tool_functions():
        for command in _bridge_commands(function):
            tool_commands[command].add(tool_name)

    native_attributes = _native_attributes()
    native_commands = [name for name, _arguments in native_attributes]

    assert len(protocol_commands) == 65
    assert schema_commands == protocol_commands
    assert set(tool_commands) == protocol_commands
    assert len(native_commands) == 65, (
        f"Expected one native dispatch handler per protocol command; found "
        f"{len(native_commands)}. Missing: {sorted(protocol_commands - set(native_commands))}"
    )
    assert len(native_commands) == len(set(native_commands)), (
        "Duplicate native command names."
    )
    assert set(native_commands) == protocol_commands

    read_only_tools = _provider_tool_sets("READ_ONLY_TOOL_NAMES")
    dry_run_commands: set[str] = set()
    read_only_commands: dict[str, bool] = {}
    for tool_name, function in _provider_tool_functions():
        commands = _bridge_commands(function)
        supports_dry_run = any(
            argument.arg == "dry_run"
            for argument in [*function.args.args, *function.args.kwonlyargs]
        )
        for command in commands:
            read_only_commands[command] = read_only_commands.get(command, True) and (
                tool_name in read_only_tools
            )
            if supports_dry_run:
                dry_run_commands.add(command)

    for command, arguments in native_attributes:
        read_only = re.search(r"\bReadOnly\s*=\s*(true|false)", arguments, re.I)
        supports_dry_run = re.search(
            r"\bSupportsDryRun\s*=\s*(true|false)", arguments, re.I
        )
        assert read_only is not None, f"{command} has no explicit ReadOnly metadata."
        assert supports_dry_run is not None, (
            f"{command} has no explicit SupportsDryRun metadata."
        )
        assert (read_only.group(1).lower() == "true") == read_only_commands[command], (
            f"{command} ReadOnly metadata disagrees with Provider tool annotations."
        )
        assert (supports_dry_run.group(1).lower() == "true") == (
            command in dry_run_commands
        ), f"{command} SupportsDryRun metadata disagrees with the Provider contract."


def test_active_rhino_and_grasshopper_handles_are_captured_in_one_context_factory() -> (
    None
):
    production_files = [
        path
        for path in (PLUGIN_ROOT / "native").rglob("*.cs")
        if "tests" not in path.relative_to(PLUGIN_ROOT / "native").parts
    ]
    context_files = [
        path for path in production_files if path.name == "RequestContext.cs"
    ]

    assert len(context_files) == 1, "The native RequestContext factory is missing."
    for path in production_files:
        source = path.read_text(encoding="utf-8")
        if path != context_files[0]:
            assert "RhinoDoc.ActiveDoc" not in source, (
                f"{path} must consume the captured RequestContext, not global ActiveDoc."
            )
            assert "Instances.ActiveDocument" not in source
            assert "Instances.ActiveCanvas" not in source

    context_source = context_files[0].read_text(encoding="utf-8")
    assert context_source.count("RhinoDoc.ActiveDoc") == 1
    assert context_source.count("Instances.ActiveDocument") == 1
    assert context_source.count("Instances.ActiveCanvas") == 1


def test_grasshopper_branch_values_from_sdk_non_generic_lists_are_adapted() -> None:
    helper_source = (FUNCTIONS_ROOT / "GrasshopperHelpers.cs").read_text(
        encoding="utf-8"
    )

    assert "values.Add(SerializeBranchItem(goo));" in helper_source
    assert re.search(
        r"internal static object\? SerializeBranchItem\(object\? item\)\s*=>\s*"
        r"item is IGH_Goo goo\s*\? SerializeGoo\(goo\)\s*:\s*SerializeObject\(item\);",
        helper_source,
    ), "SDK branch items must be type-checked before Grasshopper goo serialization."
