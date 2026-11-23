"""ExamStateSource 默认实现：画面识别 + 状态机记账（confidence = "screen"，牌库顺序未知）。

流程：识别层读整帧（hiro.vision.hif.read_hif_exam）→ Observation → reduce → GameState.exam（契约字段）→ BattleState。
- 读不到的字段保持 None，BattleState / 提示词标「未识别」，不猜。
- 识别给的手牌与执行器定位的手牌（frame.cards，点击用）张数相同时用识别的（带强化次数）；张数对不上时退回执行器的读数。
- 过场白光帧（识别 hand_layout="overlay"，压暗的牌被抬亮、可打性不可信）：返回 (None, "hand_overlay")，执行器等稳重截。

frame 是执行器的帧对象（鸭子类型）：.img、.cards（每个有 .card / .available）、.drinks（[(饮料, 位置)]）、
.remaining / .hp / .genki，可选 .hand（识别层单独读出的手牌）和 .hand_views()。
"""

from __future__ import annotations

import time
from typing import Any

from hiro.brain.cards import card_view, drink_view
from hiro.brain.state import BattleState, ExamMemory, exam_kwargs

OVERLAY_LAYOUTS = ("overlay",)


def read_screen(img) -> dict:
    """识别层的考试读数；识别异常不能打断考试，返回 {error}，observation 会把它记进 field_meta。"""
    try:
        from hiro.vision.hif import read_hif_exam
    except ImportError as e:  # 识别层没装
        return {"error": f"识别层不可用：{e!r}"}
    try:
        return read_hif_exam(img)
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)}


def screen_fields(raw: dict, session=None, img=None) -> dict:
    """识别读数 → GameState.exam（契约字段）。有 session 时写进主循环状态，只取这一帧写进去的字段（旧值不冒充本帧读数）。"""
    if session is not None:
        obs = session.observe(scene="HIF_EXAM", area="produce", img=img, fields=raw)
        st = session.state
        return {k: v for k, v in (st.exam or {}).items()
                if (src := st.sources.get(f"exam.{k}")) is not None and src.seq == obs.seq and src.origin == "screen"}  # fmt: skip
    from hiro.contracts import GameState
    from hiro.loop.reduce import reduce
    from hiro.vision.observe import observation

    st = reduce(GameState(), observation(0, time.time(), "HIF_EXAM", raw, area="produce"))
    return dict(st.exam or {})


def hand_mismatch(a: list, b: list) -> list[str]:
    """张数相同时，两边卡 ID 不同的槽位（「识别名≠执行器名」）。"""
    return [
        f"{i}:{x.name}≠{y.name}"
        for i, (x, y) in enumerate(zip(a, b))
        if x.card_id and y.card_id and x.card_id != y.card_id
    ]


def screen_state(memory: ExamMemory, profile, exam_fields: dict, frame=None, *, target_score: int | None = None,
                 conflicts: list[str] | None = None) -> BattleState:  # fmt: skip
    """GameState.exam（+ 执行器的帧）→ BattleState（截图来源）。"""
    kw = exam_kwargs(exam_fields)
    if frame is not None:
        loc_hand = [
            card_view(i, c.card if c else None, available=bool(c and c.available)) for i, c in enumerate(frame.cards)
        ]
        recognized = exam_fields.get("hand") is not None and len(kw["hand"]) == len(loc_hand)
        if not recognized and getattr(frame, "hand", None) is not None:
            kw["hand"], recognized = frame.hand_views(), True
        if not recognized:
            if conflicts is not None:
                conflicts.append(
                    f"考试手牌：识别 {[c.name for c in kw['hand']]} ≠ 执行器 {[c.name for c in loc_hand]}，采用执行器（点击按它的槽位）"
                )
            kw["hand"] = loc_hand
            kw["drinks"] = [drink_view(i, dr) for i, (dr, _) in enumerate(frame.drinks)]
        else:
            if conflicts is not None and (diff := hand_mismatch(kw["hand"], loc_hand)):
                conflicts.append(f"考试手牌：识别与执行器不一致 {diff}，采用识别（执行器只用于点击位置）")
            if len(kw["drinks"]) != len(frame.drinks):
                kw["drinks"] = [drink_view(i, dr) for i, (dr, _) in enumerate(frame.drinks)]
        for k, v in (("remaining_turns", frame.remaining), ("hp", frame.hp), ("genki", frame.genki)):
            if kw[k] is None:
                kw[k] = v
    memory.observe_turn(kw["remaining_turns"])
    return BattleState.build(memory, profile, target_score=target_score, source="screen", **kw)


class ScreenExamStateSource:
    """考试局面只从画面来；不提供预演。"""

    def __init__(self):
        self.profile = None
        self.target_score: int | None = None
        self.session = None
        self.last_raw: dict | None = None

    def begin(self, context: dict[str, Any]) -> None:
        """context：profile（IdolProfile）/ target_score / session（hiro.loop.session.Session，可选）。"""
        ctx = context or {}
        self.profile = ctx.get("profile")
        self.target_score = ctx.get("target_score")
        self.session = ctx.get("session")

    def read(self, frame: Any, memory: Any) -> tuple[Any | None, str]:
        raw = getattr(frame, "raw", None) or read_screen(frame.img)
        self.last_raw = raw
        if isinstance(raw, dict) and raw.get("hand_layout") in OVERLAY_LAYOUTS:
            return None, "hand_overlay"
        fields = screen_fields(raw, self.session, img=getattr(frame, "img", None))
        conflicts = self.session.state.conflicts if self.session is not None else None
        state = screen_state(memory, self.profile, fields, frame, target_score=self.target_score, conflicts=conflicts)
        return state, "screen"

    def confidence(self) -> str:
        return "screen"

    def executed(self, action, result) -> None:
        """画面来源不续推：下一帧重新读。"""

    def previews(self, state: Any) -> list | None:
        return None

    def end(self, result: dict[str, Any] | None) -> None:
        self.last_raw = None
