import pytest
from pathlib import Path
from scripts.build_fixture import build_fixture


def test_builder_outputs_real_embedded_interpreter_and_frozen_inventory(tmp_path):
    import json

    root = Path(__file__).resolve().parents[2]
    staged = build_fixture(root, tmp_path / "build")
    assert (staged / "runtime/python/python.exe").is_file()
    locked = json.loads((staged / "runtime-lock.json").read_text())
    assert locked["python"]["filename"] == "python-3.12.10-embed-amd64.zip"
    assert {x["name"]: x["version"] for x in locked["wheels"]}["fastmcp"] == "4.0.3"
    assert (staged / "startup-envelope.schema.json").is_file()
    assert (
        json.loads((staged / "plugin.json").read_text())["name"]
        == "fixture-engineering"
    )
    configuration = json.loads((staged / "configuration.schema.json").read_text())
    from fixture_engineering.managed_bootstrap import ConfigurationV1
    assert configuration == ConfigurationV1.model_json_schema()
    assert configuration.get("title") == "ConfigurationV1", "configuration must come from the typed source"
    lifecycle_path = staged / "lifecycle.schema.json"
    lifecycle = json.loads(lifecycle_path.read_text()) if lifecycle_path.exists() else {}
    from fixture_engineering.managed_bootstrap import ConnectorExecutionStatusV1
    from fixture_engineering._connector_control import lifecycle_schema
    from jsonschema import validate, ValidationError
    assert lifecycle == lifecycle_schema(ConnectorExecutionStatusV1.model_json_schema())
    binding = {"packageId":"fixture-engineering","releaseDigest":"sha256:"+"b"*64,"connectorInstanceId":"ci_"+"1"*32,"generation":1,"configurationRevision":1,"secretRevision":0}
    ready = {"schemaVersion":3,"kind":"ready","binding":binding,"connectorBuildId":"sha256:"+"a"*64,"port":8123}
    status = {"schemaVersion":1,"kind":"status","connectorInstanceId":"ci_"+"1"*32,"generation":1,"sequence":1,"state":"unknown","inFlight":0}
    validate(ready,lifecycle)
    validate(status,lifecycle)
    for invalid in ({**status,"sequence":0},{**status,"state":"fabricated"},{**ready,"port":80},{**status,"secret":"forbidden"}):
        with pytest.raises(ValidationError): validate(invalid,lifecycle)
    preview_schema_path = staged / "tool-preview.schema.json"
    preview_schema = json.loads(preview_schema_path.read_text()) if preview_schema_path.exists() else {}
    assert preview_schema.get("properties", {}).get("normalizationVersion", {}).get("const") == 1, "preview normalization must be locked by its typed contract"
    risk_path = staged / "risk-review.json"
    risk = json.loads(risk_path.read_text()) if risk_path.exists() else {}
    assert risk.get("status") == "pending-review/internal-fixture-only", "source and tool declarations must be bound without claiming completed review"
    sbom = json.loads((staged / "sbom.spdx.json").read_text())
    names = {package["name"] for package in sbom["packages"]}
    assert "python" in names, "the shipped interpreter must be in the SBOM"
    assert "fixture-engineering" in names, "the shipped first-party Connector must be in the SBOM"
