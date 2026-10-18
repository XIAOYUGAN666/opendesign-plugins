"""Generated discovery boundaries; crypto tests use the fixture's own environment."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "tests/fixtures/fixture-engineering/provider/.venv/Scripts/python.exe"

# Deliberately small synthetic extracted artifacts. These are generator tests,
# not a substitute for T07's real archive/runtime verification or Stable approval.
HARNESS = r'''
import copy,json,os,subprocess,sys,tempfile,time
from unittest.mock import patch
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat,PrivateFormat,NoEncryption
from scripts.generate_marketplace import generate_marketplace,verified_views
from scripts.generate_stable_view import generate_stable_view,inventory_for,snapshot_files,REPOSITORY,RELEASE_ORIGIN
from scripts.sign_marketplace import sign_marketplace
from scripts.sign_envelope import sign_envelope
from scripts.verify_artifact import canonical,digest,release_identity,NAMESPACE,read_json,MAX_ARCHIVE,MAX_FILE,validate
base=Path(sys.argv[1]); signer=Ed25519PrivateKey.generate(); now=int(time.time())
policy={'repositoryIdentity':REPOSITORY,'marketplaceKeyId':'synthetic-test','keys':{'synthetic-test':{'publicKey':signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex(),'packages':['dlubal-rfem','rhino'],'notBefore':now-1000,'expiresAt':now+7200,'revoked':False}},'approvedArchives':{},'previousSequence':4,'revokedArchives':[]}
roots={}; contexts={}; receipts=[]
def artifact(package,version):
 root=base/'artifacts'/package; root.mkdir(parents=True)
 identity=read_json(Path('tests/fixtures/fixture-engineering/plugin.json').read_bytes())
 identity['name']=package; identity['version']=version; native=identity['extensions'][NAMESPACE]; native['adapter']['version']=version; native['components'][0]['id']=package
 files={'plugin.json':canonical(identity),'README.md':b'Synthetic generator test. Native entrypoint resolves only in signed Windows ZIP.\n','configuration.schema.json':b'{"type":"object"}','tool-preview.json':canonical({'normalizationVersion':1,'tools':[]}),'sbom.spdx.json':b'{}','runtime/python/python.exe':b'synthetic-only','skills/workflow/SKILL.md':b'---\nname: workflow\ndescription: Synthetic generator test.\n---\nRead [reference](resources/reference.md).\n','skills/workflow/resources/reference.md':b'Synthetic resource\n'}
 inventory=canonical({'schemaVersion':1,'files':[{'path':name,'byteLength':len(raw),'sha256':digest(raw)} for name,raw in sorted(files.items())]})
 release=release_identity(identity,release_sequence=2); release.update(providerBuildId=digest(b'synthetic-source'),filesDigest=digest(inventory),toolCatalogDigest=digest(files['tool-preview.json']),sbomDigest=digest(files['sbom.spdx.json']))
 files.update({'files.json':inventory,'release.json':canonical(release)})
 for name,raw in files.items():
  path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
 archive=digest(canonical({'synthetic':package,'version':version}))
 url=RELEASE_ORIGIN+'/releases/download/plugin-'+package+'-v'+version+'/synthetic-only.zip'
 receipt=sign_envelope({'release':release,'archive':{'url':url,'byteLength':123,'sha256':archive},'publisherKeyId':'synthetic-test','notBefore':now-60,'expiresAt':now+3600,'revoked':False},signer)
 policy['approvedArchives'][archive]=url; roots[package]=root; contexts[package]={'archiveDigest':archive,'filesDigest':release['filesDigest'],'toolCatalogDigest':release['toolCatalogDigest'],'verification':'signed-receipt'}; receipts.append(receipt)
 return receipt
def view(receipt): return generate_stable_view(roots[receipt['release']['packageId']],receipt,base/'candidate',trust_policy=policy,verified_context=contexts[receipt['release']['packageId']])
def market(bindings=None): return generate_marketplace(receipts if bindings is None else bindings,base/'candidate/.agents/plugins/marketplace.json',view_root=base/'candidate',trust_policy=policy,artifact_roots=roots,verified_contexts=contexts)
def signing(raw=None,bindings=None,views=None,sequence=5,key=None):
 bindings=receipts if bindings is None else bindings
 expected,_=verified_views(bindings,base/'candidate',policy,roots,contexts)
 return sign_marketplace(market().read_bytes() if raw is None else raw,bindings,sequence,signer if key is None else key,views=expected if views is None else views,trust_policy=policy,view_root=base/'candidate',artifact_roots=roots,verified_contexts=contexts)
def rejects(call):
 try: call()
 except Exception: return
 raise AssertionError('unsafe candidate was accepted')
def rebind(receipt):
 package=receipt['release']['packageId']; root=roots[package]; files=snapshot_files(root)
 inventory=canonical({'schemaVersion':1,'files':[{'path':name,'byteLength':len(raw),'sha256':digest(raw)} for name,raw in sorted(files.items()) if name not in {'files.json','release.json'}]})
 (root/'files.json').write_bytes(inventory); receipt['release']['filesDigest']=digest(inventory); (root/'release.json').write_bytes(canonical(receipt['release'])); contexts[package]['filesDigest']=digest(inventory); receipt.pop('signature',None); receipt.update(sign_envelope(receipt,signer))
def resign(receipt):
 receipt.pop('signature',None); receipt.update(sign_envelope(receipt,signer)); return receipt
first=artifact('dlubal-rfem','0.1.0'); second=artifact('rhino','2.3.4')
'''


class MarketplaceGenerationTests(unittest.TestCase):
    def run_case(self, code):
        with tempfile.TemporaryDirectory(prefix="marketplace-generation-") as folder:
            result = subprocess.run([str(PYTHON), "-B", "-c", HARNESS + "\n" + code, folder], cwd=ROOT, capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_only_complete_stable_releases_are_discovered(self):
        self.run_case("""
