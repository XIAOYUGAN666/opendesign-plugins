from types import SimpleNamespace
from uuid import uuid4

import pytest

from dlubal_mcp.rfem.execution import ExecutionManager
from dlubal_mcp.rfem.model_binding import BoundApplication


class BusyError(Exception):
    def __init__(self, status):
        super().__init__("Another client is already connected to the server.")
        self.status = status

    def code(self):
        return "StatusCode." + self.status


@pytest.mark.parametrize("status", ["RESOURCE_EXHAUSTED", "UNAVAILABLE"])
@pytest.mark.parametrize("prior_write", [False, True])
def test_busy_write_never_advises_replay_or_hides_partial_changes(
    tmp_path, status, prior_write
):
    manager = ExecutionManager(tmp_path / "state.db")
    guid = str(uuid4())
    writes = []

    class App:
        def get_model_list(self):
            return SimpleNamespace(model_info=[SimpleNamespace(guid=guid)])

        def create_object(self, obj, model_id=None):
            assert model_id.guid == guid
            writes.append(obj)
            if obj == "busy":
                raise BusyError(status)

    def work(capture, record):
        app = BoundApplication(
            App(), SimpleNamespace(ModelId=SimpleNamespace), guid, record
        )
        if prior_write:
            app.create_object("first")
            capture.print("first object created")
        try:
            app.create_object("busy")
        except BusyError:
            pass
        app._check_write_outcome()

    execution_id = str(uuid4())
    result = manager.submit(execution_id, "chat", "code", work, wait_seconds=5)
    assert result["state"] == ("unknown" if status == "UNAVAILABLE" else "failed")
    assert "Nothing was executed" not in result["error"]
    assert "run the script again" not in result["error"]
    assert "Do not replay" in result["error"]
    assert "Inspect the model" in result["error"]
    assert "Another client is already connected" in result["error"]
    assert result["model_guid"] == guid
    assert result["output"] == ("first object created\n" if prior_write else "")
    assert (
        manager.submit(execution_id, "chat", "code", work)["error"] == result["error"]
    )
    assert writes == (["first", "busy"] if prior_write else ["busy"])


def test_uncertain_busy_error_does_not_recommend_retry_without_a_write_record(tmp_path):
    manager = ExecutionManager(tmp_path / "state.db")

    def work(*_):
        raise BusyError("UNAVAILABLE")

    result = manager.submit(str(uuid4()), "chat", "code", work, wait_seconds=5)
    assert result["state"] == "unknown"
    assert "Do not replay" in result["error"]
    assert "Nothing was executed" not in result["error"]
    assert (
        manager.submit(str(uuid4()), "chat", "next", lambda *_: None)["state"] == "busy"
    )
