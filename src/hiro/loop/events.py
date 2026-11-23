"""状态推算与识别互验的主循环部分（docs/state-events.md §5，契约 1.2）。

    apply_event(state, event)   事件记进 ledger，按事件改牌组，调用预测器 hiro.state.events.predict 得到预测
    check(state, obs, preds)    观测到来时：先过不变量，再和到期预测比 → Check 列表
    gate(obs, checks)           read_error 的读数从观测里去掉（不进状态、不进决策），返回干净的观测
    events_from_step(...)       由已执行的决策推出事件（action 事件）；取代 hiro.loop.ledger 的记账（同一套账）

reduce 本身仍是纯函数；Session.observe 依次调用 check → gate → reduce → mark。
"""

from __future__ import annotations

import copy
import dataclasses
from typing import Any

from hiro.contracts import Check, Event, Expect, GameState, Observation, Prediction, Source

LEDGER_KEEP = 200  # GameState.ledger 只留最近 N 条，完整的在 trace

# 观测字段（按页面 section）→ 字段路径
SECTION = {"ACTION_SELECT": "produce", "DECK_LIST": "produce", "HIF_EXAM": "exam"}


def _no_prediction(state, event) -> list:
    return []


# ---------------------------------------------------------------------------- 事件
def apply_event(state: GameState, event: Event, *, predictor=None) -> tuple[GameState, list[Prediction]]:
    """纯函数：返回新状态与预测。牌组按事件增减（已知时）；字段状态记 predicted。"""
    event = _memory_upgrade(state, event)
    s = copy.deepcopy(state)
    s.ledger.append(event)
    del s.ledger[:-LEDGER_KEEP]
    _apply_deck(s, event)
    _apply_items(s, event)
    if predictor is None:
        try:  # 游戏专用的预测器（hiro.state.events）不在开源版里：没有就不做预测
            from hiro.state.events import predict as predictor
        except ImportError:
            predictor = _no_prediction
    try:
        raw = predictor(state, event.to_dict())  # 预测基于事件之前的状态
    except Exception as e:  # 预测器异常不能影响主循环
        s.conflicts.append(f"seq {event.seq}: 预测 {event.kind} 失败：{type(e).__name__}: {e}")
        raw = []
    preds = [_prediction(p) for p in raw]
    for p in preds:
        for path in p.fields:
            if s.field_status.get(path) != "pending":
                s.field_status[path] = "predicted"
    return s, preds


def _apply_items(s: GameState, e: Event) -> None:
    """P 道具账本 produce.items：PITEM_ACQUIRE 加一件、PITEM_CUSTOMIZE 换掉上一段（同牌组按事件增减）。原来只登记预测，画面上又读不到
    produce.items 去核对，账本一直是空的（8a3a1e7 11-18：初 PRO 连固有道具都没有，H.I.F mainz4 整局 items 为空，搜索引擎里没有 P 道具）。
    同名分不清（只有 pitem_ids 多个）时不记，不猜。"""
    if e.kind not in ("PITEM_ACQUIRE", "PITEM_CUSTOMIZE"):
        return
    a = e.args or {}
    ids = [x for x in a.get("pitem_ids") or [] if x]
    item = a.get("item_id") or a.get("pitem_id") or (ids[0] if len(ids) == 1 else None)
    if not item:
        return
    s.produce = s.produce if s.produce is not None else {}
    items = list(s.produce.get("items") or [])
    if e.kind == "PITEM_CUSTOMIZE" and a.get("replaces") in items:
        items.remove(a["replaces"])
    if e.kind == "PITEM_ACQUIRE" and item in items:
        return  # 同一件已经记过（获得演出和选择动作各发一次、没配上对时）
    items.append(item)
    s.produce["items"] = items
    s.sources["produce.items"] = Source("infer" if e.origin != "observed" else "screen", e.seq)


