"""Offline audit of actual native evidence; never starts or authorizes a Connector.

ArtifactRoot is a previously Host-verified common artifact. This test-specific
evidence bundle is not a runtime protocol, signature verifier or live driver.
Synthetic auditor tests do not establish real Rhino acceptance.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path


class LiveEvidenceError(ValueError):
    """The supplied artifact and native observations do not prove this case."""


def audit_live_evidence(artifact_root: Path, evidence: dict) -> dict:
    """Check the retained BOX mixed-approval case, not all 70 engineering tools."""
    try:
        _require(
            not artifact_root.is_symlink() and not artifact_root.is_junction(),
            "linkedArtifact",
        )
        root = artifact_root.resolve(strict=True)
        release = json.loads(_read(root / "release.json", 65536))
        inventory_bytes = _read(root / "files.json", 4 * 1024 * 1024)
        _require(
            _digest(inventory_bytes) == release["filesDigest"], "inventoryMismatch"
        )
        inventory = json.loads(inventory_bytes)
        for name, maximum in (
            ("plugin.json", 65536),
            ("tool-preview.json", 1024 * 1024),
        ):
            members = [item for item in inventory["files"] if item["path"] == name]
            _require(len(members) == 1, "metadataNotBound")
            data = _read(root / name, maximum)
            _require(
                len(data) == members[0]["byteLength"]
                and _digest(data) == members[0]["sha256"],
                "metadataMismatch",
            )
        identity = json.loads(_read(root / "plugin.json", 65536))
        _require(
            identity["name"] == release["packageId"] == "rhino"
            and identity["version"] == release["pluginVersion"],
            "identityMismatch",
        )
        for field in (
            "packageId",
            "pluginVersion",
            "releaseSequence",
            "connectorBuildId",
            "filesDigest",
            "toolCatalogDigest",
        ):
            _require(
                evidence["artifact"][field] == release[field],
                "evidenceArtifactMismatch",
            )
        _require(
            evidence["softwareBuild"] == "8.26.25349.19001", "softwareBuildMismatch"
        )
        read_attrs, summary, info = (
            "rhino__get_object_attributes",
            "rhino__get_document_summary",
            "rhino__get_object_info",
        )
        update, create = "rhino__update_object_attributes", "rhino__create_layer"
        before = _phase(evidence["before"], {read_attrs, summary})
        mutation = _phase(evidence["mutation"], {update, create}, denied={create})
        after = _phase(
            evidence["after"],
            {read_attrs, summary, info},
            optional={"rhino__describe_capabilities"},
        )
        original, observed = before[read_attrs][1], after[read_attrs][1]
        args = mutation[update][0]["args"]
        object_id = args["id"]
        _require(
            original["id"] == observed["id"] == object_id
            and mutation[update][1]["id"] == object_id,
            "objectMismatch",
        )
        _require(
            before[read_attrs][0]["args"]["id"] == object_id
            and after[read_attrs][0]["args"]["id"] == object_id
            and after[info][0]["args"]["id"] == object_id,
            "selectorMismatch",
        )
        _require(
            observed["name"] == original["name"]
            and observed["visible"] == original["visible"]
            and observed["locked"] == original["locked"],
            "unexpectedAttributeChange",
        )
        _require(
            observed["layer"]["full_path"] == args["layer"]
            and [observed["color"][key] for key in ("r", "g", "b")] == args["color"]
            and all(
                observed["user_strings"].get(k) == v
                for k, v in args["user_strings"].items()
            ),
            "attributeReadbackMismatch",
        )
        prior_doc, current_doc = before[summary][1], after[summary][1]
        _require(
            current_doc["object_count"] == prior_doc["object_count"] == 1
            and current_doc["layer_count"] == prior_doc["layer_count"]
            and current_doc["model_bounding_box"] == prior_doc["model_bounding_box"],
            "documentReadbackMismatch",
        )
        layers = _layers(current_doc["layer_hierarchy"])
        _require(
            mutation[create][0]["args"]["name"] not in layers
            and args["layer"] in layers
            and _layers(prior_doc["layer_hierarchy"]) <= layers,
            "layerReadbackMismatch",
        )
        geometry = after[info][1]
        box = geometry["geometry"]
        bounds = prior_doc["model_bounding_box"]
        dimensions = [bounds[1][axis] - bounds[0][axis] for axis in range(3)]
        x, y, z = dimensions
        _require(
            all(math.isfinite(d) and d > 0 for d in dimensions)
            and geometry["id"] == object_id
            and geometry["is_valid"] is True
            and box["is_solid"] is True
            and geometry["bounding_box"] == bounds
            and math.isclose(box["volume"], math.prod(dimensions), abs_tol=1e-6)
            and math.isclose(box["area"], 2 * (x * y + x * z + y * z), abs_tol=1e-6),
            "geometryReadbackMismatch",
        )
        capabilities = after.get("rhino__describe_capabilities")
        if capabilities:
            _require(
                capabilities[1]["version"] == release["pluginVersion"],
                "loadedVersionMismatch",
            )
        return {
            "proofLevel": "artifact-bound-evidence-only",
            "pluginVersion": release["pluginVersion"],
            "toolResults": sum(len(part) for part in (before, mutation, after)),
            "coverage": ["mixed-approval", "attribute-readback", "box-readback"],
            "limits": "Requires genuine native exports and prior Host artifact verification; not complete T33 acceptance.",
        }
    except LiveEvidenceError:
        raise
    except (KeyError, TypeError, ValueError, OSError, IndexError) as error:
        raise LiveEvidenceError("invalidEvidence") from error


def _require(condition, category):
    if not condition:
        raise LiveEvidenceError(category)


def _digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read(path, maximum):
    _require(not path.is_symlink() and not path.is_junction(), "linkedMetadata")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    _require(0 < len(data) <= maximum, "invalidEvidenceSize")
    return data


def _content(message):
    value = message["content"]
    if isinstance(value, list):
        value = "".join(block["text"] for block in value if block.get("type") == "text")
    _require(
        isinstance(value, str) and 0 < len(value) <= 1024 * 1024, "invalidToolResult"
    )
    return value


def _phase(phase, required, *, denied=frozenset(), optional=frozenset()):
    _require(
        phase["checkpoint"]["checkpoint_id"]
        and phase["next"] == []
        and phase["tasks"] == [],
        "nonterminalEvidence",
    )
    calls, messages = phase["calls"], phase["tools"]
    _require(
        len(calls) == len(messages)
        and len({call["id"] for call in calls}) == len(calls)
        and len({call["name"] for call in calls}) == len(calls),
        "missingOrRepeatedCall",
    )
    names = {call["name"] for call in calls}
    _require(required <= names <= required | optional, "unexpectedTool")
    results = {}
    for call in calls:
        matches = [
            message for message in messages if message["tool_call_id"] == call["id"]
        ]
        _require(
            len(matches) == 1 and matches[0]["name"] == call["name"],
            "toolIdentityMismatch",
        )
        message = matches[0]
        text = _content(message)
        if call["name"] in denied:
            _require(
                message["status"] == "error"
                and text.startswith("User rejected the tool call")
                and call["id"] in text
                and "The tool was not executed." in text,
                "missingRejectionReceipt",
            )
            value = None
        else:
            _require(message["status"] == "success", "unsuccessfulToolResult")
            value = json.loads(text)
            _require(isinstance(value, dict), "invalidToolResult")
        results[call["name"]] = (call, value)
    return results


def _layers(nodes, depth=0):
    _require(
        isinstance(nodes, list) and len(nodes) <= 128 and depth <= 8,
        "invalidLayerEvidence",
    )
    result = set()
    for node in nodes:
        result.add(node["full_path"])
        result.update(_layers(node.get("children", []), depth + 1))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--evidence-path", type=Path, required=True)
    options = parser.parse_args()
    try:
        evidence = json.loads(_read(options.evidence_path, 8 * 1024 * 1024))
        print(json.dumps(audit_live_evidence(options.artifact_root, evidence)))
    except LiveEvidenceError as error:
        parser.exit(1, f"nativeEvidenceRejected:{error}\n")
    except (ValueError, OSError):
        parser.exit(1, "nativeEvidenceRejected:invalidEvidence\n")
