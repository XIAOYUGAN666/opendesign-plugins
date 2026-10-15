"""Signed zeros are normalized before strict response validation and MCP output."""

from __future__ import annotations

import json
import math
from unittest.mock import patch

import pytest

import rhino_mcp
from rhino_mcp.server import RhinoConnection
from tests.fixtures.bridge_contract import FakeBridge, bridge_config


@pytest.fixture
def connection():
    payload = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "circle",
        "type": "CIRCLE",
        "geometry": {"points": [[-0.0, -3.0, 0.0]], "degree": "2"},
        "_delta": {"bounds": [[-0.0, -1e-300, 0]]},
        "_health": {"value": -0.0},
        "other": [False, None, "-0.0", -2.5],
    }

    def response(command):
        if command["type"] == "describe_capabilities":
            return {
                "status": "success",
                "result": {
                    "version": "0.1.0-test",
                    "command_count": 2,
                    "commands": [
                        {"name": "create_object", "read_only": False},
                        {"name": "get_objects", "read_only": True},
                    ],
                    "perception": {"description": "none", "envelope_flags": []},
                },
            }
        return {"status": "success", "result": payload}

    fake = FakeBridge(response=response).start()
    conn = RhinoConnection(bridge_config(fake.endpoint))
    try:
        yield conn, fake, payload
    finally:
        conn.disconnect()
        fake.stop()


def _assert_positive_zero(result, *, include_metadata=True):
    point = result["geometry"]["points"][0]
    assert math.copysign(1, point[0]) == 1
    assert math.copysign(1, point[2]) == 1
    assert point[1] == -3.0
    if include_metadata:
        bounds = result["_delta"]["bounds"][0]
        assert math.copysign(1, bounds[0]) == 1
        assert bounds[1] == -1e-300
        assert type(bounds[2]) is int
        assert math.copysign(1, result["_health"]["value"]) == 1
        assert result["other"] == [False, None, "-0.0", -2.5]
        assert result["other"][0] is False


def test_bridge_normalizes_before_validation(connection):
    conn, fake, _ = connection
    with patch("rhino_mcp.bridge.validate_response") as validate:
        result = conn.send_command(
            "create_object",
            {"type": "CIRCLE", "params": {"center": [0, 0, 0], "radius": 5}},
        )

    _assert_positive_zero(result)
    checked_result = next(
        call.args[1]
        for call in validate.call_args_list
        if call.args[0] == "create_object"
    )
    _assert_positive_zero(checked_result, include_metadata=False)
    assert "_delta" not in checked_result
    assert "_health" not in checked_result
    assert [frame["type"] for frame in fake.command_frames] == [
        "describe_capabilities",
        "create_object",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["create_object", "get_objects"])
async def test_sdk_output_has_positive_zero(connection, tool):
    conn, fake, _ = connection
    arguments = (
        {"type": "CIRCLE", "params": {"center": [0, 0, 0], "radius": 5}}
        if tool == "create_object"
        else {}
    )
    with (
        patch("rhino_mcp.bridge.validate_response"),
        patch(f"rhino_mcp.tools.{tool}.get_rhino_connection", return_value=conn),
    ):
        output = await rhino_mcp.mcp.call_tool(tool, arguments)

    wire = json.loads(output.model_dump_json(by_alias=True))
    assert not wire["isError"]
    result = wire["structuredContent"]["result"]
    if tool == "get_objects":
        result = json.loads(result)
    _assert_positive_zero(result, include_metadata=False)
    text_result = json.loads(wire["content"][0]["text"])
    assert math.copysign(1, text_result["geometry"]["points"][0][0]) == 1
    assert [frame["type"] for frame in fake.command_frames] == [
        "describe_capabilities",
        tool,
    ]