def _prediction(p: Any) -> Prediction:
    if isinstance(p, Prediction):
        return p
    fields = {k: (v if isinstance(v, Expect) else Expect(**{kk: vv for kk, vv in v.items() if kk in Expect.__dataclass_fields__}))
              for k, v in (p.get("fields") or {}).items()}  # fmt: skip
    return Prediction(seq=p.get("seq", 0), event_seq=p.get("event_seq", 0), fields=fields, due=p.get("due", "any"),
                      expires_after=p.get("expires_after"))  # fmt: skip


def _memory_upgrade(state: GameState, event: Event) -> Event:
    """回忆效果展示的卡（识别给 memory_effect）：牌组里已有同一张、强化次数更低 → 是回忆把它强化了，改记 CARD_UPGRADE；
    没有 → 照旧是获得（开局回忆卡加入牌组也是这个画面，events/acquire-memory-00028）。DMM q4 11-17：选拔试验后两张回忆卡
    私がスター / 届いて！ 被强化成 +，原来各记一次获得，账本多 2 张。只看没配上动作的识别事件（apply_event 只收没配上的）。"""
    a = event.args or {}
    if event.kind != "CARD_ACQUIRE" or event.origin != "observed" or not a.get("memory_effect") or not a.get("card_id"):
        return event
    up = int(a.get("upgrade") or 0)
    deck = (state.produce or {}).get("deck") or []
    lower = [
        int(c.get("upgrade") or 0) for c in deck if c.get("card_id") == a["card_id"] and int(c.get("upgrade") or 0) < up
    ]
    if not up or not lower:
        return event
    return dataclasses.replace(event, kind="CARD_UPGRADE", args={"card_id": a["card_id"], "upgrade": max(lower), "source": "memory"},
                               note=f"回忆效果展示 {a['card_id']}+{up}：牌组里已有强化 {max(lower)} 的同一张，记成强化（不是获得）")  # fmt: skip


def _apply_deck(s: GameState, e: Event) -> None:
    if e.kind == "DECK_REVIEW" and e.args.get("deck") is not None:
        s.produce = s.produce if s.produce is not None else {}
        s.produce.setdefault("deck", [])
    prod = s.produce if s.produce is not None else None
    if prod is None or prod.get("deck") is None:
        return
    deck: list = prod["deck"]
    a = e.args

    def add(card_id, upgrade=0):
        if card_id:
            deck.append({"card_id": card_id, "upgrade": int(upgrade or 0)})

    def remove(card_id):
        i = next((i for i, c in enumerate(deck) if c.get("card_id") == card_id), None)
        if i is not None:
            deck.pop(i)
        elif card_id:
            s.pending_review.append("produce.deck") if "produce.deck" not in s.pending_review else None

    if e.kind in ("CARD_ACQUIRE", "SHOP_BUY") and a.get("card_id"):
        add(a["card_id"], a.get("upgrade"))
    elif e.kind == "CARD_DELETE":
        remove(a.get("card_id"))
    elif e.kind == "CARD_CHANGE":
        remove(a.get("remove_card_id"))
        add(a.get("card_id"), a.get("upgrade"))
    elif e.kind == "CARD_CUSTOMIZE" and a.get("customize_id"):
        i = a.get("deck_index")
        c = deck[i] if isinstance(i, int) and 0 <= i < len(deck) and deck[i].get("card_id") == a.get("card_id") else \
            next((c for c in deck if c.get("card_id") == a.get("card_id")), None)  # fmt: skip
        if c is not None:
            cz = c.setdefault("customize", {})
            cz[a["customize_id"]] = int(cz.get(a["customize_id"]) or 0) + int(a.get("levels") or 1)
            c["customized"] = True
    elif e.kind == "CARD_UPGRADE":
        c = next((c for c in deck if c.get("card_id") == a.get("card_id") and not c.get("upgrade")), None)
        if c is not None:
            c["upgrade"] = 1
    elif e.kind == "DECK_REVIEW" and a.get("deck") is not None:
        # 牌组页只读得到「有没有定制」：特別指導记下的定制按同卡同强化带过去，不被牌组确认冲掉（任务 ②）
        old = [c for c in deck if c.get("customize")]
        new = [{"card_id": c.get("card_id"), "upgrade": int(c.get("upgrade") or 0)} for c in a["deck"]]
        for c in new:
            hit = next((o for o in old if o.get("card_id") == c["card_id"] and int(o.get("upgrade") or 0) == c["upgrade"]), None) \
                or next((o for o in old if o.get("card_id") == c["card_id"]), None)  # fmt: skip
            if hit is not None:
                old.remove(hit)
                c["customize"], c["customized"] = dict(hit["customize"]), True
        prod["deck"] = new
        s.pending_review = [f for f in s.pending_review if f != "produce.deck"]
        s.field_status["produce.deck"] = "verified"
    elif e.kind in ("CARD_AUTO_PROCESS", "UNMODELED_CHANGE") and "deck" in str(a.get("field", "deck")):
        if "produce.deck" not in s.pending_review:
            s.pending_review.append("produce.deck")
        s.field_status["produce.deck"] = "pending"


