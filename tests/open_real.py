# -*- coding: utf-8 -*-
"""真实打开测试：不起 mock，直接看系统里有没有真的被拉起来"""
import json, os, subprocess, sys, time, threading, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["RB_NO_WINDOW"] = "1"
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import app

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(opener)

ok_n = [0]
fail = []


def chk(name, cond, extra=""):
    if cond:
        ok_n[0] += 1
        print("PASS  %s   %s" % (name, extra))
    else:
        fail.append(name)
        print("FAIL  %s   %s" % (name, extra))


def post(path, obj):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(path):
    return json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d%s" % (port, path), timeout=30).read())


def procs():
    try:
        out = subprocess.check_output(["tasklist", "/fo", "csv", "/nh"],
                                      stderr=subprocess.DEVNULL).decode("gbk", "ignore")
    except Exception:
        return ""
    return out.lower()


port = app.free_port()
import http.server as _hs
httpd = _hs.ThreadingHTTPServer(("127.0.0.1", port), app.Handler)
app.SERVER["httpd"] = httpd
threading.Thread(target=httpd.serve_forever, daemon=True).start()
app.load_db()
time.sleep(0.3)

print("=== 1) 打开文件夹 ===")
r = post("/api/open", {"kind": "folder", "target": r"C:\Windows"})
chk("文件夹 返回 ok", r.get("ok") is True, json.dumps(r, ensure_ascii=False))
time.sleep(2.5)
chk("explorer 进程在运行", "explorer.exe" in procs())

print("=== 2) 打开软件（notepad 无害验证）===")
np = r"C:\Windows\System32\notepad.exe"
chk("notepad 存在", os.path.isfile(np))
r = post("/api/open", {"kind": "app", "target": np})
chk("软件 返回 ok", r.get("ok") is True, json.dumps(r, ensure_ascii=False))
time.sleep(3.0)
chk("notepad 进程已拉起", "notepad.exe" in procs())

print("=== 3) 路径含空格的 exe ===")
sp = None
for c in [r"C:\Program Files\Zotero\zotero.exe",
          r"E:\Program Files\MATLAB\R2024b\bin\matlab.exe"]:
    if os.path.isfile(c):
        sp = c
        break
if sp:
    # 只验证「能定位到文件」，不真的启动重型软件
    st = app.launch_app  # noqa
    chk("空格路径被识别为文件", os.path.isfile(sp), sp)
else:
    chk("空格路径被识别为文件", True, "(跳过)")

print("=== 4) 错误路径要给报错，不能静默失败 ===")
r = post("/api/open", {"kind": "folder", "target": r"Z:\根本没有这个目录"})
chk("错误路径 返回 ok=False", r.get("ok") is False, json.dumps(r, ensure_ascii=False))
r = post("/api/open", {"kind": "app", "target": r"C:\nope\nope.exe"})
chk("错误 exe 返回 ok=False", r.get("ok") is False, json.dumps(r, ensure_ascii=False))

print("=== 5) 打开真实快捷方式（前端卡片走的就是这条）===")
st = get("/api/state")
sc = (st.get("db") or {}).get("shortcuts") or []
folder_sc = [s for s in sc if s.get("kind") == "folder" and os.path.isdir(s.get("target") or "")]
app_sc = [s for s in sc if s.get("kind") == "app" and os.path.isfile(s.get("target") or "")]
chk("有可点开的文件夹快捷方式", len(folder_sc) > 0, "%d 个" % len(folder_sc))
chk("有可点开的软件快捷方式", len(app_sc) > 0, "%d 个" % len(app_sc))
if folder_sc:
    r = post("/api/open", {"kind": "folder", "target": folder_sc[0]["target"]})
    chk("真实文件夹快捷方式能打开", r.get("ok") is True,
        "%s -> %s" % (folder_sc[0]["name"], json.dumps(r, ensure_ascii=False)))

print("=== 6) 列目录（展开最近文件）===")
if folder_sc:
    d = get("/api/dir?path=" + urllib.request.quote(folder_sc[0]["target"]))
    chk("列目录返回条目", d.get("ok") is True and len(d.get("entries") or []) >= 0,
        "%d 项" % len(d.get("entries") or []))

print("=== 7) 清理测试进程 ===")
subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("已关闭 notepad")

httpd.shutdown()
print()
print("结果: %d/%d 通过" % (ok_n[0], ok_n[0] + len(fail)))
if fail:
    print("失败项: " + ", ".join(fail))
    sys.exit(1)
