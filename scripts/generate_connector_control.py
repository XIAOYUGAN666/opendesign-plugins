"""Generate both connector codecs from the Host-owned schema bytes."""
import argparse
import hashlib
import json
from pathlib import Path

TEMPLATE = Path(__file__).parent / "templates" / "connector_control.py.tmpl"


def validate_references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef"} and (not isinstance(item, str) or not item.startswith("#/")):
                raise ValueError("Only local schema references are allowed")
            validate_references(item)
    elif isinstance(value, list):
        for item in value:
            validate_references(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema", type=Path)
    parser.add_argument("--bootstrap-schema", type=Path, required=True)
    parser.add_argument("--ready-schema", type=Path, required=True)
    parser.add_argument("outputs", type=Path, nargs="+")
    args = parser.parse_args()
    schema_bytes = args.schema.read_bytes()
    schema_text = schema_bytes.decode("utf-8-sig")
    schema = json.loads(schema_text)
    if schema.get("title") != "ConnectorWireContractV1":
        raise ValueError("Unexpected Host schema")
    validate_references(schema)
    bootstrap_bytes = args.bootstrap_schema.read_bytes()
    bootstrap_text = bootstrap_bytes.decode("utf-8-sig")
    bootstrap = json.loads(bootstrap_text)
    if bootstrap.get("title") != "ConnectorBootstrapV3":
        raise ValueError("Unexpected Host bootstrap schema")
    validate_references(bootstrap)
    ready_bytes = args.ready_schema.read_bytes()
    ready_text = ready_bytes.decode("utf-8-sig")
    ready = json.loads(ready_text)
    if ready.get("title") != "ConnectorReadyV3":
        raise ValueError("Unexpected Host ready schema")
    validate_references(ready)
    content = TEMPLATE.read_text(encoding="utf-8").replace("__SCHEMA_DIGEST__", repr(hashlib.sha256(schema_bytes).hexdigest())).replace("__SCHEMA_JSON__", repr(schema_text)).replace("__BOOTSTRAP_DIGEST__", repr(hashlib.sha256(bootstrap_bytes).hexdigest())).replace("__BOOTSTRAP_JSON__", repr(bootstrap_text)).replace("__READY_DIGEST__", repr(hashlib.sha256(ready_bytes).hexdigest())).replace("__READY_JSON__", repr(ready_text))
    for output in args.outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
