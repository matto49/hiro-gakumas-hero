"""续跑进培育、结算收尾回首页、活動支給（去 KAA 设计稿 §3.5；替代 hiro.loop.resume.enter_produce / finish_produce_end 和 KAA 的
AllowanceContext.claim）。跑在 hiro.runtime.Runtime 上，判据和旧版同一份（hiro.loop.resume_rules），不 import KAA / kotonebot。

规矩（同旧版）：破弃框只点キャンセル并停（中断层 red_line + opening.enter_produce）；结算后到了培育 / 本战开始确认页就到此为止、
绝不点开始（是否开下一局由上层决定）；有购买 / ジュエル字样的领奖页不点；关注制作人等花费类按钮不点。
"""

from __future__ import annotations

import logging

from hiro.loop import resume_rules as rr
from hiro.runtime import nav, photo_pick
from hiro.runtime.loop import DONE, Outcome
from hiro.vision.ui_templates import box

logger = logging.getLogger(__name__)

RESUME_TIMEOUT = 40.0
UNKNOWN_HOME_S = 8.0  # 续跑时画面一直认不出这么久：按回首页的按钮点一下（主页通知框之类，中断层没接的）
FINISH_TIMEOUT = 180.0
BLANK_TAP = (12, 12)  # 没有按钮的过场：点左上角空白（同 KAA skip() / hiro.loop.advance.ADVANCE_TAP）
END = Outcome("end", "培育结束")  # 页面处理器返回它：这一局打完了，Runtime.run 正常结束


def _texts(f) -> list[str]:
    return [t.text.replace(" ", "") for t in f.texts]


def resume_produce(rt, timeout: float = RESUME_TIMEOUT) -> str:
    """进行中的培育续跑：首页 / 「プロデュース再開」框 → 点「再開する」→ 进培育。返回 resumed（点了再開）/ already（本来就在培育里）。
    活动登录奖励挡在前面时点底部空白翻掉。
    认不出的画面（unknown）不当成「已在培育内」（Mac 账号 2 11-17：主页连弹 5 个「親愛度STEP3解放」+「使用期限確認」，判成 unknown
    → 直接开主循环，182 秒停机）：先交中断层处理，一直认不出 UNKNOWN_HOME_S 秒就点回首页的按钮；认出培育页 / 剧情才算 already，
    到超时还认不出就停机（ResumeBlocked），不盲进主循环。"""
    from hiro.loop import comm_error
    from hiro.vision.classify import classify_scene
    from hiro.vision.quick import quick_page

    state = {"tapped_resume": False, "clicked_ongoing": False, "unknown_since": None}

    def step(f):
        if (btn := rr.resume_button(f.texts)) is not None or (btn := nav.find(f, "Produce.ButtonResume")) is not None:
            rt.device.tap(*btn.rect.center)
            state["tapped_resume"] = True
            return None
        home = nav.at_home(f)
        if state["tapped_resume"] and not home:
            return "resumed"
        if not home:
            texts = [t.text for t in f.texts]
            if any(h in t for t in texts for h in comm_error.TITLE_HINTS):  # noqa: SIM102
                # 通信エラー（E209 资源下载失败等）：只点リトライ。11-19 账号 2：续跑入口没认这个框，判 unknown 9 秒后把
                # 「X タイトルへ」上的 X 当成关闭图标点了，游戏回标题（违反「通信错误不点タイトルへ」）
                if comm_error.handle(f.boxes, rt.device.tap, sleep=rt.sleep) is not None:
                    state["unknown_since"] = None
                    return None
            q = quick_page(f.img)
            if (
                q
                and q.get("page") == "TRANSITION"
                and (q.get("fields") or {}).get("kind") == "login_bonus"
                and q.get("tap")
            ):
                rt.device.tap(*q["tap"])  # 活动登录奖励（培育外）：翻掉再说
                return None
            hit = classify_scene(f.img)
            if hit.area == "produce" or hit.name in ("commu", "LIVE"):
                # LIVE：培育里的横屏演出（初 True End 等，协调 11-17）。主循环有 LandscapeGuard 和「TAP TO START」，交给它
                logger.info("已在培育内（%s），直接开始主循环", hit.name)
                return "already"
            alt = getattr(rt, "classify", None)
            if callable(alt) and ((h2 := alt(f.img)).name.startswith("NIA_") or h2.name == "HIF_EXAM_BRIEFING"):
                # Runtime 自己的判页认出了 N.I.A 特有页（营業场所 / 话题 / 试镜选档 / 加分，hiro.runtime.nia_sample 的判页包装）：
                # 也是在培育内（协调 11-17：在营業事件页 resume，40 秒「续跑进培育」没完成）。
                # 初的考前说明页（審査基準 / 合格条件 / タップして次へ）同样靠这层 OCR 兜底才认得出（协调 11-20：停在这一页 resume 40 秒超时）
                logger.info("已在培育内（%s），直接开始主循环", h2.name)
                return "already"
            now = rt.clock()
            if state["unknown_since"] is None:
                state["unknown_since"] = now
                logger.info("续跑：画面认不出（%s），不当成培育内，等中断层 / 回首页", hit.name)
            elif now - state["unknown_since"] >= UNKNOWN_HOME_S:
                if any(comm_error.TO_TITLE_BUTTON in t for t in texts):
                    # 画面上有「タイトルへ」：关闭图标可能就是这个按钮上的 X，不点，等它变
                    logger.warning("续跑：画面认不出，但有「タイトルへ」，不点关闭按钮")
                    return None
                for name in nav.HOME_BUTTONS:
                    if (m := nav.find(f, name)) is not None:
                        logger.info("续跑：画面认不出 %.0fs，点 %s 回首页", now - state["unknown_since"], name)
                        nav.tap_match(rt, m)
                        state["unknown_since"] = now
                        break
            return None
        state["unknown_since"] = None
        if home and not state["clicked_ongoing"]:
            rt.device.tap(*box("Produce.BoxProduceOngoing").center)
            state["clicked_ongoing"] = True
        return None

    return rt.drive(step, timeout, "续跑进培育")


