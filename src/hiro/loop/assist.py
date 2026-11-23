"""收尾 / 回首页 / 日切换恢复阶段的「认不出的画面」处理（协调 2026-11-04，第二十六局）：和主循环同一套——
快速判页（认得就按它给的位置点）→ 卡住 ≥ rescue.RESCUE_AFTER 秒交 LLM 看图（hiro.loop.rescue，同样的危险词 / 选择页 / 考试拦截）。

第二十六局：4 点日切换后首页弹「学マ水曜日」登录奖励，hiro.loop.resume.finish_produce_end 只会点中央（正中央是奖励道具框，
点了不翻页），180s 后看门狗停机；这些阶段以前既没有快速判页，也没有 LLM 兜底（只挂在主循环上）。

用法：循环里已知按钮都没匹配上时调 step(img, boxes)，返回 True 表示这一轮已经处理（点了 / 交给 LLM 在后台看），
调用方本轮不再点自己的兜底位置；返回 False 照旧。LLM 说停 / 被拦截：报警并放手（同主循环），仍由调用方的超时兜底。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from hiro.loop import rescue

logger = logging.getLogger(__name__)


class Assist:
    def __init__(self, phase: str, *, click: Callable[[int, int], None], quick: Callable | None = None, rescuer=None,
                 clock: Callable[[], float] = time.monotonic):  # fmt: skip
        self.phase, self.click, self.clock = phase, click, clock
        self.quick = quick or _default_quick
        self.rescuer = rescuer
        self._texts: frozenset | None = None
        self._since = clock()

    def _stuck(self, boxes) -> float:
        """画面文字（OCR 框文字集合）不变的秒数。"""
        texts = frozenset(t.strip() for t, _, _ in boxes if len(t.strip()) >= 2)
        if texts != self._texts:
            self._texts, self._since = texts, self.clock()
            if self.rescuer is not None:
                self.rescuer.progressed()
        return self.clock() - self._since

    def step(self, img, boxes) -> bool:
        """boxes：[(text, conf, (x1, y1, x2, y2))]（hiro.produce.ops.ocr_boxes 格式）。"""
        stuck = self._stuck(boxes)
        try:
            q = self.quick(img)
        except Exception:  # noqa: BLE001
            q = None
        if q is not None and q.get("tap") is not None:
            logger.info("%s：快速判页 %s，点 %s", self.phase, q.get("page"), q["tap"])
            self.click(*q["tap"])
            return True
        if not rescue.LLM_UNKNOWN or img is None:
            return False
        if self.rescuer is None:
            self.rescuer = rescue.Rescuer()
        r = self.rescuer
        try:
            if r.pending:
                rec = r.poll()
                if rec is None:
                    return False
            else:
                if not r.due(stuck, img=img):
                    return False
                logger.info("%s：画面 %.0fs 没变、认不出，截图交给 LLM 看（后台）", self.phase, stuck)
                rec = r.begin(img, {"phase": self.phase, "stuck_s": round(stuck)}, boxes)
                if rec is None:
                    return False
        except Exception as e:  # noqa: BLE001  兜底出错：退回调用方原来的处理
            logger.warning("%s：LLM 兜底出错：%r", self.phase, e)
            return False
        a = rec["action"]
        if a["action"] == "stop":
            logger.warning(
                "%s：LLM 兜底放弃（%s）：%s",
                self.phase,
                rec.get("page") or "?",
                rec.get("blocked") or rec.get("reason"),
            )
            return False
        if rec.get("changed"):
            self._texts, self._since = None, self.clock()
        return a["action"] != "none"


def _default_quick(img):
    from hiro.vision.quick import quick_page

    return quick_page(img)
