"""字段真值：展开成路径、识别方法表、高代价字段、逐字段比对（规格 docs/ryugi/specs/2026-11-23-recognition-dataset-fields-design.md §2–§3）。

字段路径形如 `params.vo`、`hand[2].card_id`、`drinks[0]`；查方法表时把下标换成 `[]`。
方法表是按识别代码（hiro.vision 各页 extract）整理的初版，规格 §10.1 交用户核对。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 第一条匹配的为准。icon：卡面插图 / 图标模板匹配；pixel：颜色 / 像素规则（按钮种类、可点、选中、角标）；ocr：文字识别。
METHOD_RULES: list[tuple[str, re.Pattern]] = [
    (
        "icon",
        re.compile(
            r"(card_id|drink_id|pitem_id|asset|asset_id|(^|\.)idol)$|^(drinks|bag|quick_bag)\[\]$|^status\.|^statuses\[\]\.value$"
        ),
    ),
    (
        "pixel",
        re.compile(
            r"(^|\.)(kind|available|selected|purchased|sale|dimmed|customized|recommended|upgrade|plus|state|complete|full|"
            r"confirm_enabled|danger|trouble|partial|cancelable|at_top|scrollable|view|color|hand_layout|art_consensus|maxed|"
            r"passed|final|outcome|star_ok|star_shown|quick_star_shown|finals|slot|index|row|col|rows_visible|agree|free)$"
        ),
    ),
    (
        "ocr",
        re.compile(
            r"(stamina|p_points|produce_point|params|param_caps|growth_pct|(^|\.)star$|days_left|price|_left$|(^|\.)left$|"
            r"multiplier_pct|score|gain|rank|participants|rank_line|(^|\.)hp$|(^|\.)genki$|_cost$|cost_p|cost_hp|remaining_turns|"
            r"pitem_charges|finals_round|exam_no|(^|\.)exam$|deck_size|drink_capacity|(^|\.)value$|turn)"
        ),
    ),
]

# 读错代价最大的字段（用户 11-23 定：考试手牌和分数、商店价格）
CRITICAL: dict[str, list[re.Pattern]] = {
    "HIF_EXAM": [re.compile(r"^hand\[\]\.(card_id|upgrade)$"), re.compile(r"(^|\.)my_score$")],
    "CONSULT": [re.compile(r"\.price$|^price$")],
    "HIF_ROUND_INTERVAL": [re.compile(r"\.price$")],
}


def norm_path(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


def method_of(page: str | None, path: str) -> str:
    p = norm_path(path)
    for method, rx in METHOD_RULES:
        if rx.search(p):
            return method
    return "other"


def is_critical(page: str | None, path: str) -> bool:
    p = norm_path(path)
    return any(rx.search(p) for rx in CRITICAL.get(page or "", []))


def flatten(value, prefix: str = "") -> dict:
    """嵌套的 dict / list → {路径: 叶子值}；空列表 / 空字典本身算一个叶子。"""
    out: dict = {}
    if isinstance(value, dict) and value:
        for k, v in value.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list) and value:
        for i, v in enumerate(value):
            out.update(flatten(v, f"{prefix}[{i}]"))
    elif prefix:
        out[prefix] = value
    return out


@dataclass
class Row:
    path: str
    want: object
    got: object
    ok: bool


def _same(want, got) -> bool:
    """严格相等：布尔和数字是两种东西（true != 1、False != 0），Python 的 == 会把它们算相等。"""
    if isinstance(want, bool) != isinstance(got, bool):
        return False
    return want == got


def compare(want, got) -> list[Row]:
    """真值里的每个字段和读到的比（严格相等）；读到的多出来的字段不管。"""
    flat_got = flatten(got) if got is not None else {}
    # 读数取稳定项时会丢掉值为 None 的键，所以真值是 None 时「没有这个键」也算对
    return [
        Row(p, w, flat_got.get(p), (p in flat_got and _same(w, flat_got[p])) or (w is None and flat_got.get(p) is None))
        for p, w in flatten(want).items()
    ]
