# -*- coding: utf-8 -*-
"""验证打包后的真实 exe：启动 -> 读 app.log 取端口 -> 抓首页/state -> 确认内联资源与种子随包生效"""
import os, re, subprocess, sys, time, urllib.request, json

EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "科研工作台.exe")
EXE = os.path.abspath(EXE)
LOG = os.path.join(os.environ.get("APPDATA", ""), "ResearchWorkbench", "app.log")

print("exe:", EXE, "存在:", os.path.isfile(EXE), "大小:", os.path.getsize(EXE) // 1024, "KB")

# 注意：不杀同名进程，避免误关用户正在使用的实例；本测试只 kill 自己启动的 proc。
time.sleep(1)

env = dict(os.environ)
env["RB_NO_WINDOW"] = "1"
env["no_proxy"] = "127.0.0.1,localhost"
env["NO_PROXY"] = "127.0.0.1,localhost"

proc = subprocess.Popen([EXE], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("exe PID:", proc.pid)

# 等待服务起来并写入日志
url = None
for _ in range(40):
    time.sleep(0.5)
    try:
        with open(LOG, encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        m = re.findall(r"started (http://127\.0\.0\.1:\d+/)", txt)
        if m:
            url = m[-1]
            break
    except Exception:
        pass
if not url:
    proc.kill()
    print("FAIL: 未能从 app.log 读到服务地址"); sys.exit(1)
print("服务地址:", url)

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 长稳验证：启动后刻意不发送任何请求，静默 40 秒，再确认服务仍存活。
# 对应「编辑/空闲一段时间后链接打不开」的修复——修复前主循环会在无活动
# 30 秒后自杀，真实 exe 在此会失败；现在应始终存活。
time.sleep(40)
try:
    code = op.open(url, timeout=10).status
except Exception:
    code = -1
ok = []
def chk(n, c, e=""):
    ok.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + (("   " + str(e)) if e else ""))
chk("静默 40 秒后服务仍存活(无活动不自杀)", code == 200, "HTTP %s" % code)

html = op.open(url, timeout=30).read().decode("utf-8")
chk("首页可访问(>80KB)", len(html) > 80000, len(html))
chk("首页内联无外链(无 src=http)", "src=\"http" not in html and "src='http" not in html)
chk("首页含新栏目-已立项", "已立项" in html)
chk("首页含新栏目-教学成果", "教学成果" in html)
chk("首页含新栏目-专利", "专利" in html)
chk("首页含新栏目-我的材料", "我的材料" in html)
chk("专利模块已渲染", "专利" in html)
chk("内联前端含新视图 viewGrants", "function viewGrants" in html)
chk("内联前端含我的材料表单", "addMaterial" in html)
chk("首页引用 favicon(窗口/任务栏图标)", 'rel="icon"' in html and "/favicon.ico" in html)

# 图标随包生效：/favicon.ico 由 exe 内的 ui/favicon.ico 提供
try:
    fav = op.open(url + "favicon.ico", timeout=20).read()
except Exception as e:
    fav = b""
    print("      favicon 请求异常:", e)
chk("favicon 路由可访问(>1KB)", len(fav) > 1024, len(fav))
chk("favicon 是合法 ICO", fav[:4] == b"\x00\x00\x01\x00", fav[:4].hex())

st = json.loads(op.open(url + "api/state", timeout=30).read().decode("utf-8"))["db"]
chk("state.grants=8", len(st.get("grants") or []) == 8, len(st.get("grants") or []))
chk("state.teachProjects=5", len(st.get("teachProjects") or []) == 5, len(st.get("teachProjects") or []))
chk("state.patents=1", len(st.get("patents") or []) == 1, len(st.get("patents") or []))
chk("state.pubs=31", len(st.get("pubs") or []) == 31, len(st.get("pubs") or []))
chk("state.ideas>=7", len(st.get("ideas") or []) >= 7, len(st.get("ideas") or []))
chk("state.materials>0", len(st.get("materials") or []) > 0, len(st.get("materials") or []))
chk("Zotero 已联动(shortcuts 含 Zotero)", any("Zotero" in (s.get("name") or "") for s in st.get("shortcuts") or []))

# 关键回归：打包后的 exe 启动不得重置/重新扫描常用栏目（shortcuts）
_disk = os.path.join(os.environ.get("APPDATA", ""), "ResearchWorkbench", "data.json")
if os.path.isfile(_disk):
    try:
        _d = json.load(open(_disk, encoding="utf-8"))
        chk("常用栏目 shortcuts 未被重置(与本地 data.json 数量一致)",
            len(_d.get("shortcuts") or []) == len(st.get("shortcuts") or []),
            "%s vs %s" % (len(_d.get("shortcuts") or []), len(st.get("shortcuts") or [])))
    except Exception as e:
        print("      读取本地 data.json 失败:", e)

print("\n结果: %d/%d 通过" % (sum(ok), len(ok)))
proc.kill()
sys.exit(0 if all(ok) else 1)
