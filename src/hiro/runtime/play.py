"""新主循环跑一局（HIRO_RUNTIME=own，去 KAA 第 6 步接线）：设备（hiro.devices）→ Runtime（自有判页 + HifCore 处理器 + 中断层）
→ 开局 / 续跑（hiro.runtime.opening / finish）→ rt.run 到培育结束。不 import KAA / kotonebot。

run_hif 在 HIRO_RUNTIME=own 时调 run(args, session)；其余参数、trace、报告、退出码和旧主循环一样。
没有处理器的页面交给 advance：剧情 SKIP → 横屏 LIVE 曲目页 → 一次性设置框 → 安全空白连点（hiro.loop.advance.burst，同旧主循环）。
日切换（DateChanged）按旧主循环的做法回首页再续跑，一局最多 MAX_DATE_CHANGES 次。
"""

from __future__ import annotations

import logging

from hiro.devices.base import DeviceError
from hiro.loop.errors import ResumeBlocked

logger = logging.getLogger(__name__)

MAX_DATE_CHANGES = 2
# 只在这些页上安全连点。后一行是只展示结果、点一下就过的页（自有判页认得出、但不用决策）：旧主循环里 KAA 判不出它们，
# 走的也是这条连点（2026-11-14 DMM 实机：外出拿到「眠気」的获得演出盖在剧情上，181 秒没人点被看门狗停机）
ADVANCE_PAGES = ("unknown", "IDLE", "TRANSITION", "LOADING", "commu", "LIVE",
                 "ACQUIRE", "STAR_GAIN", "UPGRADE_RESULT", "DELETE_RESULT", "CUSTOMIZE_RESULT")  # fmt: skip
# 没有按钮、只有效果说明的展示页：说明文字常带 BLOCK_WORDS（「レッスン終了時」的「終了」），不过文字过滤，没有弹窗标题条就点一下空白
# （P 道具「カスタマイズ完了」：Mac h10 11-15 原来没人点，180 秒停机）
# 结果气泡页（「〜を強化しました」「〜をカスタマイズしました」「〜を削除しました」）也只是展示：页面判页（page_ocr）认出来时
# 交 advance 会被禁点词「強化 / 削除」拦住不点（N.I.A 采样 11-17：支援卡事件强化后停了；H.I.F 平时由快速判页先认出、点 (12,12)）
SHOW_PAGES = ("PITEM_CUSTOMIZE_DONE", "UPGRADE_RESULT", "CUSTOMIZE_RESULT", "DELETE_RESULT")
# 快速判页（hiro.vision.quick）认出的「点一下就过」的页（获得演出、结果气泡、TAP 提示、礼物箱…）带 tap 直接点；
# 不点的页（读取 / 闪屏 / 考前说明演出）同一种连续 QUICK_WAIT_TAP_S 秒没过就点一下兜底（同旧主循环 HiroController._quick_page）
IDLE_STOP_S = 180.0  # H.I.F：这么久没有任何操作就停机（会动的画面「画面没变」看门狗管不到）
QUICK_WAIT_TAP_S = 15.0
QUICK_WAIT_TAP_BY_KIND = {"gift_effect": 1.0, "day_countdown": 4.0}
# 选拔 / 本战通关后的结算流程开头几页（选回忆照片、选拔突破证书），判页认不出：交 finish.finish_produce_end 一路点到开始确认页停
# （同旧主循环 HiroController._is_produce_finished）
END_MARKERS = ("メモリーにするフォト", "選抜試験突破者")
# 剧情 SKIP（左下 (225,1200)）在选行动页上正是饮料栏第 3 格（协调 11-18 初 MASTER 20:31：剧情帧上隔 0.36 秒点了两次 SKIP，
# 第一下已经跳过、第二下落在刚出来的选行动页上，点开「Pドリンク詳細」盖住页面，选行动读不到选项停机）。
# 点过 SKIP 这么久之内不再点；点之前对新截的帧再核一次底栏还在
COMMU_RETAP_S = 2.0
QUICK_WAIT_TAP_POS = {"day_countdown": "safe"}  # 倒计时下面就是选行动页，(12,12) 会点进左上框：改点安全空白


