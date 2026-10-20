"""Signing-boundary checks run in the existing independent fixture tool project."""
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "tests/fixtures/fixture-engineering/provider/.venv/Scripts/python.exe"

CHECK = r'''
import base64,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.publisher_signing import load_signers,take_secrets,TARGETS
from scripts.verify_artifact import canonical
# Deterministic synthetic fixtures only; never production signing-key generation.
keys=[Ed25519PrivateKey.from_private_bytes(bytes([x])*32) for x in (7,8)]
policy={'schemaVersion':1,'publishers':{}}
encoded={}
for (package,(target,secret)),key in zip(TARGETS.items(),keys):
 policy['publishers'][target]={'publicKey':key.public_key().public_bytes_raw().hex(),'scope':{'repositoryIdentity':'github:XIAOYUGAN666/opendesign-plugins','releasePackages':[package],'marketplacePackages':[]}}
 encoded[package]=base64.b64encode(key.private_bytes_raw()).decode()
with tempfile.TemporaryDirectory() as folder:
 schema=Path(folder);raw=canonical(policy);(schema/'host-publisher-policy.json').write_bytes(raw)
 inventory={'contracts':{'host-publisher-policy.json':{'owner':'host','source':'apps/desktop/src-tauri/resources/plugins/publisher-trust.v1.json','version':'1','sha256':hashlib.sha256(raw).hexdigest()}}}
 (schema/'owner-contracts.json').write_bytes(canonical(inventory))
 scenario=sys.argv[1]
 if scenario=='historical-id':
  policy['publishers']['publisher-v1']=policy['publishers'].pop('rfem-publisher-v1')
  raw=canonical(policy);(schema/'host-publisher-policy.json').write_bytes(raw);inventory['contracts']['host-publisher-policy.json']['sha256']=hashlib.sha256(raw).hexdigest();(schema/'owner-contracts.json').write_bytes(canonical(inventory))
 if scenario=='match':
  signers,reports=load_signers(schema,encoded)
  assert len(signers)==2 and reports[0]['candidateKeyId']=='rfem-publisher-v1'
  assert reports[0]['hostPolicyKeyId']=='rfem-publisher-v1'
  assert reports[1]['hostPolicyKeyId']=='rhino-publisher-v1'
 elif scenario=='sanitized':
  with patch.dict(os.environ,{fields[1]:encoded[p] for p,fields in TARGETS.items()}):
   captured=take_secrets();assert captured==encoded
   observed=subprocess.check_output([sys.executable,'-B','-c','import os;print(any(k.endswith("_PUBLISHER_V1_KEY_B64") for k in os.environ))'])
   assert observed.strip()==b'False'
 elif scenario=='leak-check':
  from scripts import publisher_signing as boundary
  signers,_=load_signers(schema,encoded);repo=schema/'repo';(repo/'scripts').mkdir(parents=True);leak=repo/'subject'
  for private in (encoded['dlubal-rfem'].encode(),keys[0].private_bytes_raw(),keys[0].private_bytes_raw().hex().encode()):
   leak.write_bytes(private)
   with patch.object(boundary,'__file__',str(repo/'scripts/publisher_signing.py')),patch.object(boundary.subprocess,'check_output',return_value=b'subject\0'):
    try:boundary.reject_tracked_private_material(encoded,signers)
    except ValueError as error:assert str(error)=='publisherSecretInRepository'
    else:raise AssertionError('tracked private material accepted')
 else:
  expected='publisherKeyMismatch'
  if scenario=='swapped': encoded['dlubal-rfem'],encoded['rhino']=encoded['rhino'],encoded['dlubal-rfem']
  elif scenario=='missing': encoded['rhino']='';expected='missingPublisherSecret'
  elif scenario=='malformed': encoded['rhino']='not a key';expected='invalidPublisherSecret'
  elif scenario=='wrong-length': encoded['rhino']=base64.b64encode(b'x'*31).decode();expected='invalidPublisherSecret'
  elif scenario=='tampered-export': (schema/'host-publisher-policy.json').write_bytes(raw+b' ');expected='invalidHostPolicyExport'
  elif scenario=='historical-id':expected='invalidHostPolicyExport'
  elif scenario=='wrong-public-key':
   policy['publishers']['rfem-publisher-v1']['publicKey']=keys[1].public_key().public_bytes_raw().hex()
   raw=canonical(policy);(schema/'host-publisher-policy.json').write_bytes(raw);inventory['contracts']['host-publisher-policy.json']['sha256']=hashlib.sha256(raw).hexdigest();(schema/'owner-contracts.json').write_bytes(canonical(inventory))
  elif scenario=='shared':
   encoded['rhino']=encoded['dlubal-rfem'];policy['publishers']['rhino-publisher-v1']['publicKey']=keys[0].public_key().public_bytes_raw().hex()
   raw=canonical(policy);(schema/'host-publisher-policy.json').write_bytes(raw);inventory['contracts']['host-publisher-policy.json']['sha256']=hashlib.sha256(raw).hexdigest();(schema/'owner-contracts.json').write_bytes(canonical(inventory));expected='publisherKeysNotIndependent'
  else: raise AssertionError('unknown scenario')
  try: load_signers(schema,encoded)
  except ValueError as error: assert str(error)==expected,(scenario,str(error))
  else: raise AssertionError('invalid signer accepted')
 # Exercise the actual CLI: no secret values or raw exceptions may reach output.
 env={**os.environ,**{fields[1]:encoded[p] for p,fields in TARGETS.items()}}
 if scenario not in ('sanitized','leak-check'):
  result=subprocess.run([sys.executable,'-B','scripts/publisher_signing.py','--schemas',str(schema)],env=env,capture_output=True)
  assert result.returncode==(0 if scenario=='match' else 1)
  assert all(not v or v.encode() not in result.stdout+result.stderr for v in encoded.values())
  if scenario!='match':assert result.stderr.strip()==expected.encode()
print('PASS '+scenario)
'''


class PublisherSigningTests(unittest.TestCase):
    def test_matching_independent_keys_and_safe_failure_modes(self):
        for scenario in ("match", "sanitized", "leak-check", "swapped", "missing", "malformed", "wrong-length", "tampered-export", "shared", "historical-id", "wrong-public-key"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([str(PYTHON), "-B", "-c", CHECK, scenario], cwd=ROOT, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(result.stdout.strip(), ("PASS " + scenario).encode())
