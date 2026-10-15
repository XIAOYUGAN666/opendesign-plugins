"""Strict validation against the Provider's exact, packaged schema set."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

# Only the two owned layouts are supported; never inspect a caller's cwd.
_MODULE_PATH = Path(__file__).resolve()
if _MODULE_PATH.parent.parent.name == "provider":
    CONTRACTS_DIR = _MODULE_PATH.parents[2] / "contracts"
else:
    CONTRACTS_DIR = _MODULE_PATH.parents[5] / "contracts"

# A None value is an affirmative noResultSchema disposition, not a missing map
# entry. The inventory check below forces every protocol command to be listed
# either here or in NO_RESULT_SCHEMA_COMMANDS.
RESPONSE_SCHEMA_MAP: Mapping[str, str] = MappingProxyType(
    {
        "create_object": "object_info.json",
        "modify_object": "object_info.json",
        "get_object_info": "object_info.json",
        "get_object_attributes": "object_attributes.json",
        "update_object_attributes": "object_attributes.json",
        "analyze_objects": "analyze_objects_result.json",
        "measure_objects": "measure_result.json",
        "section_profile": "section_profile_result.json",
        "get_document_summary": "document_summary.json",
        "get_objects": "get_objects_result.json",
        "delete_object": "delete_result.json",
        "select_objects": "select_result.json",
        "create_layer": "layer_info.json",
        "delete_layer": "delete_result.json",
        "get_or_set_current_layer": "layer_info.json",
        "execute_rhinoscript_python_code": "execute_script_result.json",
        "capture_viewport": "capture_viewport_result.json",
        "describe_capabilities": "capabilities.json",
        "boolean_union": "boolean_result.json",
        "boolean_difference": "boolean_result.json",
        "boolean_intersection": "boolean_result.json",
    }
)

NO_RESULT_SCHEMA_COMMANDS = frozenset(
    {
        "create_objects",
        "create_planar_region",
        "execute_rhinocommon_csharp_code",
        "extrude_curve",
        "get_commands",
        "get_selected_objects_info",
        "gh_add_component",
        "gh_batch_get_component_type_info",
        "gh_batch_search_components",
        "gh_build_graph",
        "gh_capture_preview",
        "gh_clear_canvas",
        "gh_clear_graph",
        "gh_connect_components",
        "gh_create_document",
        "gh_delete_component",
        "gh_disconnect_components",
        "gh_expire_solution",
        "gh_get_available_components",
        "gh_get_canvas_state",
        "gh_get_component_info",
        "gh_get_component_type_info",
        "gh_get_document_info",
        "gh_get_graph",
        "gh_get_parameter_value",
        "gh_layout_components",
        "gh_list_component_categories",
        "gh_list_components",
        "gh_mutate_graph",
        "gh_run_solution",
        "gh_search_components",
        "gh_set_parameter_value",
        "gh_update_component",
        "intersect_curves",
        "loft",
        "modify_objects",
        "offset_curve",
        "pipe",
        "project_curve",
        "redo",
        "run_command",
        "split_curve",
        "sweep1",
        "undo",
    }
)


class SchemaInfrastructureError(RuntimeError):
    """The exact local contract set is missing, unreadable, or inconsistent."""

    category = "schemaInfrastructureError"


@dataclass(frozen=True)
class ContractInventory:
    command_count: int
    response_schema_count: int
    no_result_schema_count: int


def _canonical_root(contract_root: str | Path) -> Path:
    try:
        root = Path(contract_root)
        if not root.is_absolute():
            raise SchemaInfrastructureError("Contract root must be an absolute path.")
        canonical = root.resolve(strict=True)
        if not canonical.is_dir():
            raise SchemaInfrastructureError("Contract root is not a directory.")
        return canonical
    except SchemaInfrastructureError:
        raise
    except OSError as exc:
        raise SchemaInfrastructureError("Contract root is unavailable.") from exc


def _schema_path(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise SchemaInfrastructureError(
            "Contract reference is not a safe relative path."
        )

    candidate = root.joinpath(*relative.parts)
    try:
        canonical = candidate.resolve(strict=True)
        canonical.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SchemaInfrastructureError(
            f"Contract schema '{relative_path}' is missing or outside the contract root."
        ) from exc
    if not canonical.is_file():
        raise SchemaInfrastructureError(
            f"Contract schema '{relative_path}' is not a file."
        )
    return canonical


@lru_cache(maxsize=256)
def _load_schema_cached(root: Path, relative_path: str) -> dict[str, Any]:
    schema_path = _schema_path(root, relative_path)
    try:
        with schema_path.open("r", encoding="utf-8") as stream:
            schema = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaInfrastructureError(
            f"Contract schema '{relative_path}' is unreadable."
        ) from exc
    if not isinstance(schema, dict):
        raise SchemaInfrastructureError(
            f"Contract schema '{relative_path}' is not an object."
        )
    try:
        Draft202012Validator = jsonschema.Draft202012Validator
        Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise SchemaInfrastructureError(
            f"Contract schema '{relative_path}' is invalid."
        ) from exc
    return schema


@lru_cache(maxsize=32)
def _command_names(root: Path) -> frozenset[str]:
    protocol = _load_schema_cached(root, "protocol.json")
    try:
        command_enum = protocol["$defs"]["command"]["properties"]["type"]["enum"]
    except (KeyError, TypeError) as exc:
        raise SchemaInfrastructureError(
            "Protocol schema has no command declaration."
        ) from exc
    if (
        not isinstance(command_enum, list)
        or not command_enum
        or any(not isinstance(name, str) or not name for name in command_enum)
        or len(set(command_enum)) != len(command_enum)
    ):
        raise SchemaInfrastructureError("Protocol command declaration is invalid.")
    return frozenset(command_enum)


@lru_cache(maxsize=32)
def _registry(root: Path) -> Registry:
    resources: list[tuple[str, Resource[Any]]] = []
    for directory in ("commands", "responses", "common"):
        folder = root / directory
        try:
            files = sorted(folder.glob("*.json"))
        except OSError as exc:
            raise SchemaInfrastructureError(
                "Contract reference set is unreadable."
            ) from exc
        if not files:
            raise SchemaInfrastructureError(
                f"Contract directory '{directory}' is empty."
            )
        for schema_file in files:
            relative = f"{directory}/{schema_file.name}"
            schema = _load_schema_cached(root, relative)
            resource = Resource.from_contents(
                schema,
                default_specification=DRAFT202012,
            )
            resources.append((schema_file.name, resource))
            if directory == "common":
                resources.append((f"common/{schema_file.name}", resource))
                resources.append((f"../common/{schema_file.name}", resource))
    return Registry().with_resources(resources)


def validate_contract_inventory(
    contract_root: str | Path = CONTRACTS_DIR,
) -> ContractInventory:
    """Fail closed unless every declared command has schemas and a response disposition."""
    root = _canonical_root(contract_root)
    commands = _command_names(root)
    response_dispositions = set(RESPONSE_SCHEMA_MAP) | set(NO_RESULT_SCHEMA_COMMANDS)
    if response_dispositions != set(commands):
        missing = set(commands) - response_dispositions
        undeclared = response_dispositions - set(commands)
        detail = "missing or undeclared response disposition"
        if missing:
            detail = "a command has no explicit response disposition"
        elif undeclared:
            detail = "response disposition names an undeclared command"
        raise SchemaInfrastructureError(f"Contract inventory has {detail}.")

    for command_type in commands:
        _load_schema_cached(root, f"commands/{command_type}.json")
    for response_schema in RESPONSE_SCHEMA_MAP.values():
        _load_schema_cached(root, f"responses/{response_schema}")
    _registry(root)
    return ContractInventory(
        command_count=len(commands),
        response_schema_count=len(RESPONSE_SCHEMA_MAP),
        no_result_schema_count=len(NO_RESULT_SCHEMA_COMMANDS),
    )


def _validate_schema(
    schema_path: str,
    value: Any,
    *,
    contract_root: str | Path,
    raise_on_error: bool,
) -> bool:
    root = _canonical_root(contract_root)
    schema = _load_schema_cached(root, schema_path)
    validator = jsonschema.Draft202012Validator(schema, registry=_registry(root))
    try:
        validator.validate(value)
    except jsonschema.ValidationError:
        if raise_on_error:
            raise
        return False
    except Exception as exc:
        # Unresolvable refs and registry/provider errors are infrastructure
        # failures, never a validation verdict about user or Rhino data.
        raise SchemaInfrastructureError(
            "A local contract reference could not be resolved."
        ) from exc
    return True


def validate_command(
    command_type: str,
    params: Any,
    raise_on_error: bool = True,
    *,
    contract_root: str | Path = CONTRACTS_DIR,
) -> bool:
    root = _canonical_root(contract_root)
    if command_type not in _command_names(root):
        raise SchemaInfrastructureError(
            "Command is not declared by the protocol contract."
        )
    return _validate_schema(
        f"commands/{command_type}.json",
        params,
        contract_root=root,
        raise_on_error=raise_on_error,
    )


def validate_response(
    command_type: str,
    response: Any,
    raise_on_error: bool = True,
    *,
    contract_root: str | Path = CONTRACTS_DIR,
) -> bool:
    root = _canonical_root(contract_root)
    if command_type not in _command_names(root):
        raise SchemaInfrastructureError(
            "Command is not declared by the protocol contract."
        )
    if command_type in NO_RESULT_SCHEMA_COMMANDS:
        return True
    try:
        response_schema = RESPONSE_SCHEMA_MAP[command_type]
    except KeyError as exc:
        raise SchemaInfrastructureError(
            "Command has no explicit response contract."
        ) from exc
    return _validate_schema(
        f"responses/{response_schema}",
        response,
        contract_root=root,
        raise_on_error=raise_on_error,
    )


def validate_protocol_command(
    data: dict[str, Any],
    raise_on_error: bool = True,
    *,
    contract_root: str | Path = CONTRACTS_DIR,
) -> bool:
    if not isinstance(data, dict) or "type" not in data or "params" not in data:
        if raise_on_error:
            raise ValueError("Command envelope must contain 'type' and 'params'.")
        return False
    return validate_command(
        data["type"], data["params"], raise_on_error, contract_root=contract_root
    )


def validate_protocol_response(
    data: dict[str, Any], raise_on_error: bool = True
) -> bool:
    if not isinstance(data, dict):
        if raise_on_error:
            raise ValueError("Response envelope must be an object.")
        return False
    if data.get("status") == "error" and isinstance(data.get("message"), str):
        return True
    if data.get("status") == "success" and "result" in data:
        return True
    if raise_on_error:
        raise ValueError("Response envelope has an invalid status or result.")
    return False
