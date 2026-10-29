"""Typed, read-only RFEM result access.

This module deliberately exposes a very small allowlist instead of forwarding
``Application.get_results``.  RFEM accepts arbitrary result/filter identifiers
and currently ignores unknown filter columns, so caller-controlled identifiers
would not form a security boundary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from contextlib import AbstractContextManager
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dlubal_rfem_mcp.rfem.state_fingerprint import (
    RFEMStateFingerprint,
    StateFingerprintError,
    capture_model_state,
)

logger = logging.getLogger("dlubal_rfem_mcp.rfem.results")


class RFEMConnection(Protocol):
    def connection(self) -> AbstractContextManager[Any]: ...


ResultKind = Literal[
    "calculation_status",
    "member_internal_forces",
    "node_deformations",
    "stability_critical_load_factors",
    "steel_member_design_ratios",
]
ResultStatus = Literal["ok", "no_results", "unavailable", "invalid_request"]

_MAX_OBJECT_NOS = 100
_MAX_LOADINGS = 50
_MAX_ROWS = 100
_LOADING_RE = re.compile(r"^(?:LC|CO|RC|DS)\d{1,9}$")
_CHECK_TYPE_RE = re.compile(r"^[A-Z]{1,8}\d{1,8}(?:\.\d{1,4})?$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9°%/·*^²³(). -]{0,24}$")
_MODEL_GUID_RE = re.compile(
    r"^(?:\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}|[0-9A-Fa-f]{8}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})$",
)
_VERSION_RE = re.compile(r"^[0-9A-Za-z._-]{1,40}$")


class RFEMResultRow(BaseModel):
    """Fixed row shape shared by the allowlisted result kinds."""

    model_config = ConfigDict(extra="forbid")

    loading: str | None = None
    member_no: int | None = None
    node_no: int | None = None
    location_x: float | None = None
    mode_no: int | None = None
    critical_load_factor: float | None = None
    u: float | None = None
    u_x: float | None = None
    u_y: float | None = None
    u_z: float | None = None
    phi_x: float | None = None
    phi_y: float | None = None
    phi_z: float | None = None
    n: float | None = None
    v_y: float | None = None
    v_z: float | None = None
    m_t: float | None = None
    m_y: float | None = None
    m_z: float | None = None
    design_check_type: str | None = None
    design_ratio: float | None = None
    design_check_details_id: int | None = None


class RFEMResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    governing_column: str | None = None
    governing_value: float | None = None
    governing_unit: str | None = None


class RFEMResultSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: Literal["RFEM 6"] = "RFEM 6"
    model_guid: str
    application_version: str
    state_fingerprint: RFEMStateFingerprint | None = None


class RFEMResultResponse(BaseModel):
    """Bounded response safe to expose as MCP structured content."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = "1.1"
    status: ResultStatus
    error_code: str | None = None
    result_type: ResultKind
    source: RFEMResultSource | None = None
    has_results: bool | None = None
    row_count: int = 0
    returned_count: int = 0
    truncated: bool = False
    units: dict[str, str] = Field(default_factory=dict)
    rows: list[RFEMResultRow] = Field(default_factory=list)
    summary: RFEMResultSummary = Field(default_factory=RFEMResultSummary)
    result_hash: str | None = None
    warning_codes: list[str] = Field(default_factory=list)


class _ResultSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    enum_name: str
    object_column: Literal["member_no", "node_no"] | None = None
    allowed_columns: tuple[str, ...]
    governing_columns: tuple[str, ...]
    governing_order: Literal["max_abs", "max", "min_positive"]


