"""Market additions must retain only previously authenticated exact releases."""
import copy
import unittest
from pathlib import Path

from scripts.publisher_signing import validate_market_entry_context


class SkillMarketContextTests(unittest.TestCase):
    def test_market_discovery_order_is_canonical_for_three_packages(self):
        from scripts.generate_marketplace import marketplace_document
        result = marketplace_document([{'name': 'dlubal-rfem'}, {'name': 'rhino'}, {'name': 'rfem-timber-engineering'}])
        self.assertEqual([item['name'] for item in result['plugins']], ['dlubal-rfem', 'rfem-timber-engineering', 'rhino'])

    def test_workflow_signs_only_bound_drafts_and_keeps_market_isolated(self):
        text = (Path(__file__).resolve().parents[1] / '.github/workflows/release-skill.yml').read_text(encoding='utf-8')
        for required in ('environment: plugin-signing', "github.ref == 'refs/heads/main'", 'persist-credentials: false',
                         'Previous market mismatch', 'Previous archive digest mismatch', 'Existing tag targets another commit',
                         'Draft changed during signing', '--previous-market-digest', 'signed-receipt.json', 'market-review'):
            self.assertIn(required, text)
        for forbidden in ('gh release create', 'gh release edit', 'gh release upload', 'git push', 'path: published'):
            self.assertNotIn(forbidden, text)

    def test_retained_release_requires_exact_receipt_and_public_archive(self):
        receipt = {"release": {"packageId": "dlubal-rfem", "pluginVersion": "0.1.2"},
                   "archive": {"url": "https://github.com/XIAOYUGAN666/opendesign-plugins/releases/download/plugin-dlubal-rfem-v0.1.2/a.zip", "sha256": "sha256:" + "a" * 64}}
        context = {"published": True, "packageId": "dlubal-rfem", "version": "0.1.2",
                   "tag": "plugin-dlubal-rfem-v0.1.2", "assetUrl": receipt["archive"]["url"], "archiveDigest": receipt["archive"]["sha256"]}
        validate_market_entry_context(receipt, context, "a.zip", "f" * 40, {"dlubal-rfem": receipt})
        tampered = copy.deepcopy(receipt)
        tampered["archive"]["sha256"] = "sha256:" + "b" * 64
        with self.assertRaises(ValueError):
            validate_market_entry_context(tampered, context, "a.zip", "f" * 40, {"dlubal-rfem": receipt})
        with self.assertRaises(ValueError):
            validate_market_entry_context(receipt, context, "a.zip", "f" * 40, {})
        bad_context = {**context, "published": False}
        with self.assertRaises(ValueError):
            validate_market_entry_context(receipt, bad_context, "a.zip", "f" * 40, {"dlubal-rfem": receipt})

    def test_new_release_still_requires_exact_commit_draft(self):
        receipt = {"release": {"packageId": "new-skill", "pluginVersion": "1.0.0"},
                   "archive": {"url": "https://github.com/XIAOYUGAN666/opendesign-plugins/releases/download/plugin-new-skill-v1.0.0/a.zip", "sha256": "sha256:" + "a" * 64}}
        context = {"draft": True, "sourceCommit": "f" * 40, "targetCommit": "f" * 40,
                   "packageId": "new-skill", "version": "1.0.0", "tag": "plugin-new-skill-v1.0.0",
                   "assetUrl": receipt["archive"]["url"], "archiveDigest": receipt["archive"]["sha256"]}
        validate_market_entry_context(receipt, context, "a.zip", "f" * 40, {})
        with self.assertRaises(ValueError):
            validate_market_entry_context(receipt, {**context, "targetCommit": "e" * 40}, "a.zip", "f" * 40, {})
