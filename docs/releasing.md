# Independent candidates and approved Stable publication

The official repository is `XIAOYUGAN666/opendesign-plugins`, with one development branch `main`. The runtime source identity remains `github:XIAOYUGAN666/opendesign-plugins`. Development inputs live under `plugins/<id>/`; each Provider owns its project, frozen lock, virtual environment and tests. The root tooling has no Python environment or shared dependency project.

The generated `.agents/plugins/marketplace.json` currently contains an empty discovery array. No approved Stable release, `published/` view or signed marketplace snapshot exists. Empty discovery does not establish a signed empty snapshot or Codex client UI acceptance. RFEM and Rhino now have independent source projects; their native engineering acceptance remains separately tracked in T32/T33. Synthetic fixture tests do not prove either engineering integration.

## Verification and candidate workflows

Run root checks with `python -B -m unittest discover -s tests -v`. Cryptographic and runtime checks execute through the existing independent fixture tool environment, prepared from `tests/fixtures/fixture-engineering/provider/` with `uv sync --frozen --extra test --extra build`. Run its tests there with `uv run --frozen --extra test python -B -m pytest -q`.

Both workflows pin official action commits, Windows 2025, Python 3.12.10 and uv 0.9.21; projects consume their own frozen locks. `verify.yml` has root binding checks, independent fixture checks and two independently resolved Provider projects. An absent project is `notPresent`, not a Provider test/build pass. Once a real Provider migrates, its own checks run in its own job/environment.

The manual `release-plugin.yml` defaults to validation/build of the synthetic fixture. It derives the version from the selected root and checks the project version. It does not create a tag, GitHub Release, draft Release, Stable view, marketplace or signature. There is no signing job, secret or protected environment configured by this workflow. Local common tooling accepts the migrated Provider builders; this public workflow still deliberately rejects real Provider artifact uploads and builds only the synthetic fixture. Source migration does not enable publication.

Actions artifacts in this public repository are public evidence, not private channels. Uploads are limited to safe synthetic fixture candidates, with no engineering software, keys, business models or production authorization. Real Provider prerelease candidates are never automatically uploaded. A future controlled signing job requires independently confirmed environment protection/reviewers and signer scope; an environment name alone is not approval protection.

## Generate from immutable verified inputs

Every artifact build requires an explicit publisher `--release-sequence`; there is no default counter. It is independent of the marketplace snapshot sequence. Advance the package/variant counter and root version for changed assets; never reuse a published version, reset Host trust history or rebind an old counter to new ZIP bytes. Root Manifest remains the sole human-maintained plugin identity.

Generate inputs with `python -B scripts/build_artifact.py plugins/<id> --generate-release-input --release-sequence <counter>`. One invocation derives the root `packaging/release-input.json` from root bytes and publisher counter, and synchronizes an existing Provider copy used by its build. It does not create unused Provider input files. Build with the same explicit counter and a fresh external output directory; drift is rejected before Provider staging. RFEM embeds and hashes its generated Provider input. Archive verification checks that embedded input against root bytes, release identity and the receipt-bound counter. The synthetic CI fixture explicitly uses counter 1; this grants no signing or Stable approval.

The three Python interfaces are:

```python
generate_stable_view(artifact_root, receipt, output_root,
                     trust_policy=policy, verified_context=verification)
generate_marketplace(bindings, output, view_root=output_root,
                     trust_policy=policy, artifact_roots=roots,
                     verified_contexts=verifications)
sign_marketplace(raw, bindings, sequence, signer, views=views,
                 trust_policy=policy, view_root=output_root,
                 artifact_roots=roots, verified_contexts=verifications)
```

Nonempty inputs fail closed without caller-controlled authorization and immutable extracted artifact roots. The policy is release-tool input, not a plugin manifest, second identity catalog or Host runtime contract. It contains the exact `repositoryIdentity`; `keys` indexed by key ID, with raw public-key hex, authorized package IDs, validity times and explicit `revoked=false`; `approvedArchives` mapping approved archive digests to actually confirmed URLs; `revokedArchives`; and, for marketplace signing, `marketplaceKeyId` and the previously accepted `previousSequence`. Authorization and confirmed URLs come from the publisher, never from the receipt's self-assertion. No production trust or approval is included in this repository. Test policies and ephemeral signers authorize only synthetic test inputs inside temporary folders.

T07 independently verifies the signed Windows archive with `scripts/verify_artifact.py`, returning `archiveDigest`, `filesDigest`, `toolCatalogDigest` and `verification="signed-receipt"`. Provide that result as `verified_context`. It is a cross-check/cache record, not cryptographic proof: generation still independently verifies the receipt signature, authorized repository/package/key/time/revocation, release bytes, full closed inventory, every file digest, root identity and preview/SBOM bindings. It never executes Provider/MCP just to project a static view. Marketplace generation and signing repeat those checks and compare every active view file to the approved artifact projection. No current development root is scanned for formal discovery.

