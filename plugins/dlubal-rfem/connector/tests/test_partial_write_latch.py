from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dlubal_mcp.rfem.execution import ExecutionManager
from dlubal_mcp.rfem.model_binding import BoundApplication, bound_connection


class App:
    def __init__(self):
        self.guid = str(uuid4())
        self.writes = []
        self.failure = ValueError("partial write")

    def get_model_list(self):
        return SimpleNamespace(model_info=[SimpleNamespace(guid=self.guid)])

    def get_base_data(self, model_id=None):
        return model_id.guid

    def create_object(self, obj, model_id=None):
        self.writes.append(obj)
        if obj == "fail":
            raise self.failure

    def create_model(self):
        self.writes.append("create")
        return SimpleNamespace(guid=self.guid)

    def open_model(self):
        self.writes.append("open")
        return SimpleNamespace(guid=self.guid)


COMMON = SimpleNamespace(ModelId=SimpleNamespace, OptionalModelId=SimpleNamespace)


def test_caught_write_error_blocks_repairs_and_lifecycle_but_allows_reads():
    app = App()
    bound = BoundApplication(app, COMMON, app.guid, lambda _: None)
    with pytest.raises(ValueError) as first:
        bound.create_object("fail")
    assert bound.get_base_data() == app.guid
    for operation in (
        lambda: bound.create_object("repair"),
        bound.create_model,
        bound.open_model,
    ):
        with pytest.raises(ValueError) as blocked:
            operation()
        assert blocked.value is first.value
    assert app.writes == ["fail"]
    # The latch belongs to this execution, not every future authorized request.
    BoundApplication(app, COMMON, app.guid, lambda _: None).create_object("new request")
    assert app.writes == ["fail", "new request"]


def test_read_failure_does_not_prevent_a_later_write():
    app = App()

    def fail_read(model_id=None):
        raise ValueError("read failed")

    app.get_base_data = fail_read
    bound = BoundApplication(app, COMMON, app.guid, lambda _: None)
    with pytest.raises(ValueError, match="read failed"):
        bound.get_base_data()
    bound.create_object("write")
    assert app.writes == ["write"]


def test_managed_connection_cannot_report_success_after_swallowed_write_error():
    app = App()
    closed = []

    class Session:
        @contextmanager
        def connection(self):
            try:
                yield app
            finally:
                closed.append(True)

    with (
        pytest.raises(ValueError, match="partial write"),
        bound_connection(Session(), COMMON, app.guid, lambda _: None) as bound,
    ):
        try:
            bound.create_object("fail")
        except ValueError:
            pass
        assert bound.get_base_data() == app.guid
    assert closed == [True]


def test_failed_lifecycle_call_also_latches():
    app = App()

    def fail_open():
        app.writes.append("open failed")
        raise app.failure

    app.open_model = fail_open
    bound = BoundApplication(app, COMMON, app.guid, lambda _: None)
    with pytest.raises(ValueError):
        bound.open_model()
    with pytest.raises(ValueError):
        bound.create_object("repair")
    assert app.writes == ["open failed"]


@pytest.mark.parametrize("prior_write", [False, True])
@pytest.mark.parametrize("failure", ["foreign_model", "invalid_arguments"])
def test_rejected_write_latches_without_recording_an_sdk_mutation(prior_write, failure):
    app = App()
    mutations = []

    class Recorder:
        def __call__(self, guid):
            pass

        def mutated(self):
            mutations.append(True)

    bound = BoundApplication(app, COMMON, app.guid, Recorder())
    if prior_write:
        bound.create_object("first")
    with pytest.raises((RuntimeError, TypeError)) as first:
        if failure == "foreign_model":
            bound.create_object(
                "wrong model", model_id=SimpleNamespace(guid=str(uuid4()))
            )
        else:
            bound.create_object("invalid", unexpected=True)
    assert bound.get_base_data() == app.guid
    with pytest.raises(type(first.value)) as blocked:
        bound.create_object("repair")
    assert blocked.value is first.value
    with pytest.raises(type(first.value)) as final:
        bound._check_write_outcome()
    assert final.value is first.value
    assert app.writes == (["first"] if prior_write else [])
    assert mutations == ([True] if prior_write else [])


def test_rejected_read_target_does_not_latch_writes():
    app = App()
    bound = BoundApplication(app, COMMON, app.guid, lambda _: None)
    with pytest.raises(RuntimeError, match="different model"):
        bound.get_base_data(model_id=SimpleNamespace(guid=str(uuid4())))
    bound.create_object("authorized model")
    assert app.writes == ["authorized model"]


def test_swallowed_target_rejection_finishes_as_failed_execution(tmp_path):
    app = App()

    class Session:
        @contextmanager
        def connection(self):
            yield app

    def work(capture, record):
        with bound_connection(Session(), COMMON, app.guid, record) as bound:
            try:
                bound.create_object(
                    "foreign", model_id=SimpleNamespace(guid=str(uuid4()))
                )
            except RuntimeError:
                capture.print("caught")

    manager = ExecutionManager(tmp_path / "state.db")
    result = manager.submit(str(uuid4()), "chat", "code", work, wait_seconds=5)
    assert result["state"] == "failed"
    assert "different model" in result["error"]
    assert "caught" in result["output"]
    assert app.writes == []
