"""考试局面来源的转接层（2026-11-25 插件架构 spec 4.3「考试转接」、4.8 第 1 条）。

考试循环（hiro.exec.exam.run_exam）仍通过 ExamStateSource 接口拿局面。strategy.json 配了数据来源时，run_exam 用这个类包住原来的
局面来源：先照常让原来的实现读，再问覆盖 HIF_EXAM 的数据来源插件，新鲜的读数按档位盖到 / 补进 BattleState，hidden 填进
BattleState.hidden。没配数据来源时 run_exam 不包，行为和原来一样。

考试循环不是每帧都经过 Session.observe，所以这里直接问插件，合并规则和 hiro.loop.sources 一样：
时间差超过容差不用；只收 FIELD_CATALOG["HIF_EXAM"] 里登记的字段；档位高于 screen 时以插件为准，否则只补读不到的。
手牌 / 饮料可以给完整的 CardView / DrinkView 字典，也可以只给 {card_id, upgrade, available} / {drink_id}，按主数据补全；
手牌来自插件时 confidence() 报 save、hand_from_plugin 为真，点击按卡 id 对到屏幕位置；
只有数值来自存档级插件、手牌还是画面的，confidence() 也报 save，但 hand_from_plugin 为假，点击仍按画面顺序。
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Any

from hiro.contracts import FIELD_CATALOG
from hiro.contracts.game_state import PRIORITY

logger = logging.getLogger(__name__)
PAGE = "HIF_EXAM"


class ObservationExamStateSource:
    def __init__(self, inner: Any):
        self.inner = inner
        self.used_trust: str | None = None  # 上一次 read 用到的最高档位
        self.lists_from_plugin = False  # 上一次 read 的手牌 / 饮料来自插件
        self.hand_from_plugin = False  # 上一次 read 的手牌来自插件（run_exam 据此决定要不要按卡 id 对屏幕位置）
        self.notes: list[str] = []  # 冲突 / 出错说明（最近一次 read）

    # ---- ExamStateSource 接口，除 read / confidence 外原样转给原来的实现
    def begin(self, context: dict) -> None:
        self.context = context
        self.inner.begin(context)

    def executed(self, action, result) -> None:
        self.inner.executed(action, result)

    def previews(self, state):
        return self.inner.previews(state)

    def end(self, result) -> None:
        self.inner.end(result)

    def confidence(self) -> str:
        # 用了 save 档的数值、或手牌 / 饮料来自插件，都报 save（可信度）。点击要不要按卡 id 对屏幕位置看 hand_from_plugin，不看这个
        if self.used_trust == "save" or self.lists_from_plugin:
            return "save"
        return self.inner.confidence()

    def read(self, frame, memory):
        from hiro import plugins, strategy
        from hiro.brain.plugin_gate import PluginTimeout, call_with_timeout
        from hiro.loop.sources import ensure_started, fresh
        from hiro.plugin_record import RECORD

        state, why = self.inner.read(frame, memory)
        self.used_trust, self.notes, self.lists_from_plugin = None, [], False
        # 手牌顺序先按原来的局面来源算：它自己带了 hand_from_plugin 就用它；没带的（私有存档插件）可信度 save 就是存档顺序。
        # 数据来源插件只有真给了手牌才再置真
        inner_flag = getattr(self.inner, "hand_from_plugin", None)
        self.hand_from_plugin = inner_flag if isinstance(inner_flag, bool) else self.inner.confidence() == "save"
        cfg = strategy.current()
        if not cfg.sources:
            return state, why
        frame_t = float(getattr(frame, "wall_t", None) or time.time())
        allowed = set(FIELD_CATALOG.get(PAGE, ()))
        taken: dict[str, int] = {}
        for sid in cfg.sources:
            src = plugins.get_by_id("observation_source", sid)
            if src is None:
                continue
            pages = getattr(src, "pages", None)
            if pages is not None and PAGE not in pages:
                continue
            lab = plugins.label(src)
            point = f"source.{sid}"
            if (why := ensure_started(sid, src, lab, {"page": PAGE, "exam": True})) is not None:
                if why:
                    RECORD.error(point, lab, why)
                    self.notes.append(why)
                continue
            view = copy.deepcopy(state)  # 插件拿副本
            try:
                r = call_with_timeout(
                    lambda src=src, view=view: src.read(PAGE, frame_t, view),
                    cfg.source_timeout_for(sid),
                    key=f"source:{sid}",
                )
            except (PluginTimeout, Exception) as e:  # noqa: BLE001
                RECORD.error(point, lab, f"数据来源 {lab} 出错：{e}")
                self.notes.append(f"数据来源 {lab} 出错：{e}")
                continue
            if r is None or not fresh(r.t, frame_t):
                RECORD.deferred(point, lab)
                continue
            trust = getattr(src, "trust", "screen")
            trust = trust if trust in PRIORITY else "screen"
            rank = PRIORITY[trust]
            raw = {k: v for k, v in (r.fields or {}).items() if k in allowed and v is not None}
            fields = {k: self._convert(k, v, lab) for k, v in raw.items()}
            fields = {k: v for k, v in fields.items() if v is not _SKIP}
            hidden = dict(r.hidden or {})
            used = False
            if state is None:
                if rank > PRIORITY["screen"] and fields:
                    state = self._build(memory, fields, trust)
                    if state is not None:
                        used = True
                        taken.update(dict.fromkeys(fields, rank))
                        self.lists_from_plugin = self.lists_from_plugin or bool({"hand", "drinks"} & set(fields))
                        self.hand_from_plugin = self.hand_from_plugin or "hand" in fields
                if state is None:
                    RECORD.deferred(point, lab)
                    continue
            else:
                for k, v in fields.items():
                    if not hasattr(state, k) or (k in taken and taken[k] >= rank):
                        continue
                    cur = getattr(state, k)
                    missing = cur is None or (isinstance(cur, (list, dict)) and not cur)
                    if not missing and rank <= PRIORITY["screen"]:
                        if _brief(cur) != _brief(v):
                            self.notes.append(
                                f"数据来源冲突：考试.{k} 画面={_brief(cur)!r} vs {lab}={_brief(v)!r}，采用画面"
                            )
                        continue
                    if not missing and _brief(cur) != _brief(v):
                        self.notes.append(
                            f"数据来源冲突：考试.{k} 画面={_brief(cur)!r} vs {lab}={_brief(v)!r}，采用{lab}"
                        )
                    setattr(state, k, self._remember(memory, k, v))
                    taken[k] = rank
                    used = True
                    if k in ("hand", "drinks"):
                        self.lists_from_plugin = True
                    if k == "hand":
                        self.hand_from_plugin = True
            if hidden:
                h = getattr(state, "hidden", None)
                if isinstance(h, dict):
                    h.update(hidden)
                    used = True
                RECORD.hidden(set(hidden), lab)
            if used:
                RECORD.decided(point, lab)
                if self.used_trust is None or rank > PRIORITY[self.used_trust]:
                    self.used_trust = trust
            else:
                RECORD.deferred(point, lab)
        self._deliver_notes()
        return state, why

    # ---- 字段转换：插件交的是契约形状（和识别层一样），手牌 / 饮料要变成 CardView / DrinkView
    def _convert(self, k: str, v: Any, lab: str) -> Any:
        try:
            if k == "hand":
                return self._hand(v, lab)
            if k == "drinks":
                return self._drinks(v, lab)
            if k == "buffs":
                return dict(v) if isinstance(v, dict) else _SKIP
            if k == "attr":
                from hiro.brain.state import _ATTR

                return _ATTR.get(v, v)
        except Exception as e:  # noqa: BLE001  转换失败的字段这次不用
            self.notes.append(f"数据来源 {lab} 的考试.{k} 转换失败（{type(e).__name__}: {e}），不用")
            return _SKIP
        return v

    def _hand(self, v: Any, lab: str) -> Any:
        """手牌：每项是完整 CardView 字典，或 {card_id, upgrade, available, slot, badge_score}（和识别层一样，按主数据补全）。
        认不出的卡留一个占位（不丢格子：位置要和屏幕对得上），记一条说明。"""
        from hiro.brain.cards import CardView, card_view

        if not isinstance(v, list):
            self.notes.append(f"数据来源 {lab} 的考试.hand 不是列表，不用")
            return _SKIP
        out, bad = [], []
        for i, c in enumerate(v):
            if isinstance(c, dict) and "name" in c and "cost" in c:
                out.append(CardView.from_dict(c))
                continue
            card = None
            if isinstance(c, dict) and c.get("card_id"):
                from hiro.vision.cards import card_version

                card = card_version(c["card_id"], c.get("upgrade") or 0)
            view = card_view(
                int(c.get("slot", i)) if isinstance(c, dict) else i,
                card,
                available=bool(c.get("available", True)) if isinstance(c, dict) else False,
            )
            if card is None:
                bad.append(i)
            elif isinstance(c, dict):
                if "upgrade" in c and c["upgrade"] is None:
                    view.upgrade = None  # type: ignore[assignment]  # 强化次数未知：同识别层，不冒充 0
                view.badge_score = c.get("badge_score")
            out.append(view)
        if bad and len(bad) == len(out):
            self.notes.append(f"数据来源 {lab} 的考试.hand 一张都认不出（没有 card_id 或主数据里没有），不用")
            return _SKIP
        if bad:
            self.notes.append(f"数据来源 {lab} 的考试.hand 第 {bad} 格认不出（没有 card_id 或主数据里没有），留占位")
        return out

    def _drinks(self, v: Any, lab: str) -> Any:
        """饮料：每项是完整 DrinkView 字典，或 {drink_id, slot}；认不出的丢掉（饮料按 id 点，不靠位置）。"""
        from hiro import master
        from hiro.brain.cards import DrinkView, drink_view

        if not isinstance(v, list):
            self.notes.append(f"数据来源 {lab} 的考试.drinks 不是列表，不用")
            return _SKIP
        by_id = {d.id: d for d in master.drinks()}
        out, bad = [], []
        for i, d in enumerate(v):
            if isinstance(d, dict) and "name" in d and "drink_id" in d and "text" in d:
                out.append(DrinkView.from_dict(d))
            elif isinstance(d, dict) and (drink := by_id.get(str(d.get("drink_id") or ""))) is not None:
                out.append(drink_view(int(d.get("slot", i)), drink))
            else:
                bad.append(i)
        if bad:
            self.notes.append(f"数据来源 {lab} 的考试.drinks 第 {bad} 项认不出，丢掉")
        return out

    @staticmethod
    def _remember(memory, k: str, v: Any) -> Any:
        """和 BattleState.build 一样更新本场记忆（体力历史、分数核对、P 道具次数、回合）。"""
        if memory is None:
            return v
        if k == "pitem_charges":
            memory.item_charges_left = v
        elif k == "hp":
            memory.observe_hp(v)
        elif k == "score":
            v, _ = memory.check_score(v)
        elif k == "remaining_turns":
            memory.observe_turn(v)
        return v

    def _deliver_notes(self) -> None:
        """冲突 / 丢字段的说明交给 Session，进这一步的 trace（spec 4.8 第 1 条）。"""
        session = (getattr(self, "context", None) or {}).get("session")
        note = getattr(session, "note", None)
        if note is None:
            return
        for n in self.notes:
            try:
                note(n)
            except Exception:  # noqa: BLE001
                break

    def _build(self, memory, fields: dict, trust: str):
        """原来的实现给不出局面、插件档位又高于画面时，用插件字段建局面（同 screen_state：exam_kwargs 的形状 → BattleState.build）。"""
        try:
            from hiro.brain.state import BattleState, exam_kwargs

            ctx = getattr(self, "context", None) or {}
            if memory is None:
                return None  # 没有本场记忆建不了局面（考试循环里总是有）
            kw = exam_kwargs({})
            kw.update(fields)
            memory.observe_turn(kw.get("remaining_turns"))
            return BattleState.build(
                memory, ctx.get("profile"), target_score=ctx.get("target_score"), source=trust, **kw
            )
        except Exception as e:  # noqa: BLE001  字段不全时建不出来，交回等待
            self.notes.append(f"用数据来源字段建考试局面失败：{type(e).__name__}: {e}")
            return None


_SKIP = object()  # 转换失败、这次不用的字段


def _brief(v: Any) -> Any:
    """冲突说明里比较 / 显示用：手牌、饮料比 id 列表。"""
    if isinstance(v, list) and v and hasattr(v[0], "card_id"):
        return [getattr(c, "card_id", None) for c in v]
    if isinstance(v, list) and v and hasattr(v[0], "drink_id"):
        return [getattr(d, "drink_id", None) for d in v]
    return v
