"""本战 R1 记下的信息（牌组、R1 结束剩下的饮料、三属性倍率），轮间 / R2 决策用；落盘到 trace 目录，续跑时读回。"""

from __future__ import annotations

import json
from pathlib import Path

CONTEXT: dict = {}  # {"deck": [(card_id, upgrade)], "drinks": [drink_id], "permil": {Vo, Da, Vi}, "max_hp": 画面读到的最大体力}
CONTEXT_FILE = "finals_context.json"


def note_exam(label: str, *, deck=None, drinks=None, permil=None, max_hp=None, trace_dir=None) -> None:
    """本战 R1 的考试循环调用：读到牌组时记牌组，考试结束时记剩下的饮料、三属性倍率和最大体力（开考时的画面读数）。"""
    if label != "r1":
        return
    if deck is not None:
        CONTEXT["deck"] = [list(x) for x in deck]
    if drinks is not None:
        CONTEXT["drinks"] = list(drinks)
    if permil:
        CONTEXT["permil"] = {k: int(v) for k, v in permil.items() if v}
    if max_hp:
        CONTEXT["max_hp"] = int(max_hp)
    if trace_dir is not None:
        try:  # noqa: SIM105
            (Path(trace_dir) / CONTEXT_FILE).write_text(json.dumps(CONTEXT, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    _notify_sim(label, permil, trace_dir)


def _notify_sim(label: str, permil, trace_dir) -> None:
    """模拟器在时（hiro.sim）：轮间缓存目录 + R2 预热。没装引擎 / 主数据时什么都不做。"""
    try:
        from hiro.brain.deciders import _sim_ready

        if not _sim_ready():
            return
        from hiro.sim import interval

        interval.on_context(label, permil=permil, trace_dir=trace_dir)
    except Exception:  # noqa: BLE001  预热失败不影响考试
        pass


def load_context(trace_dir) -> dict:
    try:  # noqa: SIM105
        CONTEXT.update(json.loads((Path(trace_dir) / CONTEXT_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return CONTEXT
