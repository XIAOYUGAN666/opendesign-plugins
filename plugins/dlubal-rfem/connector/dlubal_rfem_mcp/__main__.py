"""Managed Connector entry point; no environment or source-mode fallback."""


def main() -> None:
    import sys

    if sys.argv[1:] == ["--stdio"]:
        from dlubal_rfem_mcp.core.server import run_stdio

        run_stdio()
        return
    from dlubal_rfem_mcp.managed_bootstrap import main as managed_main

    managed_main()


if __name__ == "__main__":
    main()