_SPECS: dict[str, _ResultSpec] = {
    "member_internal_forces": _ResultSpec(
        enum_name="STATIC_ANALYSIS_MEMBERS_INTERNAL_FORCES",
        object_column="member_no",
        allowed_columns=(
            "loading",
            "member_no",
            "node_no",
            "location_x",
            "n",
            "v_y",
            "v_z",
            "m_t",
            "m_y",
            "m_z",
        ),
        governing_columns=("n", "v_y", "v_z", "m_t", "m_y", "m_z"),
        governing_order="max_abs",
    ),
    "node_deformations": _ResultSpec(
        enum_name="STATIC_ANALYSIS_NODES_GLOBAL_DEFORMATIONS",
        object_column="node_no",
        allowed_columns=(
            "loading",
            "node_no",
            "u",
            "u_x",
            "u_y",
            "u_z",
            "phi_x",
            "phi_y",
            "phi_z",
        ),
        governing_columns=("u", "u_x", "u_y", "u_z"),
        governing_order="max_abs",
    ),
    "stability_critical_load_factors": _ResultSpec(
        enum_name="STABILITY_ANALYSIS_CRITICAL_LOAD_FACTORS",
        allowed_columns=("loading", "mode_no", "mode", "critical_load_factor"),
        governing_columns=("critical_load_factor",),
        governing_order="min_positive",
    ),
    "steel_member_design_ratios": _ResultSpec(
        enum_name="STEEL_DESIGN_MEMBERS_DESIGN_RATIOS",
        object_column="member_no",
        allowed_columns=(
            "loading",
            "member_no",
            "location_x",
            "design_check_type",
            "design_ratio",
            "design_check_details_id",
        ),
        governing_columns=("design_ratio",),
        governing_order="max",
    ),
}

_FALLBACK_UNITS = {
    "location_x": "m",
    "u": "m",
    "u_x": "m",
    "u_y": "m",
    "u_z": "m",
    "phi_x": "rad",
    "phi_y": "rad",
    "phi_z": "rad",
    "n": "N",
    "v_y": "N",
    "v_z": "N",
    "m_t": "N·m",
    "m_y": "N·m",
    "m_z": "N·m",
    "critical_load_factor": "1",
    "design_ratio": "1",
}


def _safe_scalar(value, column: str, warning_codes: set[str]):
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            logger.debug("scalar conversion unavailable", exc_info=True)
            return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if column == "loading":
        text = str(value).strip().upper()
        if _LOADING_RE.fullmatch(text):
            return text
    elif column == "design_check_type":
        text = str(value).strip().upper()
        if _CHECK_TYPE_RE.fullmatch(text):
            return text
    warning_codes.add("unsafe_string_redacted")
    return None


def _normalise_row(raw: dict, spec: _ResultSpec, warning_codes: set[str]) -> dict:
    row: dict = {}
    for column in spec.allowed_columns:
        source_column = column
        if column == "mode_no" and column not in raw and "mode" in raw:
            source_column = "mode"
        if source_column not in raw:
            continue
        value = _safe_scalar(raw[source_column], column, warning_codes)
        if value is not None:
            row[column] = value
    return row


def _metric(row: dict, spec: _ResultSpec) -> tuple[float, str | None]:
    candidates: list[tuple[float, str]] = []
    for column in spec.governing_columns:
        value = row.get(column)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            candidates.append((float(value), column))
    if not candidates:
        return (math.inf if spec.governing_order == "min_positive" else -math.inf, None)
    if spec.governing_order == "max_abs":
        value, column = max(candidates, key=lambda item: abs(item[0]))
        return abs(value), column
    if spec.governing_order == "min_positive":
        positive = [item for item in candidates if item[0] > 0]
        if not positive:
            return math.inf, None
        return min(positive, key=lambda item: item[0])
    return max(candidates, key=lambda item: item[0])


def _units(table, spec: _ResultSpec) -> dict[str, str]:
    result = {
        key: unit
        for key, unit in _FALLBACK_UNITS.items()
        if key in spec.allowed_columns
    }
    for meta in getattr(table, "columns", []) or []:
        column_id = str(getattr(meta, "id", ""))
        unit = str(getattr(meta, "unit", "")).strip()
        # RFEM's design_ratio and critical_load_factor values are canonical
        # ratios (e.g. 0.85 and 10.0). Some table metadata renders the former
        # as "%" for display without scaling the value. Never let a display
        # label change the threshold semantics of the typed contract.
        if column_id in {"design_ratio", "critical_load_factor"}:
            continue
        if column_id in spec.allowed_columns and unit and _UNIT_RE.fullmatch(unit):
            result[column_id] = unit
    return dict(sorted(result.items()))


