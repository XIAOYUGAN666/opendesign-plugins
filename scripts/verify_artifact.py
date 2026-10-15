"""Independent extracted-byte verification; no Provider private imports."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import tempfile
import threading
import time
import tomllib
import zipfile

NAMESPACE = "io.github.xiaoyugan666.native"
MAX_ARCHIVE = 1024 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024

PROBE_PHASES = frozenset({"startup", "ready", "connect", "initialize", "listTools", "metadataCall", "runtime"})
SAFE_ERRORS = frozenset({"outputMustBeFreshAndOutsideRepository", "missingSource", "reparseSource", "unsafeProviderModule", "rootChangedDuringBuild", "typedStagedCatalogMismatch", "invalidArtifact", "archiveReceiptMismatch", "releaseReceiptMismatch", "rootReleaseMismatch", "providerRootVersionMismatch", "providerRootInputsMismatch", "filesDigestMismatch", "invalidFileInventory", "fileDigestMismatch", "undeclaredFile", "toolCatalogDigestMismatch", "unsupportedPreviewNormalization", "startupDeclarationVersionMismatch", "sbomDigestMismatch", "riskEvidenceMismatch", "unknownBuildIdentityProfile", "buildIdentityMismatch", "sbomRuntimeMismatch", "stagedToolCatalogMismatch", "runtimeIsolationMismatch", "stagedProviderProbeFailed", "stagedProviderProbeTimeout", "providerReadyTimeout"}) | frozenset("stagedProviderProbeFailed_" + phase for phase in PROBE_PHASES)


def safe_error_category(error):
    if isinstance(error, ValueError) and str(error) in SAFE_ERRORS:
        return str(error)
    if isinstance(error, subprocess.TimeoutExpired):
        return "buildStepTimeout"
    if isinstance(error, subprocess.CalledProcessError):
        return "buildStepFailed"
    return "verificationFailed"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicateKey")
        result[key] = value
    return result


def read_json(data):
    def reject(_value):
        raise ValueError("invalidJsonConstant")
    return json.loads(data, object_pairs_hook=unique, parse_constant=reject)


def safe_path(name):
    parts = name.split("/")
    if not name or "\\" in name or ":" in name or any(p in {"", ".", ".."} for p in parts):
        raise ValueError("unsafeArchivePath")
    if any(p.endswith((".", " ")) or PureWindowsPath(p).is_reserved() for p in parts):
        raise ValueError("unsafeArchivePath")
    return parts


def extract(archive, root):
    seen = set()
    total = 0
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            parts = safe_path(item.orig_filename)
            folded = item.orig_filename.casefold()
            total += item.file_size
            if folded in seen or item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000 or item.flag_bits & 1 or item.file_size > MAX_FILE or total > MAX_ARCHIVE:
                raise ValueError("unsafeArchive")
            seen.add(folded)
            target = root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as stream:
                data = stream.read(item.file_size + 1)
            if len(data) != item.file_size:
                raise ValueError("unsafeArchive")
            target.write_bytes(data)


def validate(value, schema, definition=None):
    # Official validator, offline registry: unrecognized references fail closed.
    from jsonschema import FormatChecker, validators
    from referencing import Registry
    if definition:
        schema = {"$schema": schema.get("$schema", "https://json-schema.org/draft/2020-12/schema"), "$defs": schema["$defs"], "$ref": "#/$defs/" + definition}
    def deny(_uri):
        raise ValueError("externalSchemaReference")
    validator = validators.validator_for(schema)
    validator.check_schema(schema)
    checker = FormatChecker()
    # Width semantics extend the official format hook, never copied field schemas.
    def register_widths(node):
        if isinstance(node, dict):
            width = re.fullmatch(r"(u?)int(8|16|32|64)", str(node.get("format", "")))
            if width:
                bits = int(width[2])
                lower, upper = (0, 2**bits) if width[1] else (-2**(bits-1), 2**(bits-1))
                checker.checks(width[0])(lambda number, lower=lower, upper=upper: type(number) is int and lower <= number < upper)
            for child in node.values():
                register_widths(child)
        elif isinstance(node, list):
            for child in node:
                register_widths(child)
    register_widths(schema)
    validator(schema, registry=Registry(retrieve=deny), format_checker=checker).validate(value)


def release_identity(identity):
    native = identity["extensions"][NAMESPACE]
    keys = ("platform", "hostContract", "agentContract", "startupEnvelopeVersion", "entrypoint", "module", "components", "mcp", "software", "adapter", "configurationSchemaVersion")
    result = {k: native[k] for k in keys}
    if "native" in native:
        result["native"] = native["native"]
    result.update(manifestSchemaVersion=1, packageId=identity["name"], pluginVersion=identity["version"], releaseSequence=1, artifactVariantId=f"{native['platform']['os']}-{native['platform']['architecture']}-{native['software']['product']}-{native['software']['major']}")
    return result


def validate_configuration_declaration(schema):
    from jsonschema import validators
    validators.validator_for(schema).check_schema(schema)


# Runs inside the archive's embedded interpreter and imports only official clients.
# Provider startup is its public -m/anonymous-stdin interface, discovery standard MCP.
PROBE = r'''
import asyncio,json,os,socket,sys,uuid,platform
from pathlib import Path
from importlib.metadata import version
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams
import httpx2
from jsonschema import validators
from referencing import Registry
sys.stdout.reconfigure(encoding='utf-8')
probe_stage='startup'
async def main():
 global probe_stage
 root=Path(sys.argv[1]); release=json.loads((root/'release.json').read_text('utf-8'))
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
 connector='ci_'+uuid.uuid4().hex; token=uuid.uuid4().hex+uuid.uuid4().hex
 envelope={'schemaVersion':release['startupEnvelopeVersion'],'mode':'metadata','connectorInstanceId':connector,'generation':1,'bind':{'host':'127.0.0.1','port':port,'path':'/mcp'},'bearerToken':token,'providerBuildId':release['providerBuildId']}
 schema=json.loads((root/'startup-envelope.schema.json').read_text('utf-8'))
 validator=validators.validator_for(schema); validator.check_schema(schema)
 def deny(_uri): raise ValueError('externalSchemaReference')
 validator(schema,registry=Registry(retrieve=deny)).validate(envelope)
 env={k:os.environ[k] for k in ('SystemRoot','WINDIR','TEMP','TMP') if k in os.environ}
 process=await asyncio.create_subprocess_exec(sys.executable,'-I','-s','-B','-m',release['module'],cwd=root,env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
 try:
  process.stdin.write(json.dumps(envelope,separators=(',',':')).encode()); await process.stdin.drain(); process.stdin.close()
  probe_stage='ready'
  try: ready=json.loads(await asyncio.wait_for(process.stdout.readline(),30))
  except TimeoutError: raise ValueError('providerReadyTimeout') from None
  expected={'schemaVersion':2,'kind':'ready','mode':'metadata','connectorInstanceId':connector,'generation':1,'providerBuildId':release['providerBuildId'],'port':port}
  if ready!=expected: raise ValueError('invalidReady')
  async def drain_lifecycle():
   sequence=0
   lifecycle=json.loads((root/'lifecycle.schema.json').read_text('utf-8'))
   while True:
    line=await process.stdout.readline()
    if not line: return
    if len(line)>4096: raise ValueError('oversizedLifecycleFrame')
    frame=json.loads(line)
    validators.validator_for(lifecycle)(lifecycle,registry=Registry(retrieve=deny)).validate(frame)
    if frame.get('kind')!='status' or frame.get('generation')!=1 or frame.get('connectorInstanceId')!=connector or type(frame.get('sequence')) is not int or frame['sequence']<=sequence: raise ValueError('invalidLifecycleStatus')
    sequence=frame['sequence']
  drain=asyncio.create_task(drain_lifecycle())
  probe_stage='connect'
  async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False) as http:
   async with streamable_http_client(f'http://127.0.0.1:{port}/mcp',http_client=http) as streams:
    async with ClientSession(streams[0],streams[1]) as client:
     probe_stage='initialize'
     await client.initialize(); declarations=[]; cursor=None; seen=set()
     probe_stage='listTools'
     while True:
      page=await client.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
      declarations.extend(t.model_dump(mode='json',by_alias=True,exclude_none=True) for t in page.tools)
      cursor=page.next_cursor
      if not cursor: break
      if cursor in seen or len(seen)>1024: raise ValueError('invalidToolPagination')
      seen.add(cursor)
     tools=sorted(declarations,key=lambda t:t['name'])
     if len({t['name'] for t in tools})!=len(tools): raise ValueError('duplicateToolIdentity')
     probe_stage='metadataCall'
     for tool in tools:
      result=await client.call_tool(tool['name'],{})
      if not getattr(result,'is_error',False): raise ValueError('metadataToolDispatched')
  probe_stage='runtime'
  if drain.done() and drain.exception(): raise drain.exception()
  print(json.dumps({'preview':{'normalizationVersion':1,'tools':tools},'ready':ready,'runtime':{'python':platform.python_version(),'architecture':platform.machine(),'isolated':sys.flags.isolated,'packages':{w['name']:version(w['name']) for w in json.loads((root/'runtime-lock.json').read_text('utf-8'))['wheels']},'executable':sys.executable}},ensure_ascii=False,separators=(',',':')))
 finally:
  if process.returncode is None: process.kill()
  await process.wait()
  if 'drain' in locals():
   drain.cancel()
   try: await drain
   except asyncio.CancelledError: pass
try:
 asyncio.run(asyncio.wait_for(main(),65))
except Exception as error:
 failure='providerReadyTimeout' if isinstance(error,ValueError) and str(error)=='providerReadyTimeout' else 'stagedProviderProbeTimeout' if isinstance(error,TimeoutError) else 'stagedProviderProbeFailed_'+probe_stage if probe_stage in {'startup','ready','connect','initialize','listTools','metadataCall','runtime'} else 'stagedProviderProbeFailed'
 print(json.dumps({'probeFailure':failure},separators=(',',':')))
 raise SystemExit(1) from None
'''


def discover(root):
    environment = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
    release = read_json((root / "release.json").read_bytes())
    executable = root.joinpath(*safe_path(release["entrypoint"]))
    process = subprocess.Popen([str(executable), "-I", "-s", "-B", "-c", PROBE, str(root)], cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks = []
    oversized = threading.Event()
    def stop_tree():
        if process.poll() is None:
            subprocess.run([str(Path(environment["SystemRoot"]) / "System32/taskkill.exe"), "/F", "/T", "/PID", str(process.pid)], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    def read_output():
        length = 0
        while chunk := process.stdout.read(65536):
            length += len(chunk)
            if length > 16 * 1024 * 1024:
                oversized.set()
                stop_tree()
                break
            chunks.append(chunk)
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        process.wait(timeout=75)
    except subprocess.TimeoutExpired:
        stop_tree()
        process.wait(timeout=10)
        raise ValueError("stagedProviderProbeTimeout") from None
    finally:
        if process.poll() is None:
            stop_tree()
            process.wait(timeout=10)
        reader.join(timeout=10)
        process.stdout.close()
    if process.returncode or oversized.is_set() or reader.is_alive():
        try:
            failure = read_json(b"".join(chunks))
            if set(failure) == {"probeFailure"} and failure["probeFailure"] in ({"providerReadyTimeout", "stagedProviderProbeTimeout"} | {"stagedProviderProbeFailed_" + phase for phase in PROBE_PHASES}):
                raise ValueError(failure["probeFailure"])
        except (TypeError, KeyError, json.JSONDecodeError):
            pass
        raise ValueError("stagedProviderProbeFailed")
    return read_json(b"".join(chunks))


def verify_build_identity(root, module, identity, expected):
    """Rebuild a fixed public input profile from extracted bytes, never code."""
    if set(identity) != {"profile", "files"} or identity["profile"] != "input-manifest-v1":
        raise ValueError("unknownBuildIdentityProfile")
    parts = module.split(".")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts):
        raise ValueError("unsafeProviderModule")
    base = root / "runtime/python/Lib/site-packages"
    for name in ("pyproject.toml", "uv.lock"):
        outer, embedded = root / name, base / name
        if not outer.is_file() or not embedded.is_file() or outer.read_bytes() != embedded.read_bytes():
            raise ValueError("providerRootInputsMismatch")
    source = base / parts[0]
    files = list(source.rglob("*.py")) + [base / "pyproject.toml", base / "uv.lock"]
    for name in ("runtime-lock.json", "release-input.json"):
        path = base / "packaging" / name
        if path.exists():
            files.append(path)
    manifest = {path.relative_to(base).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    if not list(source.rglob("*.py")) or identity["files"] != manifest or expected != digest(canonical(manifest)):
        raise ValueError("buildIdentityMismatch")


def verify_artifact(archive: Path, receipt: Path, schema_bundle: Path, *, trusted_keys=None, unsigned_candidate=False) -> dict[str, str]:
    archive = archive.resolve(strict=True)
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise ValueError("invalidArtifact")
    signed = read_json(receipt.read_bytes())
    if unsigned_candidate:
        if "signature" in signed:
            raise ValueError("signedReceiptInUnsignedMode")
        shape = {**signed, "signature": ""}
    else:
        shape = signed
    contract = read_json((schema_bundle / "host-contract.v3.schema.json").read_bytes())
    validate(shape, contract, "SignedReleaseV1")
    if type(signed["notBefore"]) is not int or type(signed["expiresAt"]) is not int or signed["notBefore"] <= 0 or signed["expiresAt"] <= signed["notBefore"] or signed["revoked"] is not False:
        raise ValueError("invalidReceiptValidity")
    if not unsigned_candidate:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = (trusted_keys or {}).get(signed["publisherKeyId"])
        if key is None or not re.fullmatch(r"[a-f0-9]{128}", signed["signature"]):
            raise ValueError("untrustedReceipt")
        payload = {k: v for k, v in signed.items() if k != "signature"}
        Ed25519PublicKey.from_public_bytes(key).verify(bytes.fromhex(signed["signature"]), canonical({"signatureSchemaVersion": 1, **payload}))
        if not signed["notBefore"] <= int(time.time()) < signed["expiresAt"]:
            raise ValueError("receiptExpiredOrNotYetValid")
    data = archive.read_bytes()
    if signed["archive"]["byteLength"] != len(data) or signed["archive"]["sha256"] != digest(data):
        raise ValueError("archiveReceiptMismatch")
    with tempfile.TemporaryDirectory(prefix="artifact-verify-") as folder:
        root = Path(folder)
        extract(io.BytesIO(data), root)
        release = read_json((root / "release.json").read_bytes())
        if release != signed["release"]:
            raise ValueError("releaseReceiptMismatch")
        validate(release, contract, "ReleaseManifestV1")
        identity = read_json((root / "plugin.json").read_bytes())
        validate(identity, read_json((schema_bundle / "agent-plugins-1.0.0.schema.json").read_bytes()))
        validate(identity["extensions"][NAMESPACE], read_json((schema_bundle / "host-native.schema.json").read_bytes()))
        for key, value in release_identity(identity).items():
            if release.get(key) != value:
                raise ValueError("rootReleaseMismatch")
        project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]
        if project["version"] != identity["version"]:
            raise ValueError("providerRootVersionMismatch")
        inventory_bytes = (root / "files.json").read_bytes()
        inventory = read_json(inventory_bytes)
        if release["filesDigest"] != digest(inventory_bytes) or set(inventory) != {"schemaVersion", "files"} or inventory["schemaVersion"] != 1:
            raise ValueError("filesDigestMismatch")
        expected = {"files.json", "release.json"}
        for item in inventory["files"]:
            if set(item) != {"path", "byteLength", "sha256"} or item["path"] in expected:
                raise ValueError("invalidFileInventory")
            path = root.joinpath(*safe_path(item["path"]))
            content = path.read_bytes()
            if type(item["byteLength"]) is not int or len(content) != item["byteLength"] or digest(content) != item["sha256"]:
                raise ValueError("fileDigestMismatch")
            expected.add(item["path"])
        if {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} != expected:
            raise ValueError("undeclaredFile")
        preview_bytes = (root / "tool-preview.json").read_bytes()
        preview = read_json(preview_bytes)
        if canonical(preview) != preview_bytes or release["toolCatalogDigest"] != digest(preview_bytes):
            raise ValueError("toolCatalogDigestMismatch")
        preview_schema = read_json((root / "tool-preview.schema.json").read_bytes())
        validate(preview, preview_schema)
        if preview_schema["properties"]["normalizationVersion"].get("const") != 1:
            raise ValueError("unsupportedPreviewNormalization")
        configuration = read_json(root.joinpath(*safe_path(identity["extensions"][NAMESPACE]["configurationSchemaPath"])).read_bytes())
        validate_configuration_declaration(configuration)
        startup = read_json((root / "startup-envelope.schema.json").read_bytes())
        from jsonschema import validators
        validators.validator_for(startup).check_schema(startup)
        if startup.get("properties", {}).get("schemaVersion", {}).get("const") != release["startupEnvelopeVersion"]:
            raise ValueError("startupDeclarationVersionMismatch")
        if release["sbomDigest"] != digest((root / "sbom.spdx.json").read_bytes()):
            raise ValueError("sbomDigestMismatch")
        risk = read_json((root / "risk-review.json").read_bytes())
        validate(risk, read_json((root / "risk-review.schema.json").read_bytes()))
        module = release["module"].split(".")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in module):
            raise ValueError("unsafeProviderModule")
        source_root = root / "runtime/python/Lib/site-packages" / module[0]
        sources = [{"path": p.relative_to(source_root).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(source_root.rglob("*.py"))]
        if not sources or risk["sourceFiles"] != sources or risk["sourceDigest"] != digest(canonical(sources)) or risk["providerBuildId"] != release["providerBuildId"] or risk["previewDigest"] != release["toolCatalogDigest"] or risk["status"] not in {"pending-review/internal-fixture-only", "pending-review/provider-candidate"} or risk["engineeringAcceptance"] != "not-performed":
            raise ValueError("riskEvidenceMismatch")
        if "buildIdentity" in risk:
            verify_build_identity(root, release["module"], risk["buildIdentity"], release["providerBuildId"])
        elif risk["providerBuildId"] != digest(canonical(sources)):
            raise ValueError("buildIdentityMismatch")
        locked = read_json((root / "runtime-lock.json").read_bytes())
        sbom = read_json((root / "sbom.spdx.json").read_bytes())
        expected_packages = {(x["name"], x["version"], x["url"], x["sha256"][7:]) for x in locked["wheels"]}
        expected_packages.add(("python", "3.12.10", locked["python"]["url"], locked["python"]["sha256"][7:]))
        expected_packages.add((identity["name"], identity["version"], "NOASSERTION", release["providerBuildId"][7:]))
        actual_packages = {(x["name"], x["versionInfo"], x["downloadLocation"], x["checksums"][0]["checksumValue"]) for x in sbom["packages"]}
        if sbom["name"] != identity["name"] or len(sbom["packages"]) != len(expected_packages) or actual_packages != expected_packages or any(x["checksums"][0]["algorithm"] != "SHA256" for x in sbom["packages"]):
            raise ValueError("sbomRuntimeMismatch")
        observed = discover(root)
        if observed["preview"] != preview:
            raise ValueError("stagedToolCatalogMismatch")
        validate(observed["ready"], read_json((root / "lifecycle.schema.json").read_bytes()))
        runtime = observed["runtime"]
        if runtime["python"] != "3.12.10" or runtime["architecture"].upper() != "AMD64" or runtime["isolated"] != 1 or runtime["packages"] != {w["name"]:w["version"] for w in locked["wheels"]} or not Path(runtime["executable"]).is_relative_to(root):
            raise ValueError("runtimeIsolationMismatch")
    return {"archiveDigest": digest(data), "filesDigest": release["filesDigest"], "toolCatalogDigest": release["toolCatalogDigest"], "verification": "unsigned-candidate" if unsigned_candidate else "signed-receipt"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("schema_bundle", type=Path)
    parser.add_argument("--unsigned", action="store_true")
    parser.add_argument("--public-key", help="Caller-controlled raw Ed25519 public key hex")
    parser.add_argument("--key-id")
    args = parser.parse_args()
    try:
        keys = {args.key_id: bytes.fromhex(args.public_key)} if args.public_key and args.key_id else None
        print(canonical(verify_artifact(args.archive, args.receipt, args.schema_bundle, trusted_keys=keys, unsigned_candidate=args.unsigned)).decode())
    except Exception as error:
        parser.exit(1, "artifactVerificationFailed:" + safe_error_category(error) + "\n")


if __name__ == "__main__":
    main()
