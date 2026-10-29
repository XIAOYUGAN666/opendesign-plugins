"""Named port selections must not silently target the default numeric port."""

from unittest.mock import Mock

import pytest

from rhino_mcp.tools import grasshopper_parameters as tools
from rhino_mcp.validation import validate_command


@pytest.mark.parametrize("direction", ["input", "output"])
def test_named_port_omits_competing_default_index(monkeypatch, direction):
    send = Mock(return_value={})
    monkeypatch.setattr(tools, "send_grasshopper_command", send)
    identity = "a0d62394-a118-422d-abb3-6af115c75b25"
    if direction == "input":
        tools.gh_set_parameter_value(
            None, instance_id=identity, value=4, input_name="B"
        )
        command = "gh_set_parameter_value"
    else:
        tools.gh_get_parameter_value(None, instance_id=identity, output_name="R")
        command = "gh_get_parameter_value"
    (_, actual_command, parameters), _ = send.call_args
    assert actual_command == command
    validate_command(command, parameters)
    assert parameters[f"{direction}_name"] == ("B" if direction == "input" else "R")
    assert f"{direction}_index" not in parameters


@pytest.mark.parametrize("index", [0, 1])
def test_numeric_port_selection_is_preserved(monkeypatch, index):
    send = Mock(return_value={})
    monkeypatch.setattr(tools, "send_grasshopper_command", send)
    tools.gh_set_parameter_value(None, nickname="control", value=4, input_index=index)
    assert send.call_args.args[2]["input_index"] == index
    assert "input_name" not in send.call_args.args[2]