def _produce_finished(frame) -> bool:
    texts = [t.text.strip() for t in frame.texts]
    return any(m in t for t in texts for m in END_MARKERS) or "完了する" in texts


def advance(rt, frame, hit) -> bool:
    """没有处理器的页面：先认非弹窗场景，再按旧主循环的规则安全连点。返回是否点了。"""
    if hit.name == "unknown" and _produce_finished(frame):
        from hiro.runtime import finish

        logger.info("认到结算流程（回忆照片 / 突破证书）：交给结算收尾，点到开始确认页停")
        finish.finish_produce_end(rt)
        rt.finished = True  # 这一段打完了：run 的 until 看它
        return True
    from hiro.loop import advance as adv
    from hiro.runtime import scenes
    from hiro.vision.pages import dialog_header

    if (q := (getattr(hit, "fields", None) or {}).get("_quick")) is not None and q.get("dispatch") is None:
        return _quick_tap(rt, q, frame.t)
    if scenes.live_start(rt, frame):
        return True
    if frame.img is not None and frame.img.shape[1] > frame.img.shape[0]:
        return False  # 横屏演出中：不点
    if hit.name in ADVANCE_PAGES and scenes.tap_next(rt, frame):
        return True
    if scenes.commu_skip(rt, frame):
        _flag_deck_after_commu(rt)
        return True
    if scenes.first_run_settings(rt, frame):
        return True
    if hit.name in SHOW_PAGES:
        if dialog_header(frame.img) is not None:
            return False
        rt.device.tap(*adv.ADVANCE_TAP)
        return True
    if hit.name not in ADVANCE_PAGES:
        return False
    n = adv.burst([t.text for t in frame.texts], shoot=lambda: rt.screen.shot().img, click=rt.device.tap, sleep=rt.sleep,
                  in_live=lambda img: img is not None and img.shape[1] > img.shape[0], header=dialog_header)  # fmt: skip
    return n > 0


def _flag_deck_after_commu(rt) -> None:
    """培育中跳过了一段剧情：支援事件给的技能卡在跳过后不弹获得页，账本记不上（DMM mainverify2 11-17：支援卡 s_card-3-0094 的事件给
    「頭上に注意！」，R1 起手就有、模拟器牌组里没有，搜索整场跟丢）。把牌组标成待复核（UNMODELED_CHANGE），下次到选行动页读一次牌组。"""
    session = getattr(rt, "session", None)
    state = getattr(session, "state", None)
    prod = getattr(state, "produce", None) or {}
    if not prod.get("deck") or "produce.deck" in (getattr(state, "pending_review", None) or []):
        return
    from hiro.contracts import Event

    session.emit(Event(seq=session.trace.next_seq(), kind="UNMODELED_CHANGE", origin="inferred",
                       args={"field": "produce.deck", "why": "跳过了培育剧情（支援事件可能给卡，不弹获得页）"}, basis="observe_only"))  # fmt: skip
    logger.info("跳过培育剧情：牌组标成待复核，下次选行动页读一次牌组")


def _clock(rt) -> float:
    import time

    return rt.clock() if callable(getattr(rt, "clock", None)) else time.monotonic()


def _commu_tap_ok(rt) -> bool:
    """剧情 SKIP 能不能点：上次点 SKIP 不到 COMMU_RETAP_S 秒不点（剧情正在收尾，下一帧多半已是选行动页）；
    新截一帧底栏不在了也不点。"""
    from hiro.vision.quick import commu_bar

    last = getattr(rt, "_commu_tap_at", None)
    if last is not None and _clock(rt) - last < COMMU_RETAP_S:
        logger.info("快速判页 commu：%.1f 秒前刚点过 SKIP，不再点（等剧情收尾）", _clock(rt) - last)
        return False
    if getattr(rt, "screen", None) is None:
        return True  # 没有截图通道（离线替身）：只按间隔
    try:
        fresh = rt.screen.shot().img
    except Exception:  # noqa: BLE001  截不到图：不点，下一拍再看
        return False
    if not commu_bar(fresh):
        logger.info("快速判页 commu：点之前重截，剧情底栏已经不在，不点 SKIP")
        return False
    return True


