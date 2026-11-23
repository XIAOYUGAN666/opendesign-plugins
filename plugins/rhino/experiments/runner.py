"""Windows evaluation against an explicitly supplied Rhino Bridge and saved evidence."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import jsonschema
from experiments.bridge import assert_document, script, use_bridge
from experiments.evaluator import evaluate

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluator_versions():
    return {
        name: sha256(ROOT / "experiments" / name)
        for name in (
            "evaluate.cs",
            "evaluator.py",
            "tasks/schema.json",
            "strip_task.py",
            "strip_measure.cs",
            "panel_task.py",
            "panel_measure.cs",
            "trimmed_task.py",
            "trimmed_measure.cs",
            "strip_probe.py",
            "gh_task.py",
            "bridge.py",
            "tasks/gh_definition.schema.json",
            "workflow/gh-reviewed-components.json",
            "workflow/gh_ownership.py",
            "scene_task.py",
            "scene_measure.cs",
            "tasks/workflow_scene.schema.json",
        )
    }


def schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def load_task(path):
    task = json.loads(path.read_text(encoding="utf-8"))
    if task.get("type") == "gh_definition":
        from experiments.gh_task import validate

        return validate(task)
    if task.get("type") == "workflow_scene":
        from experiments.scene_task import validate

        return validate(task)
    jsonschema.validate(
        task, json.loads((ROOT / "experiments/tasks/schema.json").read_text(encoding="utf-8"))
    )
    numbers = (
        task["dimensions"]
        + task.get("minimum", task.get("translation", []))
        + [task["linear_tolerance"], task["volume_tolerance"]]
        + [task.get("rotation_z_degrees", 0), task.get("area_tolerance", 0)]
        + task.get("hole_center", [])
        + [
            task.get("hole_radius", 0),
            task.get("panel_height", 0),
            task.get("rotation_x_degrees", 0),
            task.get("shape_tolerance", 0),
        ]
    )
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError("Task numbers must be finite")
    if not math.isfinite(math.prod(task["dimensions"])):
        raise ValueError("Task volume exceeds numeric range")
    if task["type"] == "box_through_hole":
        for center, minimum, size in zip(
            task["hole_center"], task["minimum"], task["dimensions"]
        ):
            margin = task["hole_radius"] + task["linear_tolerance"]
            if not minimum + margin < center < minimum + size - margin:
                raise ValueError("Hole must lie strictly inside the block's XY bounds")
    if task["type"] == "trimmed_planar_patch":
        for center, size in zip(task["hole_center"], task["dimensions"][:2]):
            margin = task["hole_radius"] + task["linear_tolerance"]
            if not margin < center < size - margin:
                raise ValueError("Opening must lie strictly inside the local patch")
    return task


def save_candidate(path, serial, marker):
    assert_document(serial, marker)
    script(f"""
if (!doc.WriteFile({json.dumps(str(path))}, new Rhino.FileIO.FileWriteOptions()))
    throw new Exception("Candidate save failed");
""")
    return sha256(path)


def measure(path, task=None):
    if task and task["type"] == "workflow_scene":
        from experiments.scene_task import measure as measure_scene

        return measure_scene(path, task)
    if task and task["type"] == "trimmed_planar_patch":
        from experiments.trimmed_task import measure as measure_trimmed

        return measure_trimmed(path, task)

    if task and task["type"] == "biquadratic_panel":
        from experiments.panel_task import measure as measure_panel

        return measure_panel(path, task)

    if task and task["type"] == "quarter_annular_strip":
        from experiments.strip_task import measure as measure_strip

        return measure_strip(path, task)
    before = sha256(path)
    code = (
        (ROOT / "experiments/evaluate.cs")
        .read_text(encoding="utf-8")
        .replace("ARTIFACT_PATH", json.dumps(str(path)))
    )
    result = json.loads(script(code))
    if sha256(path) != before:
        raise RuntimeError("Artifact changed during evaluation")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True, help="Saved 3dm or native GH evidence JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bridge-port", type=int, help="Dedicated Windows Rhino instance port for saved 3dm evaluation")
    args = parser.parse_args()
    task = load_task(args.task)
    source = args.input.resolve(strict=True)
    before = sha256(source)
    if task["type"] == "gh_definition":
        sample = json.loads(source.read_text(encoding="utf-8"))
    else:
        if not args.bridge_port or source.suffix.lower() != ".3dm":
            parser.error("Saved model evaluation requires a test Bridge and a .3dm file")
        with use_bridge(args.bridge_port):
            sample = measure(source, task)
    if sha256(source) != before:
        raise RuntimeError("Test artifact changed during evaluation")
    result = evaluate(task, sample)
    save(args.output, {
        "taskSha256": sha256(args.task), "artifactSha256": before,
        "evaluators": evaluator_versions(), "observations": sample, "evaluation": result,
    })
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
