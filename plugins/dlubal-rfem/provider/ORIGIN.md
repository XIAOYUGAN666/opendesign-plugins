# One-time source provenance

- Source distribution: official `dlubal-mcp==1.0.14`.
- Acquired and hash-verified: 2026-09-12.
- Registry: <https://pypi.org/pypi/dlubal-mcp/1.0.14/json>.
- Archive: `dlubal_mcp-1.0.14.tar.gz`.
- SHA-256: `947fac2c183061a79bdb59ddab053a30aebd8ea7d23702202bf516c42c20b0b7`.
- Authors/source attribution: Dlubal; original source attribution and docstrings are retained where applicable.

The verified sdist was downloaded and inspected outside the repository. RFEM and shared core are the one-time blueprint; applicable file, class, function and native tool names are preserved under the independent `dlubal_rfem_mcp` package boundary. RSTAB/RSECTION source, tests, extras and unified product dispatch are excluded. No source, fixture, configuration or lock was taken from the legacy repository Provider.

Source inventory confirms eight definitions across `core/server.py`, `rfem/model_operations.py` and `rfem/tools.py`: `health_check`, `get_rfem_application_info`, `inspect_rfem_api`, `write_rfem_model`, `calculate_rfem_model`, `run_rfem_script`, `get_rfem_execution`, `read_rfem_results`. This is AST evidence, not eight successful runtime calls.

Adopted behavior includes RFEM session and model binding, typed model/results operations, introspection, state fingerprints, operation admission, durable execution and their official test concepts. Initial adaptation replaced framework composition with FastMCP 4/MCP 2 and made managed credential/config input explicit. The SDK itself remains unmodified; its value-only INI reads were approved by the user. Current managed behavior is verified by the package tests, independently of this historical provenance. Upstream's FastMCP `<3`/Pydantic `<2.12` constraints are not carried into the independent environment.

This is a historical provenance record. The new package's own source/configuration/tests become authoritative; there is no upstream-sync job, patch queue, vendored mirror, continuing parity obligation or runtime dependency on the official `dlubal-mcp` distribution. Source adoption alone does not establish managed startup, secret safety, signing, Host integration or real RFEM verification.
