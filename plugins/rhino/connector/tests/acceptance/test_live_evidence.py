"""Synthetic negative checks for the offline auditor, not Rhino engineering."""

import copy
import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


API = runpy.run_path(
    str(Path(__file__).parents[1] / "live/test_unified_rhino_contract.py")
)
audit = API["audit_live_evidence"]
EvidenceError = API["LiveEvidenceError"]
OBJECT = "00000000-0000-4000-8000-000000000001"


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def phase(pairs):
    calls, tools = [], []
    for index, (name, args, result, status) in enumerate(pairs):
        call_id = f"{name}-{index}"
        calls.append({"id": call_id, "name": name, "args": args})
        content = (
            json.dumps(result)
            if status == "success"
            else (
                f"User rejected the tool call for `{name}` with id {call_id}. "
                "The tool was not executed. Do not retry."
            )
        )
        tools.append(
            {
                "name": name,
                "tool_call_id": call_id,
                "status": status,
                "content": content,
            }
        )
    return {
        "checkpoint": {"checkpoint_id": "actual-checkpoint"},
        "next": [],
        "tasks": [],
        "calls": calls,
        "tools": tools,
    }


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "verified-artifact"
    root.mkdir()
    plugin = {"name": "rhino", "version": "0.1.3"}
    (root / "plugin.json").write_text(json.dumps(plugin), encoding="utf-8")
    (root / "tool-preview.json").write_text('{"tools": []}', encoding="utf-8")
    inventory = {
        "files": [
            {
                "path": name,
                "byteLength": len((root / name).read_bytes()),
                "sha256": digest((root / name).read_bytes()),
            }
            for name in ("plugin.json", "tool-preview.json")
        ]
    }
    (root / "files.json").write_text(json.dumps(inventory), encoding="utf-8")
    release = {
        "packageId": "rhino",
        "pluginVersion": "0.1.3",
        "releaseSequence": 2,
        "connectorBuildId": "sha256:" + "1" * 64,
        "toolCatalogDigest": "sha256:" + "2" * 64,
        "filesDigest": digest((root / "files.json").read_bytes()),
    }
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    attrs = {
        "id": OBJECT,
        "name": "BOX-test",
        "layer": {"id": "default", "full_path": "Default"},
        "color": {"r": 25, "g": 100, "b": 180},
        "user_strings": {},
        "visible": True,
        "locked": False,
    }
    summary = {
        "object_count": 1,
        "layer_count": 2,
        "layer_hierarchy": [
            {"id": "default", "full_path": "Default"},
            {"id": "test", "full_path": "Test"},
        ],
        "model_bounding_box": [[0, 5, 10], [20, 35, 50]],
    }
    changed = copy.deepcopy(attrs)
    changed.update(
        layer={"id": "test", "full_path": "Test"},
        color={"r": 80, "g": 120, "b": 200},
        user_strings={"case": "native"},
    )
    args = {
        "id": OBJECT,
        "layer": "Test",
        "color": [80, 120, 200],
        "user_strings": {"case": "native"},
    }
    info = {
        "id": OBJECT,
        "is_valid": True,
        "geometry": {"is_solid": True, "volume": 24000, "area": 5200},
        "bounding_box": summary["model_bounding_box"],
    }
    evidence = {
        "softwareBuild": "8.26.25349.19001",
        "artifact": copy.deepcopy(release),
        "before": phase(
            [
                ("rhino__get_object_attributes", {"id": OBJECT}, attrs, "success"),
                ("rhino__get_document_summary", {}, summary, "success"),
            ]
        ),
        "mutation": phase(
            [
                ("rhino__update_object_attributes", args, changed, "success"),
                ("rhino__create_layer", {"name": "DENIED"}, None, "error"),
            ]
        ),
        "after": phase(
            [
                ("rhino__get_object_attributes", {"id": OBJECT}, changed, "success"),
                ("rhino__get_document_summary", {}, summary, "success"),
                ("rhino__get_object_info", {"id": OBJECT}, info, "success"),
            ]
        ),
    }
    return root, evidence


def test_complete_case_is_an_evidence_audit_only(case):
    root, evidence = case
    assert audit(root, evidence)["proofLevel"] == "artifact-bound-evidence-only"


@pytest.mark.parametrize(
    "failure",
    [
        "build",
        "software",
        "nonterminal",
        "missing_denial",
        "unknown",
        "replay",
        "wrong_color",
        "denied_layer_exists",
        "wrong_volume",
        "tampered_root",
    ],
)
def test_incomplete_or_inconsistent_evidence_is_rejected(case, failure):
    root, evidence = case
    if failure == "build":
        evidence["artifact"]["connectorBuildId"] = "sha256:" + "3" * 64
    elif failure == "software":
        evidence["softwareBuild"] = "8.99.0"
    elif failure == "nonterminal":
        evidence["after"]["next"] = ["tools"]
    elif failure == "missing_denial":
        evidence["mutation"]["tools"].pop()
    elif failure == "unknown":
        evidence["mutation"]["tools"][0]["status"] = "unknown"
    elif failure == "replay":
        evidence["mutation"]["calls"].append(
            copy.deepcopy(evidence["mutation"]["calls"][0])
        )
    elif failure == "wrong_color":
        value = json.loads(evidence["after"]["tools"][0]["content"])
        value["color"]["r"] = 0
        evidence["after"]["tools"][0]["content"] = json.dumps(value)
    elif failure == "denied_layer_exists":
        value = json.loads(evidence["after"]["tools"][1]["content"])
        value["layer_hierarchy"].append({"id": "denied", "full_path": "DENIED"})
        evidence["after"]["tools"][1]["content"] = json.dumps(value)
    elif failure == "wrong_volume":
        value = json.loads(evidence["after"]["tools"][2]["content"])
        value["geometry"]["volume"] = 0
        evidence["after"]["tools"][2]["content"] = json.dumps(value)
    elif failure == "tampered_root":
        (root / "plugin.json").write_text('{"name":"wrong","version":"0.1.3"}')
    with pytest.raises(EvidenceError):
        audit(root, evidence)


@pytest.mark.parametrize("kind", ["invalid_json", "missing_file"])
def test_cli_rejects_bad_input_without_traceback_or_private_path(case, tmp_path, kind):
    root, _ = case
    path = tmp_path / "private-evidence-marker.json"
    if kind == "invalid_json":
        path.write_text("private-evidence-marker", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).parents[1] / "live/test_unified_rhino_contract.py"),
            "--artifact-root",
            str(root),
            "--evidence-path",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "nativeEvidenceRejected:invalidEvidence\n"
