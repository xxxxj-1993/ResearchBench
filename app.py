# -*- coding: utf-8 -*-
"""
科研工作台 Desktop  v2.4.4
- 纯 Python 标准库，本机回环服务 + 系统浏览器应用窗口
- 文件夹 / 软件 / 链接 点击直达
- 只读联动 Zotero 与 Obsidian（多 vault）
- Windows 数据保存在 %APPDATA%\\ResearchWorkbench\\
- macOS 数据保存在 ~/Library/Application Support/ResearchWorkbench/
"""
import os
import re
import sys
import json
import time
import glob
import string
import socket
import atexit
import sqlite3
import tempfile
import logging
import shutil
import threading
import subprocess
import webbrowser
import ctypes
import posixpath
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    import winreg          # 只在 Windows 有；用于兜底查找非标准目录安装的软件
except Exception:
    winreg = None

try:
    import seeddata
except Exception:  # 打包后同目录，正常情况下一定能导入
    seeddata = None

APP_NAME = "ResearchWorkbench"
VERSION = "2.4.4"
CURATED_V = 3  # 内容种子版本：升级时用于给老数据补新栏目
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# ================================================================ 基础路径
def appdata_dir():
    if IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
        d = posixpath.join(base, APP_NAME)
    else:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def resource_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


DATA_DIR = appdata_dir()
DATA_FILE = os.path.join(DATA_DIR, "data.json")
OLD_LAUNCH = os.path.join(DATA_DIR, "launch.json")
LOG_FILE = os.path.join(DATA_DIR, "app.log")

logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")

INDEX = os.path.join(resource_dir(), "ui", "index.html")
FAVICON = os.path.join(resource_dir(), "ui", "favicon.ico")


def iso(days=0):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() + days * 86400))


def now_ms():
    return int(time.time() * 1000)


# ================================================================ 系统操作
# ============ 窗口置顶辅助：让外部窗口浮到最前，不被工作台遮住 ============
def _win_user32():
    if sys.platform != "win32":
        return None
    try:
        return ctypes.windll.user32
    except Exception:
        return None


def _win_kernel32():
    if sys.platform != "win32":
        return None
    try:
        return ctypes.windll.kernel32
    except Exception:
        return None


# explorer/系统的常驻窗口。explorer 是单实例进程，按 PID 查找会误命中桌面窗口
# （标题 "Program Manager"，可见），导致把“桌面”置顶、真正的文件夹窗口反被遮住。
_IGNORE_CLASSES = ("Progman", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                   "WorkerW", "Shell_DimWindowClass",
                   "Windows.UI.Core.CoreWindow", "EdgeUiInputWndClass",
                   "ApplicationFrameInputSinkWindow")
_EXPLORER_CLASSES = ("CabinetWClass", "ExploreWClass")
# 识别“新出现的窗口”时要排除的系统常驻窗口（UWP 应用窗口不排除，否则会漏掉新版应用）
_SHELL_CLASSES = ("Progman", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "WorkerW",
                  "Shell_DimWindowClass", "EdgeUiInputWndClass",
                  "ApplicationFrameInputSinkWindow", "WindowsDash.Deck")
_NOISE_TITLES = ("新通知", "Windows 输入体验", "搜索", "任务视图", "Program Manager")


def _enum_windows(predicate):
    """枚举可见窗口，predicate(hwnd)->bool，返回满足条件的 hwnd 列表（按 Z 序，最前在前）。
    注意：不使用 ctypes.wintypes（PyInstaller 单文件打包后该子模块可能未加载），
    改用 ctypes 基础类型，避免在打包环境里抛 AttributeError。"""
    user32 = _win_user32()
    if not user32:
        return []
    found = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        try:
            if predicate(hwnd):
                found.append(hwnd)
        except Exception:
            pass
        return True
    try:
        user32.EnumWindows(cb, 0)
    except Exception:
        pass
    return found


def _win_text(hwnd):
    user32 = _win_user32()
    if not user32:
        return ""
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def _win_class(hwnd):
    user32 = _win_user32()
    if not user32:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value or ""
    except Exception:
        return ""


def _win_pid(hwnd):
    user32 = _win_user32()
    if not user32:
        return 0
    try:
        pid_buf = ctypes.c_uint32()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
        return int(pid_buf.value)
    except Exception:
        return 0


def _find_windows_by_pid(pid):
    """按进程找窗口；过滤掉桌面/任务栏等常驻窗口，避免误置顶。"""
    def pred(hwnd):
        user32 = _win_user32()
        if not user32 or not user32.IsWindowVisible(hwnd):
            return False
        if _win_class(hwnd) in _IGNORE_CLASSES:
            return False
        if not _win_text(hwnd):
            return False
        return _win_pid(hwnd) == pid
    return _enum_windows(pred)


def _find_explorer_window(name, cabinet_only=True):
    """定位资源管理器窗口。
    explorer 是单实例进程：Popen("explorer 路径") 会立刻把命令转交给已有的
    explorer 并退出，所以按 PID 查找极不可靠（会命中桌面窗口）。
    改用「窗口类名 CabinetWClass + 标题」定位，标题形如「2026模板 - 文件资源管理器」。"""
    key = (name or "").strip().lower()
    if not key:
        return []

    def pred(hwnd):
        user32 = _win_user32()
        if not user32 or not user32.IsWindowVisible(hwnd):
            return False
        cls = _win_class(hwnd)
        if cls in _IGNORE_CLASSES:
            return False
        if cabinet_only and cls not in _EXPLORER_CLASSES:
            return False
        text = _win_text(hwnd)
        if not text:
            return False
        base = text.split(" - 文件资源管理器")[0].strip().lower()
        return key == base or key in base or key in text.lower()
    return _enum_windows(pred)


def _snapshot_windows():
    """打开动作之前，记录当前可见窗口集合，供之后识别“新出现的窗口”。"""
    def pred(hwnd):
        user32 = _win_user32()
        if not user32 or not user32.IsWindowVisible(hwnd):
            return False
        if _win_class(hwnd) in _SHELL_CLASSES:
            return False
        return bool(_win_text(hwnd))
    return set(_enum_windows(pred))


def _new_windows(before):
    """打开动作之后新出现的窗口。
    覆盖 UWP 应用、启动器中转（Popen 的 PID 立刻退出）、单实例程序等 PID 对不上的情况。"""
    def pred(hwnd):
        user32 = _win_user32()
        if not user32 or not user32.IsWindowVisible(hwnd):
            return False
        if _win_class(hwnd) in _SHELL_CLASSES:
            return False
        text = _win_text(hwnd)
        if not text or text in _NOISE_TITLES:
            return False
        if "科研工作台" in text:
            return False
        return True
    return [h for h in _enum_windows(pred) if h not in (before or set())]


def _raise_window(hwnd):
    """尽最大努力把目标窗口拉到最前并激活，绕过 Windows 的前台激活限制。"""
    user32 = _win_user32()
    if not user32:
        return False
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE：最小化时先还原
    except Exception:
        pass
    cur_tid = fg_tid = 0
    attached = False
    try:
        k32 = _win_kernel32()
        if k32:
            cur_tid = k32.GetCurrentThreadId()
        fg = user32.GetForegroundWindow()
        if fg:
            fg_tid = user32.GetWindowThreadProcessId(fg, None)
        # 把本线程挂到前台线程上，即可绕过前台锁定（经典做法）
        if cur_tid and fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
    except Exception:
        pass
    try:
        user32.BringWindowToTop(hwnd)
    except Exception:
        pass
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        # 临时置顶，确保盖过工作台（Edge）窗口
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)  # HWND_TOPMOST
    except Exception:
        pass
    if attached:
        try:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
        except Exception:
            pass
    return True


def _unpin_window(hwnd):
    """激活完成后取消“始终置顶”，让它留在普通层最前，不影响其它窗口。"""
    user32 = _win_user32()
    if not user32:
        return
    try:
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0003 | 0x0010)  # HWND_NOTOPMOST + NOACTIVATE
    except Exception:
        pass


def _sink_workbench():
    """兜底：把工作台自身窗口沉到最底层，保证外部窗口不被遮。"""
    title = "科研工作台"
    def pred(hwnd):
        user32 = _win_user32()
        if not user32 or not user32.IsWindowVisible(hwnd):
            return False
        if _win_class(hwnd) in _IGNORE_CLASSES:
            return False
        return title in _win_text(hwnd)
    for hwnd in _enum_windows(pred):
        try:
            user32 = _win_user32()
            if user32:
                # HWND_BOTTOM + SWP_NOACTIVATE，把工作台窗口沉到最底
                user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0003 | 0x0010)
        except Exception:
            pass


def _bring_to_front(proc=None, title_hint=None, use_pid=True, before=None, timeout=8.0):
    """在后台线程里把刚打开的外部窗口抬到最前；实在抬不起来就兜底把工作台沉底。
    任何异常都被吞掉，绝不影响“打开”本身的结果。
    before: 打开动作之前的窗口快照，用于识别“新出现的窗口”。"""
    user32 = _win_user32()
    target = None
    started = time.time()
    deadline = started + timeout
    while time.time() < deadline and target is None:
        # 1) 资源管理器窗口：按类名+标题精确匹配（explorer 单实例，PID 会误命中桌面）
        if title_hint:
            hwnds = _find_explorer_window(title_hint, cabinet_only=True)
            if hwnds:
                target = hwnds[0]
                break
        # 2) 打开动作后新出现的窗口（UWP / 启动器中转 / 单实例程序都靠这个兜住）
        if before is not None and time.time() - started < 3.0:
            nw = _new_windows(before)
            if nw:
                time.sleep(0.5)  # 跳过启动闪屏，取稳定后的主窗口（Z 序最前那个）
                target = (_new_windows(before) or nw)[0]
                break
        # 3) 按 PID 定位（独立启动的传统软件）
        if use_pid and proc is not None:
            hwnds = _find_windows_by_pid(proc.pid)
            if hwnds:
                target = hwnds[0]
                break
        time.sleep(0.15)
    # 4) 最后放宽一次：类名对不上时，按标题匹配任意窗口
    if target is None and title_hint:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            hwnds = _find_explorer_window(title_hint, cabinet_only=False)
            if hwnds:
                target = hwnds[0]
                break
            time.sleep(0.2)
    ok = False
    if target is not None:
        _raise_window(target)
        time.sleep(0.3)
        try:
            fg = user32.GetForegroundWindow()
            ok = bool(fg) and (fg == target or _win_pid(fg) == _win_pid(target))
        except Exception:
            ok = True
        time.sleep(0.5)  # 稳稳待在最前后，再取消“始终置顶”
        _unpin_window(target)
    if not ok:
        # 抬不起来就把工作台沉底：宁可工作台退到后面，也要让外部窗口露出来
        _sink_workbench()


def _after_open(proc=None, title_hint=None, use_pid=True, before=None):
    """打开外部程序/文件夹后，异步把新窗口抬到最前（不被工作台遮住）。
    立即返回、不阻塞请求；任何失败都被吞掉，绝不影响“打开”本身的结果。"""
    try:
        threading.Thread(target=_bring_to_front,
                         args=(proc, title_hint, use_pid, before), daemon=True).start()
    except Exception:
        pass