def _hash_response(response: RFEMResultResponse) -> str:
    payload = response.model_dump(mode="json", exclude={"result_hash"})
    canonical = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_request(
    result_type: ResultKind,
    object_nos: list[int] | None,
    loadings: list[str] | None,
    limit: int,
) -> tuple[list[int], list[str], str | None]:
    if result_type != "calculation_status" and result_type not in _SPECS:
        return [], [], "unsupported_result_type"
    if not 1 <= limit <= _MAX_ROWS:
        return [], [], "invalid_limit"
    objects = sorted(set(object_nos or []))
    if len(objects) > _MAX_OBJECT_NOS or any(
        not isinstance(no, int) or isinstance(no, bool) or no < 1 for no in objects
    ):
        return [], [], "invalid_object_nos"
    loading_values = sorted({str(value).strip().upper() for value in (loadings or [])})
    if len(loading_values) > _MAX_LOADINGS or any(
        not _LOADING_RE.fullmatch(value) for value in loading_values
    ):
        return [], [], "invalid_loadings"
    if result_type == "calculation_status" and (objects or loading_values):
        return [], [], "filters_not_supported"
    if result_type == "calculation_status":
        return objects, loading_values, None
    spec = _SPECS[result_type]
    if spec.object_column and not objects:
        return [], [], "object_filter_required"
    if not loading_values:
        return [], [], "loading_filter_required"
    if objects and result_type == "stability_critical_load_factors":
        return [], [], "object_filter_not_supported"
    return objects, loading_values, None


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, ModelSelectionError):
        return exc.error_code
    if isinstance(exc, StateFingerprintError):
        return "state_proof_unavailable"
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "rfem_timeout"
    if isinstance(exc, RuntimeError):
        return "rfem_unavailable"
    return "rfem_result_query_failed"


class ModelSelectionError(RuntimeError):
    """A safe, caller-visible failure to bind a read to one open RFEM model."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


def _normalise_model_guid(value: object) -> str | None:
    raw = str(value or "").strip()
    if not _MODEL_GUID_RE.fullmatch(raw):
        return None
    try:
        return str(UUID(raw.strip("{}")))
    except ValueError:
        return None


def _resolve_model_id(app, requested_guid: str | None):
    """Resolve an exact open model without changing RFEM's active model."""

    if requested_guid is None:
        model_id = app.get_active_model()
        if _normalise_model_guid(getattr(model_id, "guid", "")) is None:
            raise ModelSelectionError("model_identity_unavailable")
        return model_id

    model_list = app.get_model_list()
    for model_info in getattr(model_list, "model_info", ()):
        open_guid = _normalise_model_guid(getattr(model_info, "guid", ""))
        if open_guid == requested_guid:
            from dlubal.api import common

            return common.ModelId(guid=requested_guid)
    raise ModelSelectionError("model_not_open")


