import copy
import json
from pathlib import Path

from dlubal_rfem_mcp.core.catalog import catalog_digest


def test_catalog_digest_is_order_independent_and_covers_entire_contract():
    tools = json.loads(
        (Path(__file__).parent / "fixtures/tool-catalog-v1.json").read_text(
            encoding="utf-8"
        )
    )["tools"]
    original = catalog_digest(tools)
    assert original == catalog_digest(list(reversed(tools)))
    for field in ("description", "inputSchema", "annotations"):
        changed = copy.deepcopy(tools)
        changed[0][field] = "changed"
        assert catalog_digest(changed) != original