def _quick_tap(rt, q: dict, now: float) -> bool:
    """快速判页带 tap 的页直接点；不带 tap 的页同一种等太久点兜底位置。返回是否点了。"""
    from hiro.vision.quick import SAFE_BLANK_TAP

    if q.get("tap") is not None:
        rt._quick_wait = None
        skip = q["page"] == "commu" and not (q.get("fields") or {}).get(
            "kind"
        )  # 剧情底栏 SKIP（事件台词框 event_dialogue 点的是台词框，不管）
        if skip and not _commu_tap_ok(rt):
            return False
        rt.device.tap(*q["tap"])
        if skip:
            rt._commu_tap_at = _clock(rt)
        if q["page"] == "commu":
            _flag_deck_after_commu(rt)
        logger.info(
            "快速判页 %s %s：点 %s",
            q["page"],
            (q.get("fields") or {}).get("text") or (q.get("fields") or {}).get("name") or "",
            q["tap"],
        )
        return True
    kind = (q.get("fields") or {}).get("kind")
    key = (q["page"], kind)
    wait = getattr(rt, "_quick_wait", None)
    if wait is None or wait[0] != key:
        rt._quick_wait = (key, now)
        return False
    if now - wait[1] < QUICK_WAIT_TAP_BY_KIND.get(kind, QUICK_WAIT_TAP_S):
        return False
    pos = SAFE_BLANK_TAP if QUICK_WAIT_TAP_POS.get(kind) == "safe" else (12, 12)
    logger.info("快速判页 %s 已等 %.0f 秒，点 %s", key, now - wait[1], pos)
    rt._quick_wait = (key, now)
    rt.device.tap(*pos)
    return True


def fit_dmm_window(device, session) -> str | None:
    """DMM 版开局检查：游戏客户区按显示器算的 9:16 目标（hiro.devices.win_window，用户 11-14）不对就调；调不了返回原因（停机）。
    实际客户区尺寸记进 trace 备注。HIRO_DMM_FIT=0 不查（非 DMM 设备也不查）。"""
    import os

    if os.environ.get("HIRO_DEVICE") != "dmm" or os.environ.get("HIRO_DMM_FIT") == "0":
        return None
    from hiro.devices import win_window

    try:
        before, after = win_window.ensure()
    except Exception as e:  # noqa: BLE001  找不到窗口 / 显示器放不下 540x960 / 不是 Windows
        return f"DMM 窗口尺寸检查没过：{e}"
    device.screenshot()  # 改完重截一张，LogicalScreen 记下新的原生尺寸
    native = getattr(getattr(device, "screen", None), "native", None)
    note = (
        f"DMM 游戏客户区 {before[0]}x{before[1]}"
        + (f" → {after[0]}x{after[1]}" if after != before else "")
        + f"（截图原生 {native}）"
    )
    logger.info("%s", note)
    session.note(note)
    if native is not None and tuple(native) != tuple(after):
        return f"DMM 窗口调成 {after[0]}x{after[1]}，截图原生尺寸却是 {native}：停下报告"
    return None


def build(args, session, device):
    """HifCore + Runtime（调用方负责 device.connect / close 和 active.use）。"""
    from hiro.produce.handlers import make_handlers
    from hiro.produce.hif import HifCore
    from hiro.runtime.loop import Runtime
    from hiro.vision.classify import classify

    for k, v in {"session": session, "use_llm": not args.no_llm, "llm_spec": args.llm, "main_attr": args.main_attr,
                 "exams_done": args.exams_done}.items():  # fmt: skip
        setattr(HifCore, k, v)
    from hiro.loop.landscape import LandscapeGuard

    core = HifCore(scenario=args.mode, idol=args.idol)
    # 横屏 LIVE 黑屏切全屏只在 Mac PlayCover（MaaTools）上做；其他设备只用 in_live 让看门狗别在 LIVE 里停（同旧主循环）
    guard = LandscapeGuard(active=getattr(device, "name", "") == "maatools")
    return core, Runtime(raw_device=device, classify=classify, handlers=make_handlers(core), advance=advance, session=session,
                         landscape=guard, idle_stop_s=IDLE_STOP_S)  # fmt: skip


