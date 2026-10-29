"""Framing and strict JSON behavior for the authenticated Bridge transport."""

from __future__ import annotations

import pytest

from rhino_mcp.bridge import (
    MAX_FRAME_SIZE,
    BridgeClient,
    BridgeProtocolError,
    BridgeResultUnknown,
    RhinoCommandError,
    encode_frame,
    read_exact,
    read_frame,
    read_frame_bytes,
)
from rhino_mcp.validation import SchemaInfrastructureError
from tests.fixtures.bridge_contract import TOKEN, FakeBridge, bridge_config


class ChunkedSocket:
    def __init__(self, data: bytes, chunk_size: int = 3) -> None:
        self._data = bytearray(data)
        self._chunk_size = chunk_size

    def recv(self, count: int) -> bytes:
        size = min(count, self._chunk_size)
        chunk = bytes(self._data[:size])
        del self._data[:size]
        return chunk


def frame_bytes(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def test_encode_frame_uses_four_byte_big_endian_length() -> None:
    payload = b'{"type":"ping"}'
    encoded = encode_frame(payload)
    assert encoded == len(payload).to_bytes(4, "big") + payload


def test_read_exact_accumulates_fragmented_socket_reads() -> None:
    assert read_exact(ChunkedSocket(b"abcdefgh", chunk_size=2), 8) == b"abcdefgh"


def test_read_frame_handles_fragmented_header_and_payload() -> None:
    wire = frame_bytes(b'{"status":"success","result":{}}')
    assert read_frame(ChunkedSocket(wire, chunk_size=1)) == {
        "status": "success",
        "result": {},
    }


def test_back_to_back_frames_are_read_independently() -> None:
    wire = frame_bytes(b'{"sequence":1}') + frame_bytes(b'{"sequence":2}')
    stream = ChunkedSocket(wire, chunk_size=5)
    assert read_frame(stream) == {"sequence": 1}
    assert read_frame(stream) == {"sequence": 2}


def test_exact_maximum_frame_size_is_accepted() -> None:
    class MaximumFrameSocket:
        def __init__(self) -> None:
            self.header = MAX_FRAME_SIZE.to_bytes(4, "big")
            self.position = 0

        def recv(self, count: int) -> bytes:
            if self.header:
                result = self.header[:count]
                self.header = self.header[len(result) :]
                return result
            if self.position >= MAX_FRAME_SIZE:
                return b""
            size = min(count, MAX_FRAME_SIZE - self.position)
            self.position += size
            block = b" " * size
            if self.position == MAX_FRAME_SIZE:
                block = block[:-1] + b"0"
            return block

    payload = read_frame_bytes(MaximumFrameSocket())
    assert len(payload) == MAX_FRAME_SIZE
    assert payload[-1:] == b"0"


@pytest.mark.parametrize("size", [0, MAX_FRAME_SIZE + 1])
def test_zero_and_oversized_frames_are_rejected(size: int) -> None:
    stream = ChunkedSocket(size.to_bytes(4, "big"))
    with pytest.raises(BridgeProtocolError):
        read_frame_bytes(stream)


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(BridgeProtocolError, match="UTF-8"):
        read_frame(ChunkedSocket(frame_bytes(b"\xff")))


@pytest.mark.parametrize("payload", [b"{", b'{"x":NaN}', b'{"x":1,"x":2}'])
def test_invalid_nonstandard_or_duplicate_json_is_rejected(payload: bytes) -> None:
    with pytest.raises(BridgeProtocolError):
        read_frame(ChunkedSocket(frame_bytes(payload)))


def test_legacy_unframed_auth_response_is_not_sniffed_or_retried() -> None:
    with FakeBridge(raw_auth_response=b'{"type":"authenticated"}') as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(BridgeProtocolError):
            client.call("gh_clear_canvas", {})

        assert fake.wait_for_connection()
        assert fake.authentication_frames[0]["type"] == "authenticate"
        assert fake.command_frames == []
        assert fake.connection_count == 1


def test_invalid_success_result_reports_contract_violation_without_denying_execution() -> (
    None
):
    with FakeBridge() as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(BridgeResultUnknown) as caught:
            client.call(
                "create_object",
                {"type": "BOX", "params": {"width": 1, "length": 2, "height": 3}},
            )

        assert "action may already have occurred" in str(caught.value)
        assert "command was not sent" not in str(caught.value)
        assert len(fake.command_frames) == 1
        assert fake.connection_count == 1


def test_response_schema_infrastructure_error_is_typed_after_dispatch() -> None:
    from unittest.mock import patch

    with FakeBridge() as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        with (
            patch(
                "rhino_mcp.bridge.validate_response",
                side_effect=SchemaInfrastructureError("fixture failure"),
            ),
            pytest.raises(BridgeResultUnknown) as caught,
        ):
            client.call(
                "create_object",
                {"type": "BOX", "params": {"width": 1, "length": 2, "height": 3}},
            )

        assert caught.value.category == "resultUnknown"
        assert client.lifecycle_snapshot() == ("unknown", 0)
        assert "action may already have occurred" in str(caught.value)
        assert len(fake.command_frames) == 1


def test_invalid_planar_region_receipt_is_unknown_and_not_replayed() -> None:
    with FakeBridge() as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(BridgeResultUnknown) as caught:
            client.call(
                "create_planar_region",
                {"outer_curve_id": "00000000-0000-0000-0000-000000000001"},
            )
        assert caught.value.category == "resultUnknown"
        assert client.lifecycle_snapshot() == ("unknown", 0)
        assert len(fake.command_frames) == 1
        assert fake.connection_count == 1


def test_typed_rhino_error_redacts_credentials_and_windows_paths() -> None:
    def error_response(_command):
        return {
            "status": "error",
            "message": f"Operation failed for {TOKEN} at C:\\Users\\private\\model.3dm",
        }

    with FakeBridge(response=error_response) as fake:
        client = BridgeClient(bridge_config(fake.endpoint))
        with pytest.raises(RhinoCommandError) as caught:
            client.call("gh_clear_canvas", {})

        assert TOKEN not in str(caught.value)
        assert "C:\\Users" not in str(caught.value)
        assert "[local path]" in str(caught.value)
        assert len(fake.command_frames) == 1