def open_path(path):
    if not path:
        return False, "路径为空"
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.exists(path):
        return False, "路径不存在：" + path
    try:
        before = _snapshot_windows()  # 打开前的窗口快照，用于识别新窗口
    except Exception:
        before = None
    try:
        if IS_MAC:
            proc = subprocess.Popen(["open", path], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        elif os.path.isdir(path):
            # 用 explorer 打开文件夹
            proc = subprocess.Popen(["explorer", os.path.normpath(path)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.startfile(path)  # noqa: S606
            proc = None
    except Exception as e:
        logging.exception("open_path")
        return False, str(e)
    # 打开动作已完成；置顶仅为了让外部窗口不被工作台遮挡，失败也不影响“已打开”
    # 文件夹/文档一律按窗口标题定位（explorer 单实例，按 PID 会误命中桌面窗口）
    try:
        _after_open(proc, title_hint=os.path.basename(os.path.normpath(path)),
                    use_pid=False, before=before)
    except Exception:
        pass
    return True, "已打开"


def launch_app(exe, args=None):
    if not exe:
        return False, "未配置程序路径"
    exe = os.path.expandvars(os.path.expanduser(exe))
    if not (os.path.isfile(exe) or (IS_MAC and exe.lower().endswith(".app") and os.path.isdir(exe))):
        return False, "程序不存在：" + exe
    try:
        before = _snapshot_windows() if IS_WINDOWS else None
    except Exception:
        before = None
    try:
        if IS_MAC:
            cmd = ["open", exe]
            if args:
                cmd += ["--args"] + list(args)
        else:
            cmd = [exe] + list(args or [])
        proc = subprocess.Popen(cmd, cwd=os.path.dirname(exe) or None,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, close_fds=True)
    except Exception as e:
        logging.exception("launch_app")
        return False, str(e)
    try:
        if IS_WINDOWS:
            _after_open(proc, before=before)
    except Exception:
        pass
    return True, "已启动"


def open_url(url):
    if not url:
        return False, "链接为空"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        url = "https://" + url
    # 自定义协议（obsidian:// 等）交给 shell 处理，cmd start 会把 & 当分隔符
    if IS_MAC:
        try:
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            return True, "已交给系统打开"
        except Exception as e:
            return False, str(e)
    if not re.match(r"^https?:", url, re.I):
        try:
            os.startfile(url)  # noqa: S606
            try:
                _sink_workbench()
            except Exception:
                pass
            return True, "已交给系统打开"
        except Exception:
            pass
    try:
        subprocess.Popen(["cmd", "/c", "start", "", url], shell=False,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _sink_workbench()
        except Exception:
            pass
        return True, "已在浏览器打开"
    except Exception:
        try:
            webbrowser.open(url)
            try:
                _sink_workbench()
            except Exception:
                pass
            return True, "已在浏览器打开"
        except Exception as e:
            return False, str(e)


def reveal_in_explorer(path):
    path = os.path.expandvars(os.path.expanduser(path or ""))
    if not os.path.exists(path):
        return False, "路径不存在"
    try:
        before = _snapshot_windows()  # 打开前的窗口快照，用于识别新窗口
    except Exception:
        before = None
    try:
        if IS_MAC:
            args = ["open", "-R", path] if os.path.isfile(path) else ["open", path]
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        else:
            proc = subprocess.Popen(["explorer", "/select,", os.path.normpath(path)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, str(e)
    try:
        # 定位后打开的是“所在文件夹”，标题为父文件夹名
        _after_open(proc, title_hint=os.path.basename(os.path.dirname(os.path.normpath(path))),
                    use_pid=False, before=before)
    except Exception:
        pass
    return True, "已定位"


def native_pick(kind):
    if IS_MAC:
        if kind == "folder":
            script = 'POSIX path of (choose folder with prompt "选择文件夹")'
        elif kind == "app":
            script = 'POSIX path of (choose application with prompt "选择应用")'
        else:
            script = 'POSIX path of (choose file with prompt "选择文件")'
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True,
                               text=True, timeout=300)
            out = (r.stdout or "").strip()
            return (True, out) if r.returncode == 0 and out else (False, "已取消")
        except Exception as e:
            return False, str(e)
    if kind == "folder":
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$d.Description='选择文件夹';$d.ShowNewFolderButton=$true;"
              "if($d.ShowDialog() -eq 'OK'){Write-Output $d.SelectedPath}")
    else:
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$d=New-Object System.Windows.Forms.OpenFileDialog;"
              "$d.Title='选择文件';$d.Filter='所有文件|*.*';"
              "if($d.ShowDialog() -eq 'OK'){Write-Output $d.FileName}")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                           capture_output=True, timeout=300)
        out = (r.stdout or b"").decode("utf-8", "ignore").strip()
        out = out.splitlines()[0].strip() if out else ""
        return (True, out) if out else (False, "已取消")
    except Exception as e:
        return False, str(e)


def list_dir(path, limit=300):
    path = os.path.expandvars(os.path.expanduser(path or ""))
    if not path or not os.path.isdir(path):
        return []
    out = []
    try:
        names = os.listdir(path)
    except Exception:
        return []
    for n in names:
        fp = os.path.join(path, n)
        try:
            st = os.stat(fp)
        except Exception:
            continue
        out.append({"name": n, "path": fp, "dir": os.path.isdir(fp),
                    "size": 0 if os.path.isdir(fp) else st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda e: (not e["dir"], -e["mtime"]))
    return out[:limit]


# ================================================================ Zotero（只读）
ZOT = {"dir": None, "con": None, "mtime": 0, "size": 0, "err": "", "tmp": None,
       "mode": "", "snapTs": 0}
ZOT_LOCK = threading.RLock()        # 保护「连接上的查询」
ZOT_CLOCK = threading.RLock()       # 保护「建连接 / 复制快照」
ZOT_CACHE = os.path.join(DATA_DIR, "zot_cache.json")
ZOT_SNAP = os.path.join(DATA_DIR, "zot_snapshot.sqlite")
ZOT_SNAP_META = os.path.join(DATA_DIR, "zot_snapshot.json")
ZOT_SNAP_TTL = 300.0                # 快照最长复用 5 分钟

ZOT_FALLBACK_DIRS = [
    os.path.expanduser("~/Zotero"),
    r"D:\Zotero", r"E:\Zotero", r"C:\Zotero",
]


def zotero_data_dir():
    if ZOT["dir"]:
        return ZOT["dir"]
    # 1) 读 Zotero 配置
    if IS_MAC:
        pats = [os.path.expanduser("~/Library/Application Support/Zotero/Profiles/*/prefs.js"),
                os.path.expanduser("~/Library/Application Support/Zotero/Zotero/Profiles/*/prefs.js")]
    else:
        pats = [os.path.join(os.environ.get("APPDATA", ""), "Zotero", "Zotero", "Profiles", "*", "prefs.js"),
                os.path.join(os.environ.get("APPDATA", ""), "Zotero", "Profiles", "*", "prefs.js")]
    for pat in pats:
        for p in glob.glob(pat):
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
                m = re.search(r'extensions\.zotero\.dataDir",\s*"([^"]+)"', txt)
                if m:
                    d = m.group(1).replace("\\\\", "\\")
                    if os.path.isfile(os.path.join(d, "zotero.sqlite")):
                        ZOT["dir"] = d
                        return d
            except Exception:
                pass
    # 2) 候选目录
    for d in ZOT_FALLBACK_DIRS:
        f = os.path.join(d, "zotero.sqlite")
        if os.path.isfile(f) and os.path.getsize(f) > 3 * 1024 * 1024:
            ZOT["dir"] = d
            return d
    return None


def _zjson(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def _zwrite(path, obj):
    try:
        json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _zclose():
    if ZOT["con"] is not None:
        try:
            ZOT["con"].close()
        except Exception:
            pass
        ZOT["con"] = None


def zotero_conn():
    """只读连接。Zotero 运行时数据库会上锁，所以策略是：
    1) 直连重试（瞬态 journal 通常几百毫秒就释放）
    2) 复用上次快照（源未变，或快照未超过 5 分钟）
    3) 才复制一份快照（101MB，慢，尽量少做）
    """
    d = zotero_data_dir()
    if not d:
        return None
    f = os.path.join(d, "zotero.sqlite")
    try:
        mt = os.path.getmtime(f)
        sz = os.path.getsize(f)
    except Exception:
        return None

    with ZOT_CLOCK:
        if ZOT["con"] is not None and ZOT["mtime"] == mt and ZOT["size"] == sz:
            return ZOT["con"]
        _zclose()

        meta = _zjson(ZOT_SNAP_META) or {}
        sig = meta.get("sig")
        age = time.time() - (meta.get("ts") or 0)
        snap_usable = os.path.isfile(ZOT_SNAP) and bool(sig) and (
            sig == [mt, sz] or 0 <= age < ZOT_SNAP_TTL)
        # 上次是否被迫用了快照（Zotero 运行时数据库被锁）——持久化到磁盘，
        # 这样新进程一启动就能跳过必然失败的直连重试（每次约 2 秒）
        locked_before = meta.get("mode") == "snapshot" or ZOT["mode"] == "snapshot"

        # 1) 直连。上一轮已经确认被 Zotero 锁住、且快照还新鲜时直接跳过，
        #    否则每次启动都要白等好几秒的锁超时
        err = ""
        if not (snap_usable and locked_before):
            for _ in range(2):
                try:
                    uri = "file:" + f.replace("\\", "/") + "?mode=ro"
                    con = sqlite3.connect(uri, uri=True, timeout=1.2, check_same_thread=False)
                    con.execute("select count(*) from items").fetchone()
                    ZOT.update(con=con, mtime=mt, size=sz, err="", mode="direct", tmp=None)
                    _zwrite(ZOT_SNAP_META,
                            {"sig": [mt, sz], "ts": time.time(), "mode": "direct"})
                    return con
                except Exception as e:
                    err = str(e)
                    time.sleep(0.3)

        # 2) 复用既有快照
        if snap_usable:
            try:
                con = sqlite3.connect(ZOT_SNAP, timeout=5.0, check_same_thread=False)
                con.execute("select count(*) from items").fetchone()
                ZOT.update(con=con, mtime=mt, size=sz, err="", mode="snapshot",
                           tmp=ZOT_SNAP, snapTs=meta.get("ts") or 0)
                if meta.get("mode") != "snapshot":
                    _zwrite(ZOT_SNAP_META, {"sig": [mt, sz],
                                            "ts": meta.get("ts") or time.time(),
                                            "mode": "snapshot"})
                return con
            except Exception as e:
                err = str(e)

        # 3) 复制新快照
        try:
            if os.path.isfile(ZOT_SNAP):
                try:
                    os.remove(ZOT_SNAP)
                except Exception:
                    pass
            shutil.copy2(f, ZOT_SNAP)
            con = sqlite3.connect(ZOT_SNAP, timeout=5.0, check_same_thread=False)
            con.execute("select count(*) from items").fetchone()
            _zwrite(ZOT_SNAP_META, {"sig": [mt, sz], "ts": time.time(), "mode": "snapshot"})
            ZOT.update(con=con, mtime=mt, size=sz, err="", mode="snapshot",
                       tmp=ZOT_SNAP, snapTs=time.time())
            return con
        except Exception as e:
            ZOT["err"] = err or str(e)
            logging.exception("zotero_conn")
            return None


def _zfields(cur, item_ids):
    """批量取 itemData -> {itemID: {field: value}}"""
    if not item_ids:
        return {}
    qs = ",".join("?" * len(item_ids))
    cur.execute("""
        SELECT id.itemID, f.fieldName, idv.value
        FROM itemData id
        JOIN itemDataValues idv ON idv.valueID = id.valueID
        JOIN fields f ON f.fieldID = id.fieldID
        WHERE id.itemID IN (%s)
    """ % qs, item_ids)
    out = {}
    for iid, fn, v in cur.fetchall():
        out.setdefault(iid, {})[fn] = v
    return out


def _zcreators(cur, item_ids):
    if not item_ids:
        return {}
    qs = ",".join("?" * len(item_ids))
    cur.execute("""
        SELECT ic.itemID, c.firstName, c.lastName, ic.orderIndex
        FROM itemCreators ic JOIN creators c ON c.creatorID = ic.creatorID
        WHERE ic.itemID IN (%s) ORDER BY ic.itemID, ic.orderIndex
    """ % qs, item_ids)
    out = {}
    for iid, fn, ln, _o in cur.fetchall():
        nm = (str(fn) + " " + str(ln)).strip() if fn else (str(ln) if ln else "")
        if nm:
            out.setdefault(iid, []).append(nm)
    return out


def _zattach(cur, item_ids):
    """附件：返回 {itemID: [ {key, path, type, name, exists} ]}
    同时覆盖两种情况：① 有父条目的附件 ② 自身就是独立附件条目"""
    if not item_ids:
        return {}
    qs = ",".join("?" * len(item_ids))
    cur.execute("""
        SELECT ia.parentItemID, ia.itemID, i2.key, ia.path, ia.contentType
        FROM itemAttachments ia JOIN items i2 ON i2.itemID = ia.itemID
        WHERE ia.parentItemID IN (%s) OR ia.itemID IN (%s)
    """ % (qs, qs), list(item_ids) + list(item_ids))
    d = zotero_data_dir()
    out = {}

    def resolve(key, raw):
        raw = raw or ""
        if raw.startswith("storage:"):
            return os.path.join(d, "storage", key, raw[len("storage:"):])
        if raw.startswith("attachments:"):
            return os.path.join(d, raw[len("attachments:"):])
        if raw and os.path.isabs(raw):
            return raw
        return ""

    for parent_id, self_id, key, path, ctype in cur.fetchall():
        target = parent_id if parent_id else self_id
        fp = resolve(key, path)
        out.setdefault(target, []).append({
            "key": key, "path": fp, "type": ctype or "",
            "name": os.path.basename(fp) if fp else (path or "附件"),
            "exists": bool(fp) and os.path.isfile(fp),
        })
    return out


def _zshape(cur, ids):
    if not ids:
        return []
    fl = _zfields(cur, ids)
    cr = _zcreators(cur, ids)
    at = _zattach(cur, ids)
    qs = ",".join("?" * len(ids))
    cur.execute("""SELECT i.itemID, t.typeName FROM items i
                   JOIN itemTypes t ON t.itemTypeID = i.itemTypeID
                   WHERE i.itemID IN (%s)""" % qs, ids)
    types = dict(cur.fetchall())
    out = []
    for iid in ids:
        f = fl.get(iid, {})
        atts = at.get(iid, [])
        title = f.get("title") or ""
        if not title and atts:
            title = atts[0]["name"]
        out.append({
            "id": iid, "key": "",
            "title": title or "(无标题)",
            "creators": cr.get(iid, []),
            "date": f.get("date", ""),
            "publication": f.get("publicationTitle") or f.get("bookTitle") or f.get("proceedingsTitle") or "",
            "doi": f.get("DOI", ""),
            "url": f.get("url", ""),
            "abstract": (f.get("abstractNote", "") or "")[:400],
            "type": types.get(iid, ""),
            "attachments": atts,
        })
    return out


def _zlocked(fn):
    def wrap(*a, **k):
        with ZOT_LOCK:
            return fn(*a, **k)
    wrap.__name__ = fn.__name__
    return wrap


def zotero_status():
    d = zotero_data_dir()
    if not d:
        return {"ok": False, "msg": "未找到 Zotero 数据目录（请确认已安装 Zotero 并同步过）"}
    # 快路径：连接尚未就绪（Zotero 运行中、首次要复制快照）时先返回上次缓存，
    # 让界面立刻显示，不阻塞在后台预热上
    if ZOT["con"] is None:
        c = _zjson(ZOT_CACHE)
        if c and c.get("ok"):
            c = dict(c)
            c["stale"] = True
            return c
        # 别的一线程正在建连接 / 复制 100MB 快照，先让前端过会儿再来问
        if not ZOT_CLOCK.acquire(blocking=False):
            return {"ok": False, "pending": True, "dir": d,
                    "msg": "正在准备 Zotero 数据快照…"}
        ZOT_CLOCK.release()
    with ZOT_LOCK:
        con = zotero_conn()
        if con is None:
            return {"ok": False, "msg": "读取 Zotero 数据库失败：" + (ZOT["err"] or "未知错误")}
        try:
            cur = con.cursor()
            cur.execute("select count(*) from items where itemID not in (select itemID from deletedItems)")
            total = cur.fetchone()[0]
            cur.execute("select count(*) from collections where collectionName is not null")
            cols = cur.fetchone()[0]
            res = {"ok": True, "dir": d, "total": total, "collections": cols,
                   "mode": ZOT.get("mode", "")}
            _zwrite(ZOT_CACHE, res)
            return res
        except Exception as e:
            return {"ok": False, "msg": str(e)}


@_zlocked
def zotero_collections():
    con = zotero_conn()
    if con is None:
        return []
    cur = con.cursor()
    cur.execute("""
        SELECT c.collectionID, c.collectionName, c.parentCollectionID,
               (SELECT count(*) FROM collectionItems ci WHERE ci.collectionID = c.collectionID)
        FROM collections c WHERE c.collectionName IS NOT NULL
        ORDER BY c.collectionName
    """)
    rows = cur.fetchall()
    by_parent = {}
    for cid, name, pid, n in rows:
        by_parent.setdefault(pid, []).append({"id": cid, "name": name, "count": n, "children": []})
    idx = {}
    for cid, name, pid, n in rows:
        idx[cid] = None
    out = []
    for cid, name, pid, n in rows:
        node = {"id": cid, "name": name, "count": n, "children": []}
        idx[cid] = node
    for cid, name, pid, n in rows:
        node = idx[cid]
        if pid and pid in idx and idx[pid] is not None:
            idx[pid]["children"].append(node)
        else:
            out.append(node)
    return out


@_zlocked
def zotero_items(collection_id=None, q=None, limit=120, mode="recent"):
    con = zotero_conn()
    if con is None:
        return []
    cur = con.cursor()
    where = ["i.itemID NOT IN (SELECT itemID FROM deletedItems)"]
    args = []
    if collection_id:
        where.append("i.itemID IN (SELECT itemID FROM collectionItems WHERE collectionID = ?)")
        args.append(int(collection_id))
    if q:
        where.append("""i.itemID IN (
            SELECT id.itemID FROM itemData id
            JOIN itemDataValues idv ON idv.valueID = id.valueID
            JOIN fields f ON f.fieldID = id.fieldID
            WHERE f.fieldName IN ('title','abstractNote','publicationTitle','DOI')
              AND idv.value LIKE ?)""")
        args.append("%" + q + "%")
    sql = "SELECT i.itemID, i.key, i.dateAdded FROM items i WHERE " + " AND ".join(where)
    if mode == "recent":
        sql += " ORDER BY i.dateAdded DESC"
    else:
        sql += " ORDER BY i.itemID DESC"
    sql += " LIMIT ?"
    args.append(int(limit))
    cur.execute(sql, args)
    rows = cur.fetchall()
    ids = [r[0] for r in rows]
    keys = {r[0]: r[1] for r in rows}
    items = _zshape(cur, ids)
    for it in items:
        it["key"] = keys.get(it["id"], "")
    return items


@_zlocked
def zotero_profile():
    """找个人简历 / CV 相关条目"""
    con = zotero_conn()
    if con is None:
        return []
    cur = con.cursor()
    cur.execute("""
        SELECT DISTINCT i.itemID FROM items i
        JOIN itemData id ON id.itemID = i.itemID
        JOIN itemDataValues idv ON idv.valueID = id.valueID
        JOIN fields f ON f.fieldID = id.fieldID
        WHERE f.fieldName = 'title' AND (
              idv.value LIKE '%简历%' OR idv.value LIKE '%CV%' OR idv.value LIKE '%cv%'
           OR idv.value LIKE '%Resume%' OR idv.value LIKE '%resume%' OR idv.value LIKE '%Vitae%')
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        LIMIT 20
    """)
    ids = [r[0] for r in cur.fetchall()]
    return _zshape(cur, ids)


@_zlocked
def zotero_recent_notes(limit=30):
    con = zotero_conn()
    if con is None:
        return []
    cur = con.cursor()
    cur.execute("""
        SELECT i.itemID FROM items i
        JOIN itemTypes t ON t.itemTypeID = i.itemTypeID
        WHERE t.typeName = 'note'
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        ORDER BY i.dateModified DESC LIMIT ?
    """, (limit,))
    ids = [r[0] for r in cur.fetchall()]
    return _zshape(cur, ids)


# ================================================================ Obsidian（读 + 受控写）
OBS = {"vaults": [], "scan": 0}


def obsidian_vaults():
    if OBS["vaults"] and time.time() - OBS["scan"] < 60:
        return OBS["vaults"]
    out = []
    if IS_MAC:
        cfg = os.path.expanduser("~/Library/Application Support/obsidian/obsidian.json")
    else:
        cfg = os.path.join(os.environ.get("APPDATA", ""), "obsidian", "obsidian.json")
    if os.path.isfile(cfg):
        try:
            d = json.load(open(cfg, encoding="utf-8"))
            for k, v in (d.get("vaults") or {}).items():
                p = v.get("path")
                if p and os.path.isdir(p):
                    out.append({"id": k, "path": p, "name": os.path.basename(p),
                                "open": bool(v.get("open"))})
        except Exception:
            logging.exception("obsidian cfg")
    OBS["vaults"] = out
    OBS["scan"] = time.time()
    return out


OBS_SKIP = {".obsidian", ".trash", ".git", "node_modules", ".smart-env", ".space"}


def obsidian_notes(vault_path, q=None, folder=None, limit=400):
    if not vault_path or not os.path.isdir(vault_path):
        return []
    root = os.path.abspath(vault_path)
    base = os.path.join(root, folder) if folder else root
    if not os.path.isdir(base):
        base = root
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in OBS_SKIP]
        for fn in filenames:
            if not fn.lower().endswith(".md"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root)
            if q and q.lower() not in rel.lower():
                continue
            try:
                st = os.stat(fp)
            except Exception:
                continue
            out.append({"name": fn[:-3], "rel": rel, "path": fp,
                        "mtime": st.st_mtime, "size": st.st_size,
                        "folder": os.path.dirname(rel).replace("\\", "/")})
    out.sort(key=lambda e: -e["mtime"])
    return out[:limit]


def read_text(path, max_bytes=400000):
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes)
        for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "ignore")
    except Exception as e:
        return ""


def obsidian_meta(vault_path, rel):
    """读取单篇笔记正文、标签、标题"""
    fp = os.path.join(vault_path, rel)
    if not os.path.isfile(fp):
        return None
    txt = read_text(fp)
    tags = sorted(set(re.findall(r"(?<!\S)#([\u4e00-\u9fa5A-Za-z0-9_/\-]{1,30})", txt)))
    fm = {}
    if txt.startswith("---"):
        end = txt.find("\n---", 3)
        if end > 0:
            for line in txt[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
    return {"rel": rel, "path": fp, "content": txt[:200000], "tags": tags,
            "frontmatter": fm, "size": os.path.getsize(fp),
            "mtime": os.path.getmtime(fp)}


def obsidian_ideas(vault_path, limit=200):
    """扫描带 #idea / #灵感 / #想法 标签，或标题含"灵感/idea"的笔记"""
    notes = obsidian_notes(vault_path, limit=3000)
    out = []
    kw = ("#idea", "#灵感", "#想法", "#idea/", "#研究想法")
    for n in notes:
        rel_low = n["rel"].lower()
        hit = any(k in rel_low for k in ("idea", "灵感", "想法"))
        if not hit:
            try:
                head = read_text(n["path"], 4000)
            except Exception:
                head = ""
            hit = any(k in head for k in kw) or "#idea" in head.lower()
        if hit:
            meta = obsidian_meta(vault_path, n["rel"])
            out.append({
                "rel": n["rel"], "title": n["name"], "folder": n["folder"],
                "mtime": n["mtime"], "tags": (meta or {}).get("tags", []),
                "excerpt": re.sub(r"\s+", " ", (meta or {}).get("content", ""))[:220],
            })
        if len(out) >= limit:
            break
    return out


OBS_IDX = {"vault": None, "ts": 0.0, "notes": []}
OBS_IDX_TTL = 90.0
_OBS_STOP = set("""a an the of in on for and or to with by from at as is are was were
be been being this that these those using used use via based new novel study
research paper article review letter journal vol pp et al""".split())


def obsidian_index(vault_path):
    """笔记索引带缓存，避免每次打开论文详情都整库 walk 一遍"""
    if not vault_path:
        return []
    if OBS_IDX["vault"] == vault_path and time.time() - OBS_IDX["ts"] < OBS_IDX_TTL:
        return OBS_IDX["notes"]
    ns = obsidian_notes(vault_path, None, None, 3000)
    OBS_IDX.update(vault=vault_path, ts=time.time(), notes=ns)
    return ns


def _tokens(s):
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", (s or "").lower())
    out = []
    for t in s.split():
        if len(t) > 3 and t not in _OBS_STOP:
            out.append(t)
        elif len(t) > 1 and re.match(r"^[\u4e00-\u9fff]+$", t):
            out.append(t)
    return out


def resolve_vaults(vault_path):
    """vault 传空或 "*" 时跨全部仓库检索——用户往往有多个仓库，
    文献笔记放在哪个不一定"""
    if vault_path and vault_path != "*":
        return [vault_path]
    return [v["path"] for v in obsidian_vaults()]


def obsidian_match(vault_path, title, limit=6):
    """按论文标题给 Obsidian 笔记打分：命中的实词越多、越接近全命中，分越高"""
    tk = set(_tokens(title))
    if not tk:
        return []
    scored = []
    seen = set()
    for vp in resolve_vaults(vault_path):
        vn = os.path.basename(os.path.normpath(vp))
        for n in obsidian_index(vp):
            base = re.sub(r"(笔记|notes?|note)$", "", n["name"], flags=re.I)
            bt = set(_tokens(base))
            if not bt:
                continue
            inter = len(tk & bt)
            if inter < 2:
                continue
            cover = inter / float(len(bt))
            sc = inter * 2 + cover * 3
            if cover > 0.8:
                sc += 3
            key = (vn, n["rel"])
            if key in seen:
                continue
            seen.add(key)
            scored.append((sc, {
                "rel": n["rel"], "name": n["name"], "folder": n["folder"],
                "mtime": n["mtime"], "vault": vp, "vaultName": vn,
                "score": round(sc, 1),
            }))
    scored.sort(key=lambda x: -x[0])
    return [x[1] for x in scored[:limit]]


def obsidian_write(vault_path, rel, content, mode="append"):
    """写笔记：mode=append 追加 / create 新建 / overwrite 覆盖"""
    if not vault_path or not os.path.isdir(vault_path):
        return False, "vault 不存在"
    fp = os.path.abspath(os.path.join(vault_path, rel))
    root = os.path.abspath(vault_path)
    if not fp.startswith(root):
        return False, "路径越界，已拒绝"
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        if mode == "create" and os.path.exists(fp):
            return False, "文件已存在：" + rel
        if mode == "append" and os.path.exists(fp):
            old = read_text(fp)
            sep = "\n\n" if old and not old.endswith("\n") else "\n"
            with open(fp, "w", encoding="utf-8") as f:
                f.write(old + sep + content + "\n")
        else:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content + ("\n" if not content.endswith("\n") else ""))
        return True, rel
    except Exception as e:
        logging.exception("obsidian_write")
        return False, str(e)


# ================================================================ 默认数据
def _clone(key):
    """从 seeddata 取一份深拷贝；模块缺失时退化为空列表"""
    if seeddata is None:
        return []
    src = getattr(seeddata, key, None)
    if not src:
        return []
    return json.loads(json.dumps(src, ensure_ascii=False))


def _pubs():
    out = _clone("PUBS")
    for p in out:
        p.setdefault("projectId", "")
        p.setdefault("doi", "")
        p.setdefault("volume", "")
        p.setdefault("pages", "")
        p.setdefault("if_", "")
        p.setdefault("zone", "")
        p.setdefault("cites", 0)
        p.setdefault("notes", "")
    return out


def _plans():
    out = _clone("PLANS")
    for i, p in enumerate(out):
        p.setdefault("due", "")
        p.setdefault("note", "")
        p["createdAt"] = now_ms() - (len(out) - i) * 1000
    return out


def _ideas():
    out = _clone("IDEAS")
    for i, it in enumerate(out):
        it.setdefault("paperId", "")
        it.setdefault("obsidianRel", "")
        it.setdefault("tags", [])
        it["createdAt"] = now_ms() - (len(out) - i) * 1000
    return out


def seed():
    t = iso(0)
    return {
        "settings": {"lang": "zh", "me": "", "pin": "", "focusMin": 25,
                     "zoteroDir": "", "obsidianVault": "", "obsidianIdeaFolder": "灵感",
                     "semStart": "", "periodTimes": "", "navLabels": {}},
        "papers": [],
        "projects": [],
        "pubs": _pubs(),
        "grants": _clone("GRANTS"),
        "teaching": _clone("TEACHING"),
        "teachProjects": _clone("TEACH_PROJECTS"),
        "patents": _clone("PATENTS"),
        "plans": _plans(),
        "materials": _clone("MATERIALS"),
        "ideas": _ideas(),
        "diary": [],
        "events": [],
        "shortcuts": [
          {"id": "s1", "category": "本地文件", "name": "桌面", "kind": "folder",
           "target": os.path.join(os.path.expanduser("~"), "Desktop"), "note": ""}
        ],
        "focus": {"done": 0, "minutes": 0, "date": iso(0)},
        "classes": []
    }


APP_HINTS = [
    # ---------------- 仿真与数值计算 ----------------
    ("仿真计算", "COMSOL", [r"E:\Program Files\COMSOL\COMSOL*\Multiphysics\bin\win64\comsol.exe",
                            r"C:\Program Files\COMSOL\COMSOL*\Multiphysics\bin\win64\comsol.exe",
                            r"D:\Program Files\COMSOL\COMSOL*\Multiphysics\bin\win64\comsol.exe"]),
    ("仿真计算", "MATLAB", [r"E:\Program Files\MATLAB\R*\bin\matlab.exe",
                            r"C:\Program Files\MATLAB\R*\bin\matlab.exe",
                            r"D:\Program Files\MATLAB\R*\bin\matlab.exe"]),
    ("仿真计算", "ANSYS Workbench", [r"C:\Program Files\ANSYS Inc\v*\Framework\bin\Win64\runwb2.exe",
                                     r"D:\Program Files\ANSYS Inc\v*\Framework\bin\Win64\runwb2.exe",
                                     r"E:\Program Files\ANSYS Inc\v*\Framework\bin\Win64\runwb2.exe"]),
    ("仿真计算", "CST Studio Suite", [r"C:\Program Files (x86)\CST STUDIO SUITE *\CST DESIGN ENVIRONMENT*.exe",
                                      r"C:\Program Files\CST STUDIO SUITE *\CST DESIGN ENVIRONMENT*.exe",
                                      r"D:\Program Files (x86)\CST STUDIO SUITE *\CST DESIGN ENVIRONMENT*.exe"]),
    ("仿真计算", "Lumerical FDTD", [r"C:\Program Files\Lumerical\v*\bin\fdtd-solutions.exe",
                                    r"D:\Program Files\Lumerical\v*\bin\fdtd-solutions.exe"]),
    ("仿真计算", "Zemax OpticStudio", [r"C:\Program Files\Ansys Zemax OpticStudio *\OpticStudio.exe",
                                       r"C:\Program Files\Zemax OpticStudio*\OpticStudio.exe",
                                       r"D:\Program Files\Ansys Zemax OpticStudio *\OpticStudio.exe"]),
    ("仿真计算", "Python 3.14", [r"C:\Users\*\AppData\Local\Programs\Python\Python3*\python.exe"]),

    # ---------------- 三维建模 / CAD ----------------
    ("三维建模", "SolidWorks", [r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe",
                                r"C:\Program Files\SolidWorks Corp*\SOLIDWORKS\SLDWORKS.exe",
                                r"D:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"]),
    ("三维建模", "AutoCAD", [r"C:\Program Files\Autodesk\AutoCAD *\acad.exe",
                             r"D:\Program Files\Autodesk\AutoCAD *\acad.exe"]),
    ("三维建模", "3ds Max", [r"C:\Program Files\Autodesk\3ds Max *\3dsmax.exe",
                             r"D:\Program Files\Autodesk\3ds Max *\3dsmax.exe"]),
    ("三维建模", "Maya", [r"C:\Program Files\Autodesk\Maya*\bin\maya.exe",
                          r"D:\Program Files\Autodesk\Maya*\bin\maya.exe"]),
    ("三维建模", "Rhino", [r"C:\Program Files\Rhino*\System\Rhino.exe",
                           r"D:\Program Files\Rhino*\System\Rhino.exe",
                           r"C:\Program Files\Rhino *\System\Rhino.exe"]),
    ("三维建模", "Blender", [r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
                             r"D:\Program Files\Blender Foundation\Blender *\blender.exe"]),
    ("三维建模", "SketchUp", [r"C:\Program Files\SketchUp\SketchUp *\SketchUp.exe",
                              r"D:\Program Files\SketchUp\SketchUp *\SketchUp.exe"]),
    ("三维建模", "Cinema 4D", [r"C:\Program Files\Maxon Cinema 4D *\CINEMA 4D.exe",
                               r"C:\Program Files\Maxon\Cinema 4D *\Cinema 4D.exe",
                               r"D:\Program Files\Maxon Cinema 4D *\CINEMA 4D.exe"]),
    ("三维建模", "CATIA", [r"C:\Program Files\Dassault Systemes\B*\win_b64\code\bin\CNEXT.exe",
                           r"D:\Program Files\Dassault Systemes\B*\win_b64\code\bin\CNEXT.exe"]),
    ("三维建模", "FreeCAD", [r"C:\Program Files\FreeCAD *\bin\FreeCAD.exe",
                             r"D:\Program Files\FreeCAD *\bin\FreeCAD.exe"]),

    # ---------------- 绘图与图像 ----------------
    ("绘图图像", "Photoshop", [r"C:\Program Files\Adobe\Adobe Photoshop *\Photoshop.exe",
                               r"D:\Program Files\Adobe\Adobe Photoshop *\Photoshop.exe",
                               r"E:\Program Files\Adobe\Adobe Photoshop *\Photoshop.exe"]),
    ("绘图图像", "Illustrator", [r"E:\Ai*\Adobe Illustrator *\Support Files\Contents\Windows\Illustrator.exe",
                                 r"C:\Program Files\Adobe\Adobe Illustrator *\Support Files\Contents\Windows\Illustrator.exe"]),
    ("绘图图像", "CorelDRAW", [r"C:\Program Files\Corel\CorelDRAW Graphics Suite *\Programs\CorelDRW.exe",
                               r"D:\Program Files\Corel\CorelDRAW Graphics Suite *\Programs\CorelDRW.exe"]),
    ("绘图图像", "Inkscape", [r"C:\Program Files\Inkscape\bin\inkscape.exe",
                              r"C:\Program Files\Inkscape*\bin\inkscape.exe"]),
    ("绘图图像", "GIMP", [r"C:\Program Files\GIMP *\bin\gimp-*.exe",
                          r"D:\Program Files\GIMP *\bin\gimp-*.exe"]),
    ("绘图图像", "ImageJ", [r"C:\Program Files\ImageJ\ImageJ.exe",
                            r"C:\Users\*\ImageJ\ImageJ.exe",
                            r"D:\Program Files\ImageJ\ImageJ.exe"]),
    ("绘图图像", "Fiji", [r"C:\Program Files\Fiji.app\ImageJ-win64.exe",
                          r"D:\Program Files\Fiji.app\ImageJ-win64.exe"]),
    ("绘图图像", "Origin", [r"C:\Program Files\OriginLab\Origin*\Origin.exe",
                            r"D:\Program Files\OriginLab\Origin*\Origin.exe"]),
    ("绘图图像", "GraphPad Prism", [r"C:\Program Files\GraphPad\Prism *\Prism.exe",
                                    r"D:\Program Files\GraphPad\Prism *\Prism.exe"]),

    # ---------------- 文献与写作 ----------------
    ("文献写作", "Zotero", [r"C:\Program Files\Zotero\zotero.exe", r"E:\Program Files\Zotero\zotero.exe",
                            r"D:\Program Files\Zotero\zotero.exe"]),
    ("文献写作", "EndNote", [r"C:\Program Files (x86)\EndNote *\EndNote.exe",
                             r"C:\Program Files\EndNote *\EndNote.exe",
                             r"D:\Program Files (x86)\EndNote *\EndNote.exe"]),
    ("文献写作", "Mendeley", [r"C:\Program Files (x86)\Mendeley Desktop\Mendeley Desktop.exe",
                              r"C:\Users\*\AppData\Local\Mendeley Ltd\Mendeley Desktop\Mendeley Desktop.exe"]),
    ("文献写作", "TeXstudio", [r"F:\Program Files\texstudio\texstudio.exe",
                               r"C:\Program Files\texstudio\texstudio.exe",
                               r"E:\Program Files\texstudio\texstudio.exe"]),
    ("文献写作", "MiKTeX 控制台", [r"F:\CTEX\MiKTeX\miktex\bin\x64\miktex-console.exe",
                                   r"C:\Program Files\MiKTeX*\miktex\bin\x64\miktex-console.exe"]),
    ("文献写作", "MathType", [r"C:\Program Files (x86)\MathType\MathType.exe"]),
    ("文献写作", "Typora", [r"C:\Program Files\Typora\Typora.exe",
                            r"C:\Users\*\AppData\Local\Programs\Typora\Typora.exe"]),
    ("文献写作", "VS Code", [r"F:\Users\*\AppData\Local\Programs\Microsoft VS Code\Code.exe",
                             r"C:\Users\*\AppData\Local\Programs\Microsoft VS Code\Code.exe"]),
    ("文献写作", "Obsidian", [r"F:\Users\*\AppData\Local\Programs\Obsidian\Obsidian.exe",
                              r"C:\Users\*\AppData\Local\Programs\Obsidian\Obsidian.exe"]),

    # ---------------- 数据分析 ----------------
    ("数据分析", "SPSS", [r"C:\Program Files\IBM\SPSS\Statistics\*\spss.exe",
                          r"D:\Program Files\IBM\SPSS\Statistics\*\spss.exe"]),
    ("数据分析", "RStudio", [r"C:\Program Files\RStudio\rstudio.exe",
                             r"C:\Program Files\Posit\RStudio\rstudio.exe",
                             r"D:\Program Files\RStudio\rstudio.exe"]),

    # ---------------- 三维建模与渲染（补充） ----------------
    ("三维建模", "Solid Edge", [r"C:\Program Files\Siemens\Solid Edge *\Program\Edge.exe",
                                r"C:\Program Files\Solid Edge *\Program\Edge.exe"]),
    ("三维建模", "KeyShot", [r"C:\Program Files\KeyShot*\bin\keyshot.exe",
                             r"C:\Program Files\Luxion\KeyShot*\bin\keyshot.exe"]),
    ("三维建模", "ZBrush", [r"C:\Program Files\Pixologic\ZBrush *\ZBrush.exe"]),

    # ---------------- 仿真计算（补充） ----------------
    ("仿真计算", "Abaqus", [r"C:\SIMULIA\Abaqus\*\abaqus.exe",
                            r"C:\Program Files\SIMULIA\Abaqus\*\abaqus.exe",
                            r"C:\Program Files\Dassault Systemes\SimulationServices\*\Abaqus\win_b64\code\bin\abq*.exe"]),
    ("仿真计算", "Altium Designer", [r"C:\Program Files\Altium\AD*\X2.EXE"]),
    ("仿真计算", "LabVIEW", [r"C:\Program Files\National Instruments\LabVIEW *\LabVIEW.exe",
                             r"C:\Program Files (x86)\National Instruments\LabVIEW *\LabVIEW.exe"]),
    ("仿真计算", "Multisim", [r"C:\Program Files (x86)\National Instruments\Circuit Design Suite *\multisim.exe",
                              r"C:\Program Files\National Instruments\Circuit Design Suite *\multisim.exe"]),

    # ---------------- 绘图图像（补充） ----------------
    ("绘图图像", "Premiere Pro", [r"C:\Pr*\Adobe Premiere Pro *\Adobe Premiere Pro.exe",
                                  r"C:\Program Files\Adobe\Adobe Premiere Pro *\Adobe Premiere Pro.exe"]),
    ("绘图图像", "After Effects", [r"C:\Program Files\Adobe\Adobe After Effects *\Support Files\AfterFX.exe",
                                   r"C:\Ae*\Adobe After Effects *\Support Files\AfterFX.exe"]),
]


# macOS 应用包识别规则。目标保存为 .app，由 launch_app 使用 `open -a` 启动。
MAC_APP_HINTS = [
    ("仿真计算", "COMSOL", ["/Applications/COMSOL*.app", "/Applications/COMSOL*/**/*.app",
                              "~/Applications/COMSOL*.app"]),
    ("仿真计算", "MATLAB", ["/Applications/MATLAB_R*.app", "~/Applications/MATLAB_R*.app"]),
    ("仿真计算", "Lumerical FDTD", ["/Applications/Lumerical*.app", "/Applications/Lumerical*/**/FDTD*.app",
                                     "/Applications/Ansys Lumerical*.app", "/Applications/Ansys*/**/FDTD*.app"]),
    ("仿真计算", "Python", ["/Applications/Python 3*.app"]),
    ("三维建模", "AutoCAD", ["/Applications/Autodesk/AutoCAD *.app",
                               "/Applications/Autodesk/AutoCAD */AutoCAD*.app", "/Applications/AutoCAD *.app"]),
    ("三维建模", "Maya", ["/Applications/Autodesk/maya*.app", "/Applications/Autodesk/Maya*.app",
                            "/Applications/Autodesk/maya*/Maya.app", "/Applications/Autodesk/Maya*/Maya.app"]),
    ("三维建模", "Rhino", ["/Applications/Rhinoceros*.app", "/Applications/Rhino*.app"]),
    ("三维建模", "Blender", ["/Applications/Blender.app", "~/Applications/Blender.app"]),
    ("三维建模", "SketchUp", ["/Applications/SketchUp *.app", "/Applications/SketchUp*.app"]),
    ("三维建模", "Cinema 4D", ["/Applications/Maxon Cinema 4D *.app", "/Applications/Cinema 4D*.app"]),
    ("三维建模", "FreeCAD", ["/Applications/FreeCAD.app", "~/Applications/FreeCAD.app"]),
    ("三维建模", "KeyShot", ["/Applications/KeyShot*.app"]),
    ("绘图图像", "Photoshop", ["/Applications/Adobe Photoshop */Adobe Photoshop *.app", "/Applications/Adobe Photoshop*.app"]),
    ("绘图图像", "Illustrator", ["/Applications/Adobe Illustrator */Adobe Illustrator.app", "/Applications/Adobe Illustrator*.app"]),
    ("绘图图像", "Inkscape", ["/Applications/Inkscape.app", "~/Applications/Inkscape.app"]),
    ("绘图图像", "GIMP", ["/Applications/GIMP*.app", "~/Applications/GIMP*.app"]),
    ("绘图图像", "ImageJ", ["/Applications/ImageJ*.app"]),
    ("绘图图像", "Fiji", ["/Applications/Fiji.app", "~/Applications/Fiji.app"]),
    ("绘图图像", "GraphPad Prism", ["/Applications/Prism*.app", "/Applications/GraphPad Prism*.app"]),
    ("绘图图像", "Premiere Pro", ["/Applications/Adobe Premiere Pro */Adobe Premiere Pro *.app"]),
    ("绘图图像", "After Effects", ["/Applications/Adobe After Effects */Adobe After Effects *.app"]),
    ("文献写作", "Zotero", ["/Applications/Zotero.app", "~/Applications/Zotero.app"]),
    ("文献写作", "EndNote", ["/Applications/EndNote*.app"]),
    ("文献写作", "Mendeley", ["/Applications/Mendeley Reference Manager.app", "/Applications/Mendeley Desktop.app"]),
    ("文献写作", "TeXstudio", ["/Applications/texstudio.app", "~/Applications/texstudio.app"]),
    ("文献写作", "Typora", ["/Applications/Typora.app", "~/Applications/Typora.app"]),
    ("文献写作", "VS Code", ["/Applications/Visual Studio Code.app", "~/Applications/Visual Studio Code.app"]),
    ("文献写作", "Obsidian", ["/Applications/Obsidian.app", "~/Applications/Obsidian.app"]),
    ("数据分析", "SPSS", ["/Applications/IBM SPSS Statistics*/SPSS Statistics.app", "/Applications/SPSS Statistics.app"]),
    ("数据分析", "RStudio", ["/Applications/RStudio.app", "~/Applications/RStudio.app"]),
]


def mac_default_apps():
    out = []
    for cat, name, pats in MAC_APP_HINTS:
        hits = []
        for pat in pats:
            try:
                hits.extend(p for p in glob.glob(os.path.expanduser(pat), recursive=True)
                            if os.path.isdir(p))
            except Exception:
                pass
        if hits:
            best = sorted({posixpath.normpath(p) for p in hits},
                          key=lambda p: (not p.startswith("/Applications/"), p.lower()))[0]
            out.append({"id": "ap" + str(len(out)), "category": cat, "name": name,
                        "kind": "app", "target": best, "note": ""})
    return out


# ---------------- 盘符枚举、注册表兜底、多版本取舍 ----------------
_DRIVE_CACHE = None


def existing_drives():
    """本机已就绪的盘符，C 盘排最前。
    注意：glob 不支持 "?:\\" 这种通配盘符（实测零匹配），只能自己枚举。"""
    global _DRIVE_CACHE
    if _DRIVE_CACHE is None:
        ds = []
        for ch in string.ascii_uppercase:
            d = ch + ":\\"
            try:
                if os.path.isdir(d):
                    ds.append(d)
            except OSError:
                pass
        ds.sort(key=lambda x: (x[0] != "C", x))
        _DRIVE_CACHE = ds
    return _DRIVE_CACHE


def _expand_drive(pat):
    """把模板开头的盘符替换成本机每个盘，得到候选路径。"""
    if len(pat) > 2 and pat[1] == ":":
        return [d + pat[2:] for d in existing_drives()]
    return [pat]


# 绿色版 / 整合包 / 随手解压目录的特征词，命中即降权
_GREEN_WORDS = ("软件包", "绿色", "便携", "免安装", "破解", "crack", "portable",
                "green", "下载", "安装包", "toolbox", "图吧", "3dsmax2024")


def _path_score(p):
    """同一软件有多个安装时挑最"正规"的那个：
    Program Files 正式安装 > AppData 用户级安装 > 绿色版 / 软件包目录。"""
    low = p.lower()
    s = 0
    if "\\program files\\" in low or "\\program files (x86)\\" in low:
        s += 100
    elif "\\appdata\\local\\programs\\" in low:
        s += 60
    elif "\\appdata\\local\\" in low:
        s += 50
    for w in _GREEN_WORDS:
        if w in low:
            s -= 80
            break
    if p.rstrip("\\").count("\\") < 3:
        s -= 20          # 直接躺在盘根或一级目录，多半是随手解压的
    return s


_UNINSTALL_CACHE = None
_APP_PATH_CACHE = None
_SKIP_DIR = ("$recycle.bin", "system volume information", "windows", "winsxs",
             "installer", "$patchcache$", "driverstore", "temp", "tmp",
             "node_modules", "__pycache__", "package cache")


def _registry_views():
    """当前系统可用的注册表视图，兼容 32/64 位软件登记位置。"""
    if winreg is None:
        return [0]
    out = [0]
    for attr in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        flag = getattr(winreg, attr, 0)
        if flag and flag not in out:
            out.append(flag)
    return out


def _registry_path(value):
    """从注册表值中提取可用于有限深度搜索的目录。"""
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith('"'):
        m = re.match(r'^"([^"]+)"', value)
        path = m.group(1) if m else value.strip('"')
    else:
        path = value.split(",", 1)[0].strip()
        if ".exe" in path.lower():
            path = path[:path.lower().find(".exe") + 4]
    path = os.path.expandvars(path).strip().strip('"')
    if os.path.isfile(path):
        return os.path.dirname(os.path.normpath(path))
    if os.path.isdir(path):
        return os.path.normpath(path)
    return ""


def uninstall_entries():
    """注册表里的 (显示名, 安装目录)，用于兜底查找自定义安装路径。"""
    global _UNINSTALL_CACHE
    if _UNINSTALL_CACHE is None:
        items = []
        if winreg is not None:
            subs = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",)
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in subs:
                    for view in _registry_views():
                        try:
                            h = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view)
                        except OSError:
                            continue
                        i = 0
                        while True:
                            try:
                                k = winreg.EnumKey(h, i)
                            except OSError:
                                break
                            i += 1
                            try:
                                it = winreg.OpenKey(h, k, 0, winreg.KEY_READ | view)
                                dn = str(winreg.QueryValueEx(it, "DisplayName")[0] or "")
                            except OSError:
                                continue
                            loc = ""
                            for field in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    loc = _registry_path(winreg.QueryValueEx(it, field)[0])
                                except OSError:
                                    loc = ""
                                if loc:
                                    break
                            if dn and loc:
                                items.append((dn, loc))
        _UNINSTALL_CACHE = items
    return _UNINSTALL_CACHE


