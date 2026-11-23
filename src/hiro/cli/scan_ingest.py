"""扫描任务结束后，把结果（任务目录里的 out.json）导入本机信息库，并在事件流里记一条。"""

from __future__ import annotations

import json

from hiro.cli.cmd_info import import_inventory


def ingest(job) -> None:
    kind = job.kind.output
    p = job.dir / "out.json"
    if not p.is_file():
        job.emit("import_failed", "扫描没有产出结果，信息库没有更新（看上面的日志）")
        return
    try:
        inv = json.loads(p.read_text(encoding="utf-8"))
        r = import_inventory(kind, inv, source=f"scan:{job.id}")
    except (OSError, ValueError, KeyError) as e:
        job.emit("import_failed", f"扫描结果导入失败：{e}")
        return
    job.emit("imported", f"已更新信息库：{r['count']} 条", kind=kind, count=r["count"])
