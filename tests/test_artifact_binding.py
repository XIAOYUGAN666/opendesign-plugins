"""Black-box release tests; cryptography/MCP stay in the fixture tool environment."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from scripts.build_artifact import check_source_closure, generated_input
from scripts.verify_artifact import safe_path, SAFE_ERRORS
from scripts import verify_artifact as verifier

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "tests/fixtures/fixture-engineering"
PYTHON = PLUGIN / "provider/.venv/Scripts/python.exe"


class ReleaseInputTests(unittest.TestCase):
    def test_child_build_diagnostic_accepts_only_a_fixed_category(self):
        error = subprocess.CalledProcessError(
            1, ["private-command"], stderr=b"artifactBuildFailed:nativeBuildFailed\n"
        )
        self.assertEqual(verifier.safe_error_category(error), "nativeBuildFailed")

    def test_child_build_diagnostic_never_echoes_unknown_output(self):
        for message in (b"private-data", b"artifactBuildFailed:private-data", b"nativeBuildFailed\nprivate-data"):
            with self.subTest(message=message):
                error = subprocess.CalledProcessError(1, ["private-command"], stderr=message)
                self.assertEqual(verifier.safe_error_category(error), "buildStepFailed")

    def test_windows_build_environment_preserves_nuget_path_without_business_credentials(self):
        from unittest.mock import patch
        from scripts.build_artifact import build_artifact

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            root.mkdir()
            (root / "plugin.json").write_bytes((PLUGIN / "plugin.json").read_bytes())
            (root / "packaging").mkdir()
            (root / "packaging/release-input.json").write_bytes(verifier.canonical(generated_input(root, release_sequence=1)))
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"ProgramFiles(x86)": "C:/fixture-program-files", "BUSINESS_API_KEY": "must-not-forward"}, clear=True), patch("scripts.build_artifact.check_source_closure"), patch("scripts.build_artifact.subprocess.run") as stage, patch("scripts.build_artifact._finish_staged_artifact", return_value=output):
                build_artifact(root, output, release_sequence=1)
            environment = stage.call_args.kwargs["env"]
            self.assertEqual(environment.get("ProgramFiles(x86)"), "C:/fixture-program-files")
            self.assertNotIn("BUSINESS_API_KEY", environment)

    def test_build_refuses_drift_before_provider_stage(self):
        from unittest.mock import patch
        from scripts.build_artifact import build_artifact
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plugin"
            root.mkdir()
            (root / "plugin.json").write_bytes((PLUGIN / "plugin.json").read_bytes())
            (root / "packaging").mkdir()
            (root / "packaging/release-input.json").write_bytes(verifier.canonical(generated_input(root, release_sequence=1)))
            with patch("scripts.build_artifact.check_source_closure"), patch("scripts.build_artifact.subprocess.run") as stage:
                with self.assertRaisesRegex(ValueError, "providerRootInputsMismatch"):
                    build_artifact(root, Path(directory) / "output", release_sequence=2)
                stage.assert_not_called()

    def test_publisher_sequence_is_explicit_bounded_and_not_plugin_identity(self):
        identity = json.loads((PLUGIN / "plugin.json").read_text())
        release = verifier.release_identity(identity, release_sequence=2)
        self.assertEqual(release["releaseSequence"], 2)
        self.assertEqual(release["pluginVersion"], identity["version"])
        for value in (2**53, 2**64 - 1):
            self.assertEqual(verifier.release_identity(identity, release_sequence=value)["releaseSequence"], value)
        for value in (0, -1, True, 1.5, 2**64):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verifier.release_identity(identity, release_sequence=value)

    def test_generator_updates_both_derived_inputs_from_one_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "provider").mkdir()
            (root / "provider/packaging").mkdir()
            (root / "provider/packaging/release-input.json").write_bytes(b"stale generated input")
            (root / "plugin.json").write_bytes((PLUGIN / "plugin.json").read_bytes())
            result = subprocess.run([str(PYTHON), "-B", str(ROOT / "scripts/build_artifact.py"), str(root), "--generate-release-input", "--release-sequence", "2"], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            expected = verifier.canonical(generated_input(root, release_sequence=2))
            for target in (root / "packaging/release-input.json", root / "provider/packaging/release-input.json"):
                self.assertEqual(target.read_bytes(), expected)

    def test_generator_does_not_create_an_unused_provider_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "provider").mkdir()
            (root / "plugin.json").write_bytes((PLUGIN / "plugin.json").read_bytes())
            result = subprocess.run([str(PYTHON), "-B", str(ROOT / "scripts/build_artifact.py"), str(root), "--generate-release-input", "--release-sequence", "2"], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertFalse((root / "provider/packaging/release-input.json").exists())


class ArtifactBindingTests(unittest.TestCase):
    def test_environment_candidate_signer_round_trip_preserves_original_receipt_bytes(self):
        # Test-only fixture identity; production CLI still admits only the two approved packages.
        code = r'''
import json,sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts import publisher_signing as signing
from scripts.verify_artifact import canonical,verify_artifact
folder=Path(sys.argv[1]);candidate=folder/'unsigned-receipt.json';before=candidate.read_bytes();payload=json.loads(before)
archive=next(folder.glob('*.zip'));package=payload['release']['packageId'];version=payload['release']['pluginVersion']
signing.TARGETS[package]=('synthetic-test','SYNTHETIC_UNUSED')
key=Ed25519PrivateKey.from_private_bytes(bytes([7])*32)
context={'packageId':package,'version':version,'draft':True,'sourceCommit':'1'*40,'targetCommit':'1'*40,'tag':f'plugin-{package}-v{version}','assetUrl':f'https://github.com/XIAOYUGAN666/opendesign-plugins/releases/download/plugin-{package}-v{version}/{archive.name}','archiveDigest':payload['archive']['sha256']}
result,report=signing.sign_draft_candidate(archive,candidate,Path('schemas'),context,key)
assert candidate.read_bytes()==before and report['stablePublication'] is False
output=folder/'signing-round-trip.json';output.write_bytes(canonical(result))
assert verify_artifact(archive,output,Path('schemas'),trusted_keys={'synthetic-test':key.public_key().public_bytes_raw()})['verification']=='signed-receipt'
for change in ({'draft':False},{'targetCommit':'2'*40},{'archiveDigest':'sha256:'+'0'*64},{'assetUrl':'https://attacker.invalid/asset.zip'}):
 try:signing.sign_draft_candidate(archive,candidate,Path('schemas'),{**context,**change},key)
 except ValueError as error:assert str(error)=='invalidDraftContext'
 else:raise AssertionError('unconfirmed draft accepted')
print('PASS signed fixture and four closed draft negatives')
'''
        result = subprocess.run([str(PYTHON), "-B", "-c", code, str(self.folder / "first")], cwd=ROOT, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.strip(), b"PASS signed fixture and four closed draft negatives")

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="artifact-binding-")
        cls.folder = Path(cls.temporary.name)
        builder = ROOT / "scripts/build_artifact.py"
        if builder.exists():
            command = [str(PYTHON), "-B", str(builder), str(PLUGIN), "--release-sequence", "2", "--output", str(cls.folder / "first")]
        else:
            # RED baseline is the real T09 builder, not an import/path failure.
            command = [str(PYTHON), "-B", "-m", "scripts.build_fixture", "--output", str(cls.folder / "first")]
        built = subprocess.run(command, cwd=PLUGIN / "provider", check=False, capture_output=True)
        if built.returncode:
            detail = built.stderr.decode("utf-8", errors="replace").strip()
            prefix = "artifactBuildFailed:"
            category = detail.removeprefix(prefix)
            allowed = SAFE_ERRORS | {"buildStepTimeout", "buildStepFailed", "verificationFailed"}
            raise AssertionError(detail if detail == prefix + category and category in allowed else "fixtureBuildFailed")
        cls.staged = cls.folder / "first/artifact"
        cls.release = json.loads((cls.staged / "release.json").read_text()) if (cls.staged / "release.json").exists() else {}
        cls.candidate = cls.folder / "first/unsigned-receipt.json"
        cls.receipt = json.loads(cls.candidate.read_text()) if cls.candidate.exists() else {}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_metadata_discovery_never_sends_loopback_bearer_through_system_proxy(self):
        import http.server
        import threading
        from unittest.mock import patch

        requests = []
        class ProxyTrap(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(True)
                self.send_response(502)
                self.end_headers()
            do_GET = do_POST
            def log_message(self, *args):
                pass

        proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ProxyTrap)
        worker = threading.Thread(target=proxy.serve_forever, daemon=True)
        worker.start()
        try:
            url = "http://127.0.0.1:" + str(proxy.server_port)
            injected = verifier.PROBE.replace("import httpx2\n", "import httpx2\nimport httpx2._utils\nhttpx2._utils.getproxies=lambda:{'http':" + repr(url) + "}\n")
            failure = None
            with patch("scripts.verify_artifact.PROBE", injected):
                try:
                    observed = verifier.discover(self.staged)
                except ValueError as error:
                    failure = error
            self.assertEqual(requests, [], "loopback credentials were routed through the proxy")
            if failure is not None:
                raise failure
            self.assertEqual({tool["name"] for tool in observed["preview"]["tools"]}, {"echo", "synthetic_write"})
        finally:
            proxy.shutdown()
            proxy.server_close()
            worker.join(timeout=5)

    def test_root_identity_drives_release(self):
        root = json.loads((self.staged / "plugin.json").read_text())
        self.assertEqual(self.release.get("releaseSequence"), 2)
        self.assertEqual(self.release.get("packageId"), root["name"])
        self.assertEqual(self.release.get("pluginVersion"), root["version"])
        self.assertEqual(self.release.get("startupEnvelopeVersion"), 2)
        self.assertEqual(self.receipt.get("release"), self.release)

    def test_tool_preview_is_exported_from_staged_provider(self):
        preview = (self.staged / "tool-preview.json").read_bytes()
        self.assertEqual(self.release.get("toolCatalogDigest"), "sha256:" + hashlib.sha256(preview).hexdigest())
        tools = json.loads(preview)["tools"]
        self.assertEqual({t["name"] for t in tools}, {"echo", "synthetic_write"})
        for tool in tools:
            self.assertTrue(tool["description"])
            self.assertIn("inputSchema", tool)
            self.assertIn("outputSchema", tool)
            self.assertIn("annotations", tool)

    def test_inventory_has_no_self_digest_cycle(self):
        inventory_path = self.staged / "files.json"
        inventory = json.loads(inventory_path.read_text()) if inventory_path.exists() else {"files": []}
        paths = {x["path"] for x in inventory["files"]}
        self.assertIn("plugin.json", paths)
        self.assertIn("runtime/python/python.exe", paths)
        self.assertIn("skills/fixture-workflow/SKILL.md", paths)
        self.assertNotIn("files.json", paths)
        self.assertNotIn("release.json", paths)

    def test_archive_receipt_cannot_be_rebound(self):
        self.assertIn("archive", self.receipt, "receipt binding is absent in the staged-only baseline")
        archive = next((self.folder / "first").glob("*.zip"))
        self.assertEqual(self.receipt["archive"]["sha256"], "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest())
        verifier = ROOT / "scripts/verify_artifact.py"
        command = [str(PYTHON), "-B", str(verifier), str(archive), str(self.candidate), str(ROOT / "schemas"), "--unsigned"]
        good = subprocess.run(command, capture_output=True, cwd=ROOT)
        self.assertEqual(good.returncode, 0, good.stderr.decode())
        for member in ("plugin.json", "tool-preview.json", "release.json", "risk-review.json"):
            swapped = self.folder / (member.replace(".json", "") + ".zip")
            with zipfile.ZipFile(archive) as source, zipfile.ZipFile(swapped, "w") as target:
                for item in source.infolist():
                    data = source.read(item)
                    if item.filename == member:
                        value = json.loads(data)
                        value["unexpected"] = True
                        data = json.dumps(value).encode()
                    target.writestr(item, data)
            bad = subprocess.run([*command[:3], str(swapped), *command[4:]], capture_output=True, cwd=ROOT)
            self.assertNotEqual(bad.returncode, 0, member)
            # Repair outer ZIP binding to exercise independently extracted checks.
            rebound = json.loads(self.candidate.read_text())
            rebound["archive"]["byteLength"] = swapped.stat().st_size
            rebound["archive"]["sha256"] = "sha256:" + hashlib.sha256(swapped.read_bytes()).hexdigest()
            rebound_path = self.folder / (member + "-receipt.json")
            rebound_path.write_text(json.dumps(rebound), encoding="utf-8")
            bad = subprocess.run([*command[:3], str(swapped), str(rebound_path), *command[5:]], capture_output=True, cwd=ROOT)
            self.assertNotEqual(bad.returncode, 0, "extracted " + member)

    def test_signature_is_verified_before_any_runtime_process(self):
        archive = next((self.folder / "first").glob("*.zip"))
        # All key material lives in this controlled subprocess's memory/anonymous pipes.
        script = r'''
import json,sys,time,subprocess
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
from scripts.sign_envelope import sign_envelope
from scripts.verify_artifact import canonical,verify_artifact
archive,candidate,schemas,folder=map(Path,sys.argv[1:])
payload=json.loads(candidate.read_text()); payload.update(notBefore=int(time.time())-60,expiresAt=int(time.time())+3600)
candidate=folder/'valid-candidate.json'; candidate.write_bytes(canonical(payload))
key=Ed25519PrivateKey.generate(); raw=key.private_bytes_raw(); public=key.public_key().public_bytes_raw()
signed_path=folder/'signed-receipt.json'
result=subprocess.run([sys.executable,'-B','scripts/sign_envelope.py',str(archive),str(candidate),str(schemas),'--output',str(signed_path)],input=raw,capture_output=True,timeout=120)
assert result.returncode==0,result.stderr
receipt=json.loads(signed_path.read_text()); assert len(receipt['signature'])==128 and 'signatureSchemaVersion' not in receipt
keys={payload['publisherKeyId']:public}
assert verify_artifact(archive,signed_path,schemas,trusted_keys=keys)['verification']=='signed-receipt'
cli=subprocess.run([sys.executable,'-B','scripts/verify_artifact.py',str(archive),str(signed_path),str(schemas),'--public-key',public.hex(),'--key-id',payload['publisherKeyId']],capture_output=True,timeout=120)
assert cli.returncode==0,cli.stderr
for mutation in ('bad-signature','wrong-key','missing-key','expired','future','revoked'):
 bad=dict(receipt); trusted=keys
 if mutation=='bad-signature': bad['signature']='00'*64
 if mutation=='wrong-key': trusted={payload['publisherKeyId']:Ed25519PrivateKey.generate().public_key().public_bytes_raw()}
 if mutation=='missing-key': trusted={}
 if mutation=='expired': bad['expiresAt']=int(time.time())-1; bad=sign_envelope({k:v for k,v in bad.items() if k!='signature'},key)
 if mutation=='future': bad.update(notBefore=int(time.time())+60,expiresAt=int(time.time())+3600); bad=sign_envelope({k:v for k,v in bad.items() if k!='signature'},key)
 if mutation=='revoked': bad['revoked']=True; bad=sign_envelope({k:v for k,v in bad.items() if k!='signature'},key)
 path=folder/(mutation+'.json'); path.write_bytes(canonical(bad))
 with patch('scripts.verify_artifact.subprocess.Popen',side_effect=AssertionError('runtime executed before trust validation')) as spawn:
  try: verify_artifact(archive,path,schemas,trusted_keys=trusted)
  except (ValueError,InvalidSignature): pass
  else: raise AssertionError(mutation+' accepted')
  assert spawn.call_count==0,mutation
print('signed round trip and six zero-runtime trust negatives passed')
'''
        result = subprocess.run([str(PYTHON), "-B", "-c", script, str(archive), str(self.candidate), str(ROOT / "schemas"), str(self.folder)], cwd=ROOT, capture_output=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_generated_release_input_tracks_root_without_placeholder_assets(self):
        plugin = self.folder / "generated-input-root"
        plugin.mkdir()
        identity = json.loads((PLUGIN / "plugin.json").read_text())
        identity["version"] = "0.1.42"
        (plugin / "plugin.json").write_text(json.dumps(identity), encoding="utf-8")
        result = subprocess.run([str(PYTHON), "-B", str(ROOT / "scripts/build_artifact.py"), str(plugin), "--generate-release-input", "--release-sequence", "1"], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        generated = json.loads((plugin / "packaging/release-input.json").read_text())
        self.assertEqual(generated["releaseIdentity"]["pluginVersion"], "0.1.42")
        self.assertEqual(generated["rootSha256"], "sha256:" + hashlib.sha256((plugin / "plugin.json").read_bytes()).hexdigest())
        self.assertTrue({"archive", "providerBuildId", "filesDigest", "sbomDigest", "toolCatalogDigest"}.isdisjoint(generated["releaseIdentity"]))
        for name in ("dlubal-rfem", "rhino"):
            root = ROOT / "plugins" / name
            identity = json.loads((root / "plugin.json").read_text())
            copied = json.loads((root / "packaging/release-input.json").read_text())
            self.assertEqual(copied["releaseIdentity"]["packageId"], name)
            self.assertEqual(copied["releaseIdentity"]["pluginVersion"], identity["version"])
            self.assertEqual(copied["rootSha256"], "sha256:" + hashlib.sha256((root / "plugin.json").read_bytes()).hexdigest())

    def test_two_independent_builds_have_identical_archive_bytes(self):
        second = self.folder / "second"
        result = subprocess.run([str(PYTHON), "-B", str(ROOT / "scripts/build_artifact.py"), str(PLUGIN), "--release-sequence", "2", "--output", str(second)], cwd=ROOT, capture_output=True, timeout=240)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        first_archive = next((self.folder / "first").glob("*.zip"))
        second_archive = next(second.glob("*.zip"))
        self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
        for name in ("files.json", "tool-preview.json", "release.json", "tool-preview.schema.json", "lifecycle.schema.json", "configuration.schema.json", "startup-envelope.schema.json"):
            self.assertEqual((self.staged / name).read_bytes(), (second / "artifact" / name).read_bytes(), name)

    def test_nested_inventory_names_are_regular_digest_bound_resources(self):
        script = r'''
import json,subprocess,sys
from pathlib import Path
from unittest.mock import patch
import scripts.build_artifact as builder
plugin,output=map(Path,sys.argv[1:]); real_run=subprocess.run
def stage_resources(command,**kwargs):
 result=real_run(command,**kwargs)
 if 'scripts.build_fixture' in command:
  resources=output/'artifact/skills/fixture-workflow/resources'; resources.mkdir()
  for name in ('files.json','release.json'): (resources/name).write_bytes(b'{"syntheticResource":true}')
 return result
failure=None
with patch('scripts.build_artifact.subprocess.run',side_effect=stage_resources):
 try: builder.build_artifact(plugin,output,release_sequence=2)
 except subprocess.CalledProcessError as error: failure=error
inventory=json.loads((output/'artifact/files.json').read_text())
paths={item['path'] for item in inventory['files']}
assert 'skills/fixture-workflow/resources/files.json' in paths,'nested files.json was omitted'
assert 'skills/fixture-workflow/resources/release.json' in paths,'nested release.json was omitted'
assert 'files.json' not in paths and 'release.json' not in paths
if failure is not None: raise failure
print('nested resources are digest bound')
'''
        output = self.folder / "nested-resources"
        result = subprocess.run([str(PYTHON), "-B", "-c", script, str(PLUGIN), str(output)], cwd=ROOT, capture_output=True, timeout=240)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))


    def test_extraction_uses_the_verified_snapshot_after_metadata_only_path_replacement(self):
        script = r'''
import json,shutil,tempfile,zipfile,sys
from pathlib import Path
from unittest.mock import patch
import scripts.verify_artifact as verifier
source,receipt=map(Path,sys.argv[1:])
with tempfile.TemporaryDirectory(prefix='snapshot-race-') as folder:
 subject=Path(folder)/'subject.zip'; shutil.copyfile(source,subject); validated=subject.read_bytes(); real_extract=verifier.extract; seen={}
 def replacement(snapshot,root):
  with zipfile.ZipFile(subject,'a') as changed: changed.comment=b'controlled metadata-only replacement'
  observed=snapshot.getvalue() if hasattr(snapshot,'getvalue') else Path(snapshot).read_bytes()
  seen['same']=observed==validated
  real_extract(snapshot,root)
 with patch('scripts.verify_artifact.extract',side_effect=replacement): verifier.verify_artifact(subject,receipt,Path('schemas'),unsigned_candidate=True)
 assert seen['same'],'extract reopened different ZIP bytes after receipt hash verification'
'''
        result = subprocess.run([str(PYTHON), "-B", "-c", script, str(next((self.folder / "first").glob("*.zip"))), str(self.candidate)], cwd=ROOT, capture_output=True, timeout=150)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_startup_schema_mutations_are_rejected_after_all_digest_bindings_are_repaired(self):
        script = r'''
import json,tempfile,zipfile,sys
from pathlib import Path
import scripts.verify_artifact as verifier
original,original_receipt=map(Path,sys.argv[1:])
with zipfile.ZipFile(original) as source: members={i.filename:source.read(i) for i in source.infolist()}
accepted_mutations=[]
for mutation in ('invalid-schema','wrong-version','metadata-forbidden'):
 with tempfile.TemporaryDirectory(prefix='startup-declaration-') as folder:
  entries=dict(members); schema=json.loads(entries['startup-envelope.schema.json'])
  if mutation=='invalid-schema': schema['type']='invalid-type'
  if mutation=='wrong-version': schema['properties']['schemaVersion']['const']=1
  if mutation=='metadata-forbidden': schema['properties']['mode']['enum']=['execution']
  entries['startup-envelope.schema.json']=verifier.canonical(schema)
  inventory=json.loads(entries['files.json'])
  for item in inventory['files']:
   if item['path']=='startup-envelope.schema.json': item.update(byteLength=len(entries[item['path']]),sha256=verifier.digest(entries[item['path']]))
  entries['files.json']=verifier.canonical(inventory)
  release=json.loads(entries['release.json']); release['filesDigest']=verifier.digest(entries['files.json']); entries['release.json']=verifier.canonical(release)
  archive=Path(folder)/'mutated.zip'
  with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as output:
   for name,data in entries.items(): output.writestr(name,data)
  candidate=json.loads(original_receipt.read_bytes()); candidate['release']=release; candidate['archive'].update(byteLength=archive.stat().st_size,sha256=verifier.digest(archive.read_bytes()))
  receipt=Path(folder)/'candidate.json'; receipt.write_bytes(verifier.canonical(candidate))
  accepted=True
  try: verifier.verify_artifact(archive,receipt,Path('schemas'),unsigned_candidate=True)
  except Exception: accepted=False
  if accepted: accepted_mutations.append(mutation)
assert not accepted_mutations,'startup declaration mutations accepted: '+','.join(accepted_mutations)
'''
        result = subprocess.run([str(PYTHON), "-B", "-c", script, str(next((self.folder / "first").glob("*.zip"))), str(self.candidate)], cwd=ROOT, capture_output=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))


class SourceClosureTests(unittest.TestCase):
    def test_reserved_unicode_device_aliases_are_rejected_without_opening_them(self):
        for prefix in ("COM", "LPT"):
            for digit in ("¹", "²", "³"):
                for suffix in ("", ".txt"):
                    with self.subTest(name=prefix + digit + suffix):
                        accepted = True
                        try:
                            safe_path("resources/" + prefix + digit + suffix)
                        except ValueError:
                            accepted = False
                        self.assertFalse(accepted, "Windows device alias accepted")

    def test_required_secret_is_a_valid_declaration_without_user_credentials(self):
        script = r'''
from scripts.verify_artifact import validate_configuration_declaration
schema={'type':'object','additionalProperties':False,'required':['api-key'],'properties':{'api-key':{'type':'string','writeOnly':True,'format':'password'}}}
accepted=True
try: validate_configuration_declaration(schema)
except Exception: accepted=False
assert accepted,'valid required-secret declaration was treated as empty user configuration'
'''
        result = subprocess.run([str(PYTHON), "-B", "-c", script], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_copied_static_resources_reject_windows_junction_escape(self):
        with tempfile.TemporaryDirectory(prefix="source-closure-") as folder:
            base = Path(folder)
            root = base / "plugin"
            (root / "skills").mkdir(parents=True)
            (root / "provider/fixture_engineering").mkdir(parents=True)
            (root / "plugin.json").write_bytes((PLUGIN / "plugin.json").read_bytes())
            for name in ("pyproject.toml", "uv.lock"):
                (root / "provider" / name).write_bytes(b"synthetic owned source")
            target = base / "outside"
            target.mkdir()
            (target / "synthetic.txt").write_bytes(b"outside the copied source closure")
            junction = root / "skills/escape"
            script = base / "junction.ps1"
            script.write_text("param([string]$Link,[string]$Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n", encoding="utf-8")
            subprocess.run(["pwsh", "-NoProfile", "-File", str(script), "-Link", str(junction), "-Target", str(target)], check=True, capture_output=True)
            try:
                self.assertTrue(junction.is_junction(), "setup must create a real Windows reparse point")
                accepted = True
                try:
                    check_source_closure(root)
                except ValueError:
                    accepted = False
                self.assertFalse(accepted, "copied source accepted a junction escape")
            finally:
                self.assertEqual(junction.parent, root / "skills")
                os.rmdir(junction)  # Remove this owned junction, never its target.


class InputManifestIdentityTests(unittest.TestCase):
    def test_probe_failure_reports_only_a_fixed_stage_not_exception_text(self):
        prefix, separator, _ = verifier.PROBE.rpartition("try:\n asyncio.run(asyncio.wait_for(main(),65))")
        self.assertTrue(separator)
        handler = verifier.PROBE[len(prefix):]
        for phase in ("ready", "connect", "initialize", "listTools", "metadataCall", "runtime", "private-unknown-phase"):
            injection = "\nasync def main():\n global probe_stage\n probe_stage=" + repr(phase) + "\n raise ExceptionGroup('private-token-and-path', [OSError('private-secret')])\n"
            result = subprocess.run([str(PYTHON), "-B", "-c", prefix + injection + handler], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "")
            expected = "stagedProviderProbeFailed" if phase == "private-unknown-phase" else "stagedProviderProbeFailed_" + phase
            self.assertEqual(json.loads(result.stdout), {"probeFailure": expected})
            self.assertNotIn("private", result.stdout)

    def test_input_manifest_rejects_mismatched_or_missing_root_project_inputs(self):
        with tempfile.TemporaryDirectory(prefix="profile-root-input-") as folder:
            root = Path(folder)
            base = root / "runtime/python/Lib/site-packages"
            (base / "example_provider").mkdir(parents=True)
            (base / "example_provider/__init__.py").write_bytes(b"source")
            for name in ("pyproject.toml", "uv.lock"):
                (base / name).write_bytes(name.encode())
                (root / name).write_bytes(name.encode())
            manifest = {p.relative_to(base).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob("*") if p.is_file()}
            identity = {"profile":"input-manifest-v1","files":manifest}
            build_id = verifier.digest(verifier.canonical(manifest))
            verifier.verify_build_identity(root,"example_provider.managed_bootstrap",identity,build_id)
            for name in ("pyproject.toml", "uv.lock"):
                original = (root / name).read_bytes()
                (root / name).write_bytes(original+b"changed")
                with self.assertRaises(ValueError):
                    verifier.verify_build_identity(root,"example_provider.managed_bootstrap",identity,build_id)
                (root / name).unlink()
                with self.assertRaises(ValueError):
                    verifier.verify_build_identity(root,"example_provider.managed_bootstrap",identity,build_id)
                (root / name).write_bytes(original)
                (base / name).unlink()
                with self.assertRaises(ValueError):
                    verifier.verify_build_identity(root,"example_provider.managed_bootstrap",identity,build_id)
                (base / name).write_bytes(original)

    def test_build_cli_keeps_safe_stage_category_without_private_inputs(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build_artifact.py"), str(PLUGIN), "--release-sequence", "2", "--output", str(PLUGIN)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr.strip(), "artifactBuildFailed:outputMustBeFreshAndOutsideRepository")
        self.assertNotIn(str(PLUGIN), result.stderr)

    def test_verify_cli_keeps_safe_category_without_private_filename(self):
        with tempfile.TemporaryDirectory(prefix="safe-cli-") as folder:
            archive = Path(folder) / "synthetic-secret-input.zip"
            archive.write_bytes(b"")
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/verify_artifact.py"), str(archive), str(archive), str(ROOT / "schemas"), "--unsigned"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr.strip(), "artifactVerificationFailed:invalidArtifact")
            self.assertNotIn("synthetic-secret", result.stderr)

    def test_input_manifest_binds_source_and_frozen_build_inputs(self):
        self.assertTrue(callable(getattr(verifier, "verify_build_identity", None)), "input-manifest profile is unsupported")
        with tempfile.TemporaryDirectory(prefix="build-identity-") as folder:
            root = Path(folder)
            base = root / "runtime/python/Lib/site-packages"
            source = base / "example_provider"
            source.mkdir(parents=True)
            inputs = {"example_provider/__init__.py": b"source", "pyproject.toml": b"project", "uv.lock": b"lock", "packaging/runtime-lock.json": b"runtime", "packaging/release-input.json": b"release"}
            for name, data in inputs.items():
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            for name in ("pyproject.toml", "uv.lock"):
                (root / name).write_bytes(inputs[name])
            manifest = {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()}
            identity = {"profile": "input-manifest-v1", "files": manifest}
            build_id = verifier.digest(verifier.canonical(manifest))
            verifier.verify_build_identity(root, "example_provider.managed_bootstrap", identity, build_id)
            for name in inputs:
                with self.subTest(tampered=name):
                    path = base / name
                    original = path.read_bytes()
                    path.write_bytes(original + b"tampered")
                    with self.assertRaises(ValueError):
                        verifier.verify_build_identity(root, "example_provider.managed_bootstrap", identity, build_id)
                    path.write_bytes(original)
            for declared in ({"profile": "unknown", "files": manifest}, {"profile": "input-manifest-v1", "files": {k: v for k, v in manifest.items() if k != "uv.lock"}}, {"profile": "input-manifest-v1", "files": {**manifest, "../outside": "0" * 64}}, {**identity, "base": "attacker"}):
                with self.subTest(declaration=declared["profile"]), self.assertRaises(ValueError):
                    verifier.verify_build_identity(root, "example_provider.managed_bootstrap", declared, build_id)


if __name__ == "__main__":
    unittest.main()
