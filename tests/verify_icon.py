# -*- coding: utf-8 -*-
"""校验 exe 内嵌应用图标 = 我们生成的烧瓶图标（不是 PyInstaller 默认图标）

做法：把 exe 复制到纯 ASCII 临时路径（避免中文路径在 PowerShell 里的编码问题），
用 System.Drawing 提取 exe 的关联图标存成 PNG，再和我们 .ico 的对应尺寸逐像素比对。
"""
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "科研工作台.exe")
ICO = os.path.join(ROOT, "assets", "科研工作台.ico")

ok = []
def chk(n, c, e=""):
    ok.append(bool(c))
    print(("PASS  " if c else "FAIL  ") + n + (("   " + str(e)) if e else ""))


chk("exe 存在", os.path.isfile(EXE), EXE)
if not os.path.isfile(EXE):
    sys.exit(1)

tmp = tempfile.mkdtemp(prefix="rbicochk-")
exe2 = os.path.join(tmp, "app.exe")
png = os.path.join(tmp, "icon.png")
shutil.copy2(EXE, exe2)

ps = (
    "Add-Type -AssemblyName System.Drawing; "
    "$i=[System.Drawing.Icon]::ExtractAssociatedIcon('%s'); "
    "$b=$i.ToBitmap(); "
    "$b.Save('%s',[System.Drawing.Imaging.ImageFormat]::Png); "
    'Write-Output ("{0}x{1}" -f $b.Width,$b.Height)' % (exe2, png)
)
r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, text=True)
size = (r.stdout or "").strip()
chk("提取到 exe 关联图标", os.path.isfile(png), size or (r.stderr or "").strip()[:120])

if os.path.isfile(png):
    got = Image.open(png).convert("RGB")
    pw, ph = got.size
    # 与 .ico 中最接近该尺寸的帧比对
    ico = Image.open(ICO)
    sizes = sorted(ico.ico.sizes(), key=lambda s: abs(s[0] - pw))
    ico.size = sizes[0]
    ref = ico.convert("RGB").resize((pw, ph), Image.LANCZOS)

    diff = 0
    a, b = got.load(), ref.load()
    for y in range(0, ph, 2):
        for x in range(0, pw, 2):
            pa, pb = a[x, y], b[x, y]
            diff += abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2])
    n = len(range(0, ph, 2)) * len(range(0, pw, 2)) * 3
    mean = diff / float(n)
    chk("exe 图标 = 我们的烧瓶图标(非默认)", mean < 14,
        "尺寸 %dx%d 平均色差 %.1f" % (pw, ph, mean))

    # 颜色构成自查：应以品牌绿为主，且不是 PyInstaller 默认的蓝灰/黑色
    green = sum(1 for y in range(0, ph, 2) for x in range(0, pw, 2)
                if (lambda p: p[1] > p[2] + 12 and p[1] > 70)(got.load()[x, y]))
    total = len(range(0, ph, 2)) * len(range(0, pw, 2))
    ratio = green / float(total)
    chk("图标以品牌绿为主(>25%%)", ratio > 0.25, "绿色占比 %.0f%%" % (ratio * 100))

shutil.rmtree(tmp, ignore_errors=True)
print("\n结果: %d/%d 通过" % (sum(ok), len(ok)))
sys.exit(0 if all(ok) else 1)
