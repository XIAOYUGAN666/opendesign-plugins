"""Tests for rhino_mcp.validation $ref resolution.

The contract schemas carry bare relative $ids (e.g. "create_object.json") and
reference shared types as "../common/definitions.json#/$defs/...". These tests
pin that both ref styles keep resolving — and keep being enforced — through
the public validate_command / validate_response entry points.
"""

import sys
import warnings

import jsonschema
import pytest

from rhino_mcp.validation import (
    CONTRACTS_DIR,
    SchemaInfrastructureError,
    validate_command,
    validate_contract_inventory,
    validate_response,
)

GUID = "12345678-1234-1234-1234-123456789012"


class TestCrossFileRefResolution:
    """$refs into common/definitions.json must resolve and be enforced."""

    def test_command_refs_resolve(self):
        # modify_object's id and new_color fields are $refs into
        # ../common/definitions.json (guid, color).
        assert validate_command("modify_object", {"id": GUID, "new_color": [0, 255, 0]})

    def test_command_refs_enforced(self):
        # A bad guid violates the referenced common definition. If refs were
        # silently skipped instead of resolved, this would pass validation.
        with pytest.raises(jsonschema.ValidationError):
            validate_command("modify_object", {"id": "not-a-guid"})

    def test_response_refs_resolve(self):
        assert validate_response(
            "get_object_info",
            {
                "id": GUID,
                "name": "MyBox",
                "type": "BOX",
                "layer": "Default",
                "material": "-1",
                "color": {"r": 255, "g": 0, "b": 0},
                "bounding_box": [[-1, -1, -1], [1, 1, 1]],
                "geometry": {},
            },
        )

    def test_response_refs_enforced(self):
        # bounding_box is a $ref chain into common definitions
        # (boundingBox -> point3d); 2D corners must be rejected.
        with pytest.raises(jsonschema.ValidationError):
            validate_response(
                "get_object_info",
                {
                    "id": GUID,
                    "name": "MyBox",
                    "type": "BOX",
                    "layer": "Default",
                    "material": "-1",
                    "color": {"r": 255, "g": 0, "b": 0},
                    "bounding_box": [[-1, -1], [1, 1]],
                    "geometry": {},
                },
            )

    def test_local_defs_refs_resolve(self):
        # create_object discriminates params by type via local #/$defs refs;
        # sphere params on a BOX must be rejected through that ref chain.
        assert validate_command(
            "create_object",
            {"type": "BOX", "params": {"width": 1, "length": 2, "height": 3}},
        )
        with pytest.raises(jsonschema.ValidationError):
            validate_command("create_object", {"type": "BOX", "params": {"radius": 1}})

    def test_same_directory_response_refs_resolve(self):
        # responses/get_objects_result.json refs object_info.json by bare
        # name (a same-directory cross-file ref). Under the old RefResolver
        # this never resolved (its file:// fallback fails on Windows), so
        # validating a real get_objects page always raised.
        assert validate_response(
            "get_objects",
            {
                "objects": [
                    {
                        "id": GUID,
                        "name": "Box1",
                        "type": "BOX",
                        "layer": "Default",
                        "color": {"r": 1, "g": 2, "b": 3},
                        "bounding_box": [[0, 0, 0], [1, 1, 1]],
                    }
                ],
                "total_matching": 1,
                "offset": 0,
                "limit": 50,
                "has_more": False,
            },
        )

    def test_same_directory_response_refs_enforced(self):
        with pytest.raises(jsonschema.ValidationError):
            validate_response(
                "get_objects",
                {
                    "objects": [{"id": "not-a-guid"}],
                    "total_matching": 1,
                    "offset": 0,
                    "limit": 50,
                    "has_more": False,
                },
            )


class TestNoRefResolverDeprecation:
    """jsonschema.RefResolver is deprecated since 4.18 and slated for removal.
    Importing or using the validation module must not touch it."""

    def test_import_and_use_emit_no_refresolver_warning(self):
        sys.modules.pop("rhino_mcp.validation", None)
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                from rhino_mcp import validation

                validation.validate_command(
                    "modify_object", {"id": GUID, "new_color": [0, 255, 0]}
                )
            refresolver_warnings = [
                w for w in caught if "RefResolver" in str(w.message)
            ]
            assert not refresolver_warnings
        finally:
            # Leave a cleanly imported module for later tests.
            sys.modules.pop("rhino_mcp.validation", None)
            import rhino_mcp.validation  # noqa: F401


class TestContractRootAndInventory:
    def test_missing_exact_contract_root_fails_closed_without_parent_search(
        self, tmp_path
    ):
        with pytest.raises(SchemaInfrastructureError) as caught:
            validate_command(
                "create_object",
                {"type": "BOX", "params": {"width": 1, "length": 2, "height": 3}},
                contract_root=tmp_path,
            )

        assert caught.value.category == "schemaInfrastructureError"

    def test_every_declared_command_has_a_schema_and_explicit_response_disposition(
        self,
    ):
        inventory = validate_contract_inventory(CONTRACTS_DIR)

        assert inventory.command_count == 65
        assert inventory.response_schema_count == 21
        assert inventory.no_result_schema_count == 44

    def test_no_result_schema_is_an_explicit_valid_response_disposition(self):
        assert validate_response(
            "get_selected_objects_info",
            {"selected_objects": []},
            contract_root=CONTRACTS_DIR,
        )
