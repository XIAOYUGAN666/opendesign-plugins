"""Distinct MCP results for RFEM mutation dispatch and journal observation."""

import json

from fastmcp.tools import ToolResult
from mcp.types import CallToolResult, TextContent

from dlubal_rfem_mcp.rfem.execution import result_text


class ExecutionResult(ToolResult):
    def to_mcp_result(self):
        if self.structured_content is None:
            raise ValueError("executionResultMissing")
        return CallToolResult(
            content=self.content,
            structured_content=self.structured_content,
            is_error=self.structured_content["state"] != "succeeded",
        )


def execution_response(result):
    return ExecutionResult(
        content=[TextContent(type="text", text=result_text(result))],
        structured_content=result,
    )


def execution_status_response(result):
    """A successful journal read preserves the operation state, including unknown.

    MCP isError describes this lookup, not whether the observed operation worked.
    Mutation dispatch continues to use ExecutionResult and its fail-closed flag.
    """
    return ToolResult(
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
        structured_content=result,
    )