# 换卡阶段 1 选中的候选（换入卡）：阶段 2 的 ChangeCard 只带换出卡，换入卡要从阶段 1 接过来。
# 以前 CARD_CHANGE 没有 card_id，记账只减不加，每换一次牌组少记一张（Windows 第六局换 4 次后记账 17、实际 20，
# 选卡对话框拼接核对因此判「差太多」、只用第一屏）
_CHANGE_IN: dict = {}


def events_from_step(
    page: str | None, action: dict, ok: bool, page_fields: dict, produce: dict | None, *, seq: int
) -> list[Event]:
    """已执行成功的决策 → action 事件（hiro.loop.ledger 的规则，改为产出事件）。"""
    if not ok or not action:
        return []
    kind, args = action.get("kind"), action.get("args") or {}
    ev: list[Event] = []

    def E(k, **a):
        ev.append(Event(seq=seq, kind=k, origin="action", args={kk: vv for kk, vv in a.items() if vv is not None}, basis="observe_only",
                        ref={"decision_seq": seq}))  # fmt: skip

    if page == "SELECT_CARD" and kind == "ActivateEffect":
        cands = page_fields.get("candidates") or []
        for i in args.get("pick_indexes") or []:
            if 0 <= int(i) < len(cands):
                E("CARD_ACQUIRE", card_id=cands[int(i)].get("card_id"), upgrade=cands[int(i)].get("upgrade"))
    elif page == "SELECT_CARD" and kind == "Reroll":
        E("CARD_REROLL")
    elif page == "SELECT_CARD" and kind == "ExcludeCard":
        cands = page_fields.get("candidates") or []
        i = args.get("pick_index")
        E("CARD_EXCLUDE", card_id=cands[i].get("card_id") if isinstance(i, int) and 0 <= i < len(cands) else None)
    elif page == "CONSULT" and kind == "ShopBuy":
        if args.get("service") == "delete":
            deck = (produce or {}).get("deck") or []
            i = args.get("card_index")
            c = deck[i] if isinstance(i, int) and 0 <= i < len(deck) else {}
            if args.get("card_id"):  # 对话框里实际删的那张（主循环做成后写回）优先于决策写的 card_index
                c = {"card_id": args["card_id"], "upgrade": args.get("upgrade")}
            E("CARD_DELETE", card_id=c.get("card_id"), upgrade=c.get("upgrade"), source="consult")
        elif "index" in args:
            shelf = page_fields.get("shelf") or []
            i = int(args["index"])
            item = shelf[i] if 0 <= i < len(shelf) else {}
            E("SHOP_BUY", kind="card" if item.get("card_id") else "drink", card_id=item.get("card_id"), drink_id=item.get("drink_id"),
              upgrade=item.get("upgrade"), price=item.get("price"))  # fmt: skip
    elif page == "HIF_ROUND_INTERVAL" and kind == "IntervalBuy" and "index" in args:
        # 轮间买卡 / 饮料：同相談记 SHOP_BUY，卡进牌组账本（p13 复盘：以前没记，R2 开局重建找不到轮间买的 アイドル魂、退回 uinet）。
        # 货架格子编号就是决策的 index（买走后那格 card_id 变空，所以用决策这一步的货架）
        iv = page_fields.get("interval") or {}
        i = int(args["index"])
        item = next((c for c in (iv.get("cards") or []) + (iv.get("drinks") or []) if c.get("index") == i), {})
        if item.get("card_id") or item.get("drink_id"):
            E("SHOP_BUY", kind="card" if item.get("card_id") else "drink", card_id=item.get("card_id"), drink_id=item.get("drink_id"),
              upgrade=item.get("upgrade"), price=item.get("price"), source="interval")  # fmt: skip
    elif page == "SKILL_CARD_CHANGE_1" and kind == "ChangeCard" and not args.get("cancel"):
        cands = page_fields.get("candidates") or []
        i = args.get("pick_index")
        c = cands[i] if isinstance(i, int) and 0 <= i < len(cands) else {}
        _CHANGE_IN["card"] = (c.get("card_id"), c.get("upgrade")) if c.get("card_id") else None
    elif page == "SKILL_CARD_CHANGE_2" and kind == "ChangeCard":
        pick = _CHANGE_IN.pop("card", None)  # 阶段 1 选中的换入卡（同 hiro.loop.ledger 的 pending_in）
        if not args.get("cancel") and args.get("remove_card_id"):
            card_id, upgrade = args.get("card_id"), args.get("upgrade")
            if card_id is None and pick:
                card_id, upgrade = pick
            E("CARD_CHANGE", remove_card_id=args["remove_card_id"], remove_upgrade=args.get("remove_upgrade"), card_id=card_id,
              upgrade=upgrade)  # fmt: skip
    elif page == "SKILL_CARD_REMOVAL" and args.get("card_id"):
        E("CARD_DELETE", card_id=args["card_id"], upgrade=args.get("upgrade"), source="event")
    elif page == "SKILL_CARD_ENHANCE" and args.get("card_id"):
        E("CARD_UPGRADE", card_id=args["card_id"], upgrade=args.get("upgrade"), source="event")
    elif page == "SELECT_PITEM" and kind == "ActivateEffect":
        # 定制 P 道具（背包 → 毛绒 → 装饰）：选中的那一项拼成 master ID，替换上一段（任务 ②：搜索起点要带这一局的 P 道具）
        from hiro.brain import pitem

        items = page_fields.get("pitems") or []
        i = next(iter(args.get("pick_indexes") or []), None)
        chosen = items[int(i)] if isinstance(i, int) and 0 <= i < len(items) else None
        if isinstance(chosen, dict) and chosen.get("pitem_id"):
            # 初 / N.I.A 的 P 道具三选一（有名字、认得出 ID 的道具，不是 H.I.F 的定制背包）：选中就记获得
            E("PITEM_ACQUIRE", item_id=chosen["pitem_id"])
        p = (
            pitem.parse(chosen)
            if chosen is not None and not (isinstance(chosen, dict) and chosen.get("pitem_id"))
            else None
        )
        new = pitem.custom_id(p) if p else None
        if new:
            old = next((x for x in (produce or {}).get("items") or [] if str(x).startswith("customize_pitem-")), None)
            E("PITEM_CUSTOMIZE", item_id=new, replaces=old)
    elif page == "SPECIAL_GUIDANCE" and kind == "CustomizeCard" and args.get("customize_id"):
        # 记下定制了哪一项、几级（任务 ②：搜索起点要知道卡的定制，原来只记「定制过」）
        E("CARD_CUSTOMIZE", card_id=args.get("card_id"), deck_index=args.get("card_index"), customize_id=args["customize_id"],
          levels=int(args.get("levels") or 1))  # fmt: skip
    elif page == "PDRINK_MAX" and kind == "DiscardDrink" and args.get("receive") and args.get("discard"):
        E("DRINK_DISCARD", index=args["discard"][0])
    return ev


