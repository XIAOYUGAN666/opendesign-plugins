"""Skill-only artifacts require a manifest and a bounded signed inventory, not a Connector."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "tests/fixtures/fixture-engineering/connector/.venv/Scripts/python.exe"


class SkillArtifactTests(unittest.TestCase):
    def test_real_skill_package_source_builds_without_provider(self):
        with tempfile.TemporaryDirectory(prefix="skill-source-") as folder:
            output = Path(folder) / "out"
            result = subprocess.run([str(PYTHON), "-B", str(ROOT / "scripts/build_skill_artifact.py"), str(ROOT / "plugins/dlubal-rhino"), "--release-sequence", "1", "--output", str(output)], capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            receipt = json.loads((output / "unsigned-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["release"]["packageId"], "dlubal-rhino")

    def test_skill_only_artifact_and_manifest_are_reproducible_without_provider(self):
        with tempfile.TemporaryDirectory(prefix="skill-artifact-") as folder:
            root = Path(folder) / "source"
            skill = root / "skills/example"
            skill.mkdir(parents=True)
            (root / "plugin.json").write_text(json.dumps({"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "example-pack", "version": "1.0.0", "description": "Example", "author": {"name": "Fixture"}}), encoding="utf-8")
            (root / "README.md").write_text("Example", encoding="utf-8")
            (skill / "SKILL.md").write_text("---\nname: example\ndescription: Fixture\n---\n# Example\n", encoding="utf-8")
            outputs = []
            for index in range(2):
                output = Path(folder) / f"out-{index}"
                result = subprocess.run([str(PYTHON), "-B", str(ROOT / "scripts/build_skill_artifact.py"), str(root), "--release-sequence", "1", "--output", str(output)], capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                outputs.append(output)
            archives = [next(output.glob("*.zip")).read_bytes() for output in outputs]
            self.assertEqual(archives[0], archives[1])
            receipt = json.loads((outputs[0] / "unsigned-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["release"]["packageId"], "example-pack")
            self.assertNotIn("connectorBuildId", receipt["release"])
            verifier = ROOT / "scripts/verify_skill_artifact.py"
            verified = subprocess.run([str(PYTHON), "-B", str(verifier), str(next(outputs[0].glob("*.zip"))), str(outputs[0] / "unsigned-receipt.json"), "--unsigned"], capture_output=True, timeout=60)
            self.assertEqual(verified.returncode, 0, verified.stderr.decode("utf-8", errors="replace"))
            code = r'''
import sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from scripts.sign_skill_artifact import sign_draft
from scripts.verify_skill_artifact import verify
from scripts.verify_artifact import read_json
out=Path(sys.argv[1]); archive=next(out.glob('*.zip')); candidate=out/'unsigned-receipt.json'; raw=candidate.read_bytes(); payload=read_json(raw)
signer=Ed25519PrivateKey.from_private_bytes(bytes([7])*32); public=signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
policy={'publishers':{'fixture-key':{'publicKey':public.hex(),'scope':{'repositoryIdentity':'github:XIAOYUGAN666/opendesign-plugins','releasePackages':['example-pack']}}}}
commit='1'*40; version='1.0.0'; url=f'https://github.com/XIAOYUGAN666/opendesign-plugins/releases/download/plugin-example-pack-v{version}/{archive.name}'
context={'draft':True,'sourceCommit':commit,'targetCommit':commit,'packageId':'example-pack','version':version,'tag':'plugin-example-pack-v1.0.0','assetUrl':url,'archiveDigest':payload['archive']['sha256']}
signed=sign_draft(archive,candidate,context,policy,'fixture-key',signer)
from scripts import publisher_signing as publisher
publisher.TARGETS['fixture-owner']=('fixture-key','FIXTURE_UNUSED')
candidate_signed, candidate_report=publisher.sign_skill_draft_candidate(archive,candidate,context,policy,{'fixture-owner':signer})
assert candidate_signed['release']==signed['release'] and candidate_report['stablePublication'] is False
assert candidate.read_bytes()==raw
assert verify(archive,signed,trusted_keys={'fixture-key':public})['verification']=='signed-receipt'
from scripts.generate_stable_view import generate_stable_view
from scripts.generate_marketplace import generate_marketplace,verified_views
from scripts.sign_marketplace import sign_marketplace
import zipfile
policy2={'repositoryIdentity':'github:XIAOYUGAN666/opendesign-plugins','marketplaceKeyId':'fixture-key','previousSequence':0,'keys':{'fixture-key':{'publicKey':public.hex(),'packages':['example-pack'],'notBefore':1,'expiresAt':signed['expiresAt'],'revoked':False}},'approvedArchives':{signed['archive']['sha256']:url},'revokedArchives':[]}
root=out.parent/'extracted'; root.mkdir()
with zipfile.ZipFile(archive) as z:z.extractall(root)
verified=verify(archive,signed,trusted_keys={'fixture-key':public}); viewroot=out.parent/'view'
generate_stable_view(root,signed,viewroot,trust_policy=policy2,verified_context=verified)
roots={'example-pack':root}; contexts={'example-pack':verified}; market=viewroot/'marketplace.json'
generate_marketplace([signed],market,view_root=viewroot,trust_policy=policy2,artifact_roots=roots,verified_contexts=contexts)
views,_=verified_views([signed],viewroot,policy2,roots,contexts)
envelope=read_json(sign_marketplace(market.read_bytes(),[signed],1,signer,views=views,trust_policy=policy2,view_root=viewroot,artifact_roots=roots,verified_contexts=contexts))
assert envelope['releases']==[] and envelope['skillReleases']==[signed]
from scripts import publisher_signing
from scripts.verify_artifact import canonical
repo=out.parent/'isolated-repository'; (repo/'.agents/plugins').mkdir(parents=True)
(repo/'.agents/plugins/marketplace.json').write_bytes(canonical({'name':'team-stable','interface':{'displayName':'Official stable plugins'},'plugins':[]}))
publisher_signing.__file__=str(repo/'scripts/publisher_signing.py')
signed_path=out/'signed-receipt.json'; signed_path.write_bytes(canonical(signed))
policy['publishers']['fixture-key']['scope']['marketplacePackages']=['example-pack']
report=publisher_signing.sign_market_candidate([{'receipt':str(signed_path),'archive':str(archive),'context':context}],Path('schemas'),policy,signer,'fixture-key',commit,out.parent/'market-review')
assert report['stablePublication'] is False
review=read_json((out.parent/'market-review/.agents/plugins/marketplace.snapshot.json').read_bytes())
assert review['skillReleases']==[signed]
try:sign_draft(archive,candidate,{**context,'targetCommit':'2'*40},policy,'fixture-key',signer)
except ValueError:pass
else:raise AssertionError('unconfirmed draft accepted')
'''
            signed = subprocess.run([str(PYTHON), "-B", "-c", code, str(outputs[0])], cwd=ROOT, capture_output=True, timeout=60)
            self.assertEqual(signed.returncode, 0, signed.stderr.decode("utf-8", errors="replace"))
            receipt["release"]["filesDigest"] = "sha256:" + "0" * 64
            (outputs[0] / "bad-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            rejected = subprocess.run([str(PYTHON), "-B", str(verifier), str(next(outputs[0].glob("*.zip"))), str(outputs[0] / "bad-receipt.json"), "--unsigned"], capture_output=True, timeout=60)
            self.assertNotEqual(rejected.returncode, 0)
            (skill / "SKILL.md").write_text("---\nname: wrong\ndescription: Fixture\n---\n", encoding="utf-8")
            bad = subprocess.run([str(PYTHON), "-B", str(ROOT / "scripts/build_skill_artifact.py"), str(root), "--release-sequence", "1", "--output", str(Path(folder) / "bad")], capture_output=True, timeout=60)
            self.assertNotEqual(bad.returncode, 0)
