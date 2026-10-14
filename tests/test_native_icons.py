"""Exercise WM_SETICON against an invisible test window, not a user's app."""
import ctypes
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(sys.platform == 'win32', 'Windows native icons')
class NativeIconTests(unittest.TestCase):
    def test_native_small_and_large_icons(self):
        with tempfile.TemporaryDirectory(prefix='rb-native-icons-') as temp:
            with patch.dict(os.environ, {'APPDATA': temp}):
                import app
            user = ctypes.windll.user32
            create = user.CreateWindowExW
            create.restype = ctypes.c_void_p
            create.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p,
                               ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_void_p, ctypes.c_void_p]
            hwnd = create(0, 'STATIC', 'icon-test', 0, 0, 0, 20, 20, None, None, None, None)
            self.assertTrue(hwnd)
            class Proc:
                pid = os.getpid()
                calls = 0
                def poll(self):
                    self.calls += 1
                    return None if self.calls == 1 else 0
            try:
                with patch.object(app, '_find_windows_by_pid', return_value=[hwnd]), \
                     patch.object(app, '_win_class', return_value='Chrome_WidgetWin_1'), \
                     patch.object(app, '_win_text', return_value='科研工作台'), \
                     patch.object(app.time, 'sleep'):
                    app._maintain_window_icons(Proc())
                handles = []
                for kind in (0, 1):
                    result = ctypes.c_size_t()
                    self.assertTrue(user.SendMessageTimeoutW(hwnd, 0x7F, kind, 0, 2, 500, ctypes.byref(result)))
                    self.assertTrue(result.value)
                    handles.append(result.value)
                self.assertNotEqual(*handles)
            finally:
                destroy = user.DestroyWindow
                destroy.argtypes = [ctypes.c_void_p]
                destroy(hwnd)
                logging.shutdown()


if __name__ == '__main__':
    unittest.main()
