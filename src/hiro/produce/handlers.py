"""新主循环（hiro.runtime）的页面处理器：页面名 → handler(rt, frame, hit) -> Outcome，调 HifCore 的页面方法。

    rt = Runtime(raw_device=dev, classify=hiro.vision.classify.classify, handlers=make_handlers(core), ...)

HifCore 的页面方法都已经不需要 KAA 上下文（ctx 传 None）。活動支給、培育结束的执行在 hiro.runtime.finish（这里只先记观测）。没登记的页面 Runtime 交给 advance。

考试入口：KAA 的 _dispatch 只在「上一页不是考试」时调 on_exam_entered；新主循环每拍都会调处理器，考试打完（run_exam 返回）后
残留的考试帧会让它再进一次（场次重复计数、重复记录）。所以考试打完以后关门，直到处理过一个非考试页才重新开门；
再挑戦（core.exam_restarted）不关门，直接重进。
"""

from __future__ import annotations

from collections.abc import Callable

from hiro.runtime.loop import DONE, STAY, Outcome

EXAM_PAGES = ("HIF_EXAM", "EXAM")


def make_handlers(core) -> dict[str, Callable]:
    gate = {"exam_done": False}  # 考试打完、还没见过别的已处理页面

    def page(fn) -> Callable:
        def handler(rt, frame, hit) -> Outcome:
            gate["exam_done"] = False
            fn(None)
            return DONE

        handler.__name__ = f"handle_{fn.__name__}"
        return handler

    def exam(rt, frame, hit) -> Outcome:
        if gate["exam_done"] and not getattr(core, "exam_restarted", False):
            return STAY  # 刚打完的考试还有残帧：不重进，等画面走到下一页
        core.exam_restarted = False
        core.on_exam_entered(None)
        gate["exam_done"] = not getattr(core, "exam_restarted", False)
        return DONE

    def produce_end(rt, frame, hit) -> Outcome:
        """培育结算：同 HifCore.on_produce_end 先存帧记一笔，点完回首页交给 hiro.runtime.finish（返回 END，这一局结束）。"""
        from hiro.runtime import finish

        gate["exam_done"] = False
        core.s.record(core.s.observe(scene="PRODUCE_END", fields=False, save_frame=True))
        return finish.on_produce_end(rt, frame, hit)

    def allowance(rt, frame, hit) -> Outcome:
        """活動支給：观测记一笔，领取交给 hiro.runtime.finish.on_allowance（之后的选饮料 / 选卡由各自的处理器决策）。"""
        from hiro.runtime import finish

        gate["exam_done"] = False
        obs = core.s.observe(scene="ALLOWANCE")
        out = finish.on_allowance(rt, frame, hit)
        core.s.record(obs)
        return out

    handlers: dict[str, Callable] = {
        "PRODUCE_END": produce_end,
        "ALLOWANCE": allowance,
        "CONSULT": page(core.on_consult),
        "SKILL_CARD_ENHANCE": page(core.on_skill_card_enhance),
        "SKILL_CARD_REMOVAL": page(core.on_skill_card_removal),
        "PDRINK_MAX": page(core.on_pdrink_max),
        "PDRINK_MAX_CONFIRM": page(core.on_pdrink_max_confirm),
        "HIF_ROUND_INTERVAL": page(core.on_hif_round_interval),
        "SELECT_PITEM": page(core.on_select_pitem),
        "SELECT_DRINK": page(core.on_select_drink),
        "SELECT_CARD": page(core.on_select_card),
        "ACTION_SELECT": page(core.on_action_select),
        "STUDY": page(core.on_study),
        "OUTING": page(core.on_outing),
        "SKILL_CARD_CHANGE_1": page(lambda ctx: core.on_skill_card_change(ctx, stage=1)),
        "SKILL_CARD_CHANGE_2": page(lambda ctx: core.on_skill_card_change(ctx, stage=2)),
        "PRACTICE": page(core.on_practice_entered),  # H.I.F 不该出现练习课：停机
        "HIF_FINALS_RANKING": page(
            core.on_finals_ranking
        ),  # 本战名次页（开考前 / R1 后 / R2 后）：记一笔、按规则再挑戦或点次へ
        "OUTING_STAMINA_MAX": page(core.on_outing_stamina_max),  # 体力已满确认：按已做的决策继续
        "INITIAL_CHOICE": page(core.on_event_choice),  # 事件选项页（随机事件 / 开局二选一）：EVENT_CHOICE 决策
        # 特別指導 定制选卡页：定制中途的 ACQUIRE 演出让定制循环先退出（交主循环点过），回到这一页再接着按决策定制 / 结束
        # （Mac h11 11-15：原来没有处理器，回到这页 180 秒停机，少定制 2 张）
        "SPECIAL_GUIDANCE": page(core.on_special_guidance),
    }
    for name in EXAM_PAGES:
        handlers[name] = exam
    return handlers
