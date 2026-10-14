"""Generate the release icons from assets/keyan.svg, without changing its artwork.

Requires Pillow and Node.js + sharp (npm install sharp).
Usage: python tools/build_vector_icon.py [--node NODE] [--node-modules DIR]
"""
import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 28, 32, 40, 48, 64, 96, 128, 256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default='node')
    parser.add_argument('--node-modules')
    args = parser.parse_args()
    env = os.environ.copy()
    if args.node_modules:
        env['NODE_PATH'] = args.node_modules
    # Each size is independently rendered at 4x resolution from the original
    # vector, then downsampled once. Transparent square canvas preserves aspect.
    js = """
const sharp = require('sharp');
const [source, out, sizes] = process.argv.slice(1);
(async () => {
  for (const s of JSON.parse(sizes)) {
    await sharp(source, {density: 1200})
      .resize(s * 4, s * 4, {fit: 'contain', background: '#00000000'})
      .png().toFile(`${out}/${s}.png`);
  }
})().catch(e => {console.error(e); process.exit(1);});
"""
    import json
    with tempfile.TemporaryDirectory(prefix='rb-vector-icon-') as temp:
        subprocess.run([args.node, '-e', js, str(ROOT / 'assets/keyan.svg'),
                        temp, json.dumps(SIZES)], env=env, check=True)
        frames = [Image.open(Path(temp) / f'{s}.png').convert('RGBA').resize(
            (s, s), Image.Resampling.LANCZOS) for s in SIZES]
        target = ROOT / 'assets/科研工作台.ico'
        frames[-1].save(target, format='ICO', sizes=[f.size for f in frames],
                        append_images=frames[:-1])
        shutil.copy2(target, ROOT / 'ui/favicon.ico')
        frames[-1].save(ROOT / 'ui/favicon.png', format='PNG')
        frames[-1].save(ROOT / 'assets/预览_flask.png', format='PNG')
    print('Generated vector ICO frames:', ', '.join(map(str, SIZES)))


if __name__ == '__main__':
    main()