def read_results(
    session: RFEMConnection,
    result_type: ResultKind,
    object_nos: list[int] | None = None,
    loadings: list[str] | None = None,
    limit: int = 20,
    model_guid: str | None = None,
) -> RFEMResultResponse:
    """Read one allowlisted RFEM result set without executing caller code."""

    objects, loading_values, request_error = _validate_request(
        result_type,
        object_nos,
        loadings,
        limit,
    )
    if request_error:
        return RFEMResultResponse(
            status="invalid_request",
            error_code=request_error,
            result_type=result_type,
        )

    requested_guid = None
    if model_guid is not None:
        requested_guid = _normalise_model_guid(model_guid)
        if requested_guid is None:
            return RFEMResultResponse(
                status="invalid_request",
                error_code="invalid_model_guid",
                result_type=result_type,
            )

    try:
        with session.connection() as app:
            model_id = _resolve_model_id(app, requested_guid)
            app_info = app.get_application_info()
            safe_guid = _normalise_model_guid(getattr(model_id, "guid", ""))
            if safe_guid is None:
                raise ModelSelectionError("model_identity_unavailable")
            raw_version = str(
                getattr(app_info, "full_version", "")
                or getattr(app_info, "version", "")
            ).strip()
            source = RFEMResultSource(
                model_guid=safe_guid,
                application_version=raw_version
                if _VERSION_RE.fullmatch(raw_version)
                else "unknown",
            )
            first_state = capture_model_state(
                app,
                model_id=model_id,
                exporter_version=source.application_version,
            )
            has_results = bool(app.has_results(model_id=model_id).value)
            if result_type == "calculation_status" or not has_results:
                second_state = capture_model_state(
                    app,
                    model_id=model_id,
                    exporter_version=source.application_version,
                )
                if second_state != first_state:
                    return RFEMResultResponse(
                        status="unavailable",
                        error_code="model_changed_during_read",
                        result_type=result_type,
                        has_results=has_results,
                    )
                source.state_fingerprint = second_state
                response = RFEMResultResponse(
                    status="ok" if has_results else "no_results",
                    result_type=result_type,
                    source=source,
                    has_results=has_results,
                )
                response.result_hash = _hash_response(response)
                return response

            spec = _SPECS[result_type]
            from dlubal.api import rfem

            filters = []
            if objects and spec.object_column:
                filters.append(
                    rfem.results.ResultsFilter(
                        column_id=spec.object_column,
                        filter_expression=",".join(str(no) for no in objects),
                    )
                )
            if loading_values:
                filters.append(
                    rfem.results.ResultsFilter(
                        column_id="loading",
                        filter_expression=",".join(loading_values),
                    )
                )

            enum_value = getattr(rfem.results, spec.enum_name)
            table = app.get_results(
                results_type=enum_value,
                filters=filters,
                model_id=model_id,
            )
            raw_rows = table.data.to_dict(orient="records")
            warning_codes: set[str] = set()
            rows = [_normalise_row(dict(raw), spec, warning_codes) for raw in raw_rows]

            # RFEM currently ignores unknown filter columns.  Prove that each
            # requested filter was applied before exposing any data.
            if (
                objects
                and spec.object_column
                and any(row.get(spec.object_column) not in objects for row in rows)
            ):
                return RFEMResultResponse(
                    status="unavailable",
                    error_code="filter_not_enforced",
                    result_type=result_type,
                    source=source,
                    has_results=True,
                )
            if loading_values and any(
                row.get("loading") not in loading_values for row in rows
            ):
                return RFEMResultResponse(
                    status="unavailable",
                    error_code="filter_not_enforced",
                    result_type=result_type,
                    source=source,
                    has_results=True,
                )

            reverse = spec.governing_order != "min_positive"
            rows.sort(key=lambda row: _metric(row, spec)[0], reverse=reverse)
            governing_value, governing_column = (
                _metric(rows[0], spec) if rows else (None, None)
            )
            if governing_value in (math.inf, -math.inf):
                governing_value, governing_column = None, None
            units = _units(table, spec)
            selected = rows[:limit]
            second_state = capture_model_state(
                app,
                model_id=model_id,
                exporter_version=source.application_version,
            )
            if second_state != first_state:
                return RFEMResultResponse(
                    status="unavailable",
                    error_code="model_changed_during_read",
                    result_type=result_type,
                    has_results=True,
                )
            source.state_fingerprint = second_state
            response = RFEMResultResponse(
                status="ok" if rows else "no_results",
                result_type=result_type,
                source=source,
                has_results=True,
                row_count=len(rows),
                returned_count=len(selected),
                truncated=len(rows) > len(selected),
                units=units,
                rows=[RFEMResultRow.model_validate(row) for row in selected],
                summary=RFEMResultSummary(
                    governing_column=governing_column,
                    governing_value=governing_value,
                    governing_unit=units.get(governing_column)
                    if governing_column
                    else None,
                ),
                warning_codes=sorted(
                    warning_codes
                    | (
                        {"api_warning_present"}
                        if getattr(table, "warning", "")
                        else set()
                    )
                ),
            )
            response.result_hash = _hash_response(response)
            return response
    except Exception as exc:
        logger.debug("read_rfem_results failed", exc_info=True)
        return RFEMResultResponse(
            status="unavailable",
            error_code=_safe_error_code(exc),
            result_type=result_type,
        )
