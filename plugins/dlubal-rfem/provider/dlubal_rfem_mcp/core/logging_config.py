"""Allowlisted structured logs: never render arbitrary SDK/exception content."""

import io
import json
import logging
import sys

NATIVE_TOOLS = frozenset(
    {
        "health_check",
        "get_rfem_application_info",
        "inspect_rfem_api",
        "write_rfem_model",
        "calculate_rfem_model",
        "run_rfem_script",
        "get_rfem_execution",
        "read_rfem_results",
    }
)
EVENTS = frozenset({"providerStartFailed", "runtimeVersionMismatch", "toolCall"})


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = (
            record.msg
            if isinstance(record.msg, str) and record.msg in EVENTS
            else "runtimeLog"
        )
        value: dict[str, object] = {"level": record.levelname, "event": event}
        tool = getattr(record, "tool_name", None)
        if tool in NATIVE_TOOLS:
            value["tool"] = tool
            value["success"] = bool(getattr(record, "success", False))
        return json.dumps(value, separators=(",", ":"))


class DiscardRuntimeOutput(io.TextIOBase):
    """SDK print/warning output is not an approved bootstrap or logging channel."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass


def setup_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    sys.stdout = DiscardRuntimeOutput()
    sys.stderr = DiscardRuntimeOutput()


def log_tool_call(
    tool_name: str, api_key: str, duration_ms: float, success: bool, client_ip: str = ""
) -> None:
    logging.getLogger("dlubal_rfem_mcp.audit").info(
        "toolCall", extra={"tool_name": tool_name, "success": success}
    )
