"""Bounded RFEM operations without caller-supplied Python or filesystem methods."""

import json
from contextlib import nullcontext
from typing import Annotated, Literal

from fastmcp.tools import ToolResult
from google.protobuf.json_format import ParseDict, ParseError
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from dlubal_rfem_mcp.core.product_execution import execution_response
from dlubal_rfem_mcp.rfem.execution import fingerprint
from dlubal_rfem_mcp.rfem.model_binding import bound_connection, canonical_guid

OBJECT_MODULES = {
    "Material": "structure_core",
    "CrossSection": "structure_core",
    "Node": "structure_core",
    "Line": "structure_core",
    "Member": "structure_core",
    "NodalSupport": "types_for_nodes",
    "StaticAnalysisSettings": "loading",
    "LoadCase": "loading",
    "NodalLoad": "loads",
    "MemberLoad": "loads",
}


class ModelObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "Material",
        "CrossSection",
        "Node",
        "Line",
        "Member",
        "NodalSupport",
        "StaticAnalysisSettings",
        "LoadCase",
        "NodalLoad",
        "MemberLoad",
    ]
    no: int = Field(ge=1, le=1_000_000, strict=True)
    fields: dict[str, JsonValue] = Field(default_factory=dict, max_length=128)


def prepare_objects(objects, rfem):
    """Construct and validate every protobuf before admitting any model writes."""
    prepared, seen = [], set()
    for item in objects:
        identity = (item.kind, item.no)
        if identity in seen or "no" in item.fields:
            raise ValueError("duplicate_object_number")
        seen.add(identity)
        if any(key.startswith("_") for key in item.fields):
            raise ValueError("private_field_denied")
        constructor = getattr(getattr(rfem, OBJECT_MODULES[item.kind]), item.kind)
        # Constructors are an explicit allowlist. SDK protobuf validation rejects
        # unknown fields, wrong scalar types, enum values and invalid structures.
        prepared.append(
            ParseDict(
                {"no": item.no, **item.fields},
                constructor(),
                ignore_unknown_fields=False,
            )
        )
    return prepared


def register_model_operations(mcp, session, manager, control=None):
    from dlubal_rfem_mcp.rfem.connection_status import (
        ApplicationInfoFailureV1,
        ApplicationInfoSuccessV1,
        application_info_response,
        application_info_schema,
    )

    @mcp.tool(
        output_schema=application_info_schema(),
        annotations={"readOnlyHint": True},
        description="Verify the live RFEM API connection and read its version. No model or Python script is required. Does not disclose license identities.",
    )
    def get_rfem_application_info() -> ToolResult:

        from dlubal_rfem_mcp.rfem.session import restriction_category

        try:
            with (
                control.reading() if control else nullcontext(),
                session.connection() as app,
            ):
                info = app.get_application_info()
                result = ApplicationInfoSuccessV1(
                    status="ok", version=str(info.version)[:40]
                )
                return ToolResult(structured_content=result.model_dump(mode="json"))
        except Exception as error:  # noqa: BLE001 - safe fixed read-only boundary projection
            if isinstance(error, RuntimeError) and str(error).startswith(
                "RFEM is busy executing"
            ):
                failure = ApplicationInfoFailureV1(
                    status="error", failureCategory="resourceBusy"
                )
                return ToolResult(
                    content="resourceBusy",
                    structured_content=failure.model_dump(mode="json"),
                    is_error=True,
                )
            category = session.restriction or restriction_category(error)
            session.verified, session.restriction = False, category
            projection = application_info_response.validate_python(
                {"status": "error", "failureCategory": category}
            )
            return ToolResult(
                content=category,
                structured_content=projection.model_dump(mode="json"),
                is_error=True,
            )

    @mcp.tool(
        description=(
            "Create a NEW RFEM model from bounded typed objects in dependency order. "
            "Does not execute Python, open files, delete objects or modify existing models. "
            "Use inspect_rfem_api for installed field names; fields use SDK SI units. "
            "z_axis explicitly sets and verifies the NEW model's axis before any objects are written. "
            "The resulting model_guid identifies the new model. Partial writes remain on failure; "
            "query get_rfem_execution with the same caller-owned execution_id, never replay. "
            "Rigid support fields accept the SDK JSON string 'Infinity'."
        )
    )
    def write_rfem_model(
        model_name: Annotated[str, Field(min_length=1, max_length=128)],
        objects: Annotated[list[ModelObject], Field(min_length=1, max_length=1000)],
        z_axis: Literal["up", "down"],
        execution_id: str | None = None,
        context_id: str = "",
    ) -> ToolResult:
        from dlubal.api import common, rfem

        try:
            payload = json.dumps(
                {
                    "model_name": model_name,
                    "z_axis": z_axis,
                    "objects": [o.model_dump() for o in objects],
                },
                sort_keys=True,
                allow_nan=False,
            )
            if len(payload.encode()) > 1024 * 1024:
                raise ValueError("model_request_too_large")
            prepared = prepare_objects(objects, rfem)

            def work(capture, record):
                with bound_connection(session, common, None, record) as bound:
                    bound.create_model(name=model_name)
                    expected = getattr(
                        rfem.BaseData.GeneralSettings,
                        "GLOBAL_AXES_ORIENTATION_Z" + z_axis.upper(),
                    )
                    base_data = bound.get_base_data()
                    base_data.general_settings.global_axes_orientation = expected
                    bound.set_base_data(base_data=base_data)
                    if (
                        bound.get_base_data().general_settings.global_axes_orientation
                        != expected
                    ):
                        raise RuntimeError(
                            "New model axis orientation could not be verified; no objects were written"
                        )
                    for obj in prepared:
                        bound.create_object(obj)
                    capture.print(
                        f"Created {len(prepared)} objects with global Z axis {z_axis}. Calculation has not been requested."
                    )

            return execution_response(
                manager.submit(
                    execution_id,
                    context_id,
                    fingerprint("write_rfem_model:" + payload, None),
                    work,
                )
            )
        except (ValueError, TypeError, ParseError) as exc:
            return execution_response({"state": "rejected", "error": str(exc)[:1024]})

    @mcp.tool(
        description=(
            "Calculate all loadings of one explicitly identified RFEM model. No Python or file access. "
            "Does not suppress RFEM warnings. A returned running/unknown status is not success; "
            "query get_rfem_execution by the exact execution_id. Read engineering results separately."
        )
    )
    def calculate_rfem_model(
        model_guid: str,
        execution_id: str | None = None,
        context_id: str = "",
    ) -> ToolResult:
        from dlubal.api import common

        try:
            guid = canonical_guid(model_guid)

            def work(capture, record):
                with bound_connection(session, common, guid, record) as bound:
                    result = bound.calculate_all(skip_warnings=False)
                    if not result.succeeded:
                        raise RuntimeError(
                            "RFEM did not confirm a successful calculation. Inspect RFEM warnings."
                        )
                    capture.print(
                        "RFEM confirmed calculation success. Read results before drawing engineering conclusions."
                    )

            return execution_response(
                manager.submit(
                    execution_id,
                    context_id,
                    fingerprint("calculate_rfem_model:v1", guid),
                    work,
                )
            )
        except ValueError as exc:
            return execution_response({"state": "rejected", "error": str(exc)[:1024]})

    return ["write_rfem_model", "calculate_rfem_model", "get_rfem_application_info"]
