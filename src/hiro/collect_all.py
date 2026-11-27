"""一键全收集（资料库扫描设计 5.7 / D7 / D12）：按顺序做 5 步扫描（首页数值 → 偶像卡 → 支援卡 → 编成 → 回忆），每步跑完就入库；某一步出错就停下，写明第几步。
回忆这一步是 12 片逐片全读（不是增量）。只读：每一步都是现有的只读扫描任务（不点決定、不改编成）。

    python -m hiro.collect_all --dir <任务目录> [--only idols,memories]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

STEPS = (
    ("account", "scan-account", "首页数值"),
    ("idols", "scan-idols", "偶像卡"),
    ("supports", "scan-supports", "支援卡"),
    ("formations", "scan-formations", "编成"),  # 编成在回忆前：回忆扫描靠编成快照挑全空的编成（终审 I4）
    ("memories", "scan-memories", "回忆"),
)
KIND_NAMES = tuple(k for k, _, _ in STEPS)


def run(job_dir: Path, only: list[str] | None, *, runner=None, importer=None) -> int:
    """only：只做这几步（FirstScan 只补没扫过的）。返回退出码：0 全部完成，非 0 = 出错的那一步的退出码。"""
    from hiro.cli.jobs import KINDS

    if importer is None:
        from hiro.cli.cmd_info import import_inventory as importer
    runner = runner or (lambda argv, env=None: subprocess.run(argv, env=env, check=False))
    steps = [s for s in STEPS if not only or s[0] in only]
    for i, (kind, job_kind, label) in enumerate(STEPS, 1):
        if (kind, job_kind, label) not in steps:
            continue
        k = KINDS[job_kind]
        d = job_dir / f"{i}-{kind}"
        d.mkdir(parents=True, exist_ok=True)
        print(f"一键全收集：第 {i}/{len(STEPS)} 步 {label}", flush=True)
        r = runner(k.argv({"full": True} if kind == "memories" else {}, d), env={**os.environ, **k.env})
        out = d / "out.json"
        if k.output and out.is_file():
            try:
                inv = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                print(f"一键全收集：第 {i} 步（{label}）的结果读不出来，没能入库：{e}", flush=True)
                return r.returncode or 1
            # 出错时只导入按片逐片落盘的回忆（带 slices）；整表扫描的半截结果不存成最新快照（设计 I2，#116 第三轮）
            if r.returncode == 0 or (kind == "memories" and "slices" in inv):
                try:
                    res = importer(k.output, inv, source=f"collect:{job_dir.name}:{kind}")
                except (OSError, ValueError, KeyError) as e:  # 导入失败让任务失败，不能吞掉后报完成（#116 第三轮）
                    print(f"一键全收集：第 {i} 步（{label}）没能入库：{e}", flush=True)
                    return r.returncode or 1
                print(
                    f"一键全收集：{label}已入库 {res.get('count')} 条" + ("（没做完的部分）" if r.returncode else ""),
                    flush=True,
                )
        if r.returncode == SLICE_FILTER_EXIT and kind == "memories" and _slices_on() and _filter_reset(out):
            # N43（G2 11-26 Mac rc.7）：按片扫描靠读「ソート・フィルタ」弹窗，Vision 坏了换 RapidOCR 后页签 / 按钮常读不出，
            # 按片扫描停下。回忆这一步改用整表滚动扫描再做一遍（不碰筛选弹窗，RapidOCR 下也扫得完），对话里说明
            print("一键全收集：按片扫描回忆没成功，改用整表扫描再扫一遍（不动筛选，慢一些）", flush=True)
            d2 = job_dir / f"{i}-{kind}-whole"
            d2.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, **k.env, "HIRO_MEMORY_SLICES": "0"}
            old = os.environ.get("HIRO_MEMORY_SLICES")
            os.environ["HIRO_MEMORY_SLICES"] = "0"  # 参数按开关算：算参数时先关上
            try:
                argv = k.argv({"full": True}, d2)
            finally:
                if old is None:
                    os.environ.pop("HIRO_MEMORY_SLICES", None)
                else:
                    os.environ["HIRO_MEMORY_SLICES"] = old
            r = runner(argv, env=env)
            out2 = d2 / "out.json"
            if r.returncode == 0 and k.output and out2.is_file():
                try:
                    whole = json.loads(out2.read_text(encoding="utf-8"))
                except (OSError, ValueError) as e:
                    print(f"一键全收集：第 {i} 步（{label}）整表扫描的结果读不出来：{e}", flush=True)
                    return 1
                if not whole.get("complete"):
                    # 退出码 0 不等于扫完（翻页对不上会写 complete=False 正常退出）：半截不当最新库存（#173 review）
                    print(
                        f"一键全收集：第 {i} 步（{label}）整表扫描没扫完（翻页对不上），没有入库，可以再扫一次",
                        flush=True,
                    )
                    return 1
                try:
                    res = importer(k.output, whole, source=f"collect:{job_dir.name}:{kind}:whole")
                except (OSError, ValueError, KeyError) as e:
                    print(f"一键全收集：第 {i} 步（{label}）整表扫描的结果没能入库：{e}", flush=True)
                    return 1
                print(f"一键全收集：{label}已入库 {res.get('count')} 条（整表扫描）", flush=True)
        if r.returncode == SLICE_FILTER_EXIT and kind == "memories" and not _filter_reset(out):
            print("停下：按片扫描回忆时设筛选出错，回忆列表的筛选可能还停在某一片上（没能确认重置）；"
                  "请在游戏的回忆选择页点「ソート・フィルタ」→「フィルタをリセット」后再扫一次", flush=True)  # fmt: skip
        if r.returncode != 0:
            print(f"一键全收集：第 {i} 步（{label}）出错停下（退出码 {r.returncode}），前面几步已经入库", flush=True)
            return r.returncode or 1
    print("一键全收集：完成", flush=True)
    return 0


SLICE_FILTER_EXIT = 6  # 同 hiro.memory_scan.SLICE_FILTER_EXIT（这里不导入 memory_scan：它会拉起设备、视觉等模块）


def _filter_reset(out: Path) -> bool:
    """按片扫描出错后，筛选确认重置了没有（out.json 的 filter_reset）：没确认就不能改用整表扫描——
    筛选还停在某一片时「整表」其实只读到那一片（#173 review）。"""
    try:
        return bool(json.loads(out.read_text(encoding="utf-8")).get("filter_reset"))
    except (OSError, ValueError):
        return False


def _slices_on() -> bool:
    from hiro.state.memory_slices import enabled

    return enabled()


def main() -> int:
    ap = argparse.ArgumentParser(description="一键全收集（只读）")
    ap.add_argument("--dir", required=True, help="任务目录（每一步的输出放在子目录里）")
    ap.add_argument("--only", help=f"只做这几步，逗号分隔：{','.join(KIND_NAMES)}")
    a = ap.parse_args()
    only = [x.strip() for x in a.only.split(",") if x.strip()] if a.only else None
    return run(Path(a.dir), only)


if __name__ == "__main__":
    sys.exit(main())
