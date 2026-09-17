# -*- coding: utf-8 -*-
"""把 ui/app.js 内联进 ui/_template.html，产出单文件 ui/index.html"""
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")
tpl = io.open(os.path.join(UI, "_template.html"), encoding="utf-8").read()
js = io.open(os.path.join(UI, "app.js"), encoding="utf-8").read()

if "/*__PART2__*/" not in tpl:
    raise SystemExit("模板里找不到 /*__PART2__*/ 占位符")
if "</script" in js.lower():
    raise SystemExit("app.js 里出现了 </script，会截断 HTML")

out = tpl.replace("/*__PART2__*/", js)
io.open(os.path.join(UI, "index.html"), "w", encoding="utf-8").write(out)
print("built ui/index.html  %d bytes (js %d)" % (len(out), len(js)))
