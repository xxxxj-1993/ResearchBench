"""端到端验证：导航改名 -> 落盘 -> 重启加载 是否真的持久化。
不碰真实数据，全部用临时目录。"""
import os, sys, json, time, tempfile, threading, urllib.request, urllib.error

ROOT = r"F:\软件包\ResearchBench - 9-16"
sys.path.insert(0, ROOT)
import app as appmod

# 所有路径切到临时目录，避免污染真实数据
TMP = tempfile.mkdtemp(prefix="rb_e2e_")
appmod.DATA_DIR = TMP
appmod.DATA_FILE = os.path.join(TMP, "data.json")
appmod.LOG_FILE = os.path.join(TMP, "app.log")
appmod.LAUNCH = os.path.join(TMP, "launch.json")

# 初始化 STATE["db"]（相当于正常启动时的 load_db）
db0 = appmod.load_db()
print("init settings.navLabels =", db0["settings"].get("navLabels"))

# 启动真实 HTTP 服务
from http.server import ThreadingHTTPServer
httpd = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % port


def get_state():
    with urllib.request.urlopen(BASE + "/api/state", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def patch_settings(settings_obj):
    req = urllib.request.Request(
        BASE + "/api/patch",
        data=json.dumps({"key": "settings", "value": settings_obj}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) 前端 GET /api/state（模拟启动）
st = get_state()
assert st["ok"] is True, st
before = st["db"]["settings"].get("navLabels", {})
print("before patch navLabels =", before)

# 2) 模拟前端 setNavLabel('schedule','我的课表') 后 saveKey('settings')
cur = dict(st["db"]["settings"])  # 整个 settings 传回，和前端一致
cur["navLabels"] = dict(before)
cur["navLabels"]["schedule"] = "我的课表"
resp = patch_settings(cur)
print("patch resp =", resp)

# 3) 立刻再 GET /api/state（模拟前端刷新后读取）
st2 = get_state()
print("after patch (in-memory) navLabels =", st2["db"]["settings"].get("navLabels"))

# 4) 模拟「重启」：重新 load_db（从磁盘读）
db_re = appmod.load_db()
print("after restart (disk) navLabels =", db_re["settings"].get("navLabels"))

httpd.shutdown()

ok = db_re["settings"].get("navLabels", {}).get("schedule") == "我的课表"
print("ROUND-TRIP", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
