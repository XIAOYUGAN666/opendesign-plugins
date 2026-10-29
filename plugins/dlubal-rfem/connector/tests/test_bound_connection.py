import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dlubal_rfem_mcp.rfem.execution import ExecutionUncertain
from dlubal_rfem_mcp.rfem.model_binding import bound_connection


def test_journal_failure_after_model_creation_remains_unknown():
    created, closed = [], []

    class App:
        def create_model(self, **kwargs):
            model = SimpleNamespace(guid=str(uuid4()))
            created.append(model)
            return model

    class Session:
        @contextmanager
        def connection(self):
            try:
                yield App()
            finally:
                closed.append(True)

    def record(_guid):
        raise sqlite3.OperationalError("journal write failed")

    with (
        pytest.raises(ExecutionUncertain, match="journal failed"),
        bound_connection(Session(), None, None, record) as bound,
    ):
        bound.create_model(name="test")
    assert len(created) == len(closed) == 1


def test_connection_failure_before_admission_does_not_claim_a_mutation():
    class Unavailable(Exception):
        def code(self):
            return "StatusCode.UNAVAILABLE"

    class Session:
        @contextmanager
        def connection(self):
            raise Unavailable("connection failed")
            yield

    with (
        pytest.raises(RuntimeError, match="connection failed") as error,
        bound_connection(Session(), None, None, lambda _: None),
    ):
        pytest.fail("unavailable connection reached the worker")
    assert not hasattr(error.value, "code")
