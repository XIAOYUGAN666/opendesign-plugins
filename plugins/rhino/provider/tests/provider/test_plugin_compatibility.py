"""Server and plugin update on different schedules, so the server checks the
plugin it is actually talking to instead of assuming the two match.

A plugin ignores parameters it has never heard of and answers an unknown command
with a bare error; both would otherwise reach the agent as silently wrong
results or as a message with no way forward.
"""

import json
import logging
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.bridge_contract import (
    PROVIDER_BUILD_ID,
    CONTRACT_DIGEST,
    GENERATION,
    bridge_config,
)

IDS = [
    "12345678-1234-1234-1234-123456789012",
    "87654321-4321-4321-4321-210987654321",
]
ADVERTISED = [
    {"name": "boolean_union", "read_only": False, "supports_dry_run": True},
    {"name": "sweep1", "read_only": False},
]
DONE = {"result_ids": [IDS[0]], "count": 1, "message": "created 1 object(s)"}


def frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def buffered_recv(wire_bytes: bytes):
    buffer = bytearray(wire_bytes)

    def recv(n):
        chunk = bytes(buffer[:n])
        del buffer[:n]
        return chunk

    return recv


def capabilities(commands, version="0.4.0"):
    return frame(
        json.dumps(
            {
                "status": "success",
                "result": {
                    "version": version,
                    "command_count": len(commands),
                    "commands": commands,
                    "perception": {"description": "none", "envelope_flags": []},
                },
            }
        ).encode("utf-8")
    )


def success(result):
    return frame(json.dumps({"status": "success", "result": result}).encode("utf-8"))


def error(message):
    return frame(json.dumps({"status": "error", "message": message}).encode("utf-8"))


def authenticated():
    return frame(
        json.dumps(
            {
                "type": "authenticated",
                "generation": GENERATION,
                "providerBuildId": PROVIDER_BUILD_ID,
                "contractDigest": CONTRACT_DIGEST,
            }
        ).encode("utf-8")
    )


def connected(mock_socket_class, wire):
    from rhino_mcp.server import RhinoConnection

    mock_sock = MagicMock()
    mock_sock.recv.side_effect = buffered_recv(authenticated() + wire)
    mock_socket_class.return_value = mock_sock
    conn = RhinoConnection(bridge_config(("127.0.0.1", 1)))
    conn.connect()
    return conn, mock_sock


def sent_types(mock_sock):
    return [
        payload["type"]
        for call in mock_sock.sendall.call_args_list
        if (payload := json.loads(call.args[0][4:]))["type"] != "authenticate"
    ]


class TestUnsupportedCommands:
    @patch("socket.socket")
    def test_command_missing_from_the_plugins_table_is_refused_with_advice(
        self, mock_socket_class
    ):
        conn, mock_sock = connected(
            mock_socket_class, capabilities(ADVERTISED, "0.3.2")
        )

        with pytest.raises(
            Exception,
            match=r"\(0\.3\.2\) does not support 'create_planar_region', which was "
            r"added in plugin 0\.4\.0.*Plugin Manager",
        ):
            conn.send_command("create_planar_region", {"boundary_ids": IDS})

        assert sent_types(mock_sock) == ["describe_capabilities"]

    @patch("socket.socket")
    def test_plugin_without_describe_capabilities_is_told_to_update(
        self, mock_socket_class
    ):
        conn, mock_sock = connected(
            mock_socket_class, error("Unknown command type: describe_capabilities")
        )

        with pytest.raises(
            Exception,
            match=r"\(older than 0\.3\.2\) does not support 'create_planar_region'"
            r".*Plugin Manager",
        ):
            conn.send_command("create_planar_region", {"boundary_ids": IDS})

        assert sent_types(mock_sock) == ["describe_capabilities"]

    @patch("socket.socket")
    def test_an_old_plugins_unknown_command_answer_is_rewritten(
        self, mock_socket_class
    ):
        """An old plugin cannot list its commands, so a command outside the
        since-table still goes out; its refusal comes back actionable and the
        socket survives, because the plugin did answer."""
        conn, mock_sock = connected(
            mock_socket_class,
            error("Unknown command type: describe_capabilities")
            + error("Unknown command type: boolean_union"),
        )

        with pytest.raises(
            Exception,
            match=r"\(older than 0\.3\.2\) does not support 'boolean_union'; this "
            r"server is .*Plugin Manager",
        ) as info:
            conn.send_command("boolean_union", {"object_ids": IDS})

        assert not str(info.value).startswith("Communication error")
        assert sent_types(mock_sock) == ["describe_capabilities", "boolean_union"]
        assert conn.is_connected

    @patch("socket.socket")
    def test_capabilities_are_read_once_per_connection(self, mock_socket_class):
        conn, mock_sock = connected(
            mock_socket_class, capabilities(ADVERTISED) + success(DONE) + success(DONE)
        )

        conn.send_command("boolean_union", {"object_ids": IDS})
        conn.send_command("boolean_union", {"object_ids": IDS})

        assert sent_types(mock_sock) == [
            "describe_capabilities",
            "boolean_union",
            "boolean_union",
        ]


