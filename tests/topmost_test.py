# -*- coding: utf-8 -*-
"""真实桌面验证：点击“打开文件夹”后，文件夹窗口是否真的浮到最前（前台窗口）。

做法：
  1. 在临时目录建一个中文名文件夹（贴近用户真实场景）；
  2. 调 /api/open 让后端打开它；
  3. 轮询系统前台窗口，判断是否为该文件夹的资源管理器窗口；
  4. 只关闭本次自己打开的那个窗口，然后停掉服务。

用法: python tests/topmost_test.py [exe_or_py]
  - 不带参数：用源码 app.py 起服务
  - 传 exe 路径：用打包后的 exe 起服务
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
U32 = ctypes.windll.user32
WINEOF = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
WM_CLOSE = 0x0010

FOLDER_NAME = "置顶验证目录"


def title_of(h):
    try:
        l = U32.GetWindowTextLengthW(h)
        if l <= 0:
            return ""
        b = ctypes.create_unicode_buffer(l + 1)
        U32.GetWindowTextW(h, b, l + 1)
        return b.value or ""
    except Exception:
        return ""


def class_of(h):
    try:
        b = ctypes.create_unicode_buffer(256)
        U32.GetClassNameW(h, b, 256)
        return b.value or ""
    except Exception:
        return ""


def find_cabinet(name):
    found = []

    def cb(h, _):
        try:
            if U32.IsWindowVisible(h) and class_of(h) == "CabinetWClass":
                if name.lower() in title_of(h).lower():
                    found.append(h)
        except Exception:
            pass
        return True
    U32.EnumWindows(WINEOF(cb), 0)
    return found


def foreground():
    h = U32.GetForegroundWindow()
    return h, title_of(h), class_of(h)


def main():
    target_exe = sys.argv[1] if len(sys.argv) > 1 else None
    mode = sys.argv[2] if len(sys.argv) > 2 else "folder"
    if mode == "app":
        target = r"C:\Windows\System32\notepad.exe"
        probe = os.path.join(ROOT, "_appprobe")
        api_kind = "app"
    else:
        target = os.path.join(tempfile.gettempdir(), "_rb_top_test", FOLDER_NAME)
        probe = os.path.join(ROOT, "_topprobe")
        api_kind = "folder"
        os.makedirs(target, exist_ok=True)
    if os.path.isdir(probe):
        shutil.rmtree(probe, ignore_errors=True)
    os.makedirs(probe, exist_ok=True)

    env = dict(os.environ)
    env["APPDATA"] = probe
    env["RB_NO_WINDOW"] = "1"

    if target_exe:
        cmd = [target_exe]
    else:
        cmd = [sys.executable, "-u", os.path.join(ROOT, "app.py")]
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    log = None
    for cand in (os.path.join(probe, "ResearchWorkbench", "app.log"),
                 os.path.join(probe, "ResearchWorkbench", "app.log")):
        if os.path.isfile(cand):
            log = cand
    port = None
    for _ in range(60):
        if log is None:
            for cand in (os.path.join(probe, "ResearchWorkbench", "app.log"),):
                if os.path.isfile(cand):
                    log = cand
        if log and os.path.isfile(log):
            txt = open(log, encoding="utf-8", errors="ignore").read()
            for line in txt.splitlines():
                if "127.0.0.1:" in line:
                    port = line.split("127.0.0.1:")[-1].strip().rstrip("/").split()[0]
                    break
        if port:
            break
        time.sleep(0.5)

    print("PORT=%s" % port)
    if not port:
        print("FAIL: 服务未启动")
        proc.kill()
        return 1

    url = "http://127.0.0.1:%s" % port
    req = urllib.request.Request(
        url + "/api/open",
        data=json.dumps({"kind": api_kind, "target": target}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=15)
        body = r.read().decode("utf-8", "ignore")
    except Exception as e:
        print("FAIL: /api/open 异常 %r" % (e,))
        proc.kill()
        return 1
    print("API_RESPONSE=%s" % body[:200])

    def matched(h, t, c):
        if not h:
            return False
        if mode == "app":
            return c == "Notepad" or ("记事本" in t)
        return c == "CabinetWClass" and FOLDER_NAME in t

    ok = False
    fg_info = None
    seen = []
    for _ in range(50):  # 最多等 10 秒
        h, t, c = foreground()
        fg_info = (h, t, c)
        key = (c, t[:40])
        if not seen or seen[-1] != key:
            seen.append(key)  # 记录前台变化（用户自己在操作电脑时会抢回焦点）
        if matched(h, t, c):
            ok = True
            break
        time.sleep(0.2)
    print("FG_SEQUENCE=%s" % (seen[:10],))

    label = "软件(记事本)窗口" if mode == "app" else "文件夹窗口"
    print("FOREGROUND hwnd=%s class=%s title=%r" % (fg_info[0], fg_info[2], fg_info[1]))
    print("RESULT=%s" % ("PASS %s已浮到最前" % label if ok else "FAIL %s未到前台" % label))

    # 只关闭本次自己打开的那个窗口
    to_close = set(find_cabinet(FOLDER_NAME)) if mode == "folder" else set()
    if ok and fg_info and fg_info[0]:
        to_close.add(fg_info[0])
    for h in to_close:
        try:
            U32.SendMessageW(h, WM_CLOSE, 0, 0)
        except Exception:
            pass
    time.sleep(0.5)
    proc.kill()
    shutil.rmtree(probe, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
