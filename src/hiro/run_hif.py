# 来源：digital-hiro hiro/run_hif.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""一条命令跑 HIF 培育：KAA 流程骨架 + 策略（默认 hiro.produce.hif.HiroHifStrategy）+ 主循环 trace。

    python -m hiro.run_hif resume                      # 继续进行中的培育
    python -m hiro.run_hif new --mode qualify          # 新开选拔赛
    python -m hiro.run_hif new --mode main             # 新开 HIF 本战（需已有选拔赛回忆）
    选项：--llm claude:sonnet|openai:<model>  --no-llm  --trace <名>  --force（资源清单有问题也跑）
          --strategy 包.模块:类名（或环境变量 HIRO_STRATEGY）：换培育策略，须是 KAA HifGrindStrategy 的子类
考试的局面来源 / 出招走插件（hiro.plugins：HIRO_EXAM_STATE_SOURCE / HIRO_EXAM_ADVISOR / HIRO_ASSET_SOURCE，不设用默认实现）。

每局输出：traces/<名>/steps.jsonl（每个决策点的截图 + Observation + GameState + Decision + Preview + ExecutionResult），
reports/runs/<名>.json（度量：考试内截图→点击中位耗时、LLM 调用与 token、故障恢复次数）。
实机长任务前请 caffeinate -d。
"""

import argparse
import os
import importlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import hiro  # noqa: F401  兼容层
from kaa.config.const import HifScenario
from kotonebot.backend.context import init_context, manual_context

from hiro.contracts import TraceWriter
from hiro.device import connect
from hiro.kaa_env import init_kaa
from hiro.loop import manifest as manifest_mod
from hiro.loop.controller import HiroController
from hiro.loop.report import markdown, report
from hiro.loop.session import Session

logger = logging.getLogger(__name__)


MAX_CRASH_RESTARTS = 5
MAX_DATE_CHANGES = 3


DEFAULT_STRATEGY = "hiro.produce.hif:HiroHifStrategy"


def load_strategy(spec: str | None = None):
    """培育策略类：参数 > 环境变量 HIRO_STRATEGY > 默认 HiroHifStrategy。必须是 KAA HifGrindStrategy 的子类（沿用它的流程骨架）。"""
    from kaa.tasks.produce.new.strategies.hif_grind import HifGrindStrategy

    spec = spec or os.environ.get("HIRO_STRATEGY") or DEFAULT_STRATEGY
    mod, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"策略写法应为 包.模块:类名，收到 {spec!r}")
    cls = getattr(importlib.import_module(mod), attr)
    if not (isinstance(cls, type) and issubclass(cls, HifGrindStrategy)):
        raise TypeError(f"{spec} 不是 KAA HifGrindStrategy 的子类")
    return cls


def _supervised_resume(scenario, idol, session, Strategy=None) -> None:
    """跑 resume 主循环；未预期的异常记 recover:crash_restart 后从当前画面重新进入，最多 MAX_CRASH_RESTARTS 次。
    ResumeBlocked（需要用户决定的情形）不重试。DateChanged（凌晨 4 点日切换）：回到首页后从「プロデュース中」续跑，不计入崩溃次数。"""
    import time

    from hiro.loop.date_change import DateChanged
    from hiro.loop.resume import ResumeBlocked, run as resume_run

    attempt = date_changes = relaunches = 0
    while True:
        try:
            resume_run(scenario, Strategy or load_strategy(), HiroController, idol=idol)
            return
        except ResumeBlocked:
            raise
        except DateChanged:
            date_changes += 1
            if date_changes > MAX_DATE_CHANGES or not _recover_date_change(session):
                raise ResumeBlocked("日期切换后没能回到首页，需要人工处理") from None
            logger.info("日期切换已处理（第 %d 次），从首页续跑", date_changes)
        except Exception as e:
            from hiro.loop.win_desktop import DesktopUnavailable

            if isinstance(e, DesktopUnavailable):
                raise ResumeBlocked(str(e)) from e  # 已经等过桌面恢复（win_desktop）：再重试也是同样结果，讲清原因停下
            if _game_gone(session, e):
                relaunches += 1
                if relaunches > MAX_RELAUNCHES or not _relaunch_game(session):
                    raise ResumeBlocked("学マス不在前台，重新拉起后没能回到首页，需要人工处理") from e
                continue
            if attempt >= MAX_CRASH_RESTARTS:
                raise
            attempt += 1
            logger.exception("主循环异常（第 %d 次），3 秒后从当前画面继续", attempt)
            session.recover("crash_restart", f"{type(e).__name__}: {e}")
            time.sleep(3)


def _refill_ap(scenario) -> bool:
    """AP 不足：在培育页重新点 H.I.F 按钮弹出「AP不足」，交 hiro.loop.ap_refill（只喝 APドリンク，确认框有ジュエル就取消）。
    只打选拔时本战还要 20 AP，一起补上。"""
    from kaa.tasks import R
    from kotonebot import device

    from hiro.loop import ap_refill
    from hiro.produce.ops import ocr_boxes

    btn = R.Produce.ButtonHifMain if scenario == HifScenario.MAIN else R.Produce.ButtonHifQualify
    mode = "main" if scenario == HifScenario.MAIN else "qualify"
    # 多试几帧，模板没中按 OCR「本戦 / 選抜試験」定位（第二十七局：只截一次、模板没中就失败，按钮其实在）
    if not ap_refill.open_hif(device.screenshot, lambda x, y: device.click(x, y), ocr_boxes, mode=mode, try_template=btn.try_click):
        logger.error("AP 补充：%.0fs 内培育页没找到 H.I.F「%s」按钮，弹不出「AP不足」", ap_refill.OPEN_TIMEOUT, ap_refill.HIF_BUTTON_TEXT[mode])
        return False
    r = ap_refill.refill(device.screenshot, lambda x, y: device.click(x, y), ocr_boxes, extra_ap=0 if scenario == HifScenario.MAIN else 20,
                         log=logger.warning)  # fmt: skip
    logger.warning("AP 补充结果：%s", r)
    return bool(r["ok"])


MAX_RELAUNCHES = 2


def _game_gone(session, err: Exception) -> bool:
    """主循环异常时查一下学マス还在不在前台（安卓后端）。不在：开关 HIRO_RELAUNCH_GAME=1 时返回 True 交给 _relaunch_game；
    开关关着就抛 ResumeBlocked 讲清原因（以前当普通崩溃重试 5 次，每次都截到横屏桌面、裁空图崩在识别里）。"""
    from hiro.loop import game_app
    from hiro.loop.resume import ResumeBlocked

    if game_app.alive() is not False:
        return False
    logger.error("主循环异常时学マス不在前台（进程被杀 / 闪退？）：%r", err)
    if game_app.is_dmm():
        raise ResumeBlocked("DMM 版学マス的窗口不见了（闪退 / 被关？）。请从 DMM Game Player 重新启动游戏"
                            "（会弹管理员确认），回到首页后用 resume 续跑") from err
    if not game_app.enabled():
        raise ResumeBlocked("学マス不在前台（进程被杀 / 闪退？内存吃紧时 MuMu 会杀游戏）。请重新打开游戏后续跑，"
                            "或设 HIRO_RELAUNCH_GAME=1 让程序自己拉起") from err
    return True


def _relaunch_game(session) -> bool:
    """am start 拉起学マス，再按日期切换同一条路（标题页 → 首页）回首页；之后从「プロデュース中」续跑。"""
    from hiro.loop import game_app

    session.recover("crash_restart", "学マス不在前台：重新拉起游戏，回首页续跑")
    if not game_app.relaunch(game_app.adb_shell()):
        return False
    return _recover_date_change(session, note=None)


def _recover_date_change(session, note: str | None = "游戏日切换「日付変更」：回首页后续跑") -> bool:
    """日期切换：タイトルへ → TAP TO START → 登录奖励 → 首页。"""
    from kaa.tasks.actions.scenes import at_home
    from kotonebot import device

    from hiro.loop.date_change import recover_to_home
    from hiro.produce.ops import ocr_boxes

    if note:
        session.recover("date_change", note)
    return recover_to_home(device.screenshot, lambda x, y: device.click(x, y), ocr_boxes, lambda img: at_home())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["resume", "new"])
    parser.add_argument("--mode", choices=["main", "qualify"], default="main")
    parser.add_argument("--idol", default=None, help="偶像卡 skin id，new 时使用")
    parser.add_argument("--no-llm", action="store_true", help="决策只用规则与兜底，不调用 LLM")
    parser.add_argument("--llm", default=None, help="LLM 适配器 spec：claude:sonnet（默认）/ openai:<model>；也可用环境变量 HIRO_LLM")
    parser.add_argument("--main-attr", choices=["Vo", "Da", "Vi"], default=None,
                        help="主属性（按支援卡编成定）；不给时按识别到的支援卡，再不行按成长率推测")
    parser.add_argument("--exams-done", type=int, default=0, help="续跑时已完成的考试场数（决定 qualify-N 标签）")
    parser.add_argument("--trace", default=None, help="trace 目录名（默认时间戳）")
    parser.add_argument("--force", action="store_true", help="资源清单有问题（素材包过期等）也继续")
    parser.add_argument("--debug-recog", action="store_true",
                        help="调试模式（docs/state-events.md §7）：全部互验写进 trace、页面每次变化都存帧；也可设 HIRO_DEBUG_RECOG=1")
    parser.add_argument("--strategy", default=None,
                        help="培育策略类 包.模块:类名（默认 hiro.produce.hif:HiroHifStrategy）；也可设 HIRO_STRATEGY")
    parser.add_argument("--expect-memory", default="",
                        help="本战开局前核对选中的选拔回忆：逗号分隔的属性数字（如 1697,1936,815），对不上就不开局")
    parser.add_argument("--allow-discard", action="store_true",
                        help="new 时允许破弃其他端末进行中的培育（用户明确同意后才加；KAA 会自动确认「プロデュースデータの破棄」）")
    args = parser.parse_args()
    if args.debug_recog:
        os.environ["HIRO_DEBUG_RECOG"] = "1"  # Session 读它：全部 Check 写进 trace、页面变化都存帧
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    scenario = HifScenario.MAIN if args.mode == "main" else HifScenario.QUALIFY
    Strategy = load_strategy(args.strategy)
    from hiro.kaa_env import SetupMissing

    try:
        init_kaa(scenario, idol=args.idol, require_sets=args.command == "new")
    except SetupMissing as e:
        print(str(e), file=sys.stderr)
        return 2
    from hiro import kaa_env

    if args.main_attr is None and kaa_env.PLAN_MAIN_ATTR:
        args.main_attr = kaa_env.PLAN_MAIN_ATTR  # 没给 --main-attr：用开局编成方案的主属性（策略、租借格、trace 都按它）

    manifest = manifest_mod.collect()
    if manifest.problems:
        for p in manifest.problems:
            logger.error("资源清单：%s", p)
        if not args.force:
            print("资源清单不一致，停止。确认无误可加 --force。", file=sys.stderr)
            return 2

    name = args.trace or datetime.now().strftime("%Y%m%d-%H%M%S")
    trace = TraceWriter(name=name, meta={"command": args.command, "mode": args.mode, "idol": args.idol, "llm": None if args.no_llm else (args.llm or "env/default"), "main_attr": args.main_attr, "debug_recog": os.environ.get("HIRO_DEBUG_RECOG") == "1",
                                         "strategy": f"{Strategy.__module__}:{Strategy.__qualname__}", "manifest": manifest.to_dict()})  # fmt: skip
    session = Session(trace)
    HiroController.session = session
    for k, v in {"session": session, "use_llm": not args.no_llm, "llm_spec": args.llm, "main_attr": args.main_attr,
                 "exams_done": args.exams_done}.items():  # fmt: skip
        if hasattr(Strategy, k):  # 自定义策略没有这些属性就不注入
            setattr(Strategy, k, v)

    from hiro.loop import pause_flag

    pause_flag.wait_until_recording()  # 外部暂停旗标在（如录屏中断）：等它撤掉再操作设备；没设 HIRO_PAUSE_FLAG 时不启用
    init_context(target_device=connect())
    manual_context().begin()
    from kotonebot.backend.context import vars as kvars

    rec_watch = pause_flag.Watcher(kvars.flow, session=session).start()  # flow 要在主线程取，交给看守线程

    # KAA 的培育入口里写死了 HifGrindStrategy 与 ProduceController，替换为注入的策略和 HiroController。
    # kaa.tasks 导出了同名任务函数 produce，遮住了子包属性，只能按模块全名导入。
    produce_module = importlib.import_module("kaa.tasks.produce.produce")
    produce_module.HifGrindStrategy = Strategy
    produce_module.ProduceController = HiroController
    # KAA 补丁（P 道具、授業换卡、locate_idol、选拔回忆核对）resume 与 new 都要启用：以前只在 new 里 apply，
    # 续跑（-rN）和 resume 起的本战都跑的是 KAA 原版——第四局本战换卡盲选第 0 张就是这样发生的
    from hiro.loop import kaa_patches

    kaa_patches.RENTAL_ATTR = args.main_attr  # 租借格挑主属性的卡（不给时按 rental.WANT_ATTR）
    kaa_patches.EXPECT_MEMORY = tuple(v.strip() for v in args.expect_memory.split(",") if v.strip())
    kaa_patches.apply()
    # 日期切换看守：挂到 kotonebot 每个 Loop 的每一轮（任何卡在 KAA 循环里的流程都会被 DateChanged 打出来）
    from kotonebot.config.config import conf as kb_conf

    from hiro.loop.date_change import loop_callback
    from hiro.produce.ops import ocr_boxes

    kb_conf().loop.loop_callbacks.append(loop_callback(ocr_boxes))
    from hiro.loop.repeat_guard import watchdog

    # 空转看门狗（协调 2026-11-03）：画面和局面都 3 分钟没进展就停机报警（第二十三局 R2 空转 40 分钟、第二十一局选拔① 17.8 分钟没人发现）
    kb_conf().loop.loop_callbacks.append(watchdog(session))
    logger.info("trace → %s", trace.dir)
    from hiro.produce.exam_policy import policy_from_env

    # 实际生效的考试局面来源 / 出招（scripts/play.ps1 外面设了 HIRO_EXAM_STATE_SOURCE / HIRO_EXAM_POLICY 就透传）
    logger.info("考试局面来源 %s；出招 %s；HIRO_EXAM_POLICY=%s", manifest.exam_state_source, manifest.exam_advisor, policy_from_env())
    try:
        if args.command == "resume":
            # KAA 的 resume_produce 只认本战的再開对话框；用自己的入口，遇到「データの破棄」只取消不破弃
            from hiro.loop.resume import ResumeBlocked

            try:
                _supervised_resume(scenario, args.idol, session, Strategy)
            except ResumeBlocked as e:
                logger.error("%s", e)
                print(str(e), file=sys.stderr)
                return 3
        else:
            from hiro.loop.resume import ResumeBlocked, finish_produce_end, guard_new

            from kaa.tasks.actions.scenes import at_home

            from kotonebot import device as _dev

            _dev.screenshot()
            if not at_home():
                finish_produce_end(go_home=True)  # 上一局停在结算画面 / 本战开始确认页时 KAA 的 goto_home 会空转

            if args.allow_discard:
                logger.warning("--allow-discard：若有其他端末进行中的培育，将被破弃")
                kaa_patches.ALLOW_DISCARD = True
            else:
                try:
                    guard_new()  # KAA 的 do_produce 会自动确认「プロデュースデータの破棄」，先拦住
                except ResumeBlocked as e:
                    logger.error("%s", e)
                    print(str(e), file=sys.stderr)
                    return 3
            try:
                import time as _time

                HiroController.new_game_at = _time.monotonic()  # 开局过场里看到開始確認页不停（第二十三局：设置框盖在上面被判成开始确认页）
                started = produce_module.do_produce(args.idol, scenario)
                if started is False:
                    # KAA：AP 不足、已点キャンセル退出（use_ap_drink=false）。我们自己喝 APドリンク（绝不用ジュエル）再开一次
                    if not _refill_ap(scenario):
                        print("AP 不足，APドリンク 没补上（见日志），停止", file=sys.stderr)
                        return 3
                    HiroController.new_game_at = _time.monotonic()
                    produce_module.do_produce(args.idol, scenario)
            except ResumeBlocked as e:  # do_produce 里遇到破弃对话框（kaa_patches.NeverConfirmDiscard 已点キャンセル）
                logger.error("%s", e)
                print(str(e), file=sys.stderr)
                return 3
            except kaa_patches.RentalPending as e:  # 租借格要手动挑（支援卡选择系统未交付）：停在第 2 步，没开局
                logger.error("%s", e)
                print(str(e), file=sys.stderr)
                return 3
            except kaa_patches.FormationMismatch as e:  # 支援卡 / 回忆编成和开局编成方案对不上：没开局，直接停
                logger.error("%s", e)
                print(str(e), file=sys.stderr)
                return 3
            except kaa_patches.MemoryMismatch as e:  # 选拔回忆不符：没开局，直接停
                logger.error("%s", e)
                print(str(e), file=sys.stderr)
                return 3
            except Exception:
                # 培育已经开始后崩溃：记一次恢复，转入 resume 继续这一局
                logger.exception("培育中异常，转为 resume 继续")
                session.recover("crash_restart", "new 流程异常，转 resume")
                _supervised_resume(scenario, args.idol, session, Strategy)
    finally:
        rec_watch.stop()
        trace.close()
        try:
            r = report(trace.dir)
            out = Path("reports/runs") / f"{name}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            print(markdown(r))
            print(f"→ {out}")
        except Exception as e:  # 报告失败不影响已写好的 trace
            logger.error("生成报告失败：%r", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