def finish_produce_end(rt, *, go_home: bool = False, timeout: float = FINISH_TIMEOUT) -> bool:
    """培育结束后的结算画面（プロデュース完了、获得道具、升级、活动积分、回忆照片…）点回首页。返回 True：回到首页，或停在了
    开始确认页（go_home=False 时到此为止，是否开下一局由上层决定）。超时抛 ResumeBlocked。"""
    t = {"home_icon": (85, 1185)}

    def first(f, words, *, exact=True, min_y=0):
        for b in f.texts:
            s = b.text.replace(" ", "").strip()
            if (s in words if exact else any(w in s for w in words)) and b.rect.center.y > min_y:
                return b
        return None

    def step(f):
        if nav.at_home(f):
            logger.info("结算画面已点完，回到首页")
            return True
        texts = _texts(f)
        if go_home and rr.is_home_move_confirm(texts):
            ok = next((b for b in f.texts if "OK" in b.text.upper() and b.rect.center.y > 1000), None)
            rt.device.tap(
                *(ok.rect.center if ok is not None else (500, 1160))
            )  # ホーム移動確認：OK 只是回首页，回忆保留
            return None
        if any("開始確認" in s or "本戦へ" in s for s in texts):
            if not go_home:
                logger.info("结算后到了培育开始确认页，停止点击（是否开局由上层决定）")
                return True
            if (m := nav.find(f, "Common.ButtonHome")) is not None:
                nav.tap_match(rt, m)
            else:
                rt.device.tap(*t["home_icon"])  # 左下角首页图标；绝不点「本戦へ」
            return None
        if photo_pick.should_wait(texts):
            photo_pick.wait(
                texts=lambda: _texts(rt.screen.shot())
            )  # HIRO_PHOTO_PICK=stop：停下等人自己选照片（用户 11-21）
            return None
        if any("メモリーにするフォト" in s for s in texts):
            nxt = first(f, ("次へ",))
            rt.device.tap(*(nxt.rect.center if nxt is not None else (360, 1157)))  # 选回忆照片：默认第一张
            return None
        if any("メモリーとして保存しますか" in s for s in texts):
            ok = first(f, ("決定",))
            rt.device.tap(*(ok.rect.center if ok is not None else (500, 1157)))
            return None
        if go_home:
            # 编成页 / 偶像一览 / 设置这类菜单页：左下工具栏的 ⌂ 是 Common.ButtonToolbarHome（Mac 账号 2 的メモリー編成页 0.87，
            # Common.ButtonHome 只有 0.36）。原来只认 ButtonHome，认不到就一直点 (12,12)，180 秒超时停机（协调 11-14）
            for name in nav.HOME_BUTTONS:
                if (m := nav.find(f, name)) is not None:
                    nav.tap_match(rt, m)
                    return None
            if (b := nav.tower_back(f)) is not None:
                rt.device.tap(*b.rect.center)  # 偶像之路结果页 / 不合格名次页：回层选择页，下一拍点那里的首页
                return None
        for name in ("InProduce.End.ButtonGenerate", "Produce.SaveMemoPic.ButtonConfirm", "Common.ButtonIconClose",
                     "InProduce.ButtonNextNoIcon", "Common.ButtonNext"):  # fmt: skip
            if (m := nav.find(f, name)) is not None:
                nav.tap_match(rt, m)
                return None
        if (hit := first(f, rr.END_BUTTON_TEXTS, min_y=700)) is not None:
            rt.device.tap(*hit.rect.center)
        elif (tap := rr.reward_tap(f.texts)) is not None:
            rt.device.tap(*tap)
        else:
            rt.device.tap(*BLANK_TAP)
        return None

    return rt.drive(step, timeout, "结算收尾回首页")


# ---------------------------------------------------------------- 页面处理器（平台模块挂进 handlers 注册表）
def on_produce_end(rt, f, hit) -> Outcome:
    """PRODUCE_END：点完结算回首页，这一局结束。"""
    finish_produce_end(rt)
    return END


def on_allowance(rt, f, hit) -> Outcome:
    """ALLOWANCE（活動支給）：有宝箱锁先点开，再点空白推进（同 KAA AllowanceContext.claim）；之后的选饮料 / 选卡由各自的处理器决策。"""
    if (m := nav.find(f, "InProduce.LootboxSliverLock")) is not None:
        nav.tap_match(rt, m)
        rt.sleep(1.0)
    rt.device.tap(*BLANK_TAP)
    return DONE