# ---------------------------------------------------------------------------- 互验
def _flat(obs: Observation) -> dict[str, Any]:
    section = SECTION.get(obs.page.name)
    if section is None:
        return {}
    out: dict[str, Any] = {}
    for k, v in obs.fields.items():
        if isinstance(v, dict) and k in ("params",):
            for kk, vv in v.items():
                out[f"{section}.{k}.{kk}"] = vv
        else:
            out[f"{section}.{k}"] = v
    return out


def _get(state: GameState, path: str):
    cur: Any = state
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def builtin_invariants(state: GameState, obs: Observation) -> list[Check]:
    """§4 的最小集合（数据确认交付 hiro.state.invariants 后由它取代）：param_monotonic、score_monotonic、digit_count。"""
    out: list[Check] = []
    flat = _flat(obs)
    for path, v in flat.items():
        if not isinstance(v, (int, float)):
            continue
        old = _get(state, path)
        if not isinstance(old, (int, float)):
            continue
        if path.startswith("produce.params.") and v < old:
            out.append(Check(seq=obs.seq, field=path, predicted={"min": old}, observed=v, verdict="read_error", rule="param_monotonic",
                             action="discard_reread"))  # fmt: skip
        elif path == "exam.score" and v < old:
            out.append(Check(seq=obs.seq, field=path, predicted={"min": old}, observed=v, verdict="read_error", rule="score_monotonic",
                             action="discard_reread"))  # fmt: skip
        elif (
            path in ("produce.produce_point", "produce.star")
            and old >= 100
            and v > 0
            and len(str(int(v))) < len(str(int(old))) - 0
            and v < old * 0.5
        ):
            out.append(Check(seq=obs.seq, field=path, predicted={"value": old}, observed=v, verdict="read_error", rule="digit_count",
                             action="discard_reread"))  # fmt: skip
    return out


