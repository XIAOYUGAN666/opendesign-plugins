"""RFEM MCP tools."""

import ast
import json
import logging
import sqlite3
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from dlubal_mcp.core import rpc_deadline
from dlubal_mcp.core.off_loop import off_event_loop
from dlubal_mcp.core.sandbox import execute_script, script_builtins, validate_code_ast
from dlubal_mcp.rfem.execution import (
    ExecutionManager,
    ExecutionUncertain,
    default_state_path,
    fingerprint,
    result_text,
)
from dlubal_mcp.rfem.model_binding import BoundApplication, canonical_guid
from dlubal_mcp.rfem.results import RFEMResultResponse, read_results
from dlubal_mcp.rfem.session import RFEMSession

logger = logging.getLogger("dlubal_mcp.rfem")


_ORIENTATION_PREAMBLE = """\
try:
    _bd_orient = app.get_base_data()
    z_up = (_bd_orient.general_settings.global_axes_orientation ==
            rfem.BaseData.GeneralSettings.GLOBAL_AXES_ORIENTATION_ZUP)
except Exception:
    z_up = False
gravity_sign = -1 if z_up else 1
z = (lambda h: h) if z_up else (lambda h: -h)
print("[Z-axis: " + ("UP" if z_up else "DOWN") + ", gravity_sign=" + str(gravity_sign) + "]")

def refresh_orientation():
    global z_up, gravity_sign, z
    try:
        _bd2 = app.get_base_data()
        z_up = (_bd2.general_settings.global_axes_orientation ==
                rfem.BaseData.GeneralSettings.GLOBAL_AXES_ORIENTATION_ZUP)
    except Exception:
        z_up = False
    gravity_sign = -1 if z_up else 1
    z = (lambda h: h) if z_up else (lambda h: -h)
    print("[Orientation refreshed: Z-" + ("UP" if z_up else "DOWN") + "]")

"""


