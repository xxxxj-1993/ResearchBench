import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class ZoteroStorageSearchTests(unittest.TestCase):
    def test_keyword_search_requires_all_terms(self):
        with tempfile.TemporaryDirectory(prefix='rb-zot-') as td:
            storage = Path(td) / 'storage' / 'ABCD1234'
            storage.mkdir(parents=True)
            (storage / '张三-简历.pdf').write_bytes(b'pdf')
            (storage / '论文附件.pdf').write_bytes(b'pdf')
            with patch.object(app, 'zotero_data_dir', return_value=td):
                rows = app.zotero_storage_search('张三，简历')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['name'], '张三-简历.pdf')
            self.assertTrue(os.path.isabs(rows[0]['path']))


if __name__ == '__main__':
    unittest.main()