view(first); view(second); market(); assert {p['name'] for p in read_json(market().read_bytes())['plugins']}=={'dlubal-rfem','rhino'}
(roots['rhino']/'configuration.schema.json').unlink(); rejects(lambda:market())
rejects(lambda:generate_marketplace(receipts,base/'unapproved.json',view_root=base/'candidate'))
""")

    def test_plugin_versions_are_independent(self):
        self.run_case("""
view(first); view(second); entries=read_json(market().read_bytes())['plugins']; versions=[read_json((base/'candidate'/entry['source']['path']/'plugin.json').read_bytes())['version'] for entry in entries]; assert versions==['0.1.0','2.3.4']; assert all('version' not in entry for entry in entries)
""")

    def test_engineering_category_is_not_fixed_to_developer_tools(self):
        self.run_case("view(first); view(second); assert {p['category'] for p in read_json(market().read_bytes())['plugins']}=={'Engineering Design'}")

    def test_failed_publish_keeps_previous_market(self):
        self.run_case("""
view(first); view(second); output=market(); before=output.read_bytes(); policy['approvedArchives'].pop(first['archive']['sha256']); rejects(lambda:market()); assert output.read_bytes()==before
""")

    def test_main_candidate_does_not_change_stable_view(self):
        self.run_case("""
stable=view(first); before=(stable/'plugin.json').read_bytes(); development=base/'plugins/dlubal-rfem'; development.mkdir(parents=True); changed=read_json(before); changed['version']='0.2.0'; (development/'plugin.json').write_bytes(canonical(changed)); assert changed['version']!='0.1.0'; assert (stable/'plugin.json').read_bytes()==before; market([first]); assert read_json((stable/'plugin.json').read_bytes())['version']=='0.1.0'
""")

    def test_published_paths_are_relative_to_repository_root(self):
        self.run_case("""
