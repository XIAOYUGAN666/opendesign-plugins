"""Import exported owner contracts as original bytes, never runtime code."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def import_contracts(
    host_schema: Path,
    agent_schema: Path,
    destination: Path,
    *,
    host_contract_schema: Path | None = None,
    configuration_schema: Path | None = None,
    official_schema: Path | None = None,
    bridge_control_schema: Path | None = None,
    publisher_policy: Path | None = None,
) -> dict[str, str]:
    """Copy schema bytes and generate an owner/version/hash-only inventory.

    Source arguments are exported public files, not private application state.
    The original three positional arguments remain supported.
    """
    host_root = "apps/desktop/src-tauri/resources/plugins/schemas/"
    inputs = [
        ("host-native.schema.json", host_schema, "host", host_root + "native-extension-v1.schema.json"),
        ("bridge-control-v2.schema.json", bridge_control_schema, "host", host_root + "bridge-control-v2.schema.json"),
        ("agent-control.schema.json", agent_schema, "agent", "apps/backend/agent-server/schema/plugin-runtime.schema.json"),
        ("host-contract.v3.schema.json", host_contract_schema, "host", host_root + "host-contract.v3.schema.json"),
        ("plugin-configuration.schema.json", configuration_schema, "host", host_root + "plugin-configuration.schema.json"),
        ("agent-plugins-1.0.0.schema.json", official_schema, "agent-plugins", "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"),
    ]
    contracts = {}
    copies = {}
    for name, source, owner, source_reference in inputs:
        if source is None:
            continue
        data = source.read_bytes()
        schema = json.loads(data)
        if owner == "agent-plugins":
            match = re.search(r"/schemas/([^/]+)/", schema.get("$id", ""))
        else:
            match = re.search(r"V([0-9]+)$", schema.get("title", ""))
        if not match:
            raise ValueError(f"Exported schema version missing: {name}")
        copies[name] = data
        contracts[name] = {
            "owner": owner,
            "source": source_reference,
            "version": match.group(1),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    if publisher_policy is not None:
        data = publisher_policy.read_bytes()
        policy = json.loads(data)
        if set(policy) != {"schemaVersion", "publishers"} or policy["schemaVersion"] != 1:
            raise ValueError("Invalid public publisher policy")
        for publisher in policy["publishers"].values():
            if set(publisher) != {"publicKey", "scope"} or not re.fullmatch(r"[a-f0-9]{64}", publisher["publicKey"]):
                raise ValueError("Publisher export must contain public verification data only")
            if set(publisher["scope"]) != {"repositoryIdentity", "releasePackages", "marketplacePackages"}:
                raise ValueError("Invalid publisher scope")
        name = "host-publisher-policy.json"
        copies[name] = data
        contracts[name] = {"owner": "host", "source": "apps/desktop/src-tauri/resources/plugins/publisher-trust.v1.json", "version": "1", "sha256": hashlib.sha256(data).hexdigest()}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in copies.items():
        (destination / name).write_bytes(data)
    inventory = {"schemaVersion": 1, "contracts": contracts}
    (destination / "owner-contracts.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {name: contract["sha256"] for name, contract in contracts.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host_schema", type=Path)
    parser.add_argument("agent_schema", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--host-contract-schema", type=Path)
    parser.add_argument("--configuration-schema", type=Path)
    parser.add_argument("--official-schema", type=Path)
    parser.add_argument("--bridge-control-schema", type=Path)
    parser.add_argument("--publisher-policy", type=Path)
    args = parser.parse_args()
    hashes = import_contracts(
        args.host_schema, args.agent_schema, args.destination,
        host_contract_schema=args.host_contract_schema,
        configuration_schema=args.configuration_schema,
        official_schema=args.official_schema,
        bridge_control_schema=args.bridge_control_schema,
        publisher_policy=args.publisher_policy,
    )
    print(json.dumps(hashes, sort_keys=True))


if __name__ == "__main__":
    main()
