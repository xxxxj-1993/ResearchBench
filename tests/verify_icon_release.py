"""Check a packaged EXE with isolated APPDATA; never read personal data."""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='rb-icon-release-') as temp:
        env = os.environ.copy()
        env.update(APPDATA=temp, LOCALAPPDATA=temp, RB_NO_WINDOW='1')
        proc = subprocess.Popen([str(exe)], env=env)
        try:
            log = Path(temp) / 'ResearchWorkbench/app.log'
            url = None
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError('Test EXE exited before starting')
                if log.exists():
                    matches = re.findall(r'started (http://127\.0\.0\.1:\d+/)', log.read_text(encoding='utf-8'))
                    if matches:
                        url = matches[-1]
                        break
                time.sleep(.2)
            assert url, 'No local server URL'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for route, file, mime in (('favicon.ico', 'favicon.ico', 'image/x-icon'),
                                      ('favicon.png', 'favicon.png', 'image/png')):
                with opener.open(url + route + '?v=2.4.8-icon2', timeout=10) as response:
                    data = response.read()
                    assert response.headers.get_content_type() == mime
                    assert data == (ROOT / 'ui' / file).read_bytes()
                with Image.open(io.BytesIO(data)) as image:
                    assert image.size == (256, 256)
            html = opener.open(url, timeout=10).read().decode('utf-8')
            assert 'favicon.png?v=2.4.8-icon2' in html
            state = json.load(opener.open(url + 'api/state', timeout=10))
            assert state['version'] == '2.4.9'
            assert Path(state['dataDir']).resolve() == (Path(temp) / 'ResearchWorkbench').resolve()
            db = state['db']
            collections = {k: len(v) for k, v in db.items() if isinstance(v, list)}
            # Existing software discovery may populate shortcuts from the test
            # machine; these are generated locally, not embedded personal data.
            assert all(v == 0 for k, v in collections.items() if k != 'shortcuts'), collections
            print('PASS packaged HTTP ICO/PNG, version, isolated data and empty personal collections:', collections)
        finally:
            # One-file PyInstaller has a bootloader + child. Only kill this
            # test tree, identified by the Popen handle (never by executable name).
            if proc.poll() is None:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               check=True, stdout=subprocess.DEVNULL)
            proc.wait(timeout=10)


if __name__ == '__main__':
    main()