view(first); entry=read_json(market([first]).read_bytes())['plugins'][0]; assert entry['source']=={'source':'local','path':'./published/dlubal-rfem'}; assert (base/'candidate'/entry['source']['path']/'plugin.json').is_file()
""")

    def test_stable_view_has_complete_skills_and_native_boundary(self):
        self.run_case("""
stable=view(first); assert (stable/'skills/workflow/resources/reference.md').is_file(); assert (stable/'plugin.json').read_bytes()==(roots['dlubal-rfem']/'plugin.json').read_bytes(); entry=read_json((stable/'plugin.json').read_bytes())['extensions'][NAMESPACE]['entrypoint']; assert (roots['dlubal-rfem']/entry).is_file(); assert not (stable/entry).exists(); assert (stable/'configuration.schema.json').is_file(); assert not (stable/'mcp.json').exists()
""")

    def test_signatures_bind_raw_market_and_all_view_files(self):
        self.run_case("""
view(first); view(second); raw=market().read_bytes(); signed=read_json(signing(raw)); signer.public_key().verify(bytes.fromhex(signed['signature']),canonical({'signatureSchemaVersion':1,**{k:v for k,v in signed.items() if k!='signature'}})); assert signed['marketplaceSha256']==digest(raw); assert len(signed['views'])==2
(base/'candidate/published/dlubal-rfem/skills/workflow/resources/reference.md').write_bytes(b'tampered'); rejects(lambda:signing(raw))
""")

    def test_unsigned_expired_revoked_wrong_scope_and_source_are_rejected(self):
        self.run_case("""
view(first); view(second)
for mutation in ('unsigned','expired','revoked','scope','source','repo','approval','archive-revoked','key-revoked'):
 original=copy.deepcopy(policy); altered=copy.deepcopy(first)
 if mutation=='unsigned': altered.pop('signature')
 if mutation=='expired': altered['expiresAt']=now-1
 if mutation=='revoked': altered['revoked']=True
 if mutation=='scope': policy['keys']['synthetic-test']['packages']=[]
 if mutation=='source': altered['archive']['url']='https://evil.invalid/asset.zip'
 if mutation=='repo': policy['repositoryIdentity']='https://github.com/other/repo'
 if mutation=='approval': policy['approvedArchives']={}
 if mutation=='archive-revoked': policy['revokedArchives']=[first['archive']['sha256']]
 if mutation=='key-revoked': policy['keys']['synthetic-test']['revoked']=True
 rejects(lambda:generate_stable_view(roots['dlubal-rfem'],altered,base/'bad'/mutation,trust_policy=policy,verified_context=contexts['dlubal-rfem'])); policy.clear(); policy.update(original)
""")

    def test_duplicate_identity_rollback_wrong_signer_and_forged_view_fail(self):
        self.run_case("""
view(first); view(second); raw=market().read_bytes(); rejects(lambda:market([first,first])); rejects(lambda:signing(raw,sequence=4)); rejects(lambda:signing(raw,key=Ed25519PrivateKey.generate())); views,_=verified_views(receipts,base/'candidate',policy,roots,contexts); views[0]['files'][0]['sha256']=digest(b'forged'); rejects(lambda:signing(raw,views=views)); altered=read_json(raw); altered['plugins'][0]['source']['path']='./plugins/dlubal-rfem'; rejects(lambda:signing(canonical(altered)))
""")

    def test_marketplace_sequence_must_fit_exported_uint64_owner(self):
        self.run_case("view(first); view(second); raw=market().read_bytes(); rejects(lambda:signing(raw,sequence=2**64))")

    def test_broken_skill_reference_and_untracked_artifact_fail(self):
        self.run_case("""
view(first); (roots['dlubal-rfem']/'unexpected.md').write_bytes(b'undeclared'); rejects(lambda:view(first))
""")

    def test_broken_relative_resource_fails_after_valid_signed_inventory(self):
        self.run_case("""
