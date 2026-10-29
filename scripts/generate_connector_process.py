"""Generate the shared local process identity helper for each connector."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outputs", type=Path, nargs="+")
    args = parser.parse_args()
    content = (Path(__file__).parent / "templates" / "connector_process.py.tmpl").read_text(encoding="utf-8")
    for output in args.outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
