"""Build source ZIP using an explicit allowlist (no data, screenshots or logs)."""
import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    names = ['.gitignore', 'app.py', 'build_ui.py', 'LICENSE', 'README.md',
             'seeddata.py', '使用说明.txt', '科研工作台.spec',
             'assets/keyan.svg', 'assets/科研工作台.ico', 'assets/预览_flask.png']
    for folder, patterns in (('ui', ('*.html', '*.js', '*.ico', '*.png')),
                             ('tests', ('*.py', '*.js', '*.html')),
                             ('tools', ('*.py',))):
        for pattern in patterns:
            names.extend(p.relative_to(ROOT).as_posix()
                         for p in (ROOT / folder).glob(pattern) if p.is_file())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(set(names)):
            archive.write(ROOT / name, name)
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        assert all(not any(word in name for word in ('.learnings/', '.git/',
                   '__pycache__', 'data.json', '.log', '实测', '截图'))
                   for name in archive.namelist())
        print('Clean source ZIP:', args.output, 'files:', len(archive.namelist()))


if __name__ == '__main__':
    main()