(roots['dlubal-rfem']/'skills/workflow/SKILL.md').write_bytes(b'Read [missing](resources/missing.md).'); rebind(first); rejects(lambda:view(first)); assert not (base/'candidate/published/dlubal-rfem').exists()
""")

    def test_native_entrypoint_cannot_resolve_inside_portable_view(self):
        self.run_case("""
identity=read_json((roots['dlubal-rfem']/'plugin.json').read_bytes()); identity['extensions'][NAMESPACE]['entrypoint']='README.md'; (roots['dlubal-rfem']/'plugin.json').write_bytes(canonical(identity)); first['release']['entrypoint']='README.md'; rebind(first); rejects(lambda:view(first))
""")

    def test_immutable_projection_preserves_old_bytes_and_allows_identical_retry(self):
        self.run_case("""
stable=view(first); before=snapshot_files(stable); assert view(first)==stable; (roots['dlubal-rfem']/'README.md').write_bytes(b'new candidate text'); rebind(first); rejects(lambda:view(first)); assert snapshot_files(stable)==before
""")

    def test_static_binary_assets_are_preserved_without_native_runtime(self):
        self.run_case("""
asset=roots['dlubal-rfem']/'assets/icon.png'; asset.parent.mkdir(); asset.write_bytes(b'\\x89PNG\\x00synthetic-icon'); rebind(first); stable=view(first); assert (stable/'assets/icon.png').read_bytes()==asset.read_bytes(); assert not (stable/'runtime').exists()
""")

    def test_inventory_path_escape_fails_closed(self):
        self.run_case("""
inventory=read_json((roots['dlubal-rfem']/'files.json').read_bytes()); inventory['files'][0]['path']='../escape'; raw=canonical(inventory); (roots['dlubal-rfem']/'files.json').write_bytes(raw); first['release']['filesDigest']=digest(raw); (roots['dlubal-rfem']/'release.json').write_bytes(canonical(first['release'])); contexts['dlubal-rfem']['filesDigest']=digest(raw); first.pop('signature'); first.update(sign_envelope(first,signer)); rejects(lambda:view(first))
""")

    def test_authenticated_tombstone_signs_without_discovery_or_view(self):
        self.run_case("""
tombstone=copy.deepcopy(first); tombstone['revoked']=True; resign(tombstone); policy['revokedArchives']=[tombstone['archive']['sha256']]
artifact_root=roots['dlubal-rfem']; context=contexts['dlubal-rfem']; roots.clear(); contexts.clear()
raw=market([tombstone]).read_bytes(); assert read_json(raw)['plugins']==[]; signed=read_json(signing(raw,bindings=[tombstone])); assert signed['releases']==[tombstone]; assert signed['views']==[]; assert not (base/'candidate/published').exists(); rejects(lambda:generate_stable_view(artifact_root,tombstone,base/'bad',trust_policy=policy,verified_context=context))
""")

    def test_receipt_and_policy_validity_fit_exported_uint64(self):
        self.run_case("""
view(first); view(second); first['expiresAt']=2**64; policy['keys']['synthetic-test']['expiresAt']=2**64; resign(first); rejects(lambda:signing())
""")

    def test_compressed_archive_limit_matches_mandatory_verifier(self):
        self.run_case("""
first['archive']['byteLength']=MAX_ARCHIVE+1; resign(first); rejects(lambda:view(first))
first['archive']['byteLength']=MAX_ARCHIVE; resign(first); assert view(first).is_dir()
""")

    def test_extracted_metadata_limits_are_checked_before_any_read(self):
        self.run_case("""
root=roots['dlubal-rfem']; target=root/'oversized.bin'; target.write_bytes(b'small synthetic bytes'); target=target.resolve(); original_stat=Path.stat
def large_stat(path,*args,**kwargs):
 stat=original_stat(path,*args,**kwargs)
 if path==target:
  fields=list(stat); fields[6]=MAX_FILE+1; return os.stat_result(fields)
 return stat
