"""识别认出的事件（docs/state-events.md §2.1 Event、§3 事件表里认出方式为 O 的那些）。

`observed_events(obs)`：一帧 Observation → list[hiro.contracts.Event]（契约 1.2）。
- origin：`observed`（事件页面）/ `review`（牌组页全量读）；basis 一律 `observe_only`（识别只管看到了什么，推算归预测器）。
- confirmed：看到的是结果（获得弹窗、「〜を強化しました」、星星增加、考试结算）→ True；
  看到的只是意图（交換確認 / 削除确认、饮料溢出）→ None，等后续画面或 P 点变化确认。
- confidence / note：1.0 = 两种读法互相印证；更低的见各函数说明。
- 同一个弹窗会连续好几帧，传 prev（上一帧 Observation）时，页面相同且事件相同就不重复发。

和动作事件（origin=action）的关系：三选一拿卡、相談购买、授業拿卡之后都会出获得弹窗，
这时 observed 的 CARD_ACQUIRE 是对动作事件的确认（同 card_id、upgrade），不是再加一张。

选中项（删卡 / 强化 / 换卡第 2 步页面的橙色角框）不单独成事件：动作事件的 card_id / deck_index 用 `selection_args(obs)` 取。
"""

from __future__ import annotations

import json
from typing import Any

from hiro.contracts import Event

WAPPEN_ID = "pitem_00-3-265-0"  # H.I.Fワッペン（获得它本身是 PITEM_ACQUIRE；触发是 STAR_GAIN 页面）
SELECTION_PAGES = ("SKILL_CARD_REMOVAL", "SKILL_CARD_ENHANCE", "SKILL_CARD_CHANGE_2")


def _event(obs, kind: str, args: dict, *, confirmed: bool | None, confidence: float | None, note: str | None = None,
           origin: str = "observed") -> Event:  # fmt: skip
    return Event(seq=obs.seq, kind=kind, origin=origin, args=args, basis="observe_only", confirmed=confirmed,
                 ref={"observation_seq": obs.seq, "screenshot": obs.screenshot, "page": obs.page.name},
                 confidence=confidence, note=note)  # fmt: skip


def _acquire(obs, f: dict) -> list[Event]:
    """获得弹窗：名称栏文字精确命中主数据才会判成 ACQUIRE。卡再用缩略图核对：一致 1.0，缩略图没认出 0.8，两者不一致 0.5（仍以名称栏为准）。"""
    kind = f.get("kind")
    if kind in ("card", "drink") and f.get(f"{kind}_id") is None:
        return []  # 名称栏只在精确命中主数据时才判 ACQUIRE，正常到不了这里；万一没有 id 就不发（免得和动作事件配不上、多算一张）
    if kind == "card":
        agree = f.get("agree")
        conf, note = {
            True: (1.0, None),
            None: (0.8, "缩略图没认出，只按名称栏"),
            False: (0.5, "名称栏与缩略图不一致，以名称栏为准"),
        }[agree]
        args = {"card_id": f.get("card_id"), "upgrade": f.get("upgrade")}
        if f.get("memory_effect"):
            args["memory_effect"] = (
                True  # 回忆效果展示：没配上动作、牌组里已有低一级的同一张时状态层记成强化（hiro.loop.events）
            )
        return [_event(obs, "CARD_ACQUIRE", args, confirmed=True, confidence=conf, note=note)]
    if kind == "drink":
        return [_event(obs, "DRINK_ACQUIRE", {"drink_id": f.get("drink_id")}, confirmed=True, confidence=1.0)]
    if kind == "pitem":
        ids = f.get("pitem_ids") or []
        if f.get("pitem_id"):
            return [_event(obs, "PITEM_ACQUIRE", {"pitem_id": f["pitem_id"]}, confirmed=True, confidence=1.0)]
        return [_event(obs, "PITEM_ACQUIRE", {"pitem_id": None, "pitem_ids": ids}, confirmed=True, confidence=0.5,
                       note=f"同名 P 道具 {len(ids)} 个，分不出是哪个")]  # fmt: skip
    return []


def _confirm(obs, c: dict | None, *, deck_index: int | None = None) -> list[Event]:
    """交換確認 / 削除确认：kind exchange → SHOP_BUY、delete → CARD_DELETE；confirmed=None（点了「交換する / 削除する」才算发生）。
    price = 画面上「P before → after」之差（SALE 时是折后价）。物品与两个 P 数都读到 1.0，缺一项 0.6，物品没认出 0.3。"""
    if not c:
        return []
    item = c.get("item") or {}
    args: dict[str, Any] = {
        "price": c.get("price"),
        "p_before": c.get("p_before"),
        "p_after": c.get("p_after"),
        "sale": c.get("sale"),
    }
    if item.get("kind") == "drink":
        args["drink_id"] = item.get("drink_id")
    else:
        args.update(card_id=item.get("card_id"), upgrade=item.get("upgrade"))
    if c.get("kind") == "delete":
        args["deck_index"] = deck_index
    missing = [k for k in ("price",) if args[k] is None]
    conf, note = (
        (1.0, None)
        if item and not missing
        else (0.6, "P 数没读全，price 为 None")
        if item
        else (0.3, "弹窗里的物品名没认出")
    )
    kind = {"exchange": "SHOP_BUY", "delete": "CARD_DELETE"}.get(c.get("kind"))
    return [_event(obs, kind, args, confirmed=None, confidence=conf, note=note)] if kind else []


