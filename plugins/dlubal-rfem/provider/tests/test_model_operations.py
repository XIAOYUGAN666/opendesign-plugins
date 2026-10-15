from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from dlubal_rfem_mcp.rfem.execution import ExecutionManager
from dlubal_rfem_mcp.rfem.model_operations import (
    ModelObject,
    prepare_objects,
    register_model_operations,
)


def test_model_input_schema_rejects_arbitrary_operations():
    with pytest.raises(ValueError):
        ModelObject(kind="open_file", no=1)
    with pytest.raises(ValueError):
        ModelObject(kind="Node", no=1, code="print(1)")
    with pytest.raises(ValueError):
        ModelObject(kind="Node", no=True)


def test_installed_sdk_validates_fields_and_support_infinity():
    rfem = pytest.importorskip("dlubal.api.rfem")
    result = prepare_objects(
        [
            ModelObject(
                kind="NodalSupport", no=1, fields={"nodes": [1], "spring_x": "Infinity"}
            )
        ],
        rfem,
    )
    assert result[0].spring_x == float("inf")
    with pytest.raises(Exception, match="unknown_field"):
        prepare_objects(
            [ModelObject(kind="Node", no=1, fields={"unknown_field": 4})], rfem
        )
    with pytest.raises(ValueError, match="duplicate"):
        prepare_objects(
            [ModelObject(kind="Node", no=1), ModelObject(kind="Node", no=1)], rfem
        )


@pytest.mark.asyncio
async def test_typed_worker_creates_once_and_calculates_only_the_exact_model(tmp_path):
    pytest.importorskip("dlubal.api.rfem")
    from dlubal.api.common import ModelId, OperationResult
    from dlubal.api.rfem import BaseData

    models, writes, calculations = [], [], []
    base = BaseData()

    class App:
        def create_model(self, *, name):
            model = ModelId(guid=str(uuid4()))
            models.append(model)
            return model

        def get_model_list(self):
            return SimpleNamespace(model_info=models)

        def get_base_data(self, model_id=None):
            assert model_id.guid == models[0].guid
            return base

        def set_base_data(self, *, base_data, model_id=None):
            assert model_id.guid == models[0].guid
            base.CopyFrom(base_data)

        def create_object(self, obj, model_id=None):
            writes.append((obj.no, model_id.guid))

        def calculate_all(self, *, skip_warnings, model_id=None):
            assert not skip_warnings
            calculations.append(model_id.guid)
            return OperationResult(succeeded=True)

    class Session:
        @contextmanager
        def connection(self):
            yield App()

    manager = ExecutionManager(tmp_path / "typed.sqlite3")
    mcp = FastMCP("typed-test")
    register_model_operations(mcp, Session(), manager)
    async with Client(mcp) as client:
        execution = str(uuid4())
        args = {
            "model_name": "test",
            "z_axis": "up",
            "objects": [{"kind": "Node", "no": 1}],
            "execution_id": execution,
            "context_id": "chat",
        }
        first = await client.call_tool("write_rfem_model", args)
        assert first.structured_content["state"] == "succeeded"
        guid = first.structured_content["model_guid"]
        again = await client.call_tool("write_rfem_model", args)
        assert again.structured_content["execution_id"] == execution
        assert len(models) == len(writes) == 1
        assert writes[0][1] == guid
        assert (
            base.general_settings.global_axes_orientation
            == BaseData.GeneralSettings.GLOBAL_AXES_ORIENTATION_ZUP
        )
        calculated = await client.call_tool(
            "calculate_rfem_model", {"model_guid": guid, "execution_id": str(uuid4())}
        )
        assert calculated.structured_content["state"] == "succeeded"
        assert calculations == [guid]
        absent = await client.call_tool(
            "calculate_rfem_model",
            {"model_guid": str(uuid4()), "execution_id": str(uuid4())},
            raise_on_error=False,
        )
        assert absent.is_error
        assert calculations == [guid]


@pytest.mark.asyncio
async def test_axis_verification_failure_preserves_empty_model_without_writing_objects(
    tmp_path,
):
    rfem = pytest.importorskip("dlubal.api.rfem")
    from dlubal.api.common import ModelId

    models, writes = [], []

    class App:
        def create_model(self, *, name):
            model = ModelId(guid=str(uuid4()))
            models.append(model)
            return model

        def get_base_data(self, model_id=None):
            assert model_id.guid == models[0].guid
            base = rfem.BaseData()
            base.general_settings.global_axes_orientation = (
                rfem.BaseData.GeneralSettings.GLOBAL_AXES_ORIENTATION_ZDOWN
            )
            return base

        def set_base_data(self, *, base_data, model_id=None):
            assert model_id.guid == models[0].guid
            # Simulate an application that did not accept the requested axis.

        def create_object(self, obj, model_id=None):
            writes.append(obj)

    class Session:
        @contextmanager
        def connection(self):
            yield App()

    mcp = FastMCP("axis-verification")
    register_model_operations(
        mcp, Session(), ExecutionManager(tmp_path / "axis.sqlite3")
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "write_rfem_model",
            {
                "model_name": "axis-test",
                "z_axis": "up",
                "objects": [{"kind": "Node", "no": 1}],
                "execution_id": str(uuid4()),
            },
            raise_on_error=False,
        )
    assert result.is_error
    assert result.structured_content["state"] == "failed"
    assert result.structured_content["model_guid"] == models[0].guid
    assert "axis orientation" in result.structured_content["error"]
    assert writes == []
