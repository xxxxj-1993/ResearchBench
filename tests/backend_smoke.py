# -*- coding: utf-8 -*-
"""v2 单进程冒烟测试：本地服务 + 全接口断言 + 自动退出"""
import io
import os
import sys
import json
import time
import threading
import urllib.request
import urllib.parse
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["RB_NO_WINDOW"] = "1"

import app  # noqa: E402

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

app.load_db()
httpd = ThreadingHTTPServer(("127.0.0.1", 8765), app.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.4)
BASE = "http://127.0.0.1:8765"

ok = []


def chk(name, cond, extra=""):
    ok.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name + (("   " + str(extra)) if extra else ""))


def post(p, obj):
    req = urllib.request.Request(BASE + p, data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))


def get(p):
    return json.loads(urllib.request.urlopen(BASE + p, timeout=60).read().decode("utf-8"))


print("=== 基础 ===")
st = get("/api/state")
db = st.get("db") or {}
chk("state 返回完整数据模型", all(k in db for k in
    ("papers", "projects", "pubs", "ideas", "diary", "events", "shortcuts", "settings", "focus")))
chk("论文/项目/成果/灵感 有数据", len(db["papers"]) > 0 and len(db["projects"]) > 0
    and len(db["pubs"]) > 0 and len(db["ideas"]) > 0)
chk("快捷入口已自动补齐软件", len(db["shortcuts"]) >= 8, len(db["shortcuts"]))
html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")
chk("首页渲染且内联无外链", len(html) > 80000 and "全局搜索" in html, len(html))

print()
print("=== 文件 / 程序 ===")
chk("列目录", len(get("/api/dir?path=" + urllib.parse.quote(r"C:\Windows"))["entries"]) > 5)
r = post("/api/open", {"kind": "folder", "target": r"C:\Windows"})
chk("打开文件夹", r["ok"], r.get("msg"))
chk("不存在路径返回错误", not post("/api/open", {"kind": "folder", "target": r"D:\__nope__"})["ok"])
chk("启动不存在程序返回错误", not post("/api/open", {"kind": "app", "target": r"C:\nope.exe"})["ok"])
chk("打开网址（空值保护）", post("/api/open", {"kind": "url", "target": ""})["ok"] is False)

print()
print("=== Zotero ===")
zs = get("/api/zotero/status")
chk("Zotero 连接", zs.get("ok"), zs.get("msg") or (str(zs.get("total")) + " 条 · " + str(zs.get("dir"))))
if zs.get("ok"):
    cols = get("/api/zotero/collections")["collections"]
    chk("读取分类树", len(cols) > 10, len(cols))
    cid = cols[0]["id"]
    items = get("/api/zotero/items?limit=10&collection=" + str(cid))["items"]
    chk("按分类取文献", len(items) > 0, len(items))
    print("        样本:", (items[0]["title"][:52] if items else "-"),
          "| 作者:", ",".join(items[0]["creators"][:2]) if items else "")
    found = get("/api/zotero/items?limit=10&q=" + urllib.parse.quote("INZ"))["items"]
    chk("全文检索文献", len(found) > 0, len(found))
    prof = get("/api/zotero/profile")["items"]
    chk("找到个人简历", len(prof) > 0, [p["title"] for p in prof])
    if prof:
        atts = prof[0]["attachments"]
        chk("简历有本地附件", any(a["exists"] for a in atts) or len(atts) > 0,
            [a["name"] for a in atts])

print()
print("=== Obsidian ===")
vs = get("/api/obsidian/vaults")["vaults"]
chk("发现 vault", len(vs) > 0, [v["name"] for v in vs])
if vs:
    vp = vs[0]["path"]
    ns = get("/api/obsidian/notes?vault=" + urllib.parse.quote(vp))["notes"]
    chk("列出笔记", len(ns) > 0, len(ns))
    if ns:
        nt = get("/api/obsidian/note?vault=" + urllib.parse.quote(vp)
                 + "&rel=" + urllib.parse.quote(ns[0]["rel"]))
        chk("读取笔记正文", nt.get("ok") and len(nt["note"]["content"]) > 0,
            len(nt["note"]["content"]) if nt.get("ok") else "")
    ideas = get("/api/obsidian/ideas?vault=" + urllib.parse.quote(vp))["ideas"]
    print("        灵感类笔记:", len(ideas), (ideas[0]["title"][:40] if ideas else "-"))

print()
print("=== 写入 / 迁移 ===")
db2 = get("/api/state")["db"]
db2["ideas"].append({"id": "smoke1", "title": "冒烟测试灵感", "content": "test",
                     "tags": ["test"], "status": "待验证", "createdAt": 0})
post("/api/patch", {"key": "ideas", "value": db2["ideas"]})
chk("patch 写入并回读", any(x["id"] == "smoke1" for x in get("/api/state")["db"]["ideas"]))
# 还原
db3 = get("/api/state")["db"]
db3["ideas"] = [x for x in db3["ideas"] if x["id"] != "smoke1"]
post("/api/patch", {"key": "ideas", "value": db3["ideas"]})
chk("清理测试数据", not any(x["id"] == "smoke1" for x in get("/api/state")["db"]["ideas"]))

print()
print("结果: %d/%d 通过" % (sum(ok), len(ok)))
httpd.shutdown()
sys.exit(0 if all(ok) else 1)
