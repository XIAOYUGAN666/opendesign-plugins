"""Extracted-byte checks for the fixed native input profile, without SDK imports."""
import copy
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.verify_artifact import canonical, digest
from scripts import verify_artifact as verifier


class NativeBuildIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / "runtime/python/Lib/site-packages"
        self.write("plugin.json", canonical({"name": "synthetic-native", "version": "1.0.0"}))
        self.sources = {"native_provider/__init__.py": b"", "native_provider/guides/workflow.md": b"inspect first\n",
                        "native_provider/build_identity.py": b'PROVIDER_BUILD_ID = "<provider-build-id>"\n'}
        for name, data in self.sources.items():
            self.write("runtime/python/Lib/site-packages/" + name, data)
        contracts = {"contracts/protocol.json": b'{"version":1}'}
        inputs = {"provider/pyproject.toml": b'[project]\nname="native-provider"\nversion="1.0.0"\n',
                  "provider/uv.lock": b"version = 1\npackage = []\n", "global.json": b"{}",
                  "provider/packaging/runtime-lock.json": canonical({"python": {"version": "3.12.10", "architecture": "amd64", "byteLength": 1, "sha256": digest(b"python")}}),
                  "native/Bridge.csproj": b"<Project />", "native/packages.lock.json": b"{}", "native/package/manifest.yml": b"version: 1.0.0\n"}
        native = {key: value for key, value in inputs.items() if key.startswith("native/")}
        native["native/Bridge.cs"] = b"class Bridge {}"
        for name, data in contracts.items():
            self.write(name, data)
        for name, data in (inputs | native).items():
            self.write("source-inputs/" + name, data)
        for name in ("pyproject.toml", "uv.lock"):
            self.write(name, inputs["provider/" + name])
            self.write("runtime/python/Lib/site-packages/" + name, inputs["provider/" + name])
        self.write("runtime/python/Lib/site-packages/packaging/runtime-lock.json", inputs["provider/packaging/runtime-lock.json"])
        self.runtime_lock = {"schemaVersion": 1, "python": {"version": "3.12.10", "architecture": "amd64", "byteLength": 1, "sha256": digest(b"python")}, "platform": "windows-x86_64-cp312", "wheels": [], "native": []}
        self.write("runtime-lock.json", canonical(self.runtime_lock))
        records = lambda files: [{"path": key, "sha256": digest(data)} for key, data in sorted(files.items())]
        self.record = {"schemaVersion": 1, "packageId": "synthetic-native", "releaseVersion": "1.0.0",
                       "providerSource": records(self.sources), "contractSource": records(contracts),
                       "buildInputs": records(inputs), "pythonRuntime": {"version": "3.12.10", "architecture": "amd64", "byteLength": 1, "sha256": digest(b"python")},
                       "native": {"targetFrameworks": ["net48", "net8.0"], "pluginId": "11111111-1111-1111-1111-111111111111", "inputs": records(native)}}
        self.expected = digest(canonical(self.record))
        self.write("contracts/provider-build.json", canonical(self.record))
        self.assignment = 'PROVIDER_BUILD_ID = "' + self.expected + '"\n'
        self.write("runtime/python/Lib/site-packages/native_provider/build_identity.py", self.assignment.encode())
        self.identity = {"profile": "native-build-record-v1", "recordPath": "contracts/provider-build.json"}

    def write(self, name, data):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def verify(self):
        # Root tooling stays stdlib-only. Public wheel parsing runs in the
        # existing independently frozen fixture tool environment, never a
        # global install, shared backend project or extracted Provider.
        repository = Path(__file__).resolve().parents[1]
        tool = repository / "tests/fixtures/fixture-engineering/provider/.venv/Scripts/python.exe"
        script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.verify_artifact import verify_build_identity
try:
    verify_build_identity(Path(sys.argv[2]), 'native_provider.managed_bootstrap', json.loads(sys.argv[3]), sys.argv[4])
except ValueError as error:
    print(json.dumps({'error': str(error)}))
else:
    print('{}')