def recover_date_change(rt, session) -> None:
    """游戏日切换（「日付変更」框，点任何要联网的按钮都可能弹）：タイトルへ → TAP TO START → 登录奖励 → 首页
    （hiro.loop.date_change.recover_to_home，首页按自有判页认）。回不到首页就停机。"""
    from hiro.loop.date_change import recover_to_home
    from hiro.produce.ops import ocr_boxes
    from hiro.vision.classify import classify

    session.recover("date_change", "游戏日切换「日付変更」：回标题再进首页")
    ok = recover_to_home(
        lambda: rt.screen.shot().img, rt.device.tap, ocr_boxes, lambda img: classify(img).name == "home"
    )
    if not ok:
        raise ResumeBlocked("日切换后没回到首页，停机")


def run(args, session) -> int:
    """跑一段：new = 开局后打到结束；resume = 续跑到结束。返回退出码（0 正常，3 停机交给人）。
    日切换在开局或打的过程中都可能出现：回首页后，开局没开成就重新开局，已经在培育里就续跑；一段最多 MAX_DATE_CHANGES 次。
    （开局已经点了开始、正在开场剧情时日切换：重新开局会被「本机有进行中的培育」拦住停机，交给人，不盲目破弃。）"""
    from hiro import kaa_env
    from hiro.devices import active, open_device
    from hiro.loop.date_change import DateChanged
    from hiro.runtime import finish, opening

    kaa_env.apply_formation_plan(args.idol)
    if args.command == "new" and (kaa_env.SUPPORT_SET is None or kaa_env.MEMORY_SET is None):
        logger.error("new 开局要编成号：设 HIRO_SUPPORT_SET / HIRO_MEMORY_SET，或先生成开局编成方案")
        return 2
    device = open_device()
    device.connect()
    if (err := fit_dmm_window(device, session)) is not None:
        logger.error("%s", err)
        print(err)
        device.close()
        return 3
    _core, rt = build(args, session, device)
    active.use(rt)
    started = args.command != "new"  # resume：本来就在培育里
    try:
        for attempt in range(MAX_DATE_CHANGES + 1):
            try:
                if not started:
                    if not (args.mode == "main" and opening.on_start_confirm(rt.screen.shot())):
                        # 上一局停在结算页时先回首页；本战开局时已经在本战开始确认页就留着（new_produce 从这里接着核对，
                        # 原来点回首页，再从首页进本战走完整编成，Mac p15-main 11-17）
                        finish.finish_produce_end(rt, go_home=True)
                    opening.new_produce(rt, args.mode, idol=args.idol, support_set=kaa_env.SUPPORT_SET, memory_set=kaa_env.MEMORY_SET,
                                        plan=kaa_env.ACTIVE_PLAN, rental_attr=args.main_attr, main_attr=args.main_attr,
                                        expect_memory=tuple(v.strip() for v in args.expect_memory.split(",") if v.strip()))  # fmt: skip
                    started = True
                elif attempt or args.command == "resume":
                    finish.resume_produce(rt)
                rt.run(until=lambda: getattr(rt, "finished", False))
                return 0
            except DateChanged:
                if attempt >= MAX_DATE_CHANGES:
                    raise ResumeBlocked(f"一段里日切换超过 {MAX_DATE_CHANGES} 次，停机") from None
                logger.warning("日切换（第 %d 次，%s）：回首页后%s", attempt + 1, "已开局" if started else "还没开局",
                               "续跑" if started else "重新开局")  # fmt: skip
                recover_date_change(rt, session)
    except ResumeBlocked as e:
        logger.error("%s", e)
        print(str(e))
        return 3
    except DeviceError as e:  # 设备没了 / 连续空帧（hiro.devices）：停机交给人，不带着 traceback 整个崩掉
        logger.error("设备出错，停机：%s: %s", type(e).__name__, e)
        print(f"设备出错，停机：{e}")
        return 3
    finally:
        active.use(None)
        device.close()
    return 0
