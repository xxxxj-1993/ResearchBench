# -*- coding: utf-8 -*-
"""
图标生成器（打包期工具，不进 exe）。

流程：
  1) 用系统 Edge 无头模式把 SVG 符号渲染成 PNG（矢量高保真，无需额外依赖）
  2) 用 Pillow 生成多尺寸 .ico（16/24/32/48/64/128/256）
     - 小尺寸(<=32) 使用「加粗描边」母版，避免缩到 16px 时线条发灰
     - 每档单独套圆角遮罩，保证小图圆角依然利落

用法：
  python tools/make_icon.py sheet                 # 生成候选符号对照图 assets/图标候选.png
  python tools/make_icon.py build flask           # 生成 assets/科研工作台.ico + ui/favicon.ico
  python tools/make_icon.py all                   # 上面两步都做（默认 flask）
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")
UI = os.path.join(ROOT, "ui")

# 与 ui/_template.html :root 保持一致
GREEN = "#4a7c59"
GREEN_D = "#3f6b4c"

EDGE_CANDS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# ---------------------------------------------------------------- 候选符号
# 均为 24x24 viewBox 的描边符号，风格与前端 I 表一致（stroke-linecap round）
ICONS = [
    ("flask", "烧瓶 Flask",
     '<path d="M9 3h6"/><path d="M10 3v5.2L4.7 17.4A2 2 0 0 0 6.4 20.5h11.2a2 2 0 0 0 1.7-3.1L14 8.2V3"/><path d="M8 14h8"/>'),

    ("micro", "显微镜 Microscope",
     '<path d="M6 20.5h12"/><path d="M9.2 20.5V17.2"/><path d="M7 17.2h8"/>'
     '<path d="M12 17.2V9.4"/><path d="M10 9.4h4"/>'
     '<path d="M10.6 9.4V5.2h2.8v4.2"/><path d="M15.4 12.6a4.6 4.6 0 0 1 3.1 4.6"/>'),

    ("atom", "原子轨道 Atom",
     '<circle cx="12" cy="12" r="1.9"/>'
     '<ellipse cx="12" cy="12" rx="9.4" ry="3.9"/>'
     '<ellipse cx="12" cy="12" rx="9.4" ry="3.9" transform="rotate(60 12 12)"/>'
     '<ellipse cx="12" cy="12" rx="9.4" ry="3.9" transform="rotate(120 12 12)"/>'),

    ("dna", "双螺旋 DNA",
     '<path d="M9 3c0 3 6 4 6 9s-6 6-6 9"/>'
     '<path d="M15 3c0 3-6 4-6 9s6 6 6 9"/>'
     '<path d="M9.9 7h4.2"/><path d="M9.9 17h4.2"/><path d="M10.6 12h2.8"/>'),

    ("nodes", "分子网络 Molecule",
     '<circle cx="12" cy="4.8" r="2.1"/><circle cx="4.9" cy="17.3" r="2.1"/>'
     '<circle cx="19.1" cy="17.3" r="2.1"/><circle cx="12" cy="12" r="1.7"/>'
     '<path d="M12 6.9v3.4"/><path d="M10.7 13.2 6.2 15.9"/><path d="M13.3 13.2 17.8 15.9"/>'),

    ("book", "文献著作 Book",
     '<path d="M12 6.4C10.1 4.8 7.7 4 4.6 4v13.9c3.1 0 5.5.8 7.4 2.4 1.9-1.6 4.3-2.4 7.4-2.4V4C16.3 4 13.9 4.8 12 6.4z"/>'
     '<path d="M12 6.4v13.9"/>'),
]

BY_KEY = {k: (label, body) for k, label, body in ICONS}


def edge_exe():
    for p in EDGE_CANDS:
        if os.path.isfile(p):
            return p
    raise SystemExit("找不到 Edge/Chrome，无法渲染图标")


def svg(body, size, stroke):
    return ('<svg width="%d" height="%d" viewBox="0 0 24 24" fill="none" stroke="#fff" '
            'stroke-width="%s" stroke-linecap="round" stroke-linejoin="round">%s</svg>'
            % (size, size, stroke, body))


def render(html, out_png, w, h, scale=1):
    """用 Edge 无头模式把 html 渲染成 png

    注意：Edge 是 GUI 程序，启动器会立刻返回、截图由浏览器进程异步落盘，
    所以这里必须轮询等待文件生成（而不是 run 完就认为好了）。
    """
    import time
    tmp = tempfile.mkdtemp(prefix="rbicon-")
    try:
        page = os.path.join(tmp, "p.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(html)
        url = "file:///" + page.replace("\\", "/")
        if os.path.isfile(out_png):
            os.remove(out_png)
        args = [edge_exe(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                "--user-data-dir=" + os.path.join(tmp, "prof"),
                "--window-size=%d,%d" % (w, h),
                "--force-device-scale-factor=%s" % scale,
                "--virtual-time-budget=3000",
                "--screenshot=" + out_png.replace("\\", "/"),
                url]
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, timeout=90)
        # 等待异步落盘：出现且大小稳定
        last, stable = -1, 0
        for _ in range(120):
            if os.path.isfile(out_png):
                sz = os.path.getsize(out_png)
                if sz > 0 and sz == last:
                    stable += 1
                    if stable >= 2:
                        return
                last = sz
            time.sleep(0.25)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.isfile(out_png):
        raise SystemExit("渲染失败（未生成）：" + out_png)


def tile_html(key, px, radius_pct=22.5, icon_pct=60.0, stroke_large="1.75", stroke_small="3.3"):
    """生成一块纯色圆角方块图标页（整块满铺，圆角在 Pillow 阶段再切）"""
    body = BY_KEY[key][1]
    sw = stroke_small if px <= 40 else stroke_large
    isize = int(round(px * icon_pct / 100.0))
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        'html,body{margin:0;padding:0;width:%dpx;height:%dpx;overflow:hidden;background:%s}'
        '.t{width:%dpx;height:%dpx;display:flex;align-items:center;justify-content:center;'
        'background:radial-gradient(120%% 120%% at 30%% 22%%, #558a63 0%%, %s 55%%, %s 100%%)}'
        '</style></head><body><div class="t">%s</div></body></html>'
        % (px, px, GREEN, px, px, GREEN, GREEN_D, svg(body, isize, sw))
    )


def rounded_mask(px, radius_pct=22.5):
    from PIL import Image, ImageDraw
    ss = 4  # 超采样，圆角抗锯齿
    m = Image.new("L", (px * ss, px * ss), 0)
    d = ImageDraw.Draw(m)
    r = max(2, int(round(px * radius_pct / 100.0))) * ss
    d.rounded_rectangle([0, 0, px * ss - 1, px * ss - 1], radius=r, fill=255)
    return m.resize((px, px), Image.LANCZOS)


def build_ico(key, sizes, out_ico):
    from PIL import Image
    big = os.path.join(ASSETS, "_master_big_%s.png" % key)
    small = os.path.join(ASSETS, "_master_small_%s.png" % key)
    render(tile_html(key, 1024, stroke_large="1.75"), big, 1024, 1024)
    render(tile_html(key, 512, stroke_small="3.3"), small, 512, 512)
    frames = []
    for s in sizes:
        src = small if s <= 40 else big
        im = Image.open(src).convert("RGBA").resize((s, s), Image.LANCZOS)
        out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        out.paste(im, (0, 0), rounded_mask(s))
        frames.append(out)
    frames.sort(key=lambda i: i.size[0])
    frames[-1].save(out_ico, format="ICO",
                    sizes=[f.size for f in frames], append_images=frames[:-1])
    return out_ico


def cmd_sheet():
    """候选符号对照图：大图 + 任务栏小尺寸实况"""
    rows = []
    for k, label, body in ICONS:
        smalls = "".join(
            '<span class="mini" style="width:%dpx;height:%dpx;border-radius:%dpx">%s</span>'
            % (s, s, int(s * 0.225), svg(body, int(s * 0.6), "2.9" if s <= 40 else "1.75"))
            for s in (16, 24, 32, 48))
        rows.append(
            '<div class="row"><div class="big" style="background:radial-gradient(120%% 120%% at 30%% 22%%,'
            '#558a63 0%%,%s 55%%,%s 100%%)">%s</div>'
            '<div class="meta"><div class="nm">%s</div><div class="kb">%s</div>'
            '<div class="minis">%s</div></div></div>'
            % (GREEN, GREEN_D, svg(body, 74, "1.75"), label, k, smalls))
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        'body{margin:0;padding:26px 30px;background:#f7f8f5;font-family:"Microsoft YaHei",'
        '"Segoe UI",sans-serif;color:#23302a}'
        'h2{margin:0 0 4px;font-size:17px}'
        'p.sub{margin:0 0 20px;font-size:12px;color:#8e9c93}'
        '.row{display:flex;align-items:center;gap:20px;padding:13px 0;border-bottom:1px solid #e6ebe3}'
        '.big{width:96px;height:96px;border-radius:22px;display:flex;align-items:center;'
        'justify-content:center;flex:0 0 auto;box-shadow:0 5px 16px rgba(74,124,89,.20)}'
        '.nm{font-size:13.5px;font-weight:700}'
        '.kb{font-size:11px;color:#8e9c93;margin:2px 0 9px;font-family:Consolas,monospace}'
        '.minis{display:flex;align-items:flex-end;gap:12px}'
        '.mini{display:flex;align-items:center;justify-content:center;background:%s}'
        '</style></head><body><h2>科研工作台 · 图标候选</h2>'
        '<p class="sub">第一行为推荐款（与左侧栏品牌符号一致）；下方小方块是任务栏/标题栏 16·24·32·48px 的真实效果</p>'
        '%s</body></html>' % (GREEN, "".join(rows)))
    out = os.path.join(ASSETS, "图标候选.png")
    render(html, out, 760, 890, scale=1)
    print("候选对照图 ->", out)
    return out


def cmd_build(key="flask"):
    if key not in BY_KEY:
        raise SystemExit("未知符号：%s（可用：%s）" % (key, ", ".join(BY_KEY)))
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(UI, exist_ok=True)
    ico = build_ico(key, [16, 24, 32, 48, 64, 128, 256],
                    os.path.join(ASSETS, "科研工作台.ico"))
    print("程序图标 ->", ico, os.path.getsize(ico), "bytes")
    fav = build_ico(key, [16, 32, 48], os.path.join(UI, "favicon.ico"))
    print("网页图标 ->", fav, os.path.getsize(fav), "bytes")
    # 各候选 256 预览图，便于用户之后换符号
    for k in BY_KEY:
        p = os.path.join(ASSETS, "预览_%s.png" % k)
        build_ico(k, [256], p)
    print("256 预览 ->", os.path.join(ASSETS, "预览_flask.png"), "等", len(BY_KEY), "张")
    return ico


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    key = sys.argv[2] if len(sys.argv) > 2 else "flask"
    os.makedirs(ASSETS, exist_ok=True)
    if cmd == "sheet":
        cmd_sheet()
    elif cmd == "build":
        cmd_build(key)
    else:
        cmd_sheet()
        cmd_build(key)
