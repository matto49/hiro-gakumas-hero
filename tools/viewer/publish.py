"""把 reports/viewer/*.html 发布到自己的服务器（rsync over ssh）。

会生成目录页 index.html，然后用 rsync 同步。
    .venv/bin/python tools/viewer/publish.py            # 同步全部
    .venv/bin/python tools/viewer/publish.py --dry-run  # 只看会传什么
服务器端：nginx 加一个 location /hiro-view/ 指向 /var/www/hiro-view/（REMOTE，可用 HIRO_VIEW_REMOTE 覆盖）。
"""

from __future__ import annotations

import argparse
import os
import html
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = os.environ.get("HIRO_VIEW_HOST", "")  # ssh 主机名（~/.ssh/config 里的别名），必填
REMOTE = os.environ.get("HIRO_VIEW_REMOTE", "/var/www/hiro-view/")
URL = os.environ.get("HIRO_VIEW_URL", "")  # 发布后的访问地址，只用于打印


def _meta(p: Path) -> dict:
    name = p.stem
    m = re.match(r"(?:[^-]+-)?(qualify|main)(\d+)?-(\d{8})-(\d{6})", name)
    kind = {"qualify": "选拔", "main": "本战"}.get(m.group(1), "?") if m else "?"
    game = m.group(2) if m and m.group(2) else ""
    when = time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime))
    return {"file": p.name, "kind": kind, "game": game, "when": when, "mtime": p.stat().st_mtime, "size": p.stat().st_size}


def index_html(pages: list[dict]) -> str:
    rows = "\n".join(
        f'<a class="row" href="{html.escape(x["file"])}"><span class="g">{("第" + x["game"] + "局") if x["game"] else ""}</span>'
        f'<span class="k k-{x["kind"]}">{x["kind"]}</span><span class="f">{html.escape(x["file"][:-5])}</span>'
        f'<span class="w">{x["when"]}</span><span class="s">{x["size"] / 1e6:.1f} MB</span></a>'
        for x in pages
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>数字小广 · 复盘页</title><style>
:root{{color-scheme:light dark;--bg:oklch(.985 .004 255);--ink:oklch(.24 .02 255);--ink3:oklch(.62 .015 255);--rule:oklch(.88 .008 255);--hover:oklch(.955 .007 255);--q:oklch(.58 .17 255);--m:oklch(.64 .2 3)}}
@media (prefers-color-scheme:dark){{:root{{--bg:oklch(.19 .012 255);--ink:oklch(.93 .008 255);--ink3:oklch(.6 .012 255);--rule:oklch(.32 .012 255);--hover:oklch(.235 .014 255);--q:oklch(.7 .14 255);--m:oklch(.72 .18 3)}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Hiragino Sans","PingFang SC",sans-serif}}
main{{max-width:880px;margin:0 auto;padding:28px 18px 60px}} h1{{font-size:22px;margin:0 0 4px}} p{{color:var(--ink3);margin:0 0 20px;font-size:13px}}
.row{{display:grid;grid-template-columns:4.5em 3.5em 1fr auto auto;gap:12px;align-items:baseline;padding:11px 8px;border-bottom:1px solid var(--rule);color:inherit;text-decoration:none}}
.row:hover{{background:var(--hover)}} .g{{font-weight:650}} .k{{font-size:12px}} .k-选拔{{color:var(--q)}} .k-本战{{color:var(--m)}}
.f,.w,.s{{color:var(--ink3);font-size:12.5px}} .f{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
@media (max-width:560px){{.row{{grid-template-columns:4.5em 3.5em 1fr}} .f,.s{{display:none}}}}
</style></head><body><main><h1>数字小广 · 复盘页</h1><p>共 {len(pages)} 份，更新于 {time.strftime("%Y-%m-%d %H:%M")}。批注存在各自浏览器里，换设备不会同步。</p>
{rows}
</main></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default=str(ROOT / "reports" / "viewer"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not HOST:
        raise SystemExit("请先设置 HIRO_VIEW_HOST（ssh 主机名）")
    src = Path(args.src)
    pages = sorted((_meta(p) for p in src.glob("*.html") if p.name != "index.html"), key=lambda x: -x["mtime"])
    (src / "index.html").write_text(index_html(pages), encoding="utf-8")
    cmd = ["rsync", "-az", "--include=*.html", "--exclude=*", str(src) + "/", f"{HOST}:{REMOTE}"]
    if args.dry_run:
        cmd.insert(1, "--dry-run")
        cmd.insert(1, "-v")
    subprocess.run(cmd, check=True)
    if not args.dry_run:  # macOS 自带 rsync 不支持 --chmod，同步后在服务器上统一放开读权限
        subprocess.run(["ssh", HOST, f"chmod 755 {REMOTE} && find {REMOTE} -type f -name '*.html' -exec chmod 644 {{}} +"], check=True)
    print(f"已发布 {len(pages)} 份 → {URL}")


if __name__ == "__main__":
    main()
