import json
from pathlib import Path
import pytest
from experiments.gh_task import validate, matches, evaluate, snapshot
from experiments.runner import load_task
from experiments.workflow.validate_gh import variants

ROOT = Path(__file__).resolve().parents[2]


def test_live_snapshot_checks_document_ownership_between_native_reads(monkeypatch):
    from experiments.workflow import gh_ownership

    owner = (42, "fixture", "test-definition")
    calls = []
    monkeypatch.setattr(gh_ownership, "guard", lambda *args: calls.append(args))

    def inspect(name, params):
        calls.append(name)
        return {"command": name}

    monkeypatch.setattr(gh_ownership, "command", inspect)
    task = load_task(ROOT / "experiments/tasks/gh_point_array.json")

    saved = snapshot(task, *owner)

    commands = [
        "gh_run_solution",
        "gh_get_document_info",
        "gh_get_graph",
        "gh_get_canvas_state",
        "gh_get_parameter_value",
    ]
    assert calls == [event for command in commands for event in (owner, command, owner)]
    assert saved["outputs"]["result0"] == {"command": "gh_get_parameter_value"}


def test_live_guard_rejects_a_different_grasshopper_document(monkeypatch):
    from experiments.workflow import gh_ownership

    monkeypatch.setattr(gh_ownership, "assert_document", lambda *args: {"object_count": 0})
    monkeypatch.setattr(
        gh_ownership, "state", lambda: {"count": 1, "id": "other", "objects": 0}
    )

    with pytest.raises(RuntimeError, match="document changed"):
        gh_ownership.guard(42, "fixture", "expected")


@pytest.mark.parametrize("name", ["point_array", "circle_profiles", "loft_sections"])
def test_declared_task(name):
    assert (
        load_task(ROOT / f"experiments/tasks/gh_{name}.json")["type"] == "gh_definition"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_finite_targets(value):
    task = load_task(ROOT / "experiments/tasks/gh_point_array.json")
    task["outputs"][0]["values"][0][0] = value
    with pytest.raises(ValueError):
        validate(task)


def test_values_strict_and_tolerant():
    assert matches([1, {"radius": 2.001}], [1, {"radius": 2}], 0.01)
    assert not matches([True], [1], 0.01)
    assert not matches([1, 2], [1], 0.01)
    assert not matches(float("nan"), 1, 0.01)






def test_missing_artifact_fails_without_agent_claim():
    task = load_task(ROOT / "experiments/tasks/gh_point_array.json")
    report = evaluate(task, {"complete": True})
    assert report["status"] == "fail"








@pytest.mark.parametrize("key", ["point_array", "circle_profiles", "loft_sections"])
def test_live_snapshot_calibration_variants(key):
    task = load_task(ROOT / f"experiments/tasks/gh_{key}.json")
    good = json.loads((ROOT / f"experiments/tests/fixtures/gh/{key}.json").read_text())
    for name, snapshot, expected in variants(good):
        assert (evaluate(task, snapshot)["status"] == "pass") == expected, name
