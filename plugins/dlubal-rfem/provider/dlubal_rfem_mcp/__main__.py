"""Managed Provider entry point; no environment or source-mode fallback."""


def main() -> None:
    from dlubal_rfem_mcp.managed_bootstrap import main as managed_main

    managed_main()


if __name__ == "__main__":
    main()