def register_tools(
    mcp: FastMCP, session: RFEMSession, config, control=None
) -> list[str]:
    """Register RFEM tools on the shared FastMCP instance. Returns list of registered tool names."""

    executions = ExecutionManager(
        Path(config.execution_state_file)
        if config.execution_state_file
        else default_state_path(),
        endpoint=f"{getattr(config, 'url', '127.0.0.1')}:{getattr(config, 'api_port', 9000)}",
        control=control,
    )

    class ExecutionResult(ToolResult):
        def to_mcp_result(self):
            return CallToolResult(
                content=self.content,
                structuredContent=self.structured_content,
                isError=self.structured_content["state"] != "succeeded",
            )

    def response(result):
        return ExecutionResult(
            content=[TextContent(type="text", text=result_text(result))],
            structured_content=result,
        )

    @mcp.tool(
        annotations={"readOnlyHint": True, "destructiveHint": False},
        description="Read the durable status and bounded output of an RFEM execution by execution_id. A successful lookup can return state=failed with the original error and model_guid. Does not connect to RFEM, wait for the session lock, or repeat a script. running is not a completed calculation; unknown requires operator inspection before further scripts.",
    )
    @off_event_loop
    def get_rfem_execution(execution_id: str) -> ToolResult:
        result = executions.status(execution_id)
        return ToolResult(
            content=[
                TextContent(type="text", text=json.dumps(result, ensure_ascii=False))
            ],
            structured_content=result,
        )

    @mcp.tool(
        description="""Executes Python code against RFEM 6 for operations without a typed tool. For input inspection, pass the exact model_guid and use only read methods such as get_object_id_list, get_object_list and has_results.

Available objects: 'app' (RFEM Application), 'rfem' (dlubal.api.rfem module), 'math'.
No imports or 'with' blocks needed.

execution_id is an optional caller-generated UUID. Reusing it retrieves the same operation; changed input is rejected. context_id is an optional caller-owned chat scope. model_guid explicitly selects a model; otherwise the context reuses its remembered GUID. A 120-second wait can return running: query get_rfem_execution by ID, never replay the code. Busy/unknown operations block further scripts. Earlier changes are not rolled back on failure.

CROSS-SECTIONS: every cross-section write prints a [cross-section] line with the name, type and manufacturing type RFEM actually stored, and flags fields that differ from the request; report these to the user. A name RFEM rejects leaves no section behind. Never substitute a different size, standard or parametric section for a rejected or changed one without asking the user.

CRITICAL DATA TYPES (Protobuf gRPC):
- definition_nodes, nodes, members: ALWAYS int lists e.g. [1, 2] — NEVER strings!
- Enum fields: full label string e.g. "ANALYSIS_TYPE_GEOMETRICALLY_LINEAR", "TYPE_BEAM"
- load_direction: "LOAD_DIRECTION_GLOBAL_Z_OR_USER_DEFINED_W_TRUE_LENGTH"

Example — populate a model you are authorized to modify. Read existing identifiers first. The example updates support 1, support 2, analysis settings 1 and load case 1 after confirming RFEM already created them. Use create_object only for identifiers that are absent:
  app.create_object(rfem.structure_core.Material(no=1, name="S235"))
  app.create_object(rfem.structure_core.CrossSection(no=1, name="IPE 240", material=1))
  app.create_object(rfem.structure_core.Node(no=1, coordinate_1=0.0, coordinate_2=0.0, coordinate_3=0.0))
  app.create_object(rfem.structure_core.Node(no=2, coordinate_1=6.0, coordinate_2=0.0, coordinate_3=0.0))
  app.create_object(rfem.structure_core.Line(no=1, definition_nodes=[1, 2]))
  app.create_object(rfem.structure_core.Member(no=1, line=1, cross_section_start=1))
  app.update_object(rfem.types_for_nodes.NodalSupport(no=1, nodes=[1], spring_x=float('inf'), spring_y=float('inf'), spring_z=float('inf')))
  app.update_object(rfem.types_for_nodes.NodalSupport(no=2, nodes=[2], spring_y=float('inf'), spring_z=float('inf')))
  app.update_object(rfem.loading.StaticAnalysisSettings(no=1, analysis_type="ANALYSIS_TYPE_GEOMETRICALLY_LINEAR"))
  app.update_object(rfem.loading.LoadCase(no=1, name="LC1", static_analysis_settings=1, to_solve=True))
  app.create_object(rfem.loads.MemberLoad(no=1, load_type="LOAD_TYPE_FORCE", members=[1], load_case=1, load_distribution="LOAD_DISTRIBUTION_UNIFORM", load_direction="LOAD_DIRECTION_GLOBAL_Z_OR_USER_DEFINED_W_TRUE_LENGTH", magnitude=-10000))
"""
    )
    @off_event_loop
    def run_rfem_script(
        code: str,
        execution_id: str | None = None,
        context_id: str = "",
        model_guid: str | None = None,
    ) -> ToolResult:
        """execution_id is a UUID: reusing it only reads that same operation; changed input is rejected.
        context_id binds subsequent calls to the same model even if RFEM's UI active model changes.
        Explicit model_guid, create_model or open_model intentionally selects and remembers another model.
        After 120 seconds get_rfem_execution retrieves progress without repeating code.
        """
        from dlubal_mcp.core.logging_config import log_tool_call

        start = time.monotonic()
        error = validate_code_ast(code, available_objects="app, rfem, common, math")
        if not error:
            for node in ast.walk(ast.parse(code)):
                if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                    error = "Access to private attributes is not allowed."
                    break
        if error:
            return response({"state": "rejected", "error": error})

        def work(capture, record):
            from dlubal.api import common, rfem

            # The worker owns admission, the connection and cleanup. A returned
            # HTTP timeout never leaves a thread using a closed/unlocked session.
            bound_app = None
            try:
                with session.connection() as active_app:
                    guid = model_guid or executions.binding(context_id)
                    bound_app = BoundApplication(
                        active_app, common, guid, record, capture.print
                    )
                    safe_builtins = script_builtins(capture.print)
                    safe_globals = {
                        "__builtins__": safe_builtins,
                        "rfem": rfem,
                        "common": common,
                        "app": bound_app,
                        "math": __import__("math"),
                    }
                    try:
                        execute_script(
                            code, safe_globals, preamble=_ORIENTATION_PREAMBLE
                        )
                    finally:
                        bound_app._check_write_outcome()
            except ExecutionUncertain:
                raise
            except Exception as exc:
                logger.debug("RFEM execution failed", exc_info=True)
                if bound_app is None or not bound_app._mutation_attempted:
                    # Startup and read-only RPC failures cannot leave one of
                    # this script's model changes running. Keep readiness retryable.
                    raise RuntimeError(str(exc)) from exc
                if isinstance(exc, sqlite3.Error):
                    raise ExecutionUncertain(
                        "Execution journal failed after a model operation."
                    ) from exc
                raise

        try:
            if model_guid:
                model_guid = canonical_guid(model_guid)
            result = executions.submit(
                execution_id, context_id, fingerprint(code, model_guid), work
            )
        except Exception as exc:
            logger.debug("RFEM tool failed", exc_info=True)
            result = {"state": "rejected", "error": str(exc)}
        log_tool_call(
            "run_rfem_script",
            "server",
            (time.monotonic() - start) * 1000,
            result["state"] == "succeeded",
        )
        return response(result)

    @mcp.tool(
        description="""Look up the EXACT RFEM API field names, types, enum values, AND app method signatures from the INSTALLED RFEM build — copy them verbatim into run_rfem_script. Deterministic and version-exact; works even when RFEM is NOT running (no connection needed).

Use this to find the precise identifier/signature to type. For how a field is USED, worked examples, or what it MEANS in prose, use search_dlubal_apidocs instead.

Query forms:
- Object:            inspect_rfem_api("LoadCase")              -> fields with types, enums, relations
- Object (filtered): inspect_rfem_api("LoadCase weight")       -> only fields matching the term(s)
- Field family:      inspect_rfem_api("LoadCase wind_*")       -> only fields starting with the prefix
- Enum (filtered):   inspect_rfem_api("ActionCategory office") -> matching enum value names (never dumps all 400+)
- Method:            inspect_rfem_api("set_base_data")         -> the app.* method signature (e.g. keyword-only args)
- Keyword search:    inspect_rfem_api("concrete addon")        -> ranked matches across classes/fields/enums/methods
- More of a list:    inspect_rfem_api("ActionCategory", offset=15) -> the next page of whatever the answer listed

When an answer ends with "... (+N more; call with offset=X)", repeat the SAME query with that offset
to continue the list — do not rephrase the query to try to see the rest. offset/limit page whichever
list the answer is made of (fields, enum values, or ranked matches); limit can only narrow a page,
never widen it past the built-in cap.

Examples that prevent common mistakes:
- inspect_rfem_api("concrete addon")  -> base_data.addons.concrete_design_active
- inspect_rfem_api("stress")          -> stress_strain_analysis_active  (NOT stress_analysis_active)
- inspect_rfem_api("set_base_data")   -> set_base_data(*, base_data=..., ...) — base_data is KEYWORD-only
- inspect_rfem_api("ActionCategory office") -> the exact enum value to assign to LoadCase.action_category
"""
    )
    @off_event_loop
    def inspect_rfem_api(
        query: str,
        offset: Annotated[
            int,
            Field(
                ge=0,
                description="Skip this many entries of the list the answer shows.",
            ),
        ] = 0,
        limit: Annotated[
            int,
            Field(
                ge=0,
                le=40,
                description="Max entries to show; 0 uses the default page size.",
            ),
        ] = 0,
    ) -> str:
        from dlubal_mcp.core import introspection
        from dlubal_mcp.core.logging_config import log_tool_call

        start = time.monotonic()
        try:
            result = introspection.inspect("rfem", query, offset, limit)
            log_tool_call(
                "inspect_rfem_api", "server", (time.monotonic() - start) * 1000, True
            )
            return result
        except Exception as exc:
            logger.debug("RFEM tool failed", exc_info=True)
            log_tool_call(
                "inspect_rfem_api", "server", (time.monotonic() - start) * 1000, False
            )
            return f"inspect_rfem_api error: {exc}"

    @mcp.tool(
        description="""Reads a bounded, typed RFEM 6 result summary without executing caller-supplied Python and without changing or recalculating the model.

Allowed result types only: calculation_status, member_internal_forces, node_deformations, stability_critical_load_factors, steel_member_design_ratios.
Result queries require explicit object_nos (except stability) and loadings (LC/CO/RC/DS identifiers). They are allowlist-validated and verified again against every returned row. Results are capped at 100 rows, sorted by the governing numeric value, carry units and a canonical result_hash, and omit model paths, object names, raw warnings and raw exceptions.

Pass model_guid whenever the caller is bound to a specific model. The reader resolves that exact GUID from RFEM's open-model list without changing the active model and fails closed when it is not open. Omitting model_guid retains active-model behavior for interactive compatibility.

Use calculation_status first when you only need to know whether the bound model has results. A no_results or unavailable status is not a successful engineering check and must never be treated as completed.

error_code rfem_timeout means RFEM did not answer within 120 s, typically because a dialog is open in RFEM or RFEM is still busy with another task: ask the user to check RFEM, then retry."""
    )
    @off_event_loop
    def read_rfem_results(
        result_type: Literal[
            "calculation_status",
            "member_internal_forces",
            "node_deformations",
            "stability_critical_load_factors",
            "steel_member_design_ratios",
        ],
        object_nos: Annotated[
            list[Annotated[int, Field(ge=1)]],
            Field(max_length=100),
        ]
        | None = None,
        loadings: Annotated[list[str], Field(max_length=50)] | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        model_guid: Annotated[
            str,
            Field(
                pattern=r"^(?:\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})$",
                description="GUID of the exact open RFEM model to read",
            ),
        ]
        | None = None,
        context_id: str = "",
    ) -> RFEMResultResponse:
        from dlubal_mcp.core.logging_config import log_tool_call

        start = time.monotonic()

        class ContextSession:
            @contextmanager
            def connection(self):
                from dlubal.api import common

                with session.connection() as active_app:
                    bound = BoundApplication(
                        active_app,
                        common,
                        model_guid or executions.binding(context_id),
                        lambda guid: executions.bind(context_id, guid),
                    )
                    # Capture the first read's model while still holding the
                    # session lock, before any subsequent script can enter.
                    bound._model()
                    yield bound

        with (
            rpc_deadline.time_limit(rpc_deadline.RESULT_READ_SECONDS),
            control.reading() if control else nullcontext(),
        ):
            response = read_results(
                session=ContextSession() if context_id else session,
                result_type=result_type,
                object_nos=object_nos,
                loadings=loadings,
                limit=limit,
                model_guid=model_guid,
            )
        log_tool_call(
            "read_rfem_results",
            "server",
            (time.monotonic() - start) * 1000,
            response.status in {"ok", "no_results"},
        )
        return response

    from dlubal_mcp.rfem.model_operations import register_model_operations

    model_tools = register_model_operations(mcp, session, executions, control)
    return [
        *model_tools,
        "run_rfem_script",
        "get_rfem_execution",
        "inspect_rfem_api",
        "read_rfem_results",
    ]
