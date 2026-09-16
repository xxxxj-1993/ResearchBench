# -*- coding: utf-8 -*-
"""真机取证：启动 exe 的真实窗口，抓标题栏截图，确认窗口图标已换成烧瓶

只截目标窗口本身（PrintWindow），不会拍到桌面上其它内容。
"""
import os
import subprocess
import sys
import tempfile
import time

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "科研工作台.exe")
ASSETS = os.path.join(ROOT, "assets")

tmp = tempfile.mkdtemp(prefix="rbcap-")
shot = os.path.join(tmp, "win.png")

subprocess.run(["taskkill", "/F", "/IM", "科研工作台.exe"],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
env = dict(os.environ)
env.pop("RB_NO_WINDOW", None)
proc = subprocess.Popen([EXE], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("已启动 exe pid", proc.pid, "，等待窗口出现…")
time.sleep(12)

ps = r'''
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class RBWin {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern IntPtr FindWindow(string cls, string name);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr a, IntPtr b);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowTextW(IntPtr h, System.Text.StringBuilder s, int n);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@
$found = $null
$targets = @("科研工作台", "ResearchBench")
foreach ($t in $targets) {
  $h = [RBWin]::FindWindow($null, $t)
  if ($h -ne [IntPtr]::Zero -and [RBWin]::IsWindowVisible($h)) { $found = $h; break }
}
if ($found -eq $null) {
  # 退而求其次：遍历 Chrome_WidgetWin_1 找可见窗口
  $hs = Get-Process | Where-Object { $_.MainWindowTitle -ne "" } | Select-Object -First 5
  foreach ($p in $hs) {
    if ($p.MainWindowTitle -match "科研工作台") { $found = $p.MainWindowHandle; break }
  }
}
if ($found -eq $null -or $found -eq [IntPtr]::Zero) { Write-Output "NOWINDOW"; exit }
$r = New-Object RBWin+RECT
[void][RBWin]::GetWindowRect($found, [ref]$r)
$w = $r.R - $r.L; $h2 = $r.B - $r.T
Write-Output ("RECT {0} {1} {2} {3}" -f $r.L, $r.T, $w, $h2)
$bmp = New-Object System.Drawing.Bitmap($w, $h2)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[void][RBWin]::PrintWindow($found, $hdc, 2)   # 2 = PW_RENDERFULLCONTENT
$g.ReleaseHdc($hdc)
$g.Dispose()
$bmp.Save("SHOTPATH", [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "SAVED"
Write-Output ("TITLE " + $p.MainWindowTitle)
'''
ps = ps.replace("SHOTPATH", shot.replace("\\", "/"))
r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, encoding="utf-8", errors="replace")
print("PS 输出:", (r.stdout or "").strip()[:400])
if (r.stderr or "").strip():
    print("PS 错误:", r.stderr.strip()[:300])

if os.path.isfile(shot):
    im = Image.open(shot).convert("RGB")
    print("窗口截图尺寸:", im.size)
    # 裁标题栏左上角（图标 + 标题）并放大，便于肉眼核对
    tw, th = min(im.size[0], 420), min(im.size[1], 44)
    bar = im.crop((0, 0, tw, th))
    out = os.path.join(ASSETS, "标题栏实测.png")
    bar.resize((tw * 2, th * 2), Image.LANCZOS).save(out)
    print("标题栏截图 ->", out)
    full = os.path.join(ASSETS, "窗口实测.png")
    im.resize((im.size[0] // 2, im.size[1] // 2), Image.LANCZOS).save(full)
    print("整窗截图 ->", full)

# 收尾：只关我们这次打开的那个窗口（WM_CLOSE），绝不按进程名杀 msedge
# —— 否则会连带关掉用户自己正在用的 Edge 浏览器。
ps_close = r'''
$hit = Get-Process | Where-Object { $_.MainWindowTitle -match "科研工作台" }
if ($hit) { foreach ($x in $hit) { [void]$x.CloseMainWindow(); Write-Output ("CLOSED " + $x.Id) } }
else { Write-Output "NOWINDOW" }
'''
rc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_close],
                    capture_output=True, encoding="utf-8", errors="replace")
print("关窗:", (rc.stdout or "").strip())
time.sleep(3)
# 窗口关了，exe 的守卫检测到退出即自行结束；仍在则只清我们自己这个进程
if proc.poll() is None:
    subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("已清理（仅本程序进程，未触碰其它 Edge）")
sys.exit(0 if os.path.isfile(shot) else 1)
