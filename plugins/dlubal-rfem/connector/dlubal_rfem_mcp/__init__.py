"""Independent RFEM Connector package identity."""

import tomllib
from pathlib import Path

__version__ = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text("utf-8")
)["project"]["version"]
