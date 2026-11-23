import json
from pathlib import Path
import pytest
from experiments.gh_task import validate, matches, evaluate
from experiments.runner import load_task
from experiments.workflow.validate_gh import variants

ROOT = Path(__file__).resolve().parents[2]


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