def check(state: GameState, obs: Observation, predictions: list[Prediction]) -> list[Check]:
    """先过不变量（read_error），再和到期预测比（consistent / model_gap），其余字段 unpredicted。"""
    try:
        from hiro.state.invariants import check_invariants  # 数据确认交付

        inv = [c if isinstance(c, Check) else Check(**{k: v for k, v in c.items() if k in Check.__dataclass_fields__})
               for c in check_invariants(state, obs)]  # fmt: skip
    except ImportError:
        inv = builtin_invariants(state, obs)
    bad = {c.field for c in inv if c.verdict == "read_error"}
    out = list(inv)
    flat = _flat(obs)
    seen: set[str] = set()
    for p in predictions:
        if p.due not in ("any", obs.page.name):
            continue
        for path, exp in p.fields.items():
            if path not in flat or path in bad or path in seen or flat[path] is None:
                continue
            seen.add(path)
            ok = exp.admits(flat[path])
            out.append(Check(seq=obs.seq, field=path, predicted=exp.to_dict() if hasattr(exp, "to_dict") else exp.__dict__,
                             observed=flat[path], verdict="consistent" if ok else "model_gap", rule="prediction",
                             action="accept" if ok else "mark_pending"))  # fmt: skip
    for path, v in flat.items():
        if v is not None and path not in bad and path not in seen:
            out.append(
                Check(seq=obs.seq, field=path, observed=v, verdict="unpredicted", rule="prediction", action="accept")
            )
    return out