with patch.object(Path,'stat',large_stat),patch.object(Path,'open',side_effect=AssertionError('size preflight read a file')) as opened:
 try: snapshot_files(root)
 except ValueError: pass
 else: raise AssertionError('oversized extracted metadata was accepted')
 assert opened.call_count==0
""")

    def test_old_tombstone_and_new_active_release_share_one_package(self):
        self.run_case("""
tombstone=copy.deepcopy(first); tombstone['revoked']=True; resign(tombstone); policy['revokedArchives']=[tombstone['archive']['sha256']]
identity=read_json((roots['dlubal-rfem']/'plugin.json').read_bytes()); identity['version']='0.2.0'; identity['extensions'][NAMESPACE]['adapter']['version']='0.2.0'; (roots['dlubal-rfem']/'plugin.json').write_bytes(canonical(identity)); first['release']['pluginVersion']='0.2.0'; first['release']['adapter']['version']='0.2.0'; first['archive']['sha256']=digest(b'new synthetic release'); first['archive']['url']=RELEASE_ORIGIN+'/releases/download/plugin-dlubal-rfem-v0.2.0/synthetic-only.zip'; policy['approvedArchives'][first['archive']['sha256']]=first['archive']['url']; contexts['dlubal-rfem']['archiveDigest']=first['archive']['sha256']; rebind(first); view(first)
bindings=[tombstone,first]; raw=market(bindings).read_bytes(); assert [p['name'] for p in read_json(raw)['plugins']]==['dlubal-rfem']; signed=read_json(signing(raw,bindings=bindings)); assert signed['releases']==bindings; assert len(signed['views'])==1; assert read_json((base/'candidate/published/dlubal-rfem/plugin.json').read_bytes())['version']=='0.2.0'
rejects(lambda:market([tombstone,tombstone])); conflicting=copy.deepcopy(first); conflicting['revoked']=True; resign(conflicting); rejects(lambda:market([first,conflicting])); other_active=copy.deepcopy(first); other_active['archive']['sha256']=digest(b'conflicting active'); policy['approvedArchives'][other_active['archive']['sha256']]=other_active['archive']['url']; resign(other_active); rejects(lambda:market([first,other_active]))
""")

    def test_tombstone_still_requires_authenticated_approved_history_and_scope(self):
        self.run_case("""
tombstone=copy.deepcopy(first); tombstone['revoked']=True; resign(tombstone)
for mutation in ('history','signature','scope','expired','source','repository'):
 original=copy.deepcopy(policy); altered=copy.deepcopy(tombstone)
 if mutation=='history': policy['approvedArchives'].pop(altered['archive']['sha256'])
 if mutation=='signature': altered['signature']='0'*128
 if mutation=='scope': policy['keys']['synthetic-test']['packages']=[]
 if mutation=='expired': altered['expiresAt']=now-1; resign(altered)
 if mutation=='source': altered['archive']['url']='https://evil.invalid/asset.zip'; policy['approvedArchives'][altered['archive']['sha256']]=altered['archive']['url']; resign(altered)
 if mutation=='repository': policy['repositoryIdentity']='github:other/repo'
 rejects(lambda:market([altered])); policy.clear(); policy.update(original)
""")

    def test_policy_times_are_bounded_without_receipt_overflow(self):
        self.run_case("""
for invalid in (2**64,-1,True,1.5):
 original=copy.deepcopy(policy); policy['keys']['synthetic-test']['expiresAt']=invalid; rejects(lambda:view(first)); policy.clear(); policy.update(original)
view(first); view(second); raw=market().read_bytes(); policy['keys']['market-only']={**policy['keys']['synthetic-test'],'expiresAt':2**64}; policy['marketplaceKeyId']='market-only'; rejects(lambda:signing(raw))
""")

    def test_shared_validator_checks_exported_integer_width_formats(self):
        self.run_case("""
