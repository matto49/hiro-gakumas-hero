"""一条命令跑 H.I.F 培育：新主循环（hiro.runtime：自有判页 + HifCore 处理器 + 中断层），不用 KAA。

    python -m hiro.run_hif resume                      # 继续进行中的培育
    python -m hiro.run_hif new --mode qualify          # 新开选拔赛
    python -m hiro.run_hif new --mode main             # 新开 HIF 本战（需已有选拔赛回忆）
    选项：--llm claude:sonnet|openai:<model>  --no-llm  --trace <名>  --force（资源清单有问题也跑）
考试的局面来源 / 出招走插件（hiro.plugins：HIRO_EXAM_STATE_SOURCE / HIRO_EXAM_ADVISOR / HIRO_ASSET_SOURCE，不设用默认实现）。
旧的 KAA 主循环（HIRO_RUNTIME=kaa、--strategy、--allow-discard）2026-11-24 删掉了（用户 11-20 定退役）：别的端末有进行中的培育时
开局一律停下（退出码 3），是否破弃由使用者在游戏里自己决定。

每局输出：traces/<名>/steps.jsonl（每个决策点的截图 + Observation + GameState + Decision + Preview + ExecutionResult），
reports/runs/<名>.json（度量：考试内截图→点击中位耗时、LLM 调用与 token、故障恢复次数）。
实机长任务前请 caffeinate -d。
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from hiro.contracts import TraceWriter
from hiro.loop import manifest as manifest_mod
from hiro.loop.report import markdown, report
from hiro.loop.session import Session

logger = logging.getLogger(__name__)


def last_run_identity(mode: str, root: Path | None = None) -> dict | None:
    """续跑没给 --idol 时：同一模式最近一次带偶像的 trace（run.json 的 meta）里的 idol / main_attr。
    以前 resume 不带 --idol 时考试出牌拿不到偶像（idol=null、没有偶像档案和角色提示词），11-18 选拔第 2 场因此连败。"""
    import json

    from hiro.contracts.trace import TRACES

    best = None
    for f in (root or TRACES).glob("*/run.json"):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("mode") != mode or not meta.get("idol"):
            continue
        if best is None or (meta.get("started") or 0) > (best.get("started") or 0):
            best = {**meta, "trace": f.parent.name}
    return best


def resume_trace_name(prev_trace: str, root: Path | None = None) -> str:
    """续跑的 trace 名：接在同一局上一段后面，<基名>-rN（N 取下一个没用过的）。hiro.produce.hif 只认这个命名来找之前的段
    （牌组记账、考完几场、体力 / P 点打底）；11-19 续跑起成别的名字，选拔第 3 场计数成 1、最大体力读不到。"""
    import re

    from hiro.contracts.trace import TRACES

    base = re.sub(r"-r\d+$", "", prev_trace)
    used = {
        int(m.group(1))
        for d in (root or TRACES).glob(base + "-r*")
        if (m := re.fullmatch(re.escape(base) + r"-r(\d+)", d.name))
    }
    return f"{base}-r{max(used, default=0) + 1}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["resume", "new"])
    parser.add_argument("--mode", choices=["main", "qualify"], default="main")
    parser.add_argument("--idol", default=None, help="偶像卡 skin id，new 时使用")
    parser.add_argument("--no-llm", action="store_true", help="决策只用规则与兜底，不调用 LLM")
    parser.add_argument(
        "--llm", default=None, help="LLM 适配器 spec：claude:sonnet（默认）/ openai:<model>；也可用环境变量 HIRO_LLM"
    )
    parser.add_argument(
        "--main-attr",
        choices=["Vo", "Da", "Vi"],
        default=None,
        help="主属性（按支援卡编成定）；不给时按识别到的支援卡，再不行按成长率推测",
    )
    parser.add_argument("--exams-done", type=int, default=0, help="续跑时已完成的考试场数（决定 qualify-N 标签）")
    parser.add_argument("--trace", default=None, help="trace 目录名（默认时间戳）")
    parser.add_argument("--force", action="store_true", help="资源清单有问题（素材包过期等）也继续")
    parser.add_argument(
        "--debug-recog",
        action="store_true",
        help="调试模式（docs/state-events.md §7）：全部互验写进 trace、页面每次变化都存帧；也可设 HIRO_DEBUG_RECOG=1",
    )
    parser.add_argument(
        "--expect-memory",
        default="",
        help="本战开局前核对选中的选拔回忆：逗号分隔的属性数字（如 1697,1936,815），对不上就不开局",
    )
    args = parser.parse_args()
    if args.debug_recog:
        os.environ["HIRO_DEBUG_RECOG"] = "1"  # Session 读它：全部 Check 写进 trace、页面变化都存帧
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    from hiro.brain import guides

    guides.refresh_if_stale()  # 偶像卡攻略包：没有或超过 24 小时就下载，失败不阻塞（协调 11-20；H.I.F 要 HIRO_HIF_GUIDES=1 才用）
    if args.command == "resume" and not args.trace:
        prev = last_run_identity(args.mode)
        if prev is not None:
            args.trace = resume_trace_name(prev["trace"])
            logger.warning("续跑没给 --trace：接在同一局 %s 后面，记为 %s", prev["trace"], args.trace)
    if args.command == "resume" and not args.idol:
        prev = last_run_identity(args.mode)
        if prev is not None:
            args.idol = prev["idol"]
            args.main_attr = args.main_attr or prev.get("main_attr")
            logger.warning(
                "续跑没给 --idol：沿用最近一次 %s 的 trace %s：偶像 %s、主属性 %s",
                args.mode,
                prev["trace"],
                args.idol,
                args.main_attr,
            )
        else:
            logger.warning("续跑没给 --idol，也找不到同模式带偶像的 trace：考试出牌没有偶像档案（建议加 --idol）")

    if (os.environ.get("HIRO_RUNTIME") or "own").strip().lower() != "own":
        logger.warning("HIRO_RUNTIME=%s：KAA 主循环已经删了，照样用新主循环", os.environ.get("HIRO_RUNTIME"))
    from hiro import formation_env

    formation_env.apply_formation_plan(args.idol)
    if args.main_attr is None and formation_env.PLAN_MAIN_ATTR:
        args.main_attr = (
            formation_env.PLAN_MAIN_ATTR
        )  # 没给 --main-attr：用开局编成方案的主属性（策略、租借格、trace 都按它）

    manifest = manifest_mod.collect()
    if manifest.problems:
        for p in manifest.problems:
            logger.error("资源清单：%s", p)
        if not args.force:
            print("资源清单不一致，停止。确认无误可加 --force。", file=sys.stderr)
            return 2

    name = args.trace or datetime.now().strftime("%Y%m%d-%H%M%S")
    trace = TraceWriter(name=name, meta={"command": args.command, "mode": args.mode, "idol": args.idol, "llm": None if args.no_llm else (args.llm or "env/default"), "main_attr": args.main_attr, "debug_recog": os.environ.get("HIRO_DEBUG_RECOG") == "1",
                                         "strategy": "hiro.produce.hif:HifCore", "runtime": "own", "manifest": manifest.to_dict()})  # fmt: skip
    session = Session(trace)
    from hiro.loop import pause_flag
    from hiro.runtime import play

    pause_flag.wait_until_recording()  # 外部暂停旗标在（如录屏中断）：等它撤掉再操作设备；没设 HIRO_PAUSE_FLAG 时不启用
    logger.info("新主循环；trace → %s", trace.dir)
    try:
        return play.run(args, session)
    finally:
        trace.close()
        _write_report(trace, name)


def _write_report(trace, name: str) -> None:
    """每段结束写 reports/runs/<名>.json 并打印摘要。"""
    try:
        r = report(trace.dir)
        out = Path("reports/runs") / f"{name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(markdown(r))
        print(f"→ {out}")
    except Exception as e:  # 报告失败不影响已写好的 trace
        logger.error("生成报告失败：%r", e)


if __name__ == "__main__":
    sys.exit(main())