def gate(obs: Observation, checks: list[Check], state: GameState | None = None) -> Observation:
    """去掉 read_error 的读数：params 的子键换回状态里的旧值（params 是整体替换的字段，不能缺项），其余字段整项去掉。返回新观测。"""
    bad = [c.field for c in checks if c.verdict == "read_error"]
    if not bad:
        return obs
    o = copy.deepcopy(obs)
    for path in bad:
        parts = path.split(".")
        if len(parts) == 3 and isinstance(o.fields.get(parts[1]), dict):
            old = _get(state, path) if state is not None else None
            sub = {k: v for k, v in o.fields[parts[1]].items() if k != parts[2]}
            if old is not None:
                sub[parts[2]] = old
            o.fields[parts[1]] = sub
        elif len(parts) >= 2:
            o.fields.pop(parts[1], None)
    return o


def mark(state: GameState, checks: list[Check]) -> GameState:
    """按 Check 更新字段状态：consistent → verified；model_gap → pending + pending_review；read_error → 记冲突；unpredicted → observed。"""
    s = copy.deepcopy(state)
    for c in checks:
        if c.verdict == "consistent":
            s.field_status[c.field] = "verified"
        elif c.verdict == "model_gap":
            s.field_status[c.field] = "pending"
            if c.field not in s.pending_review:
                s.pending_review.append(c.field)
        elif c.verdict == "read_error":
            s.conflicts.append(
                f"seq {c.seq}: {c.field} 读数 {c.observed!r} 违反 {c.rule}（参考 {c.predicted!r}），丢弃"
            )
            del s.conflicts[:-20]
        elif s.field_status.get(c.field) not in ("verified", "pending"):
            s.field_status[c.field] = "observed"
    return s


def reduce_checked(state: GameState, obs: Observation, predictions: list[Prediction]) -> tuple[GameState, list[Check]]:
    """主循环与离线回放共用：互验 → 去掉读错 → reduce → 标字段状态。只对截图来源做互验；存档是真值。"""
    from hiro.loop.reduce import reduce

    if obs.source not in ("screen", "replay", "dataset") or not obs.fields:
        return reduce(state, obs), []
    checks = check(state, obs, predictions)
    return mark(reduce(state, gate(obs, checks, state)), checks), checks


def settle(predictions: list[Prediction], checks: list[Check]) -> list[Prediction]:
    """核对过的预测出队。"""
    done = {c.field for c in checks if c.verdict in ("consistent", "model_gap")}
    return [p for p in predictions if not (set(p.fields) & done)]


PAIR_WINDOW = 8  # 动作事件与识别认出的同一件事（获得弹窗、交換確認）相隔不超过这么多个 seq 就算同一件


def _pair_key(e: Event) -> tuple | None:
    a = e.args or {}
    ident = a.get("card_id") or a.get("drink_id") or a.get("pitem_id")
    if e.kind == "CARD_CHANGE":
        ident = a.get("card_id")
    if not ident:
        return None
    effect = {"CARD_ACQUIRE": "add", "SHOP_BUY": "add", "CARD_CHANGE": "add", "DRINK_ACQUIRE": "drink", "PITEM_ACQUIRE": "pitem",
              "CARD_DELETE": "delete", "CARD_UPGRADE": "upgrade"}.get(e.kind)  # fmt: skip
    return (effect, ident) if effect else None


def find_pair(event: Event, recent: list[Event]) -> Event | None:
    """recent 里与 event 是同一件事的另一来源事件（动作 ↔ 识别），按 seq 最近的配。没有返回 None。"""
    key = _pair_key(event)
    if key is None:
        return None
    for other in reversed(recent):
        if other.origin == event.origin or other.ref.get("paired"):
            continue
        if abs(other.seq - event.seq) <= PAIR_WINDOW and _pair_key(other) == key:
            return other
    return None


def to_event(d: dict | Event) -> Event:
    if isinstance(d, Event):
        return d
    return Event(**{k: v for k, v in d.items() if k in Event.__dataclass_fields__})
