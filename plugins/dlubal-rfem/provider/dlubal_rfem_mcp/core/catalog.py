"""Canonical catalog normalization shared by runtime and release generation."""

import hashlib
import json

NORMALIZATION_VERSION = 1
CONTRACT_FIELDS = ("name", "description", "inputSchema", "outputSchema", "annotations")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def normalize_tools(tools: list[dict]) -> list[dict]:
    names = [tool["name"] for tool in tools]
    if len(set(names)) != len(names):
        raise ValueError("duplicateToolIdentity")
    return [
        {key: tool[key] for key in CONTRACT_FIELDS if key in tool}
        for tool in sorted(tools, key=lambda item: item["name"])
    ]


def catalog_digest(tools: list[dict]) -> str:
    payload = {
        "normalizationVersion": NORMALIZATION_VERSION,
        "tools": normalize_tools(tools),
    }
    return "sha256:" + hashlib.sha256(canonical_json(payload)).hexdigest()