for bits in (8,16,32,64):
 schema={'type':'integer','format':'uint'+str(bits)}; validate(0,schema); validate(2**bits-1,schema)
 for invalid in (-1,2**bits,True,1.0): rejects(lambda:validate(invalid,schema))
 schema={'type':'integer','format':'int'+str(bits)}; validate(-2**(bits-1),schema); validate(2**(bits-1)-1,schema); rejects(lambda:validate(2**(bits-1),schema)); rejects(lambda:validate(-2**(bits-1)-1,schema))
exported=read_json(Path('schemas/host-contract.v3.schema.json').read_bytes()); oversized=copy.deepcopy(first); oversized['release']['software']['major']=2**32; rejects(lambda:validate(oversized,exported,'SignedReleaseV1')); oversized=copy.deepcopy(first); oversized['notBefore']=2**64; rejects(lambda:validate(oversized,exported,'SignedReleaseV1'))
""")

    def test_total_extracted_metadata_limit_is_checked_before_any_read(self):
        self.run_case("""
root=roots['dlubal-rfem']; sized=set()
for i in range(4):
 path=root/('sized-'+str(i)+'.bin'); path.write_bytes(b'small'); sized.add(path.resolve())
original_stat=Path.stat
def large_stat(path,*args,**kwargs):
 stat=original_stat(path,*args,**kwargs)
 if path in sized:
  fields=list(stat); fields[6]=MAX_FILE; return os.stat_result(fields)
 return stat
with patch.object(Path,'stat',large_stat),patch.object(Path,'open',side_effect=AssertionError('total size preflight read a file')) as opened:
 try: snapshot_files(root)
 except ValueError: pass
 else: raise AssertionError('oversized total extracted metadata was accepted')
 assert opened.call_count==0
""")

    def test_cli_signs_only_from_anonymous_stdin_and_preserves_existing_output(self):
        self.run_case("""
view(first); view(second); source=market(); views,_=verified_views(receipts,base/'candidate',policy,roots,contexts)
inputs={'bindings':receipts,'views':views,'policy':policy,'artifact-roots':{k:str(v) for k,v in roots.items()},'verified-contexts':contexts}
command=[sys.executable,'-B','scripts/sign_marketplace.py','--marketplace',str(source),'--view-root',str(base/'candidate'),'--sequence','5','--output',str(base/'snapshot.json')]
for name,value in inputs.items():
 path=base/(name+'.json'); path.write_bytes(canonical(value)); command.extend(['--'+name,str(path)])
private=signer.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()); result=subprocess.run(command,input=private,capture_output=True); assert result.returncode==0,result.stderr; raw=(base/'snapshot.json').read_bytes(); assert read_json(raw)['publisherKeyId']=='synthetic-test'; assert not result.stdout
again=subprocess.run(command,input=private,capture_output=True); assert again.returncode!=0; assert (base/'snapshot.json').read_bytes()==raw; assert private.hex().encode() not in result.stdout+result.stderr+again.stdout+again.stderr
""")

    def test_cli_empty_generation_matches_committed_official_market(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "marketplace.json"
            result = subprocess.run(["python", "-B", "scripts/generate_marketplace.py", "--view-root", str(ROOT), "--output", str(output)], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(output.read_bytes(), (ROOT / ".agents/plugins/marketplace.json").read_bytes())
    def test_unapproved_development_roots_are_not_discovered(self):
        from scripts.generate_marketplace import generate_marketplace
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "marketplace.json"
            generate_marketplace([], output, view_root=ROOT)
            self.assertEqual(json.loads(output.read_bytes())["plugins"], [])

    def test_official_empty_market_is_generated(self):
        # RED uses current repository behavior, not a missing import.
        path = ROOT / ".agents/plugins/marketplace.json"
        actual = json.loads(path.read_bytes()) if path.exists() else None
        self.assertEqual(actual, {"name": "team-stable", "interface": {"displayName": "Official stable plugins"}, "plugins": []})


if __name__ == "__main__":
    unittest.main()
