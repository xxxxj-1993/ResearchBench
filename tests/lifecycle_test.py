# -*- coding: utf-8 -*-
"""验证生命周期守卫 run_guard（对应「编辑一段时间后链接打不开」的根因修复）。

修复前 main 主循环是 `while time.time() - LAST_SEEN["t"] < 30.0`，
只要连续 30 秒没有前端心跳（/api/ping）就 os._exit 杀掉后端。
Edge 默认开启「睡眠标签页」，窗口空闲几分钟后会冻结 JS 定时器、停掉心跳，
于是后端被误杀——界面还在却点不开任何 /api/open 链接。

修复后：run_guard 以「Edge 窗口进程是否还活着」为准，窗口关了才退出；
无活动多久都不会自杀。本测试直接验证这一不变量。
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import threading, time
import app

results = []
def chk(n, c, e=""):
    results.append(bool(c))
    print(("PASS  " if c else "FAIL  ") + n + (("   " + str(e)) if e else ""))


class FakeProc:
    """模拟 Edge 窗口进程：alive=True 时 poll() 返回 None（仍运行）。"""
    def __init__(self):
        self.alive = True
    def poll(self):
        return None if self.alive else 0


# 1) 无活动 45 秒，窗口一直活着 -> 不应退出（修复前 30 秒就会自杀）
fp = FakeProc()
t = threading.Thread(target=app.run_guard, args=(fp, False), daemon=True)
t.start()
time.sleep(45)
chk("无活动 45 秒后端仍存活(未自杀)", t.is_alive(), "线程状态=%s" % t.is_alive())

# 2) 模拟用户关闭窗口 -> poll() 返回非 None -> 应在几秒内退出
fp.alive = False
time.sleep(4)
chk("窗口关闭后后端退出", not t.is_alive(), "线程状态=%s" % t.is_alive())

# 3) 启动失败兜底：proc=None 且非 no_window -> 8 秒后退出（避免空跑占端口）
t2 = threading.Thread(target=app.run_guard, args=(None, False), daemon=True)
t2.start()
time.sleep(11)
chk("窗口启动失败 8 秒后兜底退出", not t2.is_alive())

# 4) RB_NO_WINDOW 模式：proc=None 但 no_window=True -> 持续运行不自杀
t3 = threading.Thread(target=app.run_guard, args=(None, True), daemon=True)
t3.start()
time.sleep(3)
chk("RB_NO_WINDOW 模式持续运行(不自杀)", t3.is_alive())
# 不强行结束 t3；daemon=True 随主进程退出

print("\n结果: %d/%d 通过" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
