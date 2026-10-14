"""Persistence regression tests for background/rapid-save data loss."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rb-persist-')
        self.old = (app.DATA_DIR, app.DATA_FILE, app.STATE['db'])
        app.DATA_DIR = self.tmp.name
        app.DATA_FILE = os.path.join(self.tmp.name, 'data.json')
        app.STATE['db'] = {'ideas': [], 'settings': {}}

    def tearDown(self):
        app.DATA_DIR, app.DATA_FILE, app.STATE['db'] = self.old
        self.tmp.cleanup()

    def test_atomic_save_keeps_previous_valid_backup(self):
        app.STATE['db']['ideas'] = [{'id': 'one'}]
        app.save_db()
        app.STATE['db']['ideas'] = [{'id': 'two'}]
        app.save_db()
        with open(app.DATA_FILE + '.bak', encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['ideas'][0]['id'], 'one')
        with open(app.DATA_FILE, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['ideas'][0]['id'], 'two')

    def test_corrupt_live_file_recovers_backup(self):
        app.STATE['db']['ideas'] = [{'id': 'saved'}]
        app.save_db()
        app.STATE['db']['ideas'] = [{'id': 'latest'}]
        app.save_db()
        Path(app.DATA_FILE).write_text('{broken', encoding='utf-8')
        restored = app.load_db()
        self.assertEqual(restored['ideas'][0]['id'], 'saved')

    def test_concurrent_saves_do_not_corrupt_json(self):
        def write(i):
            app.STATE['db']['ideas'] = [{'id': str(i)}]
            app.save_db()
        threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
        for t in threads: t.start()
        for t in threads: t.join()
        with open(app.DATA_FILE, encoding='utf-8') as fh:
            data = json.load(fh)
        self.assertIn(data['ideas'][0]['id'], {str(i) for i in range(12)})


if __name__ == '__main__':
    unittest.main()