"""
        result = subprocess.run([str(tool), "-I", "-B", "-c", script, str(repository), str(self.root), canonical(self.identity).decode(), self.expected], cwd=repository, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, "frozen fixture verification process failed")
        error = json.loads(result.stdout).get("error")
        if error is not None:
            raise ValueError(error)

    def test_valid_deployed_source_and_native_inputs(self):
        self.verify()

    def test_deployed_non_python_resource_is_not_only_self_attested(self):
        self.write("runtime/python/Lib/site-packages/native_provider/guides/workflow.md", b"changed")
        with self.assertRaisesRegex(ValueError, "buildIdentityMismatch"):
            self.verify()

    def test_assignment_suffix_cannot_hide_code_from_identity(self):
        self.write("runtime/python/Lib/site-packages/native_provider/build_identity.py", (self.assignment.rstrip() + '; print("hidden")\n').encode())
        with self.assertRaisesRegex(ValueError, "buildIdentityMismatch"):
            self.verify()

    def test_duplicate_assignment_is_rejected(self):
        self.write("runtime/python/Lib/site-packages/native_provider/build_identity.py", (self.assignment * 2).encode())
        with self.assertRaisesRegex(ValueError, "buildIdentityMismatch"):
            self.verify()

    def test_input_tamper_or_extra_input_is_rejected(self):
        for path in ("source-inputs/native/Bridge.cs", "source-inputs/native/extra.cs"):
            with self.subTest(path=path):
                target = self.root / path
                previous = target.read_bytes() if target.exists() else None
                self.write(path, b"tampered")
                with self.assertRaisesRegex(ValueError, "buildIdentityMismatch"):
                    self.verify()
                if previous is None:
                    target.unlink()
                else:
                    target.write_bytes(previous)

    def test_actual_contract_and_project_are_bound(self):
        for path in ("contracts/protocol.json", "pyproject.toml"):
            with self.subTest(path=path):
                target = self.root / path
                previous = target.read_bytes()
                self.write(path, b"changed")
                with self.assertRaisesRegex(ValueError, "buildIdentityMismatch"):
                    self.verify()
                target.write_bytes(previous)

    def test_record_cannot_add_circular_or_unknown_fields(self):
        record = copy.deepcopy(self.record)
        record["rootSha256"] = digest(b"root")
        self.write("contracts/provider-build.json", canonical(record))
        with self.assertRaisesRegex(ValueError, "buildIdentityMismatch"):
            self.verify()

    def test_native_runtime_binds_actual_archive_lock_and_contract(self):
        self.add_native()
        packages = verifier.native_runtime_packages(self.root, self.release, self.locked)
        self.assertEqual(packages, {("synthetic-bridge", "1.0.0", "NOASSERTION", self.release["native"]["sha256"][7:])})

    def add_native(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for path in ("manifest.yml", "net48/Bridge.rhp", "net8.0/Bridge.rhp"):
                archive.writestr(path, b"synthetic fixture only")
        data = output.getvalue()
        path = "native/synthetic-bridge-1.0.0.yak"
        self.write(path, data)
        self.release = {"native": {"relativePath": path, "sha256": digest(data), "packageId": "synthetic-bridge", "version": "1.0.0", "pluginId": self.record["native"]["pluginId"], "contractDigest": digest((self.root / "contracts/protocol.json").read_bytes()), "capabilityDigest": digest(b"capability"), "contractVersion": 1}}
        self.locked = {"native": [{"name": "synthetic-bridge", "version": "1.0.0", "path": path, "sha256": digest(data)}]}

    def test_native_archive_and_lock_tampering_are_rejected(self):
        self.add_native()
        for changed in ("archive", "lock", "guid", "contract"):
            with self.subTest(changed=changed):
                locked, release = copy.deepcopy(self.locked), copy.deepcopy(self.release)
                original = (self.root / release["native"]["relativePath"]).read_bytes()
                if changed == "archive":
                    self.write(release["native"]["relativePath"], b"changed")
                elif changed == "lock":
                    locked["native"][0]["sha256"] = digest(b"changed")
                elif changed == "guid":
                    release["native"]["pluginId"] = "22222222-2222-2222-2222-222222222222"
                else:
                    release["native"]["contractDigest"] = digest(b"changed")
                with self.assertRaisesRegex(ValueError, "nativeArtifactMismatch"):
                    verifier.native_runtime_packages(self.root, release, locked)
                self.write(release["native"]["relativePath"], original)

    def test_effective_runtime_lock_cannot_escape_frozen_inputs(self):
        for kind in ("python", "wheel", "extra"):
            with self.subTest(kind=kind):
                locked = copy.deepcopy(self.runtime_lock)
                if kind == "python":
                    locked["python"]["sha256"] = digest(b"different")
                elif kind == "wheel":
                    locked["wheels"] = [{"name": "invented", "version": "1.0", "filename": "invented.whl", "url": "https://example.invalid/invented.whl", "byteLength": 1, "sha256": digest(b"invented")}]
                else:
                    locked["unboundOption"] = True
                self.write("runtime-lock.json", canonical(locked))
                with self.assertRaisesRegex(ValueError, "buildIdentityMismatch"):
                    self.verify()

    def test_native_container_cannot_omit_a_target_or_bundle_software(self):
        for extra in (None, "net48/RhinoCommon.dll", "net8.0/../escape.dll"):
            with self.subTest(extra=extra):
                self.add_native()
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w") as archive:
                    archive.writestr("manifest.yml", b"version: 1.0.0")
                    archive.writestr("net48/Bridge.rhp", b"synthetic")
                    if extra is not None:
                        archive.writestr("net8.0/Bridge.rhp", b"synthetic")
                        archive.writestr(extra, b"synthetic")
                data = output.getvalue()
                self.write(self.release["native"]["relativePath"], data)
                self.release["native"]["sha256"] = digest(data)
                self.locked["native"][0]["sha256"] = digest(data)
                with self.assertRaisesRegex(ValueError, "nativeArtifactMismatch"):
                    verifier.native_runtime_packages(self.root, self.release, self.locked)

    def test_selected_wheel_matches_windows_target_and_deployed_wheel_tags(self):
        wheels = []
        for platform in ("win_amd64", "manylinux_2_17_aarch64"):
            filename = "example-1.0-cp312-cp312-" + platform + ".whl"
            wheels.append({"url": "https://example.invalid/" + filename, "hash": digest(platform.encode()), "size": 1})
        text = 'version = 1\n[[package]]\nname = "example"\nversion = "1.0"\n'
        text += 'wheels = [' + ','.join('{url="' + wheel["url"] + '",hash="' + wheel["hash"] + '",size=1}' for wheel in wheels) + ']\n'
        data = text.encode()
        for path in ("source-inputs/provider/uv.lock", "uv.lock", "runtime/python/Lib/site-packages/uv.lock"):
            self.write(path, data)
        for item in self.record["buildInputs"]:
            if item["path"] == "provider/uv.lock":
                item["sha256"] = digest(data)
        self.expected = digest(canonical(self.record))
        self.write("contracts/provider-build.json", canonical(self.record))
        self.write("runtime/python/Lib/site-packages/native_provider/build_identity.py", ('PROVIDER_BUILD_ID = "' + self.expected + '"\n').encode())
        self.write("runtime/python/Lib/site-packages/example-1.0.dist-info/METADATA", b"Name: example\nVersion: 1.0\n")
        self.write("runtime/python/Lib/site-packages/example-1.0.dist-info/WHEEL", b"Wheel-Version: 1.0\nTag: cp312-cp312-win_amd64\n")
        for index, wheel in enumerate(wheels):
            self.runtime_lock["wheels"] = [{"name": "example", "version": "1.0", "filename": wheel["url"].rsplit("/", 1)[-1], "url": wheel["url"], "byteLength": 1, "sha256": wheel["hash"]}]
            self.write("runtime-lock.json", canonical(self.runtime_lock))
            if index == 0:
                self.verify()
            else:
                with self.assertRaisesRegex(ValueError, "buildIdentityMismatch"):
                    self.verify()
