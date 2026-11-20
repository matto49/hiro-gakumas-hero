"""决策器从偶像卡攻略（hiro.brain.guides）读知识的适配层：把条目合并结果变成决策器要问的几个问题。

- CardKnow（card_pick）：这张卡是不是社区强卡（weight ≥ 75，A 级起）、可复用核心卡留几张（keep）、是不是要避开（avoid）。
  广：默认用手写表（knowledge.YARUKI_CARDS / SMALL_DECK_CORE / 冲突表），HIRO_GUIDES_HIRO=1 且有条目时改从条目读，两者等价
  （tests/test_guides_hiro.py 核对）。别的偶像：条目在 flows / 流派优先表之上补充。
- customize_plan（card_upgrade）：定制顺序 + 目标级数；没有条目时用广的 CUSTOMIZE_PRIORITY / CUSTOMIZE_LEVELS（H.I.F 照旧）。
- main_attr（formation.main_attr）：开局编成的默认主属性。
- notes（任意决策点）：条目的 why + 出处，拼进提示词的「偶像」一节。
"""

from __future__ import annotations

from dataclasses import dataclass, field

STRONG = 75  # 广的 A 级（guides_hiro.TIER_WEIGHT）
TIER_OF_WEIGHT = {100: "必拿", 90: "S", 75: "A", 60: "B"}


def _mode_diff(mode_id: str | None) -> tuple[str | None, str | None]:
    """produce-00x → (攻略的 mode, difficulty)。初 / N.I.A 之外返回 (None, None)：H.I.F 这次不接攻略。"""
    from hiro.state.modes import HAJIME, NIA, MODES

    m = MODES.get(mode_id or "")
    if m is None or m.family not in (HAJIME, NIA):
        return None, None
    return m.family, m.difficulty


def lookup(mode_id: str | None, idol_card: str | None, decision: str, ctx=None, pool=None):
    from hiro.brain import guides

    mode, diff = _mode_diff(mode_id)
    if mode is None:
        return []
    try:
        return guides.lookup(mode=mode, difficulty=diff, idol_card=idol_card, decision=decision, ctx=ctx, pool=pool)
    except Exception:  # noqa: BLE001  攻略包坏了不影响决策：退回现在的规则
        import logging

        logging.getLogger(__name__).warning("查攻略失败（%s / %s），按现在的规则", idol_card, decision, exc_info=True)
        return []


@dataclass
class CardKnow:
    prefer: dict = field(default_factory=dict)  # id → Item 字段
    avoid: dict = field(default_factory=dict)
    entry_of: dict = field(default_factory=dict)  # id → 条目 id
    notes: list = field(default_factory=list)  # unless 判不了时的提醒

    def weight(self, cid: str) -> int:
        """条目的 weight；统计条目没有 weight，用占比换算（share × 100：占比 ≥ 75% 算强卡，排行榜是「打法先验」，设计文档 3）。"""
        f = self.prefer.get(cid) or {}
        if f.get("weight") is not None:
            return int(f["weight"])
        share = (f.get("stat") or {}).get("share")
        return int(round(float(share) * 100)) if share is not None else 0

    def strong(self, cid: str) -> bool:
        return self.weight(cid) >= STRONG

    def tier(self, cid: str) -> str | None:
        return TIER_OF_WEIGHT.get(self.weight(cid))

    def keep(self, cid: str) -> int | None:
        """可复用核心卡要留几张：条目 keep 有值、而且这张卡不是一次性的（一次性的 keep 只表示「第二张也要」，不是循环件）。"""
        from hiro.state.knowledge import is_once

        k = (self.prefer.get(cid) or {}).get("keep")
        return int(k) if k and is_once(cid) is not True else None

    def avoided(self, cid: str) -> bool:
        return cid in self.avoid

    def conflict(self, cid: str) -> bool:
        """同 deciders.conflict_like 的口径：条目里避开的；或者条目没提到、卡面纯好印象、不带再動的。"""
        from hiro.brain.deciders import has_redo, is_pure_impression

        if cid in self.avoid:
            return True
        return cid not in self.prefer and is_pure_impression(cid) and not has_redo(cid)

    def source(self, cid: str) -> str | None:
        return self.entry_of.get(cid)


def card_know(mode_id: str | None, idol_card: str | None, ctx=None) -> CardKnow | None:
    """card_pick 条目合并 → CardKnow；没有条目（没包 / 开关关 / 广没开 HIRO_GUIDES_HIRO）时 None，调用方用手写表 / flows。"""
    from hiro.brain import guides

    hits = lookup(mode_id, idol_card, "card_pick", ctx=ctx)
    if not hits:
        return None
    k = CardKnow(notes=[h.note for h in hits if h.note])
    for it in guides.merge(hits):
        (k.prefer if it.side == "prefer" else k.avoid)[it.id] = it.fields
        k.entry_of[it.id] = it.entry.id
    return k


def customize_plan(mode_id: str | None, idol_card: str | None) -> tuple[list, dict] | None:
    """card_upgrade 条目 → (CUSTOMIZE_PRIORITY 同格式 [(card_id, 名字, 定制键, 理由)], CUSTOMIZE_LEVELS 同格式 {card_id: (级数, 必须做满)})；
    没有条目时 None。"""
    from hiro.brain import guides

    hits = lookup(mode_id, idol_card, "card_upgrade")
    if not hits:
        return None
    pri, lv = [], {}
    for it in guides.merge(hits):
        f = it.fields
        if it.side != "prefer" or not f.get("custom"):
            continue  # 统计条目只有强化比例、没有定制项（排行榜没有定制信息）：不当定制顺序
        name = (f.get("note") or "").split("：")[0] or it.id
        pri.append((it.id, name, list(f.get("custom") or []), f"{f.get('note') or ''}（攻略 {it.entry.id}）"))
        if int(f.get("levels") or 1) > 1 or f.get("full"):  # 1 级、不必做满是缺省（同 CUSTOMIZE_LEVELS 没列的卡）
            lv[it.id] = (int(f.get("levels") or 1), bool(f.get("full")))
    return (pri, lv) if pri else None


def main_attr(mode_id: str | None, idol_card: str | None) -> tuple[str, str] | None:
    """formation.main_attr 条目 → (Vo / Da / Vi, 条目 id)；没有时 None。"""
    hits = lookup(mode_id, idol_card, "formation.main_attr")
    for h in hits:
        for it in h.entry.content.get("prefer") or []:
            a = (it if isinstance(it, str) else it.get("id") or "").lower()
            if a in ("vo", "da", "vi"):
                return a.capitalize(), h.entry.id
    return None


def notes(mode_id: str | None, idol_card: str | None, decisions=("card_pick", "card_remove", "card_upgrade", "action", "drink", "exam_style"),
          limit: int = 6) -> list[str]:  # fmt: skip
    """各决策点排最前的条目的 why + 出处（拼提示词用）。"""
    out = []
    for d in decisions:
        for h in lookup(mode_id, idol_card, d)[:1]:
            why = h.entry.content.get("why")
            if why:
                out.append(f"- {d}：{why}（{h.entry.sources() or h.entry.id}）")
    return out[:limit]
