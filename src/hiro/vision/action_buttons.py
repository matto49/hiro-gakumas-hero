"""H.I.F 选行动页上能点的行动按钮（去 KAA 第 5/6 步，替换 KAA ActionSelectContext.fetch_available_actions 的 H.I.F 分支）。

照 KAA 移植：休息 / 外出 / 相談 / 差し入れ（ギフト）/ 三种授業 各一个模板，找到就算可用；三种レッスン 先找 Vo（普通或 H.I.F 版），
找到再找 Da、Vi，SP 图标落在哪个按钮左边的区间就是哪个 SP。顺序同 KAA（休息、外出、相談、ギフト、授業 Vi / Vo / Da、レッスン Da / Vo / Vi）。
行动用决策契约的 (option, attr, sp) 表示，不再用 KAA 的 ProduceAction。
"""

from __future__ import annotations

from dataclasses import dataclass

from hiro.vision.ocr import Box
from hiro.vision.ui_templates import normalize, tpl

import numpy as np


@dataclass(frozen=True)
class ActionButton:
    option: str  # training / class / outing / gift / shop / rest（同 Action.produce_next 的 option）
    attr: str | None  # Vo / Da / Vi
    sp: bool | None  # レッスン 是不是 SP；其余 None
    rect: Box
    template: str  # 点了以后等它消失

    @property
    def label(self) -> str:
        return "/".join(str(v) for v in (self.option, self.attr, "SP" if self.sp else None) if v)


SINGLE = (
    ("InProduce.Rest", "rest", None),
    ("InProduce.ButtonIconOuting", "outing", None),
    ("InProduce.ButtonIconConsult", "shop", None),
    ("InProduce.ButtonIconGift", "gift", None),
    ("InProduce.ButtonIconStudyVisualHif", "class", "Vi"),
    ("InProduce.ButtonIconStudyVocalHif", "class", "Vo"),
    ("InProduce.ButtonIconStudyDanceHif", "class", "Da"),
)
LESSONS = {"Vo": ("InProduce.ButtonPracticeVocal", "InProduce.ButtonPracticeVocalHif"),
           "Da": ("InProduce.ButtonPracticeDance", "InProduce.ButtonPracticeDanceHif"),
           "Vi": ("InProduce.ButtonPracticeVisual", "InProduce.ButtonPracticeVisualHif")}  # fmt: skip


def _first(img, names) -> tuple[str, object] | None:
    for n in names:
        if (m := tpl(n).find(img)) is not None:
            return n, m
    return None


def available_actions(img: np.ndarray) -> list[ActionButton]:
    img = normalize(img)
    out = [
        ActionButton(option, attr, None, m.rect, name)
        for name, option, attr in SINGLE
        if (m := tpl(name).find(img)) is not None
    ]
    vo = _first(img, LESSONS["Vo"])
    if vo is None:
        return out
    da, vi = _first(img, LESSONS["Da"]), _first(img, LESSONS["Vi"])
    if da is None or vi is None:  # KAA 这里 require() 直接抛错；画面不全时不给レッスン
        return out
    cx = {k: hit[1].rect.center.x for k, hit in (("Vo", vo), ("Da", da), ("Vi", vi))}
    sp = {"Vo": False, "Da": False, "Vi": False}
    for m in tpl("InProduce.IconSp").find_all(img):
        x = m.rect.center.x
        if x < cx["Vo"]:
            sp["Vo"] = True
        if cx["Vo"] < x < cx["Da"]:
            sp["Da"] = True
        if cx["Da"] < x < cx["Vi"]:
            sp["Vi"] = True
    for attr, (name, m) in (("Da", da), ("Vo", vo), ("Vi", vi)):
        out.append(ActionButton("training", attr, sp[attr], m.rect, name))
    return out