def _upgrade_result(obs, f: dict) -> list[Event]:
    """「〜を強化しました」：强化已完成。args.upgrade 是强化后的等级；卡名认不出时 card_id / upgrade 为 None（牌组记 pending）。"""
    ok = f.get("card_id") is not None
    args = {"card_id": f.get("card_id"), "upgrade": f.get("upgrade")}
    return [
        _event(
            obs,
            "CARD_UPGRADE",
            args,
            confirmed=True,
            confidence=1.0 if ok else None,
            note=None if ok else f"卡名「{f.get('name')}」没认出",
        )
    ]


def _star_gain(obs, f: dict) -> list[Event]:
    """ワッペン触发：星星 +gain（金字塔左侧气泡，单帧可读）。累计星星会滚动，不放进事件。"""
    if f.get("gain") is None:
        return []
    return [_event(obs, "WAPPEN_TRIGGER", {"star_gain": f["gain"]}, confirmed=True, confidence=None)]


def _pdrink_max(obs, f: dict) -> list[Event]:
    """饮料溢出：要拿的饮料已出现，但丢哪瓶由动作决定（DRINK_DISCARD）；也可能整份不要（報酬スキップ）→ confirmed=None。
    held 是当时已持有的饮料，complete=True 时可当饮料栏的复核。"""
    inc = f.get("incoming") or {}
    held = [d.get("drink_id") for d in f.get("held") or []] if f.get("complete") else None
    args = {"drink_id": inc.get("drink_id"), "overflow": True, "held": held}
    if inc.get("drink_id") is None and held is None:
        return []
    return [_event(obs, "DRINK_ACQUIRE", args, confirmed=None, confidence=1.0 if inc.get("drink_id") else None,
                   note="饮料已满：之后的 DRINK_DISCARD（动作）或報酬スキップ 决定结果")]  # fmt: skip


def _exam_result(obs, f: dict) -> list[Event]:
    if f.get("score") is None and f.get("passed") is None:
        return []
    meta = obs.field_meta.get("score")
    args = {"exam": f.get("exam_no"), "score": f.get("score"), "rank": f.get("rank"), "passed": f.get("passed")}
    return [_event(obs, "EXAM_RESULT", args, confirmed=True, confidence=meta.confidence if meta else None)]


def _deck_review(obs, f: dict) -> list[Event]:
    """牌组页看全（两屏拼好、张数对上）才发；看不全不发事件（problems 里写了缺什么）。"""
    if not f.get("complete") or f.get("deck") is None:
        return []
    args = {"deck": f["deck"], "deck_size": f.get("deck_size")}
    return [_event(obs, "DECK_REVIEW", args, origin="review", confirmed=True, confidence=1.0,
                   note="强化只分 0 / 1（++ 还没有样本）")]  # fmt: skip


def _card_dialog(obs, f: dict) -> list[Event]:
    """删卡页上叠着削除确认时出 CARD_DELETE（选中格的 index 当 deck_index；弹窗盖住角框时为 None）。"""
    return _confirm(obs, f.get("confirm"), deck_index=f.get("selected"))


PAGE_EVENTS = {
    "ACQUIRE": _acquire,
    "DIALOG": lambda obs, f: _confirm(obs, f.get("confirm")),
    "SKILL_CARD_REMOVAL": _card_dialog,
    "UPGRADE_RESULT": _upgrade_result,
    "DELETE_RESULT": lambda obs, f: [
        _event(
            obs,
            "CARD_DELETE",
            {"card_id": c["card_id"], "upgrade": c["upgrade"]},
            confirmed=True,
            confidence=1.0,
            note="「〜を削除しました」（考试后自动处理基础卡 / 相談删卡）",
        )
        for c in f.get("cards") or []
    ],  # fmt: skip  # noqa: RUF028
    "CUSTOMIZE_RESULT": lambda obs, f: [
        _event(
            obs,
            "CARD_CUSTOMIZE",
            {"card_id": f.get("card_id"), "upgrade": f.get("upgrade")},
            confirmed=True,
            confidence=1.0 if f.get("card_id") else None,
        )
    ],  # 「〜をカスタマイズしました」
    "STAR_GAIN": _star_gain,
    "PDRINK_MAX": _pdrink_max,
    "HIF_EXAM_RESULT": _exam_result,
    "DECK_LIST": _deck_review,
}


def event_key(ev: Event) -> str:
    return ev.kind + json.dumps(ev.args, sort_keys=True, ensure_ascii=False)


def observed_events(obs, prev=None) -> list[Event]:
    """一帧 Observation → 识别认出的事件。prev 是上一帧：同一页面、同样的事件（弹窗还开着）不重复发。"""
    fn = PAGE_EVENTS.get(obs.page.name)
    if fn is None or not obs.fields:
        return []
    events = fn(obs, obs.fields)
    if prev is not None and prev.page.name == obs.page.name and prev.fields:
        seen = {event_key(e) for e in PAGE_EVENTS[obs.page.name](prev, prev.fields)}
        events = [e for e in events if event_key(e) not in seen]
    return events


def selection_args(obs) -> dict | None:
    """删卡 / 强化 / 换卡第 2 步页面当前选中的卡 → 动作事件的 args {card_id, upgrade, deck_index}。
    角框与详情栏卡名不一致（agree=False）或没选中时 None。deck_index = 本页卡格 index（弹窗按牌组顺序排、不滚动）。"""
    if obs.page.name not in SELECTION_PAGES:
        return None
    card = obs.fields.get("selected_card")
    if not card or obs.fields.get("agree") is False:
        return None
    return {"card_id": card["card_id"], "upgrade": card["upgrade"], "deck_index": obs.fields.get("selected")}
