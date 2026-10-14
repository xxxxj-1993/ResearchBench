"""用用户真实数据副本，验证老数据格式下「改 schedule / today 导航名」能持久化。
不碰真实数据（复制到临时目录）。"""
import os, sys, json, shutil, tempfile, threading, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app as appmod

REAL = os.environ.get("RB_TEST_SOURCE_DATA", "")
if not REAL or not os.path.isfile(REAL):
    print("SKIP: 如需测试老数据兼容性，请显式设置 RB_TEST_SOURCE_DATA")
    sys.exit(0)
TMP = tempfile.mkdtemp(prefix="rb_realdata_")
appmod.DATA_DIR = TMP
appmod.DATA_FILE = os.path.join(TMP, "data.json")
appmod.LOG_FILE = os.path.join(TMP, "app.log")
appmod.LAUNCH = os.path.join(TMP, "launch.json")
shutil.copyfile(REAL, appmod.DATA_FILE)  # 用真实数据副本

# 真实 load_db（含老数据迁移）
db0 = appmod.load_db()
print("老数据 settings.navLabels =", db0["settings"].get("navLabels"))

from http.server import ThreadingHTTPServer
httpd = ThreadingHTTPServer(("127.0.0.1", 0), appmod.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % port


def get_state():
    with urllib.request.urlopen(BASE + "/api/state", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def patch_settings(obj):
    req = urllib.request.Request(
        BASE + "/api/patch",
        data=json.dumps({"key": "settings", "value": obj}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


st = get_state()
cur = dict(st["db"]["settings"])
cur["navLabels"] = dict(cur.get("navLabels") or {})
cur["navLabels"]["schedule"] = "我的课表"
cur["navLabels"]["today"] = "主页"
resp = patch_settings(cur)
print("patch resp =", resp)

# 重启：重新 load_db 从磁盘
db_re = appmod.load_db()
print("重启后 settings.navLabels =", db_re["settings"].get("navLabels"))

httpd.shutdown()
ok1 = db_re["settings"].get("navLabels", {}).get("schedule") == "我的课表"
ok2 = db_re["settings"].get("navLabels", {}).get("today") == "主页"
# 老数据原有 pubs 改名应保留
ok3 = db_re["settings"].get("navLabels", {}).get("pubs") == "已发表论文"
print("schedule 持久化", "OK" if ok1 else "FAIL")
print("today 持久化", "OK" if ok2 else "FAIL")
print("原 pubs 改名保留", "OK" if ok3 else "FAIL")
sys.exit(0 if (ok1 and ok2 and ok3) else 1)
