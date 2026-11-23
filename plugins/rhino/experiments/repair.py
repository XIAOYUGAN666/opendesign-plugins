"""Apply a reviewed bounded source patch in a standalone Windows candidate directory."""

import argparse
import io
import json
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from experiments.runner import sha256

ROOT = Path(__file__).resolve().parents[3]
PACKAGE_PATH = PurePosixPath("plugins/rhino")


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Candidate contains a symlink")
        if not (path.is_file() or path.is_dir()):
            raise ValueError(
                f"Candidate contains an unsupported filesystem entry: {path.relative_to(directory)}"
            )
        result[path.relative_to(directory).as_posix()] = {
            "kind": "file" if path.is_file() else "directory",
            "sha256": sha256(path) if path.is_file() else None,
            "mode": path.stat().st_mode & 0o777,
        }
    return result


def check_candidate(directory, before, write_paths=None):
    after = inventory(directory)
    if before.keys() != after.keys():
        raise ValueError("Builder added or removed files")
    changed = [path for path in before if before[path] != after[path]]
    if not changed:
        raise ValueError("Builder produced no changes")
    if set(changed) - set(write_paths or ()):
        raise ValueError("Builder changed protected files")
    if any(before[path]["mode"] != after[path]["mode"] for path in changed):
        raise ValueError("Builder changed file modes")
    return changed


def prepare(revision, candidate, patch, allowed):
    if candidate.resolve().is_relative_to(ROOT):
        raise ValueError("Use a candidate directory outside the working repository")
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", revision + "^{commit}"], cwd=ROOT, text=True,
    ).strip()
    archive = subprocess.check_output(["git", "archive", revision, str(PACKAGE_PATH)], cwd=ROOT)
    candidate.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(candidate, filter="data")
    before = inventory(candidate)
    allowed = tuple(PurePosixPath(path) for path in allowed)
    if not allowed or any(str(path) not in before or not path.is_relative_to(PACKAGE_PATH)
                          or before[str(path)]["kind"] != "file" for path in allowed):
        raise ValueError("Scope must name existing files in the plugin package")
    # Upstream patches can carry CRLF context around a file without a final newline.
    # Match that context regardless of spacing, then verify the exact changed files.
    apply = ["git", "apply", "--no-index", "--ignore-space-change", "--whitespace=error"]
    subprocess.run([*apply, "--check", str(patch.resolve(strict=True))], cwd=candidate, check=True)
    subprocess.run([*apply, str(patch.resolve(strict=True))], cwd=candidate, check=True)
    changed = check_candidate(candidate, before, [path.as_posix() for path in allowed])
    return {"base": revision, "patchSha256": sha256(patch), "changed": changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--allow", action="append", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.base, args.candidate, args.patch, args.allow), indent=2))


if __name__ == "__main__":
    main()
