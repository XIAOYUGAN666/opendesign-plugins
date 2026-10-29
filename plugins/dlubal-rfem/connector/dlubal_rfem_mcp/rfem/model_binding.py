"""Route model-scoped SDK calls to an explicit GUID, never a moving active model.

This is an API correctness guard, not a security boundary for hostile Python.
"""

import inspect
import logging
import sqlite3
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from dlubal_rfem_mcp.rfem.execution import ExecutionUncertain

logger = logging.getLogger("dlubal_rfem_mcp.rfem")


def canonical_guid(value):
    return str(UUID(getattr(value, "guid", value)))


@contextmanager
def bound_connection(session, common, guid, record):
    """Distinguish pre-write connection failures from uncertain model mutations."""
    bound = None
    try:
        with session.connection() as app:
            bound = BoundApplication(app, common, guid, record)
            yield bound
    except ExecutionUncertain:
        raise
    except Exception as exc:
        if bound is None or not bound._mutation_attempted:
            raise RuntimeError(str(exc)) from exc
        if isinstance(exc, sqlite3.Error):
            raise ExecutionUncertain(
                "Execution journal failed after a model operation."
            ) from exc
        raise


class BoundApplication:
    def __init__(self, app, common, guid, record):
        self._app = app
        self._common = common
        self._guid = canonical_guid(guid) if guid else None
        self._wire_guid = None
        self._mutation_attempted = False
        self._record = record

    def _model(self):
        if self._wire_guid:
            # Resolve once per execution. Subsequent SDK calls still carry the
            # exact GUID and therefore fail if it closes, never falling back.
            # Return a fresh protobuf so caller mutation cannot retarget us.
            return self._common.ModelId(guid=self._wire_guid)
        models = self._app.get_model_list().model_info
        if not self._guid:
            try:
                active = self._app.get_active_model().guid
            except Exception:
                logger.debug("RFEM active model is unavailable", exc_info=True)
                active = None
            if active:
                self._guid = canonical_guid(active)
            elif len(models) == 1:
                self._guid = canonical_guid(models[0].guid)
            else:
                raise RuntimeError(
                    "No bound RFEM model. Select an explicit model_guid or create a model; never guess among open models."
                )
        match = next(
            (model for model in models if canonical_guid(model.guid) == self._guid),
            None,
        )
        if match is None:
            raise RuntimeError(
                "The bound RFEM model is no longer open. No fallback to the active model is allowed."
            )
        self._record(self._guid)
        self._wire_guid = match.guid
        return self._common.ModelId(guid=match.guid)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        method = getattr(self._app, name)
        if not callable(method):
            raise TypeError("Only public RFEM application methods are exposed")

        def call(*args, **kwargs):
            if name == "get_active_model":
                return self._model()
            if name in {"create_model", "open_model"}:
                # Explicit script operations may select a new model. Persist it
                # immediately, including when a later line in this script fails.
                self._mutation_attempted = True
                model = method(*args, **kwargs)
                self._guid = canonical_guid(model)
                self._wire_guid = model.guid
                self._record(self._guid)
                return model
            if name in {"close_all_models", "close_application", "close_connection"}:
                raise RuntimeError(
                    "Session-wide operations are unavailable inside a managed RFEM script."
                )
            signature = inspect.signature(method)
            bound = signature.bind(*args, **kwargs)
            if name == "set_active_model":
                value = bound.arguments.get("model_id")
                target = self._model()
                if canonical_guid(value) != canonical_guid(target):
                    raise RuntimeError(
                        "Cannot switch the bound model implicitly. Pass model_guid explicitly for the intended model."
                    )
                return None  # Explicit model IDs make a global UI switch unnecessary.
            parameter = next(
                (
                    key
                    for key in ("model_id", "optional_model_id")
                    if key in signature.parameters
                ),
                None,
            )
            if parameter:
                target = self._model()
                supplied: Any = bound.arguments.get(parameter)
                supplied_guid = getattr(supplied, "guid", "")
                if (
                    supplied is not None
                    and supplied_guid
                    and canonical_guid(supplied) != canonical_guid(target)
                ):
                    raise RuntimeError(
                        "RFEM call targets a different model than this execution."
                    )
                bound.arguments[parameter] = (
                    target
                    if parameter == "model_id"
                    else self._common.OptionalModelId(guid=target.guid)
                )
            elif name not in {"get_application_info", "get_model_list"}:
                # A new SDK method without a verified model parameter must not
                # silently reintroduce global active-model mutation.
                raise RuntimeError(
                    f"Application method {name} has no supported model binding."
                )
            if not name.startswith("get_") and name != "has_results":
                self._mutation_attempted = True
            return method(*bound.args, **bound.kwargs)

        return call
