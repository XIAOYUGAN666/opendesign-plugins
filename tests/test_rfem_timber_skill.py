from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "plugins" / "rfem-timber-engineering" / "skills" / "rfem-timber-engineering"
SKILL_MD = SKILL_ROOT / "SKILL.md"
CASES_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "rfem_timber_skill_cases.json"
REFERENCE_NAMES = {
    "task-routing.md",
    "standards-evidence.md",
    "rfem-execution.md",
    "validation-and-reporting.md",
}
ALLOWED_BEHAVIORS = {
    "task_contract",
    "knowledge_evidence",
    "evidence_gate",
    "api_inspection",
    "staged_execution",
    "model_protection",
    "partial_state_inspection",
    "result_validation",
    "timber_design_validation",
    "optimization_contract",
}


def parse_skill() -> tuple[dict[str, object], str]:
    text = SKILL_MD.read_text(encoding="utf-8")
    match = re.fullmatch(
        r"---\n(?P<frontmatter>.*?)\n---\n(?P<body>.*)",
        text,
        re.DOTALL,
    )
    assert match is not None
    metadata = yaml.safe_load(match.group("frontmatter"))
    assert isinstance(metadata, dict)
    return metadata, match.group("body")


def load_cases() -> list[dict[str, object]]:
    loaded = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def test_rfem_timber_skill_uses_agent_skills_layout() -> None:
    assert SKILL_MD.is_file()
    references = SKILL_ROOT / "references"
    assert references.is_dir()
    assert {path.name for path in references.iterdir() if path.is_file()} == REFERENCE_NAMES
    assert not (SKILL_ROOT / "scripts").exists()
    assert not (SKILL_ROOT / "assets").exists()
    assert not (SKILL_ROOT / "agents").exists()
    assert not (SKILL_ROOT / "README.md").exists()


def test_rfem_timber_skill_frontmatter_is_specific_and_bounded() -> None:
    metadata, body = parse_skill()

    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "rfem-timber-engineering"
    description = str(metadata["description"])
    assert 1 <= len(description) <= 1024
    lowered = description.lower()
    assert "rfem" in lowered
    assert any(word in lowered for word in ("timber", "wood", "glulam", "clt"))
    assert "do not use" in lowered
    assert "no rfem goal" in lowered
    assert len(body.splitlines()) < 500


def test_rfem_timber_skill_routes_progressive_references() -> None:
    _, body = parse_skill()

    for reference_name in REFERENCE_NAMES:
        assert f"references/{reference_name}" in body


def test_rfem_timber_skill_contains_required_behavioral_contracts() -> None:
    texts = {
        path.name: path.read_text(encoding="utf-8")
        for path in [SKILL_MD, *(SKILL_ROOT / "references").glob("*.md")]
    }
    combined = "\n".join(texts.values()).lower()

    required_terms = (
        "knowledge-researcher",
        "health_check",
        "get_rfem_application_info",
        "write_rfem_model",
        "calculate_rfem_model",
        "get_rfem_execution",
        "read_rfem_results",
        "connector-prefixed",
        "inspect_rfem_api",
        "run_rfem_script",
        "evidence ledger",
        "preliminary",
        "approve",
        "reject",
        "partial",
        "timber design",
        "optimization",
    )
    for term in required_terms:
        assert term in combined
    assert "knowledge-graph" not in combined
    assert "neo4j" not in combined

    for standard in (
        "GB 50005-2017",
        "GB 50009-2012",
        "GB 55001-2021",
        "GB 55002-2021",
        "GB/T 50708-2012",
    ):
        assert standard.lower() in combined


def test_rfem_timber_skill_does_not_embed_acceptance_model_or_api_schema() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [SKILL_MD, *(SKILL_ROOT / "references").glob("*.md")]
    )

    for forbidden in ("12 m", "4.5 m", "S235", "IPE 240", "definition_nodes=[1, 2]"):
        assert forbidden not in combined


def test_trigger_evaluation_corpus_has_development_and_holdout_coverage() -> None:
    cases = load_cases()

    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert Counter(case["split"] for case in cases) == {
        "development": 14,
        "holdout": 6,
    }
    assert Counter(case["should_trigger"] for case in cases) == {
        True: 12,
        False: 8,
    }
    assert {
        case["id"]
        for case in cases
        if case["split"] == "holdout" and case["should_trigger"]
    } == {
        "holdout-positive-truss",
        "holdout-positive-arch",
        "holdout-positive-clt",
        "holdout-positive-existing-audit",
    }
    for case in cases:
        assert isinstance(case["prompt"], str) and case["prompt"].strip()
        behaviors = set(case["required_behaviors"])
        assert behaviors <= ALLOWED_BEHAVIORS
        if not case["should_trigger"]:
            assert behaviors == set()