class TestManagedSweepParameters:
    SWEEP: ClassVar[dict[str, Any]] = {
        "rail_id": IDS[0],
        "profile_ids": [IDS[1]],
        "closed": False,
        "cap_planar_ends": True,
    }

    @patch("socket.socket")
    def test_managed_release_version_does_not_use_legacy_parameter_threshold(
        self, mock_socket_class
    ):
        conn, mock_sock = connected(
            mock_socket_class, capabilities(ADVERTISED, "0.1.6") + success(DONE)
        )
        assert conn.send_command("sweep1", self.SWEEP) == DONE
        assert sent_types(mock_sock) == ["describe_capabilities", "sweep1"]
        sent = json.loads(mock_sock.sendall.call_args_list[-1].args[0][4:])
        assert sent["params"]["cap_planar_ends"] is True

    @patch("socket.socket")
    def test_sent_when_the_plugin_is_new_enough(self, mock_socket_class):
        conn, mock_sock = connected(
            mock_socket_class, capabilities(ADVERTISED, "0.4.0") + success(DONE)
        )

        assert conn.send_command("sweep1", self.SWEEP) == DONE
        assert sent_types(mock_sock) == ["describe_capabilities", "sweep1"]

    @patch("socket.socket")
    def test_a_four_part_assembly_version_counts_as_new_enough(self, mock_socket_class):
        conn, _ = connected(
            mock_socket_class, capabilities(ADVERTISED, "0.4.0.0") + success(DONE)
        )

        assert conn.send_command("sweep1", self.SWEEP) == DONE

    @patch("socket.socket")
    def test_an_unused_parameter_does_not_block_an_old_plugin(self, mock_socket_class):
        conn, mock_sock = connected(
            mock_socket_class, capabilities(ADVERTISED, "0.3.2") + success(DONE)
        )

        conn.send_command("sweep1", {**self.SWEEP, "cap_planar_ends": False})

        assert sent_types(mock_sock) == ["describe_capabilities", "sweep1"]

    @patch("socket.socket")
    def test_lost_capability_response_is_unknown_and_does_not_send_the_command(
        self, mock_socket_class
    ):
        """A capability read is also never replayed after its frame is sent."""
        from rhino_mcp.bridge import BridgeResultUnknown
        from rhino_mcp.server import RhinoConnection

        serve = buffered_recv(capabilities(ADVERTISED, "0.4.0") + success(DONE))
        auth_wire = authenticated()
        auth_remaining = len(auth_wire)
        bytes_served = 0
        failed = False

        def recv(n):
            nonlocal bytes_served, failed
            if bytes_served >= auth_remaining and not failed:
                failed = True
                raise TimeoutError("timed out")
            chunk = serve(n)
            bytes_served += len(chunk)
            return chunk

        mock_sock = MagicMock()
        mock_sock.recv.side_effect = buffered_recv(auth_wire)
        mock_socket_class.return_value = mock_sock
        conn = RhinoConnection(bridge_config(("127.0.0.1", 1)))
        conn.connect()
        bytes_served = auth_remaining
        mock_sock.recv.side_effect = recv

        with pytest.raises(BridgeResultUnknown):
            conn.send_command("sweep1", self.SWEEP)

        assert sent_types(mock_sock) == ["describe_capabilities"]
        assert (
            len(mock_sock.sendall.call_args_list) == 2
        )  # auth + one capabilities frame


