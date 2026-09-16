# -*- coding: utf-8 -*-
"""校验 dist/科研工作台.exe 里打包进去的 ui/index.html 是最新的一版。

不启动 exe（避免拉起 Edge 窗口、也避免在受限环境里被拦），
直接用 PyInstaller 的 CArchive 读取器把 ui/index.html 抽出来比对。
用法：
    python tests/verify_exe_nav.py
需要能 import PyInstaller（装了 pyinstaller 的环境即可）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "dist", "科研工作台.exe")

if not os.path.exists(EXE):
    raise SystemExit("找不到 exe: " + EXE)

try:
    from PyInstaller.archive.readers import CArchiveReader
except ImportError:
    raise SystemExit("需要先 pip install pyinstaller 才能读包（仅测试用）")

archive = CArchiveReader(EXE)
# 打包时 datas 用的是 Windows 分隔符
for key in ("ui\\index.html", "ui/index.html"):
    if key in archive.toc:
        packed = archive.extract(key).decode("utf-8")
        break
else:
    raise SystemExit("包里没有 ui/index.html")

CHECKS = [
    ("右键改名逻辑 navRename", "navRename" in packed),
    ("恢复默认名 navReset", "navReset" in packed),
    ("就地输入样式 lbin", "lbin" in packed),
    ("右键菜单样式 .ctx", ".ctx{" in packed),
    ("新增文案 timetable:'课表'", "timetable:'课表'" in packed),
    ("NAVDEF 里 schedule 指向 timetable", ",lb:'timetable'" in packed),
    ("今日页卡片仍用 schedule:'今日安排'", "schedule:'今日安排'" in packed),
]

bad = 0
print("=== dist/科研工作台.exe 内置页面校验 ===")
for name, ok in CHECKS:
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        bad += 1

# 顺带确认和源码构建产物一致，避免「改了 ui/app.js 忘了 build_ui.py」
# 注意：必须用二进制读，文本模式会把 CRLF 统一成 LF，比出来永远不等
local = os.path.join(ROOT, "ui", "index.html")
if os.path.exists(local):
    with open(local, "rb") as fh:
        same = fh.read().decode("utf-8") == packed
    print(("PASS  " if same else "FAIL  ") + "与 ui/index.html 构建产物一致")
    if not same:
        bad += 1

print("\n" + ("全部通过" if not bad else "有 %d 项失败" % bad))
sys.exit(1 if bad else 0)
