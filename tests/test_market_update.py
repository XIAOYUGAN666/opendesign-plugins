"""Subsequent markets must retain an authenticated, exact previous baseline."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "tests/fixtures/fixture-engineering/provider/.venv/Scripts/python.exe"


class MarketUpdateTests(unittest.TestCase):
    def test_previous_market_signature_digest_views_and_scope_are_checked(self):
        code = r'''
import copy,json,tempfile,shutil
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.publisher_signing import verify_previous_market,require_market_progression
from scripts.sign_envelope import sign_envelope
from scripts.verify_artifact import canonical,digest
snapshot=json.loads(Path('.agents/plugins/marketplace.snapshot.json').read_bytes())
policy=json.loads(Path('schemas/host-publisher-policy.json').read_bytes())
# The fixture signs a copy with an ephemeral synthetic signer; production keys are never read.
signer=Ed25519PrivateKey.from_private_bytes(bytes([7])*32)
key=snapshot['publisherKeyId']; policy['publishers'][key]['publicKey']=signer.public_key().public_bytes_raw().hex()
with tempfile.TemporaryDirectory() as folder:
 root=Path(folder); (root/'.agents/plugins').mkdir(parents=True)
 shutil.copyfile('.agents/plugins/marketplace.json',root/'.agents/plugins/marketplace.json')
 shutil.copytree('published',root/'published')
 def write(value):
  raw=canonical(sign_envelope({k:v for k,v in value.items() if k!='signature'},signer))
  (root/'.agents/plugins/marketplace.snapshot.json').write_bytes(raw)
  return digest(raw)
 # Authenticate historical per-plugin signatures with the real exported public keys,
 # while the outer market itself uses the fixture key.
 package_ids=[r['release']['packageId'] for r in snapshot['releases']+snapshot.get('skillReleases',[])]
 policy['publishers']['fixture-market']={'publicKey':signer.public_key().public_bytes_raw().hex(),'scope':{'repositoryIdentity':snapshot['repositoryIdentity'],'releasePackages':[],'marketplacePackages':package_ids}}
 policy['publishers'][key]=json.loads(Path('schemas/host-publisher-policy.json').read_bytes())['publishers'][key]
 snapshot['publisherKeyId']='fixture-market'
 expected=write(snapshot)
 sequence,previous=verify_previous_market(root,Path('schemas'),policy,expected)
 assert sequence==snapshot['sequence'] and set(previous)==set(package_ids)
 def rejects(action):
  try: action()
  except ValueError: pass
  else: raise AssertionError('invalid previous market accepted')
 upgrades=[]
 for old in previous.values():
  new=copy.deepcopy(old); parts=list(map(int,new['pluginVersion'].split('.')));parts[2]+=1
  new['pluginVersion']='.'.join(map(str,parts));new['releaseSequence']+=1;upgrades.append({'release':new})
 require_market_progression(previous,upgrades)
 rejects(lambda:require_market_progression(previous,upgrades[:1]))
 rollback=copy.deepcopy(upgrades);rollback[0]['release']['releaseSequence']=previous[rollback[0]['release']['packageId']]['releaseSequence']
 rejects(lambda:require_market_progression(previous,rollback))
 unchanged=copy.deepcopy(upgrades);unchanged[0]['release']['pluginVersion']=previous[unchanged[0]['release']['packageId']]['pluginVersion']
 rejects(lambda:require_market_progression(previous,unchanged))
 # Adding a package may retain exact previously signed artifacts, but cannot
 # silently replace one at the same sequence or drop an existing package.
 old_receipts={r['release']['packageId']:r for r in snapshot['releases']+snapshot.get('skillReleases',[])}
 addition={'release':{'packageId':'new-skill','pluginVersion':'1.0.0','releaseSequence':1}}
 require_market_progression(previous,list(old_receipts.values())+[addition], previous_receipts=old_receipts)
 changed=copy.deepcopy(list(old_receipts.values())); changed[0]['archive']['sha256']='sha256:'+'0'*64
 rejects(lambda:require_market_progression(previous,changed+[addition],previous_receipts=old_receipts))
 rejects(lambda:verify_previous_market(root,Path('schemas'),policy,'sha256:'+'0'*64))
 raw=(root/'.agents/plugins/marketplace.snapshot.json').read_bytes(); bad=json.loads(raw); bad['sequence']+=1
 (root/'.agents/plugins/marketplace.snapshot.json').write_bytes(canonical(bad))
 rejects(lambda:verify_previous_market(root,Path('schemas'),policy,digest(canonical(bad))))
 expected=write(snapshot)
 (root/'published/rhino/plugin.json').write_bytes(b'{}')
 rejects(lambda:verify_previous_market(root,Path('schemas'),policy,expected))
 shutil.copyfile('published/rhino/plugin.json',root/'published/rhino/plugin.json')
 denied=copy.deepcopy(policy);denied['publishers']['fixture-market']['scope']['marketplacePackages']=['dlubal-rfem']
 rejects(lambda:verify_previous_market(root,Path('schemas'),denied,expected))
print('PASS previous market identity, signature, digest, inventory and scope')
'''
        result = subprocess.run([str(PYTHON), "-B", "-c", code], cwd=ROOT, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"PASS previous market", result.stdout)
