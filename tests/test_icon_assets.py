"""Release icon regressions; does not load or modify user data."""
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZES = {16, 20, 24, 28, 32, 40, 48, 64, 96, 128, 256}


class IconAssetTests(unittest.TestCase):
    def test_ico_frames_cover_taskbar_dpi_sizes(self):
        for name in ('assets/科研工作台.ico', 'ui/favicon.ico'):
            with Image.open(ROOT / name) as ico:
                self.assertEqual(ico.ico.sizes(), {(s, s) for s in SIZES})
                for s in SIZES:
                    frame = ico.ico.getimage((s, s)).convert('RGBA')
                    # Small rounded corners occupy part of the corner pixel.
                    self.assertLess(frame.getpixel((0, 0))[3], 128)
                    color = frame.getpixel((s // 8, s // 2))[:3]
                    self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(color, (87, 134, 101))))

    def test_exe_and_web_icons_are_identical(self):
        self.assertEqual((ROOT / 'assets/科研工作台.ico').read_bytes(),
                         (ROOT / 'ui/favicon.ico').read_bytes())

    def test_png_is_real_png_and_matches_large_ico(self):
        with Image.open(ROOT / 'ui/favicon.png') as png, Image.open(ROOT / 'ui/favicon.ico') as ico:
            self.assertEqual(png.format, 'PNG')
            self.assertEqual(png.size, (256, 256))
            self.assertEqual(png.convert('RGBA').tobytes(),
                             ico.ico.getimage((256, 256)).convert('RGBA').tobytes())

    def test_html_cache_version_and_high_resolution_png(self):
        for name in ('_template.html', 'index.html'):
            html = (ROOT / 'ui' / name).read_text(encoding='utf-8')
            self.assertIn('favicon.png?v=2.4.8-icon2', html)
            self.assertIn('96x96 128x128 256x256', html)


if __name__ == '__main__':
    unittest.main()
