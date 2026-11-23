import importlib
import json
from unittest.mock import Mock


def test_viewport_capture_returns_an_image_and_readback_metadata(monkeypatch):
    module = importlib.import_module("rhino_mcp.tools.capture_viewport")
    response = {
        "image_data": "YWJj",
        "mime_type": "image/jpeg",
        "viewport_name": "Perspective",
        "display_mode": "Shaded",
        "visible_object_count": 2,
        "total_object_count": 4,
    }
    connection = Mock()
    connection.send_command.return_value = response
    monkeypatch.setattr(module, "get_rhino_connection", lambda context: connection)

    image, metadata = module.capture_viewport(None, format="jpeg", display_mode="Shaded")
    assert image.to_image_content().mime_type == "image/jpeg"
    assert json.loads(metadata.text)["visible_object_count"] == 2
    assert json.loads(metadata.text)["display_mode"] == "Shaded"
    connection.send_command.assert_called_once()
    assert connection.send_command.call_args.args[1]["format"] == "jpeg"
    assert "camera_location" not in connection.send_command.call_args.args[1]
