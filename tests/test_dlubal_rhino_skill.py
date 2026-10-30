import json
import re
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from pathlib import PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'plugins/dlubal-rhino'
PYTHON = ROOT / 'tests/fixtures/fixture-engineering/connector/.venv/Scripts/python.exe'


class DlubalRhinoSkillTests(unittest.TestCase):
    def test_evaluation_corpus_has_development_and_holdout_cases(self):
        cases = json.loads((ROOT / 'tests/fixtures/dlubal_rhino_skill_cases.json').read_text(encoding='utf-8'))
        self.assertEqual(cases['skill_name'], 'rhino-to-rfem-structural-model')
        triggers = cases['trigger_cases']
        self.assertEqual(len({item['id'] for item in triggers}), len(triggers))
        for expected in (True, False):
            selected = [item for item in triggers if item['should_trigger'] is expected]
            self.assertGreaterEqual(len(selected), 8)
            self.assertGreaterEqual(sum(item['split'] == 'holdout' for item in selected), 2)
            self.assertTrue(all(item['prompt'].strip() for item in selected))
        workflows = cases['workflow_cases']
        self.assertEqual({item['id'] for item in workflows}, {f'B{i:02d}' for i in range(1, 11)})
        self.assertGreaterEqual(sum(item['split'] == 'holdout' for item in workflows), 2)
        self.assertTrue(all(item['facts'] and item['assertions'] for item in workflows))

    def test_skill_references_resolve_inside_package(self):
        skill = SOURCE / 'skills/rhino-to-rfem-structural-model'
        text = (skill / 'SKILL.md').read_text(encoding='utf-8')
        links = re.findall(r'\]\((references/[^)]+)\)', text)
        self.assertEqual(len(links), 5)
        for link in links:
            target = (skill / link).resolve()
            self.assertTrue(target.is_relative_to(skill.resolve()))
            self.assertTrue(target.is_file(), link)

    def test_candidate_round_trip_is_skill_only_and_reproducible(self):
        self.assertTrue(SOURCE.is_dir(), 'dlubal-rhino source package is missing')
        with tempfile.TemporaryDirectory() as tmp:
            archives = []
            for i in range(2):
                out = Path(tmp) / str(i)
                built = subprocess.run([str(PYTHON), '-B', str(ROOT / 'scripts/build_skill_artifact.py'), str(SOURCE), '--release-sequence', '1', '--output', str(out)], capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stderr)
                archive = next(out.glob('*.zip'))
                receipt = json.loads((out / 'unsigned-receipt.json').read_text(encoding='utf-8'))
                verified = subprocess.run([str(PYTHON), '-B', str(ROOT / 'scripts/verify_skill_artifact.py'), str(archive), str(out / 'unsigned-receipt.json'), '--unsigned'], capture_output=True, text=True, timeout=60)
                self.assertEqual(verified.returncode, 0, verified.stderr)
                self.assertEqual(json.loads(verified.stdout)['verification'], 'unsigned-candidate')
                self.assertEqual(receipt['release']['packageId'], 'dlubal-rhino')
                self.assertEqual(receipt['release']['pluginVersion'], json.loads((SOURCE / 'plugin.json').read_text(encoding='utf-8'))['version'])
                self.assertNotIn('connectorBuildId', receipt['release'])
                with zipfile.ZipFile(archive) as z:
                    files = {n: z.read(n) for n in z.namelist()}
                skill_path = 'skills/rhino-to-rfem-structural-model/SKILL.md'
                for link in re.findall(r'\]\(([^)]+)\)', files[skill_path].decode('utf-8')):
                    self.assertFalse(link.startswith(('/', '\\')))
                    target = PurePosixPath(skill_path).parent / link
                    self.assertNotIn('..', target.parts)
                    self.assertIn(target.as_posix(), files)
                source = {p.relative_to(SOURCE).as_posix(): p.read_bytes() for p in SOURCE.rglob('*') if p.is_file()}
                self.assertEqual(files, source)
                self.assertNotIn('io.github.xiaoyugan666.native', json.loads(files['plugin.json']).get('extensions', {}))
                archives.append(archive.read_bytes())
            self.assertEqual(archives[0], archives[1])


if __name__ == '__main__':
    unittest.main()
