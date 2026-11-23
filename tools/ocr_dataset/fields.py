"""文字类型映射：页面 → [(字段名, 区域 (x1, y1, x2, y2), 类型)]（规格 docs/ryugi/specs/2026-11-23-ocr-dataset-design.md §6.3）。

区域是 720×1280 整帧坐标，取自识别代码里各页读的 ROI（hiro.vision.action / hif / shop / deck / customize / nia / pages 等的常量）；
框的中心落在哪个区域就标哪个字段，同一页里先写的优先（小区域写在前面）。类型写 None 的区域只标字段，类型按文字走兜底规则
（例如货架上既有卡名又有价格）。没覆盖到的页面所有框 field = null。页面名按 trace 里记的写，查表不分大小写。
"""

from __future__ import annotations

FIELDS: dict[str, list[tuple[str, tuple[int, int, int, int], str | None]]] = {
    "ACTION_SELECT": [
        ("action.days", (78, 82, 118, 132), "number"),
        ("action.stamina", (322, 50, 436, 94), "number"),
        ("action.points", (330, 94, 440, 136), "number"),
        ("action.advice", (0, 160, 720, 240), "body"),
        ("action.stats", (0, 680, 720, 724), "number"),
        ("action.buttons", (0, 840, 720, 1120), "button"),
    ],
    "HIF_EXAM": [
        ("exam.my_score", (222, 128, 262, 166), "number"),
        ("exam.pitem_charges", (683, 228, 715, 256), "number"),
        ("exam.attr", (105, 55, 230, 120), "number"),
        ("exam.top_strip", (0, 0, 720, 175), None),
    ],
    "HIF_FINALS_RANKING": [
        ("finals.badge", (220, 2, 480, 45), "label"),
        ("finals.score", (0, 128, 720, 168), "number"),
    ],
    "CONSULT": [
        ("consult.hint", (0, 872, 720, 926), "body"),
        ("consult.service", (0, 940, 720, 1010), "button"),
        ("consult.refresh", (0, 1020, 260, 1110), "button"),
        ("consult.shelf", (40, 470, 700, 870), None),
    ],
    "SELECT_CARD": [
        ("select.title", (230, 590, 370, 670), "title"),
        ("select.receive", (250, 1050, 470, 1130), "button"),
        ("select.panel", (0, 650, 720, 940), None),
    ],
    "SELECT_DRINK": [
        ("select.title", (230, 590, 370, 670), "title"),
        ("select.receive", (250, 1050, 470, 1130), "button"),
        ("select.panel", (0, 650, 720, 940), None),
    ],
    "SELECT_PITEM": [
        ("select.title", (60, 560, 660, 700), "title"),
        ("select.receive", (250, 1050, 470, 1130), "button"),
        ("select.panel", (0, 700, 720, 940), None),
    ],
    "SKILL_CARD_ENHANCE": [
        ("deck.title", (40, 25, 300, 90), "title"),
        ("deck.detail_name", (170, 95, 720, 160), "name"),
        ("deck.tab", (60, 1015, 340, 1075), "label"),
    ],
    "SKILL_CARD_REMOVAL": [
        ("deck.title", (40, 25, 300, 90), "title"),
        ("deck.detail_name", (170, 95, 720, 160), "name"),
        ("deck.tab", (60, 1015, 340, 1075), "label"),
    ],
    "DECK_LIST": [
        ("deck.title", (40, 25, 300, 90), "title"),
        ("deck.detail_name", (170, 95, 720, 160), "name"),
        ("deck.tab", (60, 1015, 340, 1075), "label"),
    ],
    "SPECIAL_GUIDANCE": [
        ("customize.label", (20, 20, 210, 80), "label"),
        ("customize.detail_name", (0, 165, 720, 220), "name"),
    ],
    "NIA_SALES_PLACE": [
        ("nia.votes", (540, 120, 700, 170), "number"),
    ],
    "EVENT_CHOICE": [
        ("event.effect", (0, 480, 720, 640), "body"),
        ("event.choices", (0, 650, 720, 950), "button"),
    ],
    "COMMU": [
        ("commu.speaker", (0, 880, 720, 960), "name"),
        ("commu.line", (0, 960, 720, 1130), "body"),
    ],
}


def lookup(page: str | None, box) -> tuple[str | None, str | None]:
    """框中心落在哪个字段区域：(字段名, 类型或 None)；没有返回 (None, None)。"""
    rows = FIELDS.get(page or "") or FIELDS.get((page or "").upper()) or []
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for name, (x1, y1, x2, y2), typ in rows:
        if x1 <= cx < x2 and y1 <= cy < y2:
            return name, typ
    return None, None