Authenticated `revoked=true` receipts are revocation-only bindings. They require a current authorized signer, exact source/package scope, valid signature and validity window, and explicit previously approved archive URL/digest history in the caller's `approvedArchives`. They remain in snapshot `releases` but never create a discovery entry, active view or runtime permission; no artifact root/context is needed for a tombstone. An old revoked archive and a distinct new active archive can share a package ID, producing one active view. Duplicate or conflicting statements for an exact archive and multiple active releases of one package are rejected. Active receipts for locally known revoked archives remain rejected. This producer support is available now for the later Host consumer; it does not publish a formal Stable snapshot or implement Host persisted revocation.

The shared official JSON-schema validator installs `FormatChecker` guards for exported signed/unsigned integer-width formats rather than copied field schemas; receipt/snapshot numeric widths therefore match the authoritative owner. Caller-policy times are explicitly bounded to uint64. Both archive extraction and static projection use the same `MAX_ARCHIVE` and `MAX_FILE` limits. Static projection validates every file's stat size and aggregate size before reading any content, then reads bounded bytes and rejects size changes.

CLI examples below describe input files supplied by the controlled publisher; no approved input files or asset URLs exist yet. Keep these inputs and outputs in ignored `dist/` or an external candidate directory. `artifact-roots.json` is a temporary map of package IDs to verified extracted artifact directories; `verified-contexts.json` maps the same IDs to verifier results. `views.json` contains the owner-exported `StableViewInventoryV1` records, generated with `inventory_for` from the verified portable bytes.

```powershell
python -B scripts/generate_marketplace.py --view-root . --output dist/candidate/marketplace.json
# In the existing independent fixture/Provider tooling environment:
python -B scripts/generate_stable_view.py <verified-artifact-root> <signed-receipt.json> --policy <publisher-policy.json> --verified-context <verification.json> --output-root dist/candidate
python -B scripts/generate_marketplace.py --bindings <approved-bindings.json> --view-root dist/candidate --output dist/candidate/marketplace.json --policy <publisher-policy.json> --artifact-roots <artifact-roots.json> --verified-contexts <verified-contexts.json>
python -B scripts/sign_marketplace.py --marketplace dist/candidate/marketplace.json --bindings <approved-bindings.json> --views <views.json> --policy <publisher-policy.json> --artifact-roots <artifact-roots.json> --verified-contexts <verified-contexts.json> --view-root dist/candidate --sequence <next-sequence> --output dist/candidate/marketplace.snapshot.json
```

The signing command receives exactly 32 raw private-key bytes on anonymous stdin, after validating public inputs. Never put private keys in arguments, environment values, logs or plugin runtime dependencies. Only public keys occur in policy input. The signer must match the authorized marketplace key; sequence must increase; the snapshot validity is bounded by every receipt and signer authorization. Signing uses T07's canonical Ed25519 helper and the exported `SignedMarketplaceV1`/`StableViewInventoryV1` owner schema, not duplicated schema declarations. A required engineering API key gates connection, not package installation.

Views retain original root, complete Skills/resources, assets and same-origin configuration/preview/README bytes. Broken relative Markdown references, undeclared files, reparse points and missing resources reject generation. Managed native entrypoints and Yak/runtime files resolve only in the signed Windows install asset; they cannot resolve in the portable view. The first managed plugins have no standard `mcp.json` and no fake executable endpoint is generated. Root versions come from the verified manifest; the official discovery entry has no manually copied version and uses `./published/<id>` relative to the repository root. Current engineering categories are `Engineering Design`, not a global category taxonomy.

## Final publication and failure preservation

1. Independently build each plugin from its root version and frozen lock; complete required real software/native acceptance separately. RFEM/Rhino tags are independent: `plugin-<id>-v<root-version>`.
2. With explicit candidate-operation authorization, prepare any actual draft Release and authenticated-download verification. A GitHub draft requires a tag and is not anonymous public-download proof. The current workflow performs neither operation.
3. Sign receipts under confirmed authorization, verify exact draft asset bytes, and generate candidate view, official discovery and signed snapshot in ignored output. Review exact bytes, digests, signer scope, validity, known revocations and candidate report.
4. Obtain final publisher approval for those exact inputs. Publish the immutable Stable release, anonymously download and reverify its actual URL and bytes. No invented or unconfirmed release URL may enter approved inputs.
5. Only after successful anonymous revalidation, publish the approved views, official discovery file and signed snapshot together in one `main` Git tree. Preserve `/published/** -text` and marketplace byte attributes so checkout does not change signed bytes. Consumers read this single commit consistently.

Failed validation leaves previous marketplace bytes untouched; an existing Stable projection can only be reused byte-identically and is never overwritten by a changed candidate. A new development version does not update discovery or the approved view. Failed publication/download/signing must retain the prior trusted market/view; never overwrite immutable tags/assets or repair Git byte drift by resigning altered files. Host cache, installation trust, rotation and persisted revocation remain Host responsibilities in later tasks.

Syntax parsing/local commands are not evidence of a GitHub Actions run. The controller must push the verified change, dispatch `verify.yml`, and confirm the actual run's exact `headSha` and all applicable successful jobs. `notPresent`, queued or skipped work is not proof that a real Provider passed.

References: [official repository marketplace format](https://developers.openai.com/plugins/build/plugins), [official example](https://github.com/openai/plugins/blob/main/.agents/plugins/marketplace.json), [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), [environment protection](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments), [public artifact download semantics](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).
