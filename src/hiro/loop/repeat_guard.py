# 来源：digital-hiro hiro/loop/repeat_guard.py（c3fc27f）
"""通用防空转（协调 2026-11-03，不再逐个入口修）。

两类空转：
- 第二十三局 R2：饮料栏已空，存档 / 记忆还当有 ブーストエキス，决策反复选 drink:0，每次「执行后画面变了」（点饮料闪一下），
  局面（分数 / 体力 / 手牌 / 饮料 / 剩余回合）一直不变，175 步、40 分钟；
- 第二十一局选拔①：同一张卡停在选中态（使用条件不满足），考试循环弹窗阶段再点 404 次、17.8 分钟，期间一个决策步都没有。

对应两层保护（都在 Session 上，考试内外共用）：
1. 重复无效动作（attempt）：同一页面、同一局面签名下同一个动作连续 REPEAT_MAX 次 → 调用方禁用它（考试：本回合 memory.block；
   考试外：停机报警），记 recover:tap_no_effect「重复无效动作」+ note「blunder:repeat_no_effect」。
   调用点：Session.record（每个有执行结果的决策步，签名 = 决策输入状态）、考试循环弹窗阶段（每轮点了东西又 continue，
   签名 = 本轮画面的手牌角标 / HUD）。
   考试外第 REPEAT_MAX 次本身执行成功（ExecutionResult.ok）时先不停，再来一次才停（前几次失败、这一次做成的情况不误报；
   DMM dmm1m 相談删 眠気）。
2. 没进展停机（stalled）：局面签名（observe / record）和画面（看门狗每 5 秒采样，稳定变化才算）都 STALL_S 秒没变 →
   看门狗（kotonebot Loop 回调）抛 Stalled，停机报警、写 trace。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable

from hiro.log import logging

from hiro.loop.errors import ResumeBlocked

logger = logging.getLogger(__name__)

REPEAT_MAX = 3
STALL_S = float(os.environ.get("HIRO_STALL_S") or 180.0)
SCREEN_EVERY = 5.0  # 看门狗采样间隔
SCREEN_DIFF = 8.0  # 90×160 灰度缩图平均差 ≥ 这个 = 画面变了（选中框、SELECT 字样这类小变化不算）
# 局面签名不看的字段：位置 / 置信度 / 计时 / 每帧都可能读得不一样的展示值
VOLATILE = frozenset({"center", "centers", "box", "rect", "conf", "confidence", "seconds", "ms", "t", "timings_ms", "raw", "img",
                      "badge_score", "screenshot", "stable_frames", "source"})  # fmt: skip


class RepeatedNoEffect(ResumeBlocked):
    """考试外同一局面同一动作连续 REPEAT_MAX 次没效果：停机报警（不重试）。"""


class Stalled(ResumeBlocked):
    """画面和局面都 STALL_S 秒没进展：停机报警（不重试）。"""


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in sorted(v.items()) if k not in VOLATILE}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, float):
        return round(v, 3)
    return v


def state_sig(page: str, *parts) -> str:
    """页面 + 状态片段（page_fields / exam 观测等）→ 短哈希；去掉 VOLATILE 字段。"""
    blob = json.dumps([page, *[_clean(p) for p in parts]], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


def action_key(action) -> str:
    if action is None:
        return "-"
    if hasattr(action, "to_dict"):
        action = action.to_dict()
    if isinstance(action, dict):
        action = {k: v for k, v in action.items() if k != "version"}
    return json.dumps(action, ensure_ascii=False, sort_keys=True, default=str)


def _thumb(img):
    import cv2

    return cv2.cvtColor(cv2.resize(img, (90, 160), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype("float32")


class Guard:
    def __init__(self, clock: Callable[[], float] | None = None):
        if clock is None:
            from hiro.loop.pause_flag import active_clock

            clock = active_clock  # 录屏暂停期间不计时
        self.clock = clock
        self.last: tuple | None = None  # (scope, sig, action)
        self.streak = 0
        self.progress_t = clock()
        self._seen: tuple | None = None
        self._base = self._pending = None
        self._shot_t = -1e9
        self.events: list[dict] = []

    # ---- 重复无效动作
    def attempt(self, scope: str, sig, action: str) -> int:
        """记一次「在 scope / sig 下执行了 action」，返回同一 (scope, sig, action) 连续第几次。"""
        key = (scope, sig, action)
        self.streak = self.streak + 1 if key == self.last else 1
        self.last = key
        if self.streak == REPEAT_MAX:
            self.events.append({"kind": "repeat_no_effect", "scope": scope, "action": action, "t": self.clock()})
        return self.streak

    def reset_streak(self) -> None:
        self.last, self.streak = None, 0

    # ---- 没进展
    def seen(self, page: str, sig: str) -> None:
        """局面签名变了 = 有进展。离开过上一次尝试所在的页面（中间出现别的页面）= 那个动作有效果，重复计数清零
        （第十六局选拔：连着拿到几瓶饮料，饮料满确认框连续出现、每次都处理成功，不能算重复无效）。"""
        if self.last is not None and page != self.last[0].split(":")[0]:
            self.reset_streak()
        if (page, sig) != self._seen:
            self._seen = (page, sig)
            self.progress_t = self.clock()

    def touch(self) -> None:
        """有进展（例如横屏 LIVE 演出中：画面一直在动但认不出，不算空转）。"""
        self.progress_t = self.clock()
        self._base = self._pending = None

    def screen(self, img) -> None:
        """看门狗采样画面：和基准帧差 ≥ SCREEN_DIFF、且连续两次采样彼此一致（稳定地变了，不是一闪而过的弹窗 / 动画）= 有进展。"""
        import numpy as np

        if img is None:
            return
        if img.shape[1] > img.shape[0]:  # 横屏 LIVE 演出（KAA 自己的循环里也覆盖到）：算进展
            self.touch()
            return
        th = _thumb(img)
        if self._base is None:
            self._base = th
            return
        if float(np.abs(th - self._base).mean()) < SCREEN_DIFF:
            self._pending = None
            return
        if self._pending is not None and float(np.abs(th - self._pending).mean()) < SCREEN_DIFF:
            self._base, self._pending = th, None
            self.progress_t = self.clock()
        else:
            self._pending = th

    def stalled(self) -> float:
        return self.clock() - self.progress_t


ACTIVE: dict = {"session": None}  # watchdog() 登记的会话：主循环之外的流程（KAA 培育准备）用 progress() 报进展


def progress(why: str = "") -> None:
    """主循环之外的流程每做完一个动作报一次进展（D38 第三十三局：KAA 培育准备——选偶像、切编成、租借、选回忆——
    整段 186s 页面一直 unknown、局面没变，看门狗误停）。没有登记的会话时什么都不做。"""
    g = getattr(ACTIVE["session"], "guard", None)
    if g is None:
        return
    g.touch()
    if why:
        logger.debug("进展：%s", why)


def watchdog(session, *, every: float = SCREEN_EVERY, clock: Callable[[], float] = time.monotonic):
    """kotonebot Loop 全局回调：每 every 秒用这一轮的截图采样画面；session 的局面和画面都 STALL_S 秒没进展就抛 Stalled。
    同时把 session 登记给 progress()。"""
    ACTIVE["session"] = session
    last = [-1e9]

    def cb(loop) -> bool:
        now = clock()
        if now - last[0] < every:
            return False
        last[0] = now
        g = getattr(session, "guard", None)
        if g is None:
            return False
        try:
            g.screen(getattr(loop, "screenshot", None))
        except Exception as e:  # noqa: BLE001  采样出错不能打断主流程
            logger.warning("空转看门狗采样出错：%r", e)
            return False
        stuck = g.stalled()
        if stuck >= STALL_S:
            msg = f"画面和局面都 {stuck:.0f}s 没进展（页面 {session.state.page.name}，最近动作 {g.last[2] if g.last else '-'}），停机报警"
            logger.error("空转看门狗：%s", msg)
            session.recover("unhandled", f"空转：{msg}")
            session.note("blunder:stalled " + msg)
            try:
                session.observe(scene=session.state.page.name, fields=False, save_frame=True)
                session.record()
            except Exception:  # noqa: BLE001
                logger.warning("空转看门狗写 trace 失败", exc_info=True)
            raise Stalled(msg)
        return False

    return cb
