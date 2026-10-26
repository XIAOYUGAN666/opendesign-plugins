import tempfile
import unittest
from pathlib import Path

from scripts.presentation_assets import validate_assets, validate_icon


class PresentationAssetsTests(unittest.TestCase):
    def test_magic_bytes_without_an_image_do_not_pass_the_candidate_gate(self):
        import struct
        for name, data in (
            ("icon.jpg", b'\xff\xd8'),
            ("icon.webp", b'RIFF\x04\x00\x00\x00WEBP'),
            ("icon.png", b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 64, 64)),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_icon(name, data)

    def test_declared_assets_must_exist_and_be_contained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identity = {"extensions": {"com.openai": {"interface": {"logo": "./assets/icon.svg"}}}}
            with self.assertRaises(ValueError):
                validate_assets(root, identity)
            (root / "assets").mkdir()
            (root / "assets/icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="68" height="68"/>')
            validate_assets(root, identity)
            identity["extensions"]["com.openai"]["interface"]["logo"] = "./assets/../../private.svg"
            with self.assertRaises(ValueError):
                validate_assets(root, identity)

    def test_external_svg_and_oversize_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "assets").mkdir()
            icon = root / "assets/icon.svg"
            identity = {"extensions": {"com.openai": {"interface": {"logo": "./assets/icon.svg"}}}}
            icon.write_text('<svg width="64" height="64"><image href="https://example.com/"/></svg>')
            with self.assertRaises(ValueError):
                validate_assets(root, identity)
            icon.write_bytes(b'x' * (1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                validate_assets(root, identity)
