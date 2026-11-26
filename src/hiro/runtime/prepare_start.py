"""开局前准备（G0 / D4，11-26）：一句话开局时方案没定编成号或还没写进游戏，不再反问玩家，而是在同一个开局任务里先准备好再开局。

由 hiro.cli.jobs 在开局任务（hajime / nia / hif 选拔）带 prepare 参数时包在原命令外面：

    python -m hiro.runtime.prepare_start --mode hajime --out <任务目录>/prepare.json -- <原来的开局命令…>

1. 方案没定编成号：进编成菜单读这个模式的编成位现状（只读，同 scan-formations），结果写进信息库；
   每样挑空着的最小号写进方案。都满了就停下（不替玩家覆盖已有编成），退出码 4。
2. 方案没写进游戏：跑写编成（tools/write_formation.py，写完回读核对，核对通过记 written）。失败就停，不开局。
3. exec 原来的开局命令。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[3]
LABEL = {"support": "支援卡", "memory": "回忆"}
MODE_NAME = {"hif": "H.I.F", "hajime": "「初」", "nia": "N.I.A"}


def plan_path() -> Path:
    """开局核对和写编成读的当前方案（同 tools/write_formation.py）。"""
    env = os.environ.get("HIRO_FORMATION_PLAN")
    if env:
        return Path(env)
    from hiro.cli import paths
    from hiro.cli.home import hiro_home

    return paths.plan_path(hiro_home())


def _survey(mode: str, kinds=("support", "memory")) -> dict:
    """进编成菜单读编成位现状（hiro.produce.formation_survey，只读）：只读要挑号的那几样，每样读到第一个空位就停（N33）。"""
    from hiro.produce import formation_menu
    from hiro.produce.formation_survey import survey
    from hiro.tool_device import open_tool_device
    from hiro.vision.support_screens import read_support_formation

    dev = formation_menu.tool_device(open_tool_device())
    return survey(dev, mode, kinds=tuple(kinds), read_formation=read_support_formation, stop_at_blank=True)


def _write(mode: str, what=("support", "memory"), blank_only=()) -> int:
    """写编成（写完回读核对，通过后 mark_written）。返回退出码。blank_only 见 tools/write_formation.py --blank-only。"""
    argv = [sys.executable, str(REPO / "tools" / "write_formation.py"), "--mode", mode, "--what", ",".join(what)]
    if blank_only:
        argv += ["--blank-only", ",".join(blank_only)]
    return subprocess.run(argv, check=False).returncode  # noqa: S603


def _check_memory_freshness(plan_file: Path, home: Path) -> None:
    """写回忆前的库存新鲜度检查（同手动写编成，hiro.cli.cmd_run._check_memory_freshness）：不新鲜抛 CliError。"""
    from hiro.cli.cmd_run import _check_memory_freshness as check

    check(plan_file, home)


def _exec(argv: list[str], *, platform: str = sys.platform) -> int:
    """接着跑原来的开局命令（hiro.cli.jobs 拼的）。POSIX 用 exec 换掉本进程（退出码、停止信号都直接到对局进程）。
    Windows 不能 exec：os.execvp 在 Windows 上是另起新进程、本进程立刻以 0 退出——任务层等到 0 就报「完成」，
    对局出错也一样；停止时 taskkill /T 也找不到真正在打的那个进程（N51，G3 第 3 轮 11-26：收尾超时 180 秒仍报完成）。
    Windows 上改成起子进程等它结束，原样返回它的退出码；子进程在本进程的进程树里，停止任务时一起结束。"""
    sys.stdout.flush()
    sys.stderr.flush()
    if platform == "win32":
        return subprocess.call(argv)  # noqa: S603
    os.execvp(argv[0], argv)  # noqa: S606
    return 0  # 只在测试替身里走到


def _full_hint(mode: str, what: str, rows: list) -> str:
    """编成位都满了时说清楚：有哪几号、哪些是初星助手自己写过的（覆盖它不动玩家配的）、在对话里怎么说。
    G1 第 3 轮 11-26（N34）：原来只说「告诉初星助手用哪一号覆盖」，小白不知道有哪些号、哪个能覆盖。"""
    from hiro.cli.cmd_run import slot_written_by_us
    from hiro.cli.home import hiro_home

    nums = sorted(int(r["set"]) for r in rows if r.get("kind") == what)
    span = f"{nums[0]}–{nums[-1]} 号" if nums else ""
    ours = [n for n in nums if slot_written_by_us(hiro_home(), mode, what, n)]
    msg = (
        f"{MODE_NAME[mode]}的{LABEL[what]}编成 {span}都有卡，没有空着的可以放编成方案（初星助手不替你覆盖自己配的编成）"
    )
    if ours:
        msg += f"。其中 {'、'.join(map(str, ours))} 号曾由初星助手写入，覆盖前请确认后来没有手动改过"
    return msg + f"。要覆盖哪一号，在对话里说「{LABEL[what]}编成用 N 号覆盖」；也可以先在游戏里清空一组再开局"


def _stop(why: str, code: int) -> int:
    print(f"停下：{why}", file=sys.stderr, flush=True)
    return code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--" not in args:
        print("用法：python -m hiro.runtime.prepare_start --mode <模式> [--out 文件] -- <开局命令…>", file=sys.stderr)
        return 2
    i = args.index("--")
    ap = argparse.ArgumentParser(description="开局前准备：挑空白编成位、写编成，再开局")
    ap.add_argument("--mode", required=True, choices=["hajime", "nia", "hif"])
    ap.add_argument("--out", help="准备做了什么（json）")
    ap.add_argument(
        "--what", default="support,memory", help="准备哪几样（support / memory；开局命令里指定了编成号的那样不用管）"
    )
    opts = ap.parse_args(args[:i])
    inner = args[i + 1 :]
    if not inner:
        return _stop("没有给开局命令", 2)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro.brain import formation_plan as fp

    mode = opts.mode
    path = plan_path()
    plan = fp.load(path)
    if not plan:
        return _stop(f"没有开局编成方案（{path}）", 2)
    rec: dict = {"mode": mode, "picked": {}, "wrote": False}
    what = [w for w in ("support", "memory") if w in opts.what.split(",")]
    missing = [w for w in what if (plan.get(w) or {}).get("set") is None]
    if missing:
        logger.info(
            "开局前准备：方案还没定%s编成号，先读%s的编成位", "、".join(LABEL[w] for w in missing), MODE_NAME[mode]
        )
        out = _survey(mode, missing)
        from hiro.cli.cmd_info import import_inventory

        import_inventory("formations", out, source="prepare_start")
        rows = out.get("sets") or []
        for w in missing:
            blanks = sorted(int(r["set"]) for r in rows if r.get("kind") == w and r.get("filled") == 0)
            if not blanks:
                return _stop(_full_hint(mode, w, rows), 4)
            rec["picked"][w] = blanks[0]
            plan[w] = dict(plan.get(w) or {}, set=blanks[0])
        cid = (plan.get("idol") or {}).get("idol_card_id")
        from hiro.cli.home import hiro_home

        if cid and plan.get("version") == fp.PLAN_VERSION and path == plan_path() and path.parent == hiro_home():
            fp.save_plan(hiro_home(), plan, mode)  # 按偶像卡 × 模式的那份和当前方案一起更新
        else:
            path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("开局前准备：用空着的 %s", "、".join(f"{LABEL[w]}编成 {n} 号" for w, n in rec["picked"].items()))
    plan = fp.load(path) or plan
    todo = fp.unwritten(plan, mode, what) if plan.get("version") == fp.PLAN_VERSION else []
    if todo:
        if "memory" in todo:
            from hiro.cli.home import hiro_home
            from hiro.cli.registry import CliError

            try:
                _check_memory_freshness(path, hiro_home())
            except CliError as e:
                return _stop(f"{e.message}。{e.hint}", 5)
        # 编成号是之前读数里挑的空位（或沿用的）：初星助手没写过的位，写之前再看一眼，玩家后来填了卡就停下、不覆盖（#134 review）
        from hiro.cli.cmd_run import slot_written_by_us
        from hiro.cli.home import hiro_home

        guard = [w for w in todo if not slot_written_by_us(hiro_home(), mode, w, (plan.get(w) or {}).get("set"))]
        logger.info("开局前准备：把编成方案写进游戏（%s）", "、".join(LABEL[w] for w in todo))
        rc = _write(mode, todo, guard)
        if rc != 0:
            # 具体原因写编成那一步已经以「停下：…」打过（任务失败原因取最后一条「停下」）：这里只记一行普通日志，不再盖掉它
            # （G3 11-26：对话卡只显示「写编成没成功，没有开局（原因见上面的日志）」，界面里看不到日志）
            print("写编成没成功，没有开局", file=sys.stderr, flush=True)
            return rc
        rec["wrote"] = True
    if opts.out:
        Path(opts.out).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    logger.info("开局前准备做完，开局")
    return _exec(inner)


if __name__ == "__main__":
    sys.exit(main())
