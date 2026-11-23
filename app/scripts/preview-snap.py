#!/usr/bin/env python3
"""界面预览截图：bun run preview:build 之后，把每个页面离屏渲染成 png（不弹窗、不抢焦点）。
用法：python3 scripts/preview-snap.py [页面 ...]   输出 .preview/shots/<页面>.png
截图工具 wksnap（离屏 WKWebView 截图的小程序）：用 WKSNAP 环境变量指过去，没设时从 PATH 里找。"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / ".preview"
WKSNAP = os.environ.get("WKSNAP", "wksnap")
VIEWS = sys.argv[1:] or ["chat", "start", "cockpit", "plan", "supports", "memories", "settings"]

html = (ROOT / "preview/index.html").read_text()
# file:// 下 WebKit 不跑 module 脚本：把打包出来的脚本内联
m = re.search(r'<script type="module" crossorigin src="([^"]+)"></script>', html)
if m:
    js = (ROOT / "preview" / m.group(1)).resolve().read_text()
    html = html.replace(m.group(0), '<script type="module">' + js.replace("</script", "<\\/script") + "</script>")
(ROOT / "shots").mkdir(exist_ok=True)
for v in VIEWS:
    page = ROOT / "preview" / f"{v}.html"
    # 离屏 WebKit 不跑 CSS 过渡（停在第一帧，按钮像是禁用的）：截图时关掉过渡
    head = f'<head><script>location.hash="{v}"</script><style>*,*::before,*::after{{transition:none!important;animation:none!important}}</style>'
    page.write_text(html.replace("<head>", head, 1))
    subprocess.run([WKSNAP, str(page), str(ROOT / "shots" / f"{v}.png"), "1280", "800", "2"], check=True)