def app_path_entries():
    """读取 Windows App Paths，补足没有卸载项安装目录的软件。"""
    global _APP_PATH_CACHE
    if _APP_PATH_CACHE is None:
        items = []
        if winreg is not None:
            sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for view in _registry_views():
                    try:
                        h = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view)
                    except OSError:
                        continue
                    i = 0
                    while True:
                        try:
                            exe_name = winreg.EnumKey(h, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            it = winreg.OpenKey(h, exe_name, 0, winreg.KEY_READ | view)
                            exe_path = str(winreg.QueryValueEx(it, "")[0] or "")
                        except OSError:
                            continue
                        exe_path = os.path.expandvars(exe_path).strip().strip('"')
                        if os.path.isfile(exe_path):
                            items.append((exe_name.lower(), os.path.normpath(exe_path)))
        _APP_PATH_CACHE = items
    return _APP_PATH_CACHE


def _app_path_hits(patterns):
    wanted = [_exe_regex(p) for p in patterns]
    return [path for exe_name, path in app_path_entries()
            if any(rx.match(exe_name) for rx in wanted)]


def _kw(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _reg_dirs_for(name):
    """按软件名在注册表里找安装目录，例如 3ds Max → E:/Program Files/Autodesk"""
    k = _kw(name)
    if not k:
        return []
    out, seen = [], set()
    for dn, loc in uninstall_entries():
        if k in _kw(dn):
            low = loc.lower()
            if low not in seen:
                seen.add(low)
                out.append(loc)
    return out


def _exe_regex(pat):
    """模板末段的可执行文件名转正则（* 和 ? 按通配处理）。"""
    b = os.path.basename(pat)
    body = re.escape(b).replace(r"\*", ".*").replace(r"\?", ".")
    return re.compile("^" + body + "$", re.IGNORECASE)


def _search_exe(dirs, name_res, max_depth=4, max_dirs=6000):
    """在若干安装目录里有限深度找匹配的 exe。
    带目录数上限，避免某个软件把 C:\\Program Files 整个注册成安装位置时卡住。"""
    hits, seen = [], 0
    for root in dirs:
        base = root.rstrip("\\").count("\\")
        try:
            for dp, dn, fn in os.walk(root):
                seen += 1
                if seen > max_dirs:
                    return hits
                bn = os.path.basename(dp.rstrip("\\")).lower()
                if bn in _SKIP_DIR:
                    dn[:] = []
                    continue
                if dp.count("\\") - base >= max_depth:
                    dn[:] = []
                    continue
                for f in fn:
                    if name_res.match(f):
                        hits.append(os.path.join(dp, f))
        except OSError:
            continue
        if hits:
            break
    return hits


def default_apps():
    """探测本机科研软件（仿真计算 / 三维建模 / 绘图图像 / 文献写作 / 数据分析）。

    逐级兜底：
      ① 路径模板 × 本机所有盘符（覆盖主流安装位置，不再写死 C:/D:）
      ② 注册表卸载信息里的安装目录（覆盖装在自定义路径的软件）
    同一软件有多个安装时取打分最高者，Program Files 正规版优先。"""
    # macOS 直接扫描 /Applications 与 ~/Applications 中的应用包。
    if IS_MAC:
        out = mac_default_apps()
        for i, v in enumerate(obsidian_vaults()):
            out.append({"id": "apv" + str(i), "category": "知识库",
                        "name": "Obsidian · " + v["name"], "kind": "folder",
                        "target": v["path"], "note": "笔记仓库"})
        zd = zotero_data_dir()
        if zd:
            out.append({"id": "apz0", "category": "知识库", "name": "Zotero 数据目录",
                        "kind": "folder", "target": zd, "note": "含 zotero.sqlite"})
            st = os.path.join(zd, "storage")
            if os.path.isdir(st):
                out.append({"id": "apz1", "category": "知识库", "name": "Zotero PDF 附件",
                            "kind": "folder", "target": st, "note": "所有文献附件"})
        return out

    # Windows 手工重新探测时读取本次调用的磁盘与注册表状态，避免沿用启动时缓存。
    global _UNINSTALL_CACHE, _APP_PATH_CACHE, _DRIVE_CACHE
    _UNINSTALL_CACHE = None
    _APP_PATH_CACHE = None
    _DRIVE_CACHE = None
    out = []
    for cat, name, pats in APP_HINTS:
        cands, seen = [], set()
        for p in pats:
            for c in _expand_drive(p):
                k = c.lower()
                if k not in seen:
                    seen.add(k)
                    cands.append(c)
        hits = []
        for c in cands:
            try:
                hits += [h for h in glob.glob(c) if os.path.isfile(h)]
            except Exception:
                pass
        hits += _app_path_hits(pats)
        if not hits:                      # 模板没找到 → 查注册表里的安装目录
            hits = _search_exe(_reg_dirs_for(name), _exe_regex(pats[0]))
        if not hits:
            continue
        uniq = sorted({os.path.normpath(h) for h in hits})
        best = max(uniq, key=_path_score)
        out.append({"id": "ap" + str(len(out)), "category": cat,
                    "name": name, "kind": "app", "target": best, "note": ""})
    # Obsidian vault 快捷入口
    for i, v in enumerate(obsidian_vaults()):
        out.append({"id": "apv" + str(i), "category": "知识库", "name": "Obsidian · " + v["name"],
                    "kind": "folder", "target": v["path"], "note": "笔记仓库"})
    zd = zotero_data_dir()
    if zd:
        out.append({"id": "apz0", "category": "知识库", "name": "Zotero 数据目录",
                    "kind": "folder", "target": zd, "note": "含 zotero.sqlite"})
        st = os.path.join(zd, "storage")
        if os.path.isdir(st):
            out.append({"id": "apz1", "category": "知识库", "name": "Zotero PDF 附件",
                        "kind": "folder", "target": st, "note": "所有文献附件"})
    return out


# ================================================================ 课表导入
# 从 Word / WPS / Excel / 网页复制的表格还原「星期 × 节次」网格，
# 再把每个格子的文字拆成 课程 / 班级 / 教室 / 教师 / 周次。
#
# 为什么不做「导入 docx 文件」：从 Word 里复制时，剪贴板里本身就是 HTML 表格，
# 带完整的 rowspan / colspan 结构，比反过来解析 docx 的 XML 更保真，
# 而且不挑来源——WPS、Excel、网页、QQ 复制的表格全都能用同一套逻辑。

from html.parser import HTMLParser as _HTMLParser


def _span(attrs, key):
    try:
        return max(1, int(str(attrs.get(key, "1") or "1").strip()))
    except Exception:
        return 1


class _TableGrab(_HTMLParser):
    """把 HTML 里的 <table> 抽成若干「行 → [(文本, rowspan, colspan)]」。"""

    def __init__(self):
        _HTMLParser.__init__(self, convert_charrefs=True)
        self.tables = []
        self._rows = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        d = {k: (v or "") for k, v in attrs}
        if tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ["", _span(d, "rowspan"), _span(d, "colspan")]
        elif tag in ("br", "p") and self._cell is not None:
            if self._cell[0] and not self._cell[0].endswith(" "):
                self._cell[0] += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                self._row.append(tuple(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._rows is not None:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            if self._rows:
                self.tables.append(self._rows)
            self._rows = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[0] += data


_CELL_CONT = "\u0001"   # 横向合并格右侧的占位：表示「跟左边那格是一体的」


def _cells_to_grid(rows):
    """[(文本, rowspan, colspan)] 的二维列表 → 规整网格。
    合并格只在左上角放文本；colspan 的右侧填 _CELL_CONT，供后面算节次跨度。"""
    grid, occ = [], set()
    for ri, row in enumerate(rows):
        while len(grid) <= ri:
            grid.append([])
        ci = 0
        for text, rs, cs in row:
            while (ri, ci) in occ:
                ci += 1
            for dr in range(rs):
                for dc in range(cs):
                    occ.add((ri + dr, ci + dc))
            g = grid[ri]
            while len(g) < ci:
                g.append("")
            g.append((text or "").strip())
            for _ in range(cs - 1):
                g.append(_CELL_CONT)
            ci += cs
    n = max((len(r) for r in grid), default=0)
    for r in grid:
        while len(r) < n:
            r.append("")
    return grid


def grids_from_html(html_text):
    try:
        p = _TableGrab()
        p.feed(html_text or "")
        p.close()
    except Exception:
        return []
    return [_cells_to_grid(t) for t in p.tables if t]


def grid_from_text(text):
    """纯文本兜底：按行切开，行内优先按 Tab 分列，否则按两个以上空格。"""
    grid = []
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        cells = ln.split("\t") if "\t" in ln else re.split(r"\s{2,}", ln.strip())
        grid.append([c.strip() for c in cells])
    n = max((len(r) for r in grid), default=0)
    for r in grid:
        while len(r) < n:
            r.append("")
    return grid


# ---------------- 星期与节次识别 ----------------
_DOW_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_DOW_EN = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}
_DOW_RE = re.compile(r"(?:星期|周)\s*([一二三四五六日天])")


def _day_of(s):
    t = re.sub(r"<[^>]*>", "", s or "")     # 单元格里的换行标签先清掉
    t = re.sub(r"\s+", "", t)
    if not t:
        return 0
    m = _DOW_RE.search(t)          # 星期三 / 周三（含被拆成 星 期 三 的）
    if m:
        return _DOW_CN.get(m.group(1), 0)
    if len(t) == 1 and t in _DOW_CN:
        return _DOW_CN[t]
    low = t.lower()
    for k, v in _DOW_EN.items():
        if low.startswith(k):
            return v
    return 0


def _period_of(s):
    """单元格 → (起始节, 结束节)。认 1-2 / 3 / 4 5 / 101112 / 第3节 / 1-2节。"""
    t = re.sub(r"\s+", "", s or "")
    if not t:
        return None
    t = t.replace("節", "节")
    m = re.match(r"^第?(\d{1,2})[-–~—](\d{1,2})节?$", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (a, b) if 1 <= a <= b <= 20 else None
    # 纯数字要先于「第 N 节」判断：表头里的 "12" 是「第 1-2 节」，不是第 12 节
    if t.isdigit():
        if len(t) == 1:                       # "3"
            n = int(t)
            return (n, n) if 1 <= n <= 20 else None
        if len(t) == 2:                       # "12" → 1-2 节；"45" → 4-5 节
            a, b = int(t[0]), int(t[1])
            if t[0] == "1" and b <= 9:
                a = 1
            return (a, b) if 1 <= a <= b <= 20 else None
        if len(t) == 6 and t.startswith("10"):  # "101112" → 10-12 节
            return (10, 12)
        return None
    m = re.match(r"^(?:第\s*)?(\d{1,2})\s*节$", t)   # 必须带「节」字
    if m:
        n = int(m.group(1))
        return (n, n) if 1 <= n <= 20 else None
    return None


# ---------------- 单元格文字 → 字段 ----------------
_ROOM_PATS = [
    re.compile(r"[\u4e00-\u9fa5]{2,4}\d{1,4}\s*[楼室]"),        # 思源楼7楼
    # 中文与门牌号必须紧邻、且门牌号至少两位：「海川602」认，「学物理学 25」不认
    re.compile(r"[\u4e00-\u9fa5]{2,3}\d{2,4}"),                 # 学海505 文渊310
    re.compile(r"[A-Za-z]{1,4}-?\d{2,4}"),                      # A101
]
_CLS_PATS = [
    re.compile(r"[^\s]*?\d+\s*[-–~]\s*\d+\s*班"),                # 临床9-12班
    re.compile(r"[^\s]*?班(?![级])"),                            # 临床1-4班
    re.compile(r"\d{2}级[^\s]*"),                                # 25级智工3
]
_WEEK_RE = re.compile(r"(\d{1,2})\s*[-–~]\s*(\d{1,2})\s*周")
_TEA_STOP = ("实验", "实习", "上机", "理论", "小周", "大周", "单周", "双周",
             "全周", "教室", "老师", "教授", "合班", "分班")


def _cut(pat_list, s):
    for p in pat_list:
        m = p.search(s)
        if m:
            return m.group(0).strip(), (s[:m.start()] + " " + s[m.end():])
    return "", s


def parse_class_cell(raw):
    """把一个单元格的文字拆成 课程 / 班级 / 教室 / 教师 / 周次 / 单双周 / 备注。"""
    s = re.sub(r"\s+", " ", (raw or "")).strip()
    out = {"name": "", "cls": "", "room": "", "teacher": "",
           "weeks": "", "odd": "all", "note": ""}

    m = _WEEK_RE.search(s)
    if m:
        out["weeks"] = m.group(1) + "-" + m.group(2)
        s = s[:m.start()] + " " + s[m.end():]

    if "单周" in s:
        out["odd"] = "odd"
        s = s.replace("单周", " ")
    elif "双周" in s:
        out["odd"] = "even"
        s = s.replace("双周", " ")
    m = re.search(r"实验\s*\d{1,2}\s*[-–~]\s*\d{1,2}\s*周", s)
    if m:                                   # 「实验8-10周」里的周次也算行课周次
        if not out["weeks"]:
            out["weeks"] = re.search(r"(\d{1,2})\s*[-–~]\s*(\d{1,2})", m.group(0)).group(1) \
                + "-" + re.search(r"(\d{1,2})\s*[-–~]\s*(\d{1,2})", m.group(0)).group(2)
        s = s[:m.start()] + " " + s[m.end():]
    if "实验" in s:
        out["note"] = "实验"
        s = s.replace("实验", " ")
    s = s.replace("小周", " ").replace("大周", " ")

    # 先摘班级再摘教室：「临床17-20班 学海305」里，
    # 不先拿走班级的话，教室规则会把「临床17」误当成楼名。
    cls, s = _cut(_CLS_PATS, s)
    out["cls"] = cls

    room, s = _cut(_ROOM_PATS, s)
    out["room"] = room

    # 剩下的词：第一段当课程名，其余 1-3 字纯中文且带数字优先归班级
    toks = [t for t in s.split(" ") if t]
    name, tea, cls2 = [], [], []
    for i, t in enumerate(toks):
        if not name:
            name.append(t)
            continue
        core = re.sub(r"[（）()\[\]【】/、,，。:：]", "", t)
        if re.search(r"\d", core) and not re.fullmatch(r"[^\d]*", core):
            cls2.append(t)
        elif re.fullmatch(r"[\u4e00-\u9fa5]{1,3}", core) and core not in _TEA_STOP:
            tea.append(core)
    out["name"] = " ".join(name).strip()
    out["teacher"] = " ".join(tea).strip()
    if cls2 and not out["cls"]:
        out["cls"] = " ".join(cls2).strip()
    return out


def parse_schedule_grid(grid, teacher_key=""):
    """网格 → 课程条目列表。支持两种布局：
    A 星期在首列、节次在表头行（最常见，Word 课表多是这样）
    B 星期在表头行、节次在首列"""
    if not grid:
        return [], ""
    R = len(grid)
    C = max((len(r) for r in grid), default=0)
    day = [[0] * C for _ in range(R)]
    per = [[None] * C for _ in range(R)]
    for r in range(R):
        for c in range(C):
            t = grid[r][c] if c < len(grid[r]) else ""
            day[r][c] = _day_of(t)
            per[r][c] = _period_of(t)

    col_days = [sum(1 for r in range(R) if day[r][c]) for c in range(C)]
    row_days = [sum(1 for c in range(C) if day[r][c]) for r in range(R)]
    col_pers = [sum(1 for r in range(R) if per[r][c]) for c in range(C)]
    row_pers = [sum(1 for c in range(C) if per[r][c]) for r in range(R)]

    items = []
    key = (teacher_key or "").strip()

    def span_right(r, c):
        """这一格往右合并到了哪一列（靠 _CELL_CONT 占位判断）"""
        e = c
        while e + 1 < C:
            nxt = grid[r][e + 1] if e + 1 < len(grid[r]) else ""
            if nxt == _CELL_CONT:
                e += 1
            else:
                break
        return e

    def push(d, a, b, raw):
        raw = (raw or "").strip()
        if not raw or raw == _CELL_CONT or not d or not a:
            return
        it = parse_class_cell(raw)
        it.update({"day": d, "start": a, "end": b, "raw": raw})
        it["checked"] = (not key) or (key in raw)
        items.append(it)

    if max(col_days or [0]) >= max(row_days or [0]):
        # 布局 A：星期在列，节次表头在某一行
        dc = col_days.index(max(col_days)) if max(col_days) else 0
        hr = row_pers.index(max(row_pers)) if max(row_pers) else -1
        colmap = {}
        if hr >= 0:
            for c in range(C):                     # 表头格本身也可能是合并的
                if per[hr][c]:
                    for k in range(c, span_right(hr, c) + 1):
                        colmap[k] = per[hr][c]
        cur = 0
        for r in range(R):
            if day[r][dc]:
                cur = day[r][dc]
            if not cur or r == hr:
                continue
            for c in range(C):
                if c == dc or c not in colmap:
                    continue
                t = grid[r][c] if c < len(grid[r]) else ""
                if not t:
                    continue
                a, b = 99, 0
                for k in range(c, span_right(r, c) + 1):   # 跨列课程取节次并集
                    if k in colmap:
                        a, b = min(a, colmap[k][0]), max(b, colmap[k][1])
                if a == 99:
                    continue
                push(cur, a, b, t)
        return items, "A"
    else:
        # 布局 B：星期在表头行，节次在某一列（向下继承，应对纵向合并）
        hr = row_days.index(max(row_days)) if max(row_days) else 0
        pc = col_pers.index(max(col_pers)) if max(col_pers) else -1
        if pc < 0:
            return items, "B"
        cur = None
        for r in range(R):
            if per[r][pc]:
                cur = per[r][pc]
            if r == hr or not cur:
                continue
            for c in range(C):
                d = day[hr][c]
                if not d or c == pc:
                    continue
                t = grid[r][c] if c < len(grid[r]) else ""
                if t and t != _CELL_CONT:
                    push(d, cur[0], cur[1], t)
        return items, "B"


def parse_schedule_payload(html_text="", plain_text="", teacher_key=""):
    """统一入口：优先用 HTML 表格，退回纯文本。"""
    grids = grids_from_html(html_text) if html_text else []
    if not grids and plain_text:
        grids = [grid_from_text(plain_text)]
    if not grids:
        return [], "", "没找到表格。直接从 Word / Excel 里框选整个课表复制再粘贴。"
    best, best_items, layout = None, [], ""
    for g in grids:
        its, lay = parse_schedule_grid(g, teacher_key)
        if len(its) > len(best_items):
            best_items, layout, best = its, lay, g
    if not best_items:
        return [], layout, "表格找到了，但没认出「星期」和「节次」。确认复制的是完整的课表网格（含表头）。"
    return best_items, layout, ""


# ================================================================ 论文导入
# 两条路：paste BibTeX（纯本机解析，不联网）/ 输入 DOI（查 Crossref，需要联网）。
# 两者返回同样的字段，前端共用一套「填表 + 确认」流程。
#
# 统一字段：
#   bibtype  article / inproceedings / book ...
#   bibkey   条目 key
#   title    已清理 LaTeX
#   authors  "A. Author, B. Researcher"（名缩写在前，符合理工科投稿习惯）
#   journal 期刊 / 会议 / 出版社，按优先级取一个
#   year     int
#   volume / pages / doi / publisher / abstract / url

_CROSSREF_UA = ("ResearchBench/%s" % VERSION)
_OPENALEX_UA = _CROSSREF_UA


def _native_json_request(url, headers=None, timeout=12):
    """通过系统网络工具读取 JSON。

    浏览器可联网而打包后的 Python 失败时，通常是系统代理、校园网证书或企业
    HTTPS 检查没有被 Python/OpenSSL 正确继承。Windows PowerShell 使用系统
    网络与证书设置；macOS curl 使用系统网络栈，可作为可靠兜底。
    """
    headers = dict(headers or {})
    ua = str(headers.get("User-Agent") or _CROSSREF_UA)
    accept = str(headers.get("Accept") or "application/json")
    seconds = max(3, int(timeout or 12))
    if IS_WINDOWS:
        script = (
            "$ProgressPreference='SilentlyContinue';"
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "$r=Invoke-WebRequest -UseBasicParsing -Uri $env:RB_REQUEST_URL "
            "-TimeoutSec ([int]$env:RB_REQUEST_TIMEOUT) "
            "-Headers @{'User-Agent'=$env:RB_REQUEST_UA;Accept=$env:RB_REQUEST_ACCEPT};"
            "[Console]::Out.Write($r.Content)"
        )
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
        run_env = os.environ.copy()
        run_env.update({"RB_REQUEST_URL": url, "RB_REQUEST_TIMEOUT": str(seconds),
                        "RB_REQUEST_UA": ua, "RB_REQUEST_ACCEPT": accept})
    elif IS_MAC:
        cmd = ["/usr/bin/curl", "--fail", "--silent", "--show-error", "--location",
               "--max-time", str(seconds), "--header", "User-Agent: " + ua,
               "--header", "Accept: " + accept, url]
    else:
        cmd = ["curl", "--fail", "--silent", "--show-error", "--location",
               "--max-time", str(seconds), "--header", "User-Agent: " + ua,
               "--header", "Accept: " + accept, url]
    if not IS_WINDOWS:
        run_env = None
    done = subprocess.run(cmd, capture_output=True, timeout=seconds + 5, env=run_env)
    if done.returncode != 0:
        err = (done.stderr or b"").decode("utf-8", "replace").strip()
        raise OSError(err or ("系统网络请求失败，退出码 %s" % done.returncode))
    raw = done.stdout or b""
    return json.loads(raw.decode("utf-8", "replace"))


def fetch_json(url, headers=None, timeout=12):
    """读取远程 JSON；Python 网络失败时自动改走操作系统网络通道。"""
    headers = dict(headers or {})
    first_error = None
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except HTTPError:
        raise
    except Exception as e:
        first_error = e
        logging.warning("Python network request failed, retrying with system stack: %s", e)
    try:
        return _native_json_request(url, headers, timeout)
    except Exception as e:
        logging.warning("System network request failed: %s", e)
        raise URLError("Python 通道：%s；系统通道：%s" % (first_error, e))

# LaTeX 重音与转义 → 普通字符
_TEX_ACCENT = {
    '\\"o': 'ö', '\\"a': 'ä', '\\"u': 'ü', '\\"O': 'Ö', '\\"A': 'Ä', '\\"U': 'Ü',
    "\\'e": 'é', "\\'a": 'á', "\\'i": 'í', "\\'o": 'ó', "\\'u": 'ú', "\\'E": 'É',
    "\\`e": 'è', "\\`a": 'à', "\\`u": 'ù', "\\`o": 'ò',
    "\\^o": 'ô', "\\^a": 'â', "\\^e": 'ê', "\\^i": 'î', "\\^u": 'û',
    "\\~n": 'ñ', "\\~a": 'ã', "\\~o": 'õ', "\\~N": 'Ñ',
    '\\c c': 'ç', '\\c C': 'Ç', '\\ss': 'ß', '\\aa': 'å', '\\AA': 'Å',
    '\\o': 'ø', '\\O': 'Ø', '\\l': 'ł', '\\L': 'Ł', '\\ae': 'æ', '\\AE': 'Æ',
    '\\v s': 'š', '\\v c': 'č', '\\v z': 'ž', '\\v r': 'ř',
    '\\i': 'ı', '\\j': 'ȷ',
}
# 只包一层内容、直接剥掉的格式化命令
_TEX_WRAP = ('textbf', 'textit', 'texttt', 'textrm', 'textsf', 'textsc', 'text',
             'emph', 'mathrm', 'mathit', 'mathsf', 'mathtt', 'mathbf', 'bm',
             'rm', 'it', 'bf', 'tt', 'sc', 'mbox', 'hbox', 'fbox', 'textnormal',
             'textsuperscript', 'textsubscript', 'textbfit', 'ce', 'siunitx')
# 直接丢弃的命令（后跟 {..}）
_TEX_DROP = ('thanks', 'footnote', 'footnotemark', 'citep', 'citet', 'cite',
             'ref', 'eqref', 'label', 'index', 'protect', 'noopsort')


def _tex_clean(s):
    """把 BibTeX 里的 LaTeX 记号尽量转成人能读的纯文本"""
    if not s:
        return ''
    s = str(s)
    # 先替嵌套形态 \cmd{a}{b}、\cmd{a} 的重音
    for k, v in _TEX_ACCENT.items():
        if k in s:
            cmd = k[1:]
            s = re.sub(re.escape(cmd) + r'\s*\{\s*([^{}]*?)\s*\}', v, s)
            s = s.replace(k, v)
    # 丢弃 footnote / thanks 之类
    for cmd in _TEX_DROP:
        s = re.sub(r'\\' + cmd + r'\s*\{[^{}]*\}', '', s)
    # 包裹型命令：反复剥，处理嵌套
    for _ in range(3):
        before = s
        for cmd in _TEX_WRAP:
            s = re.sub(r'\\' + cmd + r'\s*(\{[^{}]*\})', r'\1', s)
        if s == before:
            break
    # 常见转义
    s = s.replace('\\&', '&').replace('\\%', '%').replace('\\_', '_')
    s = s.replace('\\#', '#').replace('\\$', '$').replace('\\{', '{').replace('\\}', '}')
    s = s.replace('\\ ', ' ').replace('\\,', ' ').replace('\\;', ' ').replace('\\!', '')
    s = s.replace('\\\\', ' ')
    s = s.replace('---', '—').replace('--', '–').replace('~', ' ')
    # 数学式：去掉 $ 和剩余的未知命令符号（保留花括号里的内容）
    s = s.replace('$', '')
    s = re.sub(r'\\[A-Za-z]+\s*\{([^{}]*)\}', r'\1', s)   # 未知命令 + 参数 → 参数
    s = re.sub(r'\\[A-Za-z]+', ' ', s)                    # 剩下的裸命令删掉
    # 剥掉 BibTeX 的大小写保护花括号：{C}hern → Chern
    for _ in range(3):
        b2 = re.sub(r'\{([^{}\\]*)\}', r'\1', s)
        if b2 == s:
            break
        s = b2
    s = re.sub(r'\s+', ' ', s).strip()
    # 去掉多余空格紧跟标点的情况
    s = re.sub(r'\s+([,.;:)\]])', r'\1', s)
    s = re.sub(r'([(\[])\s+', r'\1', s)
    return s.strip()


_PARTICLES = {'van', 'von', 'de', 'del', 'della', 'der', 'den', 'di', 'da',
              'dos', 'du', 'la', 'le', 'ter', 'ten'}


def _initials_of(given):
    """Kosmas L. → K. L. ；Jean-Luc → J.-L. ；Ming → M."""
    given = (given or '').strip()
    if not given:
        return ''
    out = []
    for w in given.split():
        w = w.strip()
        if not w:
            continue
        if re.match(r'^[A-Za-zÀ-ÿ]\.$', w):      # 已经是 "L."
            out.append(w)
            continue
        if '-' in w:                              # Jean-Luc → J.-L.
            out.append('-'.join(p[:1].upper() + '.' for p in w.split('-') if p))
        else:
            out.append(w[:1].upper() + '.')
    return ' '.join(out)


def _bib_authors(raw):
    """Author, Alice and Researcher, Bob  →  A. Author, B. Researcher"""
    raw = re.sub(r'\s+', ' ', (raw or '').strip())
    if not raw:
        return ''
    # "others"/"et al." 原样留着
    parts = [p.strip() for p in re.split(r'\s+and\s+', raw) if p.strip()]
    out = []
    for p in parts:
        p = _tex_clean(p)
        if ',' in p:
            last, given = p.split(',', 1)
            last = last.strip()
            ini = _initials_of(given)
            out.append((ini + ' ' + last).strip() if ini else last)
        else:
            words = p.split()
            if len(words) == 1:
                out.append(words[0])
                continue
            # 没有逗号 → 最后一个（含小品词）是姓
            cut = len(words) - 1
            while cut > 1 and words[cut - 1].lower() in _PARTICLES:
                cut -= 1
            ini = _initials_of(' '.join(words[:cut]))
            last = ' '.join(words[cut:])
            out.append((ini + ' ' + last).strip() if ini else last)
    return ', '.join(out)


def _bib_read_value(s, i):
    """读一个 BibTeX 字段值，返回 (值原文, 下一个位置)"""
    n = len(s)
    while i < n and s[i] in ' \t\r\n':
        i += 1
    if i >= n:
        return '', n
    if s[i] == '{':
        depth, j = 0, i
        while j < n:
            if s[j] == '{':
                depth += 1
            elif s[j] == '}':
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], n
    if s[i] == '"':
        j = i + 1
        while j < n:
            if s[j] == '\\':
                j += 2
                continue
            if s[j] == '"':
                return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], n
    j = i
    while j < n and s[j] != ',':
        j += 1
    return s[i:j].strip(), j


def _bib_fields(body):
    out, i, n = {}, 0, len(body)
    while i < n:
        while i < n and body[i] in ' \t\r\n,':
            i += 1
        if i >= n or body[i] == '}':
            break
        j = i
        while j < n and body[j] != '=':
            j += 1
        if j >= n:
            break
        key = body[i:j].strip().lower()
        val, i = _bib_read_value(body, j + 1)
        if key:
            out[key] = val
        i += 1
    return out


def _bib_year(f):
    for k in ('year', 'date'):
        v = str(f.get(k) or '')
        m = re.search(r'(1[89]\d{2}|20\d{2})', v)
        if m:
            return int(m.group(1))
    return None


def parse_bibtex(text):
    """解析一段 BibTeX（可含多条），返回统一字段的 dict 列表。纯本机，不联网。"""
    text = '\n'.join(ln for ln in str(text or '').replace('\r\n', '\n').split('\n')
                     if not ln.lstrip().startswith('%'))
    entries, i, n = [], 0, len(text)
    while i < n:
        at = text.find('@', i)
        if at < 0:
            break
        ob = text.find('{', at)
        if ob < 0:
            break
        typ = text[at + 1:ob].strip().lower()
        depth, j = 1, ob + 1
        while j < n:
            if text[j] == '{':
                depth += 1
            elif text[j] == '}':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner = text[ob + 1:j]
        i = j + 1
        if typ in ('comment', 'string', 'preamble'):
            continue
        ck = inner.find(',')
        key = (inner[:ck] if ck >= 0 else inner).strip()
        body = inner[ck + 1:] if ck >= 0 else ''
        f = _bib_fields(body)
        if not f:
            continue
        venue = (f.get('journal') or f.get('journaltitle') or f.get('booktitle')
                 or f.get('conference') or f.get('school') or f.get('institution')
                 or f.get('publisher') or '')
        doi = (f.get('doi') or '').strip()
        doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi)
        url = (f.get('url') or '').strip()
        if not url and doi:
            url = 'https://doi.org/' + doi
        entries.append({
            "bibtype": typ or 'article',
            "bibkey": key or '',
            "title": _tex_clean(f.get('title', '')),
            "authors": _bib_authors(f.get('author') or f.get('editor') or ''),
            "journal": _tex_clean(venue),
            "year": _bib_year(f),
            "volume": _tex_clean(f.get('volume', '')),
            "pages": _tex_clean(f.get('pages', '') or f.get('numpages', '')),
            "doi": doi,
            "publisher": _tex_clean(f.get('publisher', '')),
            "abstract": _tex_clean(f.get('abstract', '')),
            "url": url,
        })
    return entries


def crossref_pub(doi, timeout=12):
    """按 DOI 查 Crossref。需要联网；失败时返回 ok=False 和一句能直接显示的原因。"""
    raw = (doi or '').strip()
    m = re.search(r'(10\.\d{4,9}/[^\s"\'<>]+)', raw)
    if not m:
        return {"ok": False, "msg": "没认出这是 DOI。长这样：10.1234/example.2025.001"}
    d = quote(m.group(1).rstrip('.,;'), safe='')
    try:
        data = fetch_json("https://api.crossref.org/works/" + d,
                          {"User-Agent": _CROSSREF_UA, "Accept": "application/json"},
                          timeout)
    except HTTPError as e:
        if getattr(e, "code", 0) == 404:
            return {"ok": False, "msg": "Crossref 里查不到这个 DOI，检查一下有没有输错。"}
        return {"ok": False, "msg": "Crossref 返回错误 %s" % getattr(e, "code", "?")}
    except (URLError, socket.timeout, TimeoutError) as e:
        logging.warning("Crossref unavailable: %s", e)
        return {"ok": False, "msg": "连不上 Crossref。程序已尝试 Python 和系统网络通道；请检查代理、防火墙或证书设置。"}
    except Exception as e:
        return {"ok": False, "msg": "查询失败：%s" % e}

    msg = (data or {}).get("message") or {}
    titles = msg.get("title") or msg.get("container-title") or []
    authors = []
    for a in (msg.get("author") or []):
        fam = (a.get("family") or '').strip()
        giv = (a.get("given") or '').strip()
        if not fam:
            continue
        authors.append((_initials_of(giv) + ' ' + fam).strip() if giv else fam)
    year = None
    try:
        year = ((msg.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    except Exception:
        year = None
    venue = ''
    for k in ("container-title", "short-container-title", "institution-name", "publisher"):
        v = msg.get(k)
        if isinstance(v, list) and v:
            venue = str(v[0]).strip()
            break
        if isinstance(v, str) and v.strip():
            venue = v.strip()
            break
    return {
        "ok": True,
        "bibtype": msg.get("type") or "article",
        "bibkey": "",
        "title": re.sub(r'\s+', ' ', str(titles[0] if titles else '')).strip(),
        "authors": ', '.join(authors),
        "journal": venue,
        "year": year,
        "volume": str(msg.get("volume") or '').strip(),
        "pages": str(msg.get("page") or msg.get("article-number") or '').replace('--', '–').strip(),
        "doi": str(msg.get("DOI") or '').strip(),
        "publisher": str(msg.get("publisher") or '').strip(),
        "abstract": _tex_clean(msg.get("abstract") or '') if msg.get("abstract") else '',
        "url": str(msg.get("URL") or '').strip(),
    }


def normalize_doi(doi):
    """从 DOI、doi: 前缀或 doi.org 链接中提取可查询的 DOI。"""
    raw = unquote(str(doi or '')).strip()
    raw = re.sub(r'^doi\s*:\s*', '', raw, flags=re.I)
    raw = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', raw, flags=re.I)
    m = re.search(r'(10\.\d{4,9}/[^\s"\'<>]+)', raw, flags=re.I)
    return m.group(1).rstrip('.,;').lower() if m else ''


def openalex_citation(doi, timeout=12):
    """按 DOI 查询 OpenAlex 被引次数；失败时绝不返回伪造的 0。"""
    clean = normalize_doi(doi)
    if not clean:
        return {"ok": False, "msg": "没有可识别的 DOI"}
    work_id = quote("https://doi.org/" + clean, safe=':/')
    url = "https://api.openalex.org/works/" + work_id + "?select=id,doi,cited_by_count"
    try:
        data = fetch_json(url, {"User-Agent": _OPENALEX_UA,
                                "Accept": "application/json"}, timeout)
    except HTTPError as e:
        code = getattr(e, "code", 0)
        if code == 404:
            return {"ok": False, "doi": clean, "msg": "OpenAlex 未收录该 DOI"}
        if code == 429:
            return {"ok": False, "doi": clean, "msg": "OpenAlex 请求过于频繁，请稍后重试"}
        return {"ok": False, "doi": clean, "msg": "OpenAlex 返回错误 %s" % (code or "?")}
    except (URLError, socket.timeout, TimeoutError):
        return {"ok": False, "doi": clean, "msg": "无法连接 OpenAlex"}
    except Exception as e:
        return {"ok": False, "doi": clean, "msg": "查询失败：%s" % e}

    cites = data.get("cited_by_count") if isinstance(data, dict) else None
    if isinstance(cites, bool) or not isinstance(cites, (int, float)) or cites < 0:
        return {"ok": False, "doi": clean, "msg": "OpenAlex 返回的引用数无效"}
    source_id = str(data.get("id") or '').rstrip('/').split('/')[-1]
    return {"ok": True, "doi": clean, "cites": int(cites),
            "source": "OpenAlex", "openalexId": source_id}


def refresh_citation_counts(items, fetcher=None, max_workers=4):
    """并发查询一批成果；只返回结果，由前端写回当前数据。"""
    fetcher = fetcher or openalex_citation
    rows = items if isinstance(items, list) else []
    rows = rows[:200]
    by_doi = {}
    skipped = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or '')
        doi = normalize_doi(row.get("doi"))
        if not rid or not doi:
            skipped.append({"id": rid, "reason": "缺少 DOI"})
            continue
        by_doi.setdefault(doi, []).append(rid)

    fetched = {}
    if by_doi:
        workers = max(1, min(int(max_workers or 1), 4, len(by_doi)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(fetcher, doi): doi for doi in by_doi}
            for future in as_completed(pending):
                doi = pending[future]
                try:
                    fetched[doi] = future.result()
                except Exception as e:
                    fetched[doi] = {"ok": False, "doi": doi, "msg": "查询失败：%s" % e}

    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    results, failed = [], []
    for doi, ids in by_doi.items():
        result = fetched.get(doi) or {"ok": False, "msg": "没有查询结果"}
        for rid in ids:
            if result.get("ok"):
                results.append({"id": rid, "doi": doi,
                                "cites": int(result.get("cites", 0)),
                                "source": "OpenAlex",
                                "openalexId": result.get("openalexId") or '',
                                "updatedAt": updated_at})
            else:
                failed.append({"id": rid, "doi": doi,
                               "reason": result.get("msg") or "查询失败"})
    return {"ok": True, "results": results, "failed": failed, "skipped": skipped,
            "updated": len(results), "failedCount": len(failed),
            "skippedCount": len(skipped)}


LINK_PRESETS = [
    ("数据库", "Zotero 数据目录", "folder", ""),
    ("数据库", "Web of Science", "link", "https://www.webofscience.com"),
    ("数据库", "Optica Publishing Group", "link", "https://opg.optica.org"),
    ("数据库", "arXiv", "link", "https://arxiv.org"),
    ("数据库", "Google Scholar", "link", "https://scholar.google.com"),
    ("数据库", "中国知网", "link", "https://www.cnki.net"),
    ("研究工具", "Overleaf", "link", "https://www.overleaf.com"),
    ("研究工具", "COMSOL 官网文档", "link", "https://www.comsol.com/documentation"),
    ("研究工具", "Matlab File Exchange", "link", "https://www.mathworks.com/matlabcentral/fileexchange"),
]


# ================================================================ 存储
STATE = {"db": None}


def _all_sample(lst):
    """列表里每条都是示例数据（或为空）时返回 True"""
    if not isinstance(lst, list) or not lst:
        return True
    for x in lst:
        if not isinstance(x, dict):
            return False
        if x.get("notes") != "示例数据" and x.get("source") != "示例数据":
            return False
    return True


def load_db():
    raw = None
    try:
        raw = json.load(open(DATA_FILE, encoding="utf-8"))
    except Exception:
        raw = None

    db = raw if isinstance(raw, dict) else None
    v1_items = []

    # ---- v1 数据（论文/仿真/截止 条目）迁移 ----
    if db is not None and "items" in db and "papers" not in db:
        v1_items = [x for x in (db.get("items") or []) if isinstance(x, dict)]
        db = seed()
    if db is None:
        db = seed()

    if v1_items:
        papers = []
        for i, it in enumerate(v1_items):
            if it.get("module") != "论文":
                continue
            papers.append({
                "id": it.get("id") or ("mig" + str(i)),
                "title": it.get("title", ""), "journal": it.get("cat", ""),
                "stage": "写作", "status": "进行中",
                "nextAction": it.get("next", ""), "due": it.get("due", ""),
                "round": "", "decision": "", "authorOrder": "", "myRole": "",
                "projectId": "", "zoteroKeys": [],
                "files": ([{"name": os.path.basename(it["path"]), "path": it["path"]}]
                          if it.get("path") else []),
                "notes": it.get("note", ""), "updatedAt": now_ms(), "history": [],
            })
        if papers:
            db["papers"] = papers + db["papers"]
        db["_migratedV1"] = len(v1_items)

    # ---- v1 快捷入口迁移 ----
    # 只在“当前还没有任何常用项”时做一次（首次从旧版升级的场景）。
    # 用户一旦自己编辑/删除过常用栏目，就完全不再动它——否则删掉的项会在每次启动时复活。
    try:
        if not db.get("_launchMigrated") and not (db.get("shortcuts") or []):
            o = json.load(open(OLD_LAUNCH, encoding="utf-8"))
            extra = []
            for f in (o.get("folders") or []):
                extra.append({"id": f.get("id", ""), "category": "本地文件",
                              "name": f.get("name", ""), "kind": "folder",
                              "target": f.get("path", ""), "note": ""})
            for a in (o.get("apps") or []):
                extra.append({"id": a.get("id", ""), "category": "研究工具",
                              "name": a.get("name", ""), "kind": "app",
                              "target": a.get("exe", ""), "note": ""})
            # 空 target 的入口打不开，直接跳过
            for s in extra:
                if not s.get("target"):
                    continue
                s["id"] = s.get("id") or ("old" + str(abs(hash(s.get("target", ""))) % (10 ** 9)))
                db.setdefault("shortcuts", []).append(s)
            db["_launchMigrated"] = True
    except Exception:
        pass

    # ---- 补齐缺失字段 ----
    base = seed()
    for k, v in base.items():
        if k not in db or db[k] is None:
            db[k] = v
    if not isinstance(db.get("settings"), dict):
        db["settings"] = base["settings"]
    for k, v in base["settings"].items():
        db["settings"].setdefault(k, v)

    # ---- 内容升级：给老数据补「已立项 / 教学成果 / 专利 / 工作计划 / 我的材料」，
    #      并把仍是示例的论文、灵感换成真实内容（用户自己加的条目一律保留）----
    if db.get("_seedV", 0) < CURATED_V:
        base2 = seed()
        for k in ("grants", "teaching", "teachProjects", "patents", "materials"):
            cur = db.get(k)
            if not isinstance(cur, list) or not cur:
                db[k] = base2.get(k, [])
        # 论文：整表仍为示例（notes == '示例数据'）时才整体替换
        if _all_sample(db.get("pubs")):
            db["pubs"] = base2.get("pubs", [])
        # 灵感：整表仍为示例时替换，否则只追加缺失的
        if _all_sample(db.get("ideas")):
            db["ideas"] = base2.get("ideas", [])
        else:
            have = set(x.get("title", "") for x in db.get("ideas", []) if isinstance(x, dict))
            for it in base2.get("ideas", []):
                if it.get("title") not in have:
                    db.setdefault("ideas", []).append(it)
        # 工作计划：整表为空才灌入
        if not isinstance(db.get("plans"), list) or not db.get("plans"):
            db["plans"] = base2.get("plans", [])
        db["_seedV"] = CURATED_V

    STATE["db"] = db
    return db


def save_db():
    if not STATE["db"]:
        return
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(STATE["db"], f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def merge_scanned_shortcuts(db, fresh):
    """把扫描结果合并进常用入口，并按规范化路径避免重复。"""
    db.setdefault("shortcuts", [])
    have = set((s.get("kind"), os.path.normcase(os.path.normpath(s.get("target") or "")))
               for s in db["shortcuts"])
    added = []
    for s in fresh:
        key = (s.get("kind"), os.path.normcase(os.path.normpath(s.get("target") or "")))
        if key in have:
            continue
        s = dict(s)
        s["id"] = "ap" + str(now_ms()) + str(len(added))
        db["shortcuts"].append(s)
        added.append(s)
        have.add(key)
    found_apps = sum(1 for s in fresh if s.get("kind") == "app")
    added_apps = sum(1 for s in added if s.get("kind") == "app")
    return {"ok": True, "added": added, "total": len(fresh),
            "foundApps": found_apps, "addedApps": added_apps,
            "foundResources": len(fresh) - found_apps,
            "addedResources": len(added) - added_apps}


# ================================================================ HTTP
LAST_SEEN = {"t": 0.0}


def touch():
    LAST_SEEN["t"] = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = "ResearchBench/2.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---------------------------------------------------- GET
    def do_GET(self):
        touch()
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path in ("/", "/index.html"):
            try:
                self._send(200, open(INDEX, encoding="utf-8").read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, "index.html 读取失败: %s" % e, "text/plain; charset=utf-8")
            return

        if path in ("/favicon.ico", "/favicon.png"):
            # 窗口标题栏 / 任务栏图标：Edge 的 --app 窗口取的是页面 favicon
            try:
                with open(FAVICON, "rb") as f:
                    self._send(200, f.read(), "image/x-icon")
            except Exception:
                self._send(404, b"", "image/x-icon")
            return

        if path == "/api/state":
            db = STATE["db"]
            self._json({"ok": True, "db": db, "version": VERSION, "dataDir": DATA_DIR,
                        "today": iso(0)})
            return

        if path == "/api/dir":
            p = (q.get("path") or [""])[0]
            self._json({"ok": True, "path": p, "entries": list_dir(p)})
            return

        if path == "/api/ping":
            self._json({"ok": True})
            return

        # ---------- Zotero ----------
        if path == "/api/zotero/status":
            self._json(zotero_status())
            return
        if path == "/api/zotero/collections":
            self._json({"ok": True, "collections": zotero_collections()})
            return
        if path == "/api/zotero/items":
            cid = (q.get("collection") or [""])[0]
            kw = (q.get("q") or [""])[0]
            lim = int((q.get("limit") or ["120"])[0])
            self._json({"ok": True, "items": zotero_items(cid or None, kw or None, lim)})
            return
        if path == "/api/zotero/profile":
            self._json({"ok": True, "items": zotero_profile()})
            return
        if path == "/api/zotero/notes":
            self._json({"ok": True, "items": zotero_recent_notes(40)})
            return

        # ---------- Obsidian ----------
        if path == "/api/obsidian/vaults":
            self._json({"ok": True, "vaults": obsidian_vaults()})
            return
        if path == "/api/obsidian/notes":
            v = unquote((q.get("vault") or [""])[0])
            kw = (q.get("q") or [""])[0]
            fo = (q.get("folder") or [""])[0]
            vs = resolve_vaults(v)
            out = []
            for vp in vs:
                vn = os.path.basename(os.path.normpath(vp))
                for n in obsidian_notes(vp, kw or None, fo or None):
                    n["vault"] = vp
                    n["vaultName"] = vn
                    out.append(n)
            out.sort(key=lambda x: -x["mtime"])
            self._json({"ok": True, "notes": out[:800]})
            return
        if path == "/api/obsidian/note":
            v = unquote((q.get("vault") or [""])[0])
            r = unquote((q.get("rel") or [""])[0])
            m = None
            for vp in resolve_vaults(v):
                m = obsidian_meta(vp, r)
                if m is not None:
                    m["vault"] = vp
                    break
            if m is None:
                self._json({"ok": False, "msg": "笔记不存在"})
            else:
                self._json({"ok": True, "note": m})
            return
        if path == "/api/obsidian/match":
            v = (q.get("vault") or [""])[0]
            ti = (q.get("title") or [""])[0]
            self._json({"ok": True,
                        "items": obsidian_match(unquote(v), unquote(ti))})
            return
        if path == "/api/obsidian/ideas":
            v = (q.get("vault") or [""])[0]
            self._json({"ok": True, "ideas": obsidian_ideas(unquote(v))})
            return

        self._json({"ok": False, "msg": "not found"}, 404)

    # ---------------------------------------------------- POST
    def do_POST(self):
        touch()
        path = urlparse(self.path).path

        if path == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=_shutdown, daemon=True).start()
            return

        b = self._body()

        if path == "/api/state":
            db = STATE["db"]
            if isinstance(b.get("db"), dict):
                STATE["db"] = b["db"]
            save_db()
            self._json({"ok": True, "saved": time.time()})
            return

        if path == "/api/patch":
            # 增量更新某一类，避免整库来回传
            db = STATE["db"]
            key = b.get("key")
            if key in db and isinstance(b.get("value"), list):
                db[key] = b["value"]
                save_db()
            elif key == "settings" and isinstance(b.get("value"), dict):
                db["settings"].update(b["value"])
                save_db()
            elif key == "focus" and isinstance(b.get("value"), dict):
                db["focus"] = b["value"]
                save_db()
            self._json({"ok": True})
            return

        if path == "/api/open":
            kind = b.get("kind") or "folder"
            target = b.get("target") or ""
            if kind == "app":
                ok, msg = launch_app(target, b.get("args"))
            elif kind == "url":
                ok, msg = open_url(target)
            else:
                ok, msg = open_path(target)
            self._json({"ok": ok, "msg": msg})
            return

        if path == "/api/reveal":
            ok, msg = reveal_in_explorer(b.get("target") or "")
            self._json({"ok": ok, "msg": msg})
            return

        if path == "/api/pick":
            ok, msg = native_pick(b.get("kind") or "folder")
            self._json({"ok": ok, "msg": msg, "path": msg if ok else ""})
            return

        if path == "/api/obsidian/write":
            ok, msg = obsidian_write(b.get("vault") or "", b.get("rel") or "",
                                     b.get("content") or "", b.get("mode") or "append")
            self._json({"ok": ok, "msg": msg})
            return

        # ---------- 论文导入：BibTeX（离线）/ DOI（Crossref，需联网） ----------
        if path == "/api/bibtex":
            rows = parse_bibtex(b.get("text") or "")
            self._json({"ok": bool(rows), "items": rows,
                        "msg": "" if rows else "没解析出条目，确认粘贴的是完整的 BibTeX 源码"})
            return

        if path == "/api/doi":
            r = crossref_pub(b.get("doi") or "")
            r.setdefault("ok", False)
            self._json(r)
            return

        if path == "/api/citations/refresh":
            items = b.get("items")
            if not isinstance(items, list):
                self._json({"ok": False, "msg": "缺少论文列表"}, 400)
                return
            self._json(refresh_citation_counts(items))
            return

        # ---------- 课表导入：粘贴 Word / Excel / 网页复制的表格 ----------
        if path == "/api/schedule/parse":
            items, layout, msg = parse_schedule_payload(
                b.get("html") or "", b.get("text") or "", b.get("teacher") or "")
            self._json({"ok": bool(items), "items": items, "layout": layout, "msg": msg})
            return

        if path == "/api/schedule/import":
            rows = b.get("items") or []
            if not rows:
                self._json({"ok": False, "msg": "没有要导入的课程"})
                return
            db = STATE["db"]
            db.setdefault("classes", [])
            added = 0
            for r in rows:
                nm = (r.get("name") or "").strip()
                if not nm:
                    continue
                a = int(r.get("start") or 1)
                e = int(r.get("end") or a)
                if e < a:
                    a, e = e, a
                db["classes"].append({
                    "id": "c" + str(now_ms()) + str(added),
                    "name": nm,
                    "day": int(r.get("day") or 1),
                    "start": a,
                    "sections": max(1, e - a + 1),
                    "location": (r.get("room") or "").strip(),
                    "cls": (r.get("cls") or "").strip(),
                    "teacher": (r.get("teacher") or "").strip(),
                    "odd": r.get("odd") or "all",
                    "weeks": (r.get("weeks") or "").strip(),
                    "note": (r.get("note") or "").strip(),
                    "color": "",
                })
                added += 1
            save_db()
            self._json({"ok": added > 0, "added": added})
            return

        if path == "/api/rescan":
            db = STATE["db"]
            fresh = default_apps()
            result = merge_scanned_shortcuts(db, fresh)
            save_db()
            self._json(result)
            return

        self._json({"ok": False, "msg": "not found"}, 404)


SERVER = {"httpd": None}


def _shutdown():
    time.sleep(0.35)
    try:
        SERVER["httpd"].shutdown()
    except Exception:
        pass
    os._exit(0)


def free_port(start=8756):
    for p in range(start, start + 40):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    return 0


def open_window(url):
    profile = os.path.join(tempfile.gettempdir(), APP_NAME + "-webview")
    if IS_MAC:
        cands = [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        cands = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
        ]
    for exe in cands:
        if exe and os.path.isfile(exe):
            args = [exe, "--app=" + url, "--window-size=1440,960",
                    "--user-data-dir=" + profile, "--no-first-run",
                    "--no-default-browser-check", "--no-proxy-server",
                    "--proxy-bypass-list=<-loopback>",
                    "--disable-features=Translate,OptimizationHints",
                    # 防止 Windows Edge「睡眠标签页」冻结后台 JS 定时器：
                    # 否则窗口空闲几分钟后心跳/Zotero 轮询会被停掉。
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding"]
            try:
                return subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            except Exception:
                continue
    webbrowser.open(url)
    return None


def run_macos_webview(url):
    """在 macOS 使用系统 Cocoa WebView；依赖缺失时返回 False 走浏览器兜底。"""
    if not IS_MAC:
        return False
    try:
        import webview
        webview.create_window("科研工作台", url, width=1440, height=960,
                              min_size=(1024, 700))
        webview.start(debug=False)
        return True
    except Exception:
        logging.exception("macOS webview")
        return False


def warm_up():
    """后台预热：Zotero 首次查询要走冷缓存，提前打开连接并跑一次小查询"""
    # 注意：不要在外层持有 ZOT_LOCK —— 查询函数内部是 CLOCK→LOCK 顺序，
    # 外层再套 LOCK 会和 HTTP 线程形成锁序反转
    try:
        zotero_status()
        if zotero_data_dir():
            zotero_conn()
            zotero_items(None, None, 20)
    except Exception:
        logging.exception("warm_up")
    try:
        obsidian_vaults()
    except Exception:
        pass


def main():
    load_db()
    threading.Thread(target=warm_up, daemon=True).start()
    port = free_port()
    if not port:
        return 1
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    SERVER["httpd"] = httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % port
    logging.info("started %s", url)

    no_window = os.environ.get("RB_NO_WINDOW") == "1"
    if no_window:
        # 仅运行本地服务（测试/CI 用），不拉起浏览器窗口，纯服务常驻。
        print("SERVING " + url)
        run_guard(None, True)
    elif IS_MAC and run_macos_webview(url):
        # Cocoa 窗口关闭后 webview.start() 返回，随后正常关闭本地服务。
        pass
    else:
        proc = open_window(url)
        # 生命周期以「Edge 窗口进程是否还活着」为准：窗口关了才退出；
        # 无活动多久都不会自杀，避免睡眠标签冻结心跳后误杀后端。
        run_guard(proc, False)
    logging.info("shutdown")
    try:
        httpd.shutdown()
    except Exception:
        pass
    return 0


def run_guard(proc, no_window):
    """生命周期守卫：只要 Edge 窗口进程还活着就一直运行，窗口关闭才退出。
    不再使用「无活动 N 秒自杀」——那种写法只能靠前端心跳续命，一旦 Edge 因
    闲置把标签页休眠（默认开启「睡眠标签页」）而停掉心跳，后端就会被误杀，
    导致界面还在却点不开任何链接。"""
    grace = 0
    try:
        while True:
            time.sleep(1)
            if proc is not None and proc.poll() is not None and not IS_MAC:
                return
            # Mac 浏览器可能保留后台进程或把命令转交给已有进程，统一以页面心跳判断。
            if IS_MAC and not no_window:
                if LAST_SEEN["t"] and time.time() - LAST_SEEN["t"] > 45:
                    return
                continue
            if proc is None and not no_window:
                # 没能拉起任何浏览器窗口（只剩 webbrowser 兜底且无声），
                # 给 8 秒缓冲后主动退出，避免空跑占端口。
                grace += 1
                if grace > 8:
                    return
    except KeyboardInterrupt:
        pass


atexit.register(lambda: logging.info("exit"))

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        logging.exception("fatal: %s", e)
        raise
