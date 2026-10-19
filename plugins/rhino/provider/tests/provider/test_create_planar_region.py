"""Transport and schema tests for create_planar_region."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from jsonschema import Draft202012Validator, RefResolver

from rhino_mcp.tools.create_planar_region import create_planar_region


OUTER = "00000000-0000-0000-0000-000000000001"
INNER_A = "00000000-0000-0000-0000-000000000002"
INNER_B = "00000000-0000-0000-0000-000000000003"


@patch("rhino_mcp.tools.create_planar_region.get_rhino_connection")
def test_preserves_actual_native_creation_receipt(mock_get_connection):
    receipt = {"result_ids": [OUTER], "count": 1, "message": "Planar region created."}
    mock_get_connection.return_value.send_command.return_value = receipt
    result = create_planar_region(ctx=None, outer_curve_id=INNER_A)
    assert result == {"success": True, **receipt}
    assert "id" not in result and "face_count" not in result


def test_native_receipt_has_a_response_contract():
    from rhino_mcp.validation import validate_response

    assert validate_response(
        "create_planar_region",
        {"result_ids": [OUTER], "count": 1, "message": "Planar region created."},
    )
    for invalid in ({}, {"id": OUTER}, {"result_ids": [], "count": 0, "message": ""}):
        assert not validate_response(
            "create_planar_region", invalid, raise_on_error=False
        )


@patch("rhino_mcp.tools.create_planar_region.get_rhino_connection")
def test_forwards_typed_command_and_returns_native_ids(mock_get_connection):
    connection = MagicMock()
    connection.send_command.return_value = {
        "result_ids": [OUTER],
        "count": 1,
        "message": "Planar region created.",
    }
    mock_get_connection.return_value = connection

    result = create_planar_region(
        ctx=None,
        outer_curve_id=OUTER,
        inner_curve_ids=[INNER_A, INNER_B],
        name="plate",
    )

    connection.send_command.assert_called_once_with(
        "create_planar_region",
        {
            "outer_curve_id": OUTER,
            "inner_curve_ids": [INNER_A, INNER_B],
            "name": "plate",
        },
    )
    assert result == {
        "success": True,
        "result_ids": [OUTER],
        "count": 1,
        "message": "Planar region created.",
    }


@patch("rhino_mcp.tools.create_planar_region.get_rhino_connection")
def test_defaults_inner_curves_to_empty_list_and_omits_name(mock_get_connection):
    connection = MagicMock()
    connection.send_command.return_value = {
        "result_ids": [OUTER],
        "count": 1,
        "message": "Planar region created.",
    }
    mock_get_connection.return_value = connection

    create_planar_region(ctx=None, outer_curve_id=OUTER)

    connection.send_command.assert_called_once_with(
        "create_planar_region",
        {"outer_curve_id": OUTER, "inner_curve_ids": []},
    )


@patch("rhino_mcp.tools.create_planar_region.get_rhino_connection")
def test_transport_errors_remain_tool_errors(mock_get_connection):
    connection = MagicMock()
    connection.send_command.side_effect = RuntimeError("Rhino unavailable")
    mock_get_connection.return_value = connection

    with pytest.raises(RuntimeError, match="Rhino unavailable"):
        create_planar_region(ctx=None, outer_curve_id=OUTER)


def _validator():
    contracts = Path(__file__).parents[3] / "contracts"
    schema = json.loads(
        (contracts / "commands" / "create_planar_region.json").read_text()
    )
    return Draft202012Validator(
        schema,
        resolver=RefResolver(
            base_uri=(contracts / "commands").as_uri() + "/",
            referrer=schema,
        ),
    )


def test_schema_accepts_outer_only_and_multiple_distinct_holes():
    validator = _validator()
    assert not list(validator.iter_errors({"outer_curve_id": OUTER}))
    assert not list(
        validator.iter_errors(
            {
                "outer_curve_id": OUTER,
                "inner_curve_ids": [INNER_A, INNER_B],
                "name": "plate",
            }
        )
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"outer_curve_id": "not-a-guid"},
        {"outer_curve_id": OUTER, "inner_curve_ids": [INNER_A, INNER_A]},
        {"outer_curve_id": OUTER, "inner_curve_ids": ["not-a-guid"]},
        {"outer_curve_id": OUTER, "inner_curve_ids": None},
        {"outer_curve_id": OUTER, "delete_sources": True},
        {"outer_curve_id": OUTER, "unknown": 1},
    ],
)
def test_schema_rejects_invalid_or_unreviewed_parameters(payload):
    assert list(_validator().iter_errors(payload))