class TestVersionSkew:
    def _run(self, mock_socket_class, caplog, server, plugin):
        with patch("rhino_mcp.server.server_version", return_value=server):
            conn, _ = connected(
                mock_socket_class,
                capabilities(ADVERTISED, plugin) + success(DONE) + success(DONE),
            )
            with caplog.at_level(logging.WARNING, logger="rhino_mcp_provider.server"):
                conn.send_command("boolean_union", {"object_ids": IDS})
                conn.send_command("boolean_union", {"object_ids": IDS})
        return [
            r.getMessage() for r in caplog.records if "Version skew" in r.getMessage()
        ]

    @patch("socket.socket")
    def test_an_older_plugin_is_reported_once_per_connection(
        self, mock_socket_class, caplog
    ):
        skew = self._run(mock_socket_class, caplog, server="0.4.0", plugin="0.3.2")

        assert len(skew) == 1
        assert "plugin (0.3.2) is older than this server (0.4.0)" in skew[0]
        assert "Plugin Manager" in skew[0]

    @patch("socket.socket")
    def test_an_older_server_points_at_its_launcher(self, mock_socket_class, caplog):
        skew = self._run(mock_socket_class, caplog, server="0.3.2", plugin="0.4.0")

        assert len(skew) == 1
        assert "server (0.3.2) is older than the Rhino plugin (0.4.0)" in skew[0]
        assert "plugin manager" in skew[0]

    @patch("socket.socket")
    def test_matching_versions_are_quiet(self, mock_socket_class, caplog):
        assert (
            self._run(mock_socket_class, caplog, server="0.4.0", plugin="0.4.0") == []
        )

    @patch("rhino_mcp.tools.describe_capabilities.get_rhino_connection")
    def test_describe_capabilities_reports_both_sides(self, mock_get_conn):
        from rhino_mcp.tools.describe_capabilities import (
            describe_capabilities,
            version_skew_report,
        )

        mock_conn = MagicMock()
        mock_conn.send_command.return_value = {
            "version": "0.3.2",
            "command_count": 0,
            "commands": [],
            "perception": {"description": "none", "envelope_flags": []},
        }
        mock_get_conn.return_value = mock_conn

        # Other tests reload the server module; patch the helper retained by this tool.
        with patch.dict(
            version_skew_report.__globals__, server_version=lambda: "0.4.0"
        ):
            result = describe_capabilities(ctx=None)

        assert result["version"] == "0.3.2"
        assert result["server_version"] == "0.4.0"
        assert result["plugin_matches_server"] is False
        assert "Plugin Manager" in result["update_advice"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0.4.0", (0, 4, 0)),
        ("0.4.0.0", (0, 4, 0)),
        ("0.4.1.1", (0, 4, 1, 1)),
        ("0.4.1.1.0", (0, 4, 1, 1)),
        ("0.4.0+abc123", (0, 4, 0)),
        ("0.0.0-mock", (0, 0, 0)),
        ("v1.2", (1, 2, 0)),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_version(text, expected):
    from rhino_mcp.server import parse_version

    assert parse_version(text) == expected


def test_version_skew_retains_patch_revision():
    from rhino_mcp.server import version_skew_report

    with patch.dict(version_skew_report.__globals__, server_version=lambda: "0.4.1.1"):
        older = version_skew_report("0.4.1")
        matching = version_skew_report("0.4.1.1")
    assert older["plugin_matches_server"] is False
    assert "older" in older["update_advice"]
    assert matching["plugin_matches_server"] is True
    assert matching["update_advice"] is None
