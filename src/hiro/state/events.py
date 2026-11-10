# 来源：digital-hiro src/hiro/state/events.py（develop 864e037）
"""状态推算的预测器（docs/state-events.md §3，H1 任务单「决策：预测器 hiro.state.events」）。

`predict(state, event) -> list[Prediction]`：事件发生后，下一个观测点各字段应该是多少（精确值或区间）。
reduce / check（总控）负责调用和比对，这里只给期望；不改 state。

类型用契约 1.2（hiro.contracts.events）：Event 输入（也接 dict），输出 list[Prediction]，字段期望是 Expect
（value | min / max、exact、tolerance、note）。
字段路径：produce.params.Vo / produce.stamina / produce.produce_point / produce.star / produce.deck（多重集，元素 "card_id+upgrade"）/
produce.deck_size / produce.drinks / produce.items / produce.wappen_left / produce.rerolls_left / produce.excludes_left / exam.* 等。

第 3 条「固定值还是随机」的核实结果见 GAIN_BASIS（每项写明依据）：固定的给精确值，真随机的给区间。
支援卡技能带来的额外加成（课程结束、差し入れ、获得卡等时机触发）是精确规则，但需要支援卡编成（卡 / 等级 / 突破）与已触发次数；
编成未知时按本地实机差值给上界（SUPPORT_SLACK），编成已知时由 support_bonus 精确算。
"""

from __future__ import annotations

from typing import Any

import hiro.sim  # noqa: F401  开源版：把 vendor/gakumas-player 加进 sys.path（预测里用 gakumas_produce 的社区规则 / 支援卡表）
from hiro.contracts import EVENT_KINDS, Event, Expect, Prediction
from hiro.state.context import _scale, catalog, schedule
from hiro.state.produce import ATTRS, ProduceState

# ---------------------------------------------------------------- 第 3 条：收益是固定值还是随机（核实依据）
GAIN_BASIS = {
    "lesson.main_sub": ("fixed", "master ProduceStepOpenLesson.mainParameter / subParameter × growth_permil（_scale 截断），"
                                 "SP 与否在选之前就显示在选项上；副属性开局决定、日程页可见 [wiki 公開レッスン表与 master 一致]"),
    "lesson.sub_attr": ("random_if_unread", "副属性哪一项：开局决定、日程可见；没读到时按社区统计 75% / 25% [wikiwiki]，此时两种都给"),
    "lesson.stamina": ("fixed", "普通 6、SP 8 [wiki][seesaa]，master ProduceStepOpenLesson.stamina"),
    "lesson.star": ("fixed", "master ProduceStepOpenLesson.star × star_permil（截断）[wiki スター性]"),
    "lesson.produce_point": ("fixed", "HIFSchedule.stage.base_p_points（master）"),
    "class.param": ("fixed", "选拔 3/6 日 120、10/17 日 150，本战 1/4 日 180，不套成长率 [community_rules.CLASS_PARAMETER，wiki 授業表]"),
    "class.stamina": ("fixed", "A / B 档体力 -5，眠気档 0 [wiki]"),
    "outing.stamina": ("fixed", "回复最大体力 60%（截断）[wiki おでかけ表，community_rules.OUTING_RECOVER_PERMIL]"),
    "outing.produce_point": ("fixed", "付费档 -50P [wiki]"),
    "gift.produce_point": ("fixed", "差し入れ +80P [community_rules.PRESENT_POINTS]"),
    "rest.stamina": ("fixed", "回复最大体力 50%（截断）[wiki 休む]"),
    "wappen.star": ("fixed", "ワッペン 每次星星 +10 × star_permil（截断）[wiki H.I.Fワッペン]"),
    "exam.selection_star": ("fixed_formula", "社区分段公式 community_rules.selection_star_base × star_permil [wiki 选拔获得星星]"),
    "exam.selection_params": ("formula_needs_turn_scores", "community_rules.selection_param_split：要各属性回合得分（存档 / 考试记录），"
                                                           "缺时按合计分数给区间；未达上界部分社区只有样本 [wiki]"),
    "exam.finals_star": ("samples", "本战星星只有社区样本（finals_star_base 插值），给 ±3 区间"),
    "exam.r1_points": ("samples", "R1 获得 P 点只有社区样本（r1_points 插值），给 ±5 区间"),
    "support.bonus": ("fixed_if_known", "支援卡技能（gakumas_produce.supports）：触发时机与数值是 master 精确值，需要编成与已触发次数；"
                                        "编成未知时用 SUPPORT_SLACK 上界"),
    "auto_process": ("random", "选拔①② 通过后随机删 2 张「基本」卡 [wiki]：张数固定、哪两张随机 → 只给张数，牌组 pending"),
    "card_reward": ("observe_only", "三选一 / 礼物 / 外出拿到哪张卡、哪瓶饮料：由候选识别给出，不能推算"),
}

# 支援卡加成上界（编成未知时）：本地实机差值的上界，按事件时机。第 6 局（traces/lead35-qualify6-20261031-232418）
# 第 1 天差し入れ后 Da +17、第 2 天 Da 普通课后 Da / Vi 比基础值多出十几点（支援卡「レッスン終了時」「差し入れ開始時」技能）。
# 由 hiro.state.events_audit 对三局 + 第六局 trace 统计（选拔① 前）：SP 课主属性额外 +30（第 2、3、6 局）、+21 / +51（第 1 局），
# 普通课 0；差し入れ +17；授業 ≤ +11；外出 ≤ +4。上界取实测最大值留余量。
# 支援卡回复体力的上界（编成未知时）：第 3 局 SP 课后体力比 -8 多 +4、第 6 局 +8（「SPレッスン終了時 体力回復」类技能）
SUPPORT_HP_SLACK = {"lesson": 8}
SUPPORT_SLACK = {"lesson_sp": 60, "lesson": 40, "class": 25, "outing": 25, "gift": 25, "shop": 25, "card": 15, "drink": 15, "rest": 15, "exam": 25}



# ---------------------------------------------------------------- 小工具
BASIS_OF = {"observe": "observe_only", "action": "observe_only"}  # 预测器内部的说法 → 契约 BASES


def make_event(kind: str, args: dict | None = None, *, seq: int = 0, origin: str = "action", basis: str | None = None) -> Event:
    assert kind in EVENT_KINDS, kind
    return Event(seq=seq, kind=kind, origin=origin, args=dict(args or {}), basis=BASIS_OF.get(basis, basis) or "observe_only")


def expect(value: Any = None, *, min: Any = None, max: Any = None, exact: bool | None = None, tolerance: Any = None, note: str | None = None) -> Expect:  # noqa: A002
    if exact is None:
        exact = value is not None and min is None and max is None
    return Expect(value=value, min=min, max=max, exact=exact, tolerance=tolerance, note=note)


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _event_parts(event) -> tuple[str, dict, int]:
    return _get(event, "kind"), dict(_get(event, "args") or {}), _get(event, "seq", 0) or 0


def _produce(state) -> dict:
    p = _get(state, "produce") or {}
    return p if isinstance(p, dict) else p.to_dict()


def _ps(state) -> ProduceState | None:
    """state.produce → ProduceState（必填字段缺时 None）。GameState、ProduceState、dict 都接。"""
    if isinstance(state, ProduceState):
        return state
    fn = getattr(state, "produce_state", None)
    if callable(fn):
        return fn()
    try:
        return ProduceState.from_dict(_produce(state))
    except Exception:
        return None


def _pred(seq: int, fields: dict, *, due: str = "ACTION_SELECT", basis: str = "master", expires_after: int | None = 3) -> list[Prediction]:
    """basis 只用于文档（推算依据写在 Event.basis 与 Expect.note 里），Prediction 契约没有这个字段。"""
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return []
    return [Prediction(seq=seq, event_seq=seq, fields=fields, due=due, expires_after=expires_after)]


def _key(card_id: str, upgrade: int = 0) -> str:
    return f"{card_id}+{upgrade or 0}"


def deck_keys(deck) -> list[str]:
    return sorted(_key(_get(c, "card_id"), _get(c, "upgrade", 0)) for c in deck or [])


def _deck_known(prod: dict) -> bool:
    return bool(prod.get("deck"))


def _cap_add(value: int | None, delta: int, cap: int | None) -> int | None:
    if value is None:
        return None
    v = value + delta
    return min(cap, v) if cap is not None else v


def _param_expect(before: int | None, gain: int, cap: int | None, slack: int, note: str) -> dict | None:
    """属性：基础收益精确，支援卡加成在 [0, slack] 内。"""
    if before is None:
        return None
    lo = _cap_add(before, gain, cap)
    hi = _cap_add(before, gain + slack, cap)
    if slack == 0 or lo == hi:
        return expect(lo, note=note)
    return expect(min=lo, max=hi, exact=False, note=note + f"；支援卡加成未知，上界 +{slack}（SUPPORT_SLACK）")


def _params_fields(prod: dict, gains: dict, slack_kind: str | None, note: str, *, support: dict | None = None) -> dict:
    """gains：基础收益 {Vo: n}；support：已知的支援卡加成 {Vo: n}（给出时不加 slack）。没有收益的属性也给（只受支援卡影响）。"""
    params = prod.get("params") or {}
    cap = prod.get("param_cap") or 3000
    slack = 0 if support is not None or slack_kind is None else SUPPORT_SLACK[slack_kind]
    out = {}
    for a in ATTRS:
        g = gains.get(a, 0) + (support or {}).get(a, 0)
        out[f"produce.params.{a}"] = _param_expect(params.get(a), g, cap, slack, note)
    return out


STAR_PERMIL_RANGE = (1000, 2000)  # star_permil 未识别时的范围：亲爱度加成 1.0–1.5×，加 H.I.F 加成 / 支援卡最多到 2.0×（第 2 局 SP 课程实测 2.0×）


def _star_expect(prod: dict, base: int, note: str) -> dict | None:
    """星星 + base × star_permil（截断）。倍率读到时精确；没读到时给 STAR_PERMIL_RANGE 区间。"""
    star = prod.get("star")
    if star is None:
        return None
    permil = prod.get("star_permil")
    if permil:
        return expect(star + _scale(base, permil), note=note)
    lo, hi = (star + _scale(base, p) for p in STAR_PERMIL_RANGE)
    return expect(min=lo, max=hi, exact=False, note=note + f"；星星获得倍率未识别，按 {STAR_PERMIL_RANGE[0] / 1000:g}–{STAR_PERMIL_RANGE[1] / 1000:g}×")


def _wappen_fields(prod: dict, times: int = 1) -> dict:
    """获得卡触发ワッペン：次数 -1、星星 +10×star_permil（次数为 0 时不触发）。"""
    left = prod.get("wappen_left")
    if left is None or prod.get("star") is None:
        return {}
    n = min(times, left)
    return {"produce.wappen_left": expect(left - n), "produce.star": _star_expect(prod, 10 * n, f"ワッペン ×{n}（{GAIN_BASIS['wappen.star'][1]}）")}


def _stage(prod: dict) -> dict | None:
    try:
        return schedule(prod["mode"]).stage(prod["step"])
    except Exception:
        return None


# ---------------------------------------------------------------- 初始牌组
# 偶像能力在开局把一张基础卡换成指定卡并强化（master ProduceEffectType_ProduceCardChangeUpgrade；换成哪张 master 里没有直接字段，
# 按 wiki「アピールの基本か距離感の基本をえいえいおーにチェンジして強化」补）。
IDOL_START_CHANGE = {
    "i_card-shro-3-007": (("p_card-00-act-0_001", "p_card-02-men-0_039"), "p_card-02-men-1_031", 1),
}
UNIQUE_CARD_PLUS_RANK = 3  # 固有卡在特訓段階 3 起为 + [wiki 固有スキル：特訓2段階までは初期。特訓3段階で+]


def initial_deck(idol_card_id: str, produce_id: str = "produce-007", *, limit_rank: int = 6, memories: list[dict] | None = None) -> list[Prediction]:
    """开局牌组：ProduceInitialDeck（按偶像おすすめ効果）→ ExamInitialDeck 的基础卡 + 偶像固有卡（特訓≥3 为 +）
    + 偶像能力的开局换卡（IDOL_START_CHANGE）+ 记忆带入的卡（{card_id, upgrade, customizations?}）。返回 [{card_id, upgrade, customized}]。"""
    c = catalog()
    idol = c.one("IdolCard", idol_card_id.replace("i_card-skin-", "i_card-"))
    deck_id = next((r["examInitialDeckId"] for r in c.rows("ProduceInitialDeck")
                    if r["produceId"] == produce_id and r["examEffectType"] == idol["examEffectType"]), idol.get("examInitialDeckId"))  # fmt: skip
    row = c.by_id("ExamInitialDeck", deck_id)[0]
    ups = row.get("produceCardUpgradeCounts") or []
    deck = [{"card_id": cid, "upgrade": ups[i] if i < len(ups) else 0, "customized": False} for i, cid in enumerate(row["produceCardIds"])]
    if idol.get("produceCardId"):
        deck.append({"card_id": idol["produceCardId"], "upgrade": 1 if limit_rank >= UNIQUE_CARD_PLUS_RANK else 0, "customized": False})
    change = IDOL_START_CHANGE.get(idol["id"])
    if change:
        sources, target, up = change
        i = next((i for i, d in enumerate(deck) if d["card_id"] in sources), None)
        if i is not None:
            deck[i] = {"card_id": target, "upgrade": up, "customized": False}
    for m in memories or []:
        deck.append({"card_id": m["card_id"], "upgrade": m.get("upgrade") or 0, "customized": bool(m.get("customizations"))})
    return deck


# ---------------------------------------------------------------- 支援卡加成（编成已知时）
def support_bonus(prod: dict, phase: str, context: dict | None = None) -> dict | None:
    """编成已知（produce.supports 每张有 card_id / level / limit_rank）时，这次时机触发的支援卡属性加成上界 {Vo, Da, Vi}。
    已触发次数没有记账（ledger 未接）时按「都还能触发」算上界；编成未知返回 None（调用方用 SUPPORT_SLACK）。"""
    sups = prod.get("supports") or []
    if not sups or any(_get(s, "level") is None or _get(s, "limit_rank") is None for s in sups):
        return None
    from gakumas_produce import supports as gs

    c = catalog()
    plan = c.one("IdolCard", prod["idol_card_id"])["planType"]
    out = {a: 0 for a in ATTRS}
    for s in sups:
        try:
            card = gs.compile_card(c, _get(s, "card_id"), _get(s, "level"), _get(s, "limit_rank"), plan)
        except Exception:
            return None
        for sk in card["skills"]:
            if sk["trigger"]["phaseType"] != "ProducePhaseType_" + phase:
                continue
            try:
                if not gs.trigger_matches(sk["trigger"], context or {}, c):
                    continue
            except Exception:
                continue
            kind = sk["effect"]["produceEffectType"]
            if kind in gs.SCALARS:
                out[gs.SCALARS[kind]] += sk["effect"]["effectValueMin"]
    return out


# ---------------------------------------------------------------- 各事件
def _lesson(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """LESSON {attr, sp, sub?}：主 / 副属性 × 成长率、体力、星星、P 点（master）；副属性未知时两种取并集。"""
    st = _stage(prod)
    attr, sp, sub = args.get("attr"), args.get("sp"), args.get("sub")
    if st is None or attr is None or sp is None:
        return []
    rows = [catalog().one("ProduceStepOpenLesson", k) for k in st["open_lesson_ids"]]
    enum = {"Vo": "Vocal", "Da": "Dance", "Vi": "Visual"}
    subs = [sub] if sub else [a for a in ATTRS if a != attr]
    growth = prod.get("growth_permil") or {}
    options = []
    for s in subs:
        row = next((r for r in rows if r["subParameterType"].endswith(enum[s]) and ("-sp-" in r["id"]) == bool(sp)), None)
        if row is None:
            continue
        gains = {attr: _scale(row["mainParameter"], growth.get(attr, 1000)), s: _scale(row["subParameter"], growth.get(s, 1000))}
        options.append((gains, row))
    if not options:
        return []
    row = options[0][1]
    support = support_bonus(prod, "LessonEnd", {"attribute": attr, "sp": bool(sp)})
    params = prod.get("params") or {}
    cap = prod.get("param_cap") or 3000
    slack = 0 if support is not None else SUPPORT_SLACK["lesson_sp" if sp else "lesson"]
    fields = {}
    for a in ATTRS:
        if params.get(a) is None:
            continue
        lo = min(_cap_add(params[a], g.get(a, 0) + (support or {}).get(a, 0), cap) for g, _ in options)
        hi = max(_cap_add(params[a], g.get(a, 0) + (support or {}).get(a, 0) + slack, cap) for g, _ in options)
        note = f"{attr} {'SP' if sp else '普通'}课程（{GAIN_BASIS['lesson.main_sub'][1][:40]}…）" + ("" if sub else "；副属性未读到，两种都算")
        fields[f"produce.params.{a}"] = expect(lo, note=note) if lo == hi else expect(min=lo, max=hi, exact=False, note=note)
    hp, mx = prod.get("stamina"), prod.get("max_stamina")
    if hp is not None:
        lo = max(0, hp - row["stamina"])
        hp_slack = 0 if support is not None else SUPPORT_HP_SLACK["lesson"]
        hi = min(mx or 99, lo + hp_slack)
        fields["produce.stamina"] = expect(lo) if hi == lo else expect(min=lo, max=hi, exact=False,
                                                                       note=f"课程体力 -{row['stamina']}；支援卡「レッスン終了時 体力回復」未知，上界 +{hp_slack}")  # fmt: skip
    fields["produce.star"] = _star_expect(prod, row["star"], GAIN_BASIS["lesson.star"][1])
    if prod.get("produce_point") is not None:
        fields["produce.produce_point"] = expect(prod["produce_point"] + st["base_p_points"])
    fields["produce.step"] = expect(prod["step"] + 1)
    return _pred(seq, fields, basis="master")


def _study(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """STUDY {attr, choice: A|B|trouble}：属性 + CLASS_PARAMETER（不套成长率）；A/B 体力 -5；眠気档加一张眠気；获得日 +1 张卡。"""
    from gakumas_produce import community_rules as rules

    attr, choice = args.get("attr"), args.get("choice")
    value = rules.CLASS_PARAMETER.get((prod.get("mode"), prod.get("step")))
    if attr is None or value is None:
        return []
    support = support_bonus(prod, "StepEventEnd", {})
    fields = _params_fields(prod, {attr: value}, "class", f"授業 {attr}+{value}（{GAIN_BASIS['class.param'][1][:30]}…）", support=support)
    hp = prod.get("stamina")
    if hp is not None and choice in ("A", "B", "trouble"):
        fields["produce.stamina"] = expect(max(0, hp - (0 if choice == "trouble" else 5)))
    gain_day = (prod.get("mode"), prod.get("step")) in rules.CLASS_GAIN_DAYS
    if _deck_known(prod):
        n = len(prod["deck"]) + (1 if gain_day else 0) + (1 if choice == "trouble" else 0)
        fields["produce.deck_size"] = expect(n, note="获得日 +1 张（三选一另记 CARD_ACQUIRE）" if gain_day else "セレクトチェンジ：张数不变")
    fields["produce.step"] = expect(prod["step"] + 1)
    return _pred(seq, fields, basis="community")


def _outing(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """OUTING {tier: paid|trouble|free}：回复最大体力 60%；付费档 -50P；眠気档 +眠気；饮料 / 卡另由获得事件记。"""
    from gakumas_produce import community_rules as rules

    hp, mx = prod.get("stamina"), prod.get("max_stamina")
    tier = args.get("tier")
    fields = {}
    if hp is not None and mx:
        fields["produce.stamina"] = expect(min(mx, hp + _scale(mx, rules.OUTING_RECOVER_PERMIL)), note=GAIN_BASIS["outing.stamina"][1])
    if prod.get("produce_point") is not None and tier is not None:
        fields["produce.produce_point"] = expect(prod["produce_point"] - (50 if tier == "paid" else 0))
    fields.update(_params_fields(prod, {}, "outing", "外出不加属性（支援卡「おでかけ終了時」技能除外）"))
    if prod.get("step") is not None:
        fields["produce.step"] = expect(prod["step"] + 1)
    return _pred(seq, fields, basis="community")


def _gift(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """GIFT（差し入れ）：+80P；SR/SSR 饮料一瓶与卡一张另由获得事件记。"""
    from gakumas_produce import community_rules as rules

    fields = {}
    if prod.get("produce_point") is not None:
        fields["produce.produce_point"] = expect(prod["produce_point"] + rules.PRESENT_POINTS, note=GAIN_BASIS["gift.produce_point"][1])
    fields.update(_params_fields(prod, {}, "gift", "差し入れ不加属性（支援卡「差し入れ開始時」技能除外）"))
    if prod.get("step") is not None:
        fields["produce.step"] = expect(prod["step"] + 1)
    return _pred(seq, fields, basis="community")


def _allowance(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """ALLOWANCE（活動支給）：内容只能观测；给了 P 点数（args.produce_point）时预测 P。"""
    fields = {}
    if args.get("produce_point") is not None and prod.get("produce_point") is not None:
        fields["produce.produce_point"] = expect(prod["produce_point"] + int(args["produce_point"]))
    return _pred(seq, fields, basis="observe_only")


def _rest(prod: dict, args: dict, seq: int) -> list[Prediction]:
    hp, mx = prod.get("stamina"), prod.get("max_stamina")
    if hp is None or not mx:
        return []
    fields = {"produce.stamina": expect(min(mx, hp + mx // 2), note=GAIN_BASIS["rest.stamina"][1])}
    if prod.get("step") is not None:
        fields["produce.step"] = expect(prod["step"] + 1)
    return _pred(seq, fields, basis="master")


def _deck_change(prod: dict, add: list[tuple[str, int]] = (), remove: list[tuple[str, int]] = (), note: str = "") -> dict:
    """牌组已知时给精确多重集；未知时只给张数变化（相对值不能核对，不给）。"""
    if not _deck_known(prod):
        return {}
    keys = deck_keys(prod["deck"])
    for cid, up in remove:
        k = _key(cid, up)
        if k not in keys:
            return {"produce.deck_size": expect(len(keys) + len(add) - len(remove), note=f"{note}；推算牌组里没有 {k}")}
        keys.remove(k)
    keys = sorted(keys + [_key(cid, up) for cid, up in add])
    return {"produce.deck": expect(keys, note=note), "produce.deck_size": expect(len(keys))}


def _card_acquire(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """CARD_ACQUIRE {card_id, upgrade, change?}：牌组 +1；不是セレクトチェンジ时触发ワッペン。"""
    cid = args.get("card_id")
    if cid is None:
        return []
    fields = _deck_change(prod, add=[(cid, args.get("upgrade") or 0)], note="获得卡（候选识别给出 card_id / upgrade）")
    if not args.get("change"):
        fields.update(_wappen_fields(prod))
    return _pred(seq, fields, due="any", basis="observe")


def _counter(prod: dict, key: str, seq: int, note: str, extra: dict | None = None) -> list[Prediction]:
    left = prod.get(key)
    fields = dict(extra or {})
    if left is not None:
        fields[f"produce.{key}"] = expect(max(0, left - 1), note=note)
    return _pred(seq, fields, due="SELECT_CARD", basis="master")


def _card_reroll(prod: dict, args: dict, seq: int) -> list[Prediction]:
    old = args.get("candidates")
    extra = {"page_fields.candidates": expect(None, exact=False, note=f"候选应整体换掉（刷新前：{old}）；不变说明刷新没生效")} if old else {}
    return _counter(prod, "rerolls_left", seq, "再抽選 -1（整局不回复 [wiki]）", extra)


def _card_exclude(prod: dict, args: dict, seq: int) -> list[Prediction]:
    extra = {}
    if args.get("card_id"):
        extra["produce.excluded"] = expect(sorted(set(prod.get("excluded") or []) | {args["card_id"]}))
    return _counter(prod, "excludes_left", seq, "カード除去 -1（整局不回复 [wiki]）", extra)


def _card_upgrade(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """CARD_UPGRADE {card_id, upgrade(强化前), source: consult|event|interval, price?}：牌组里这张 +1；相談 / 轮间扣 P。"""
    from gakumas_produce import community_rules as rules

    cid, up = args.get("card_id"), args.get("upgrade") or 0
    fields = _deck_change(prod, add=[(cid, up + 1)], remove=[(cid, up)], note="强化") if cid else {}
    src = args.get("source", "consult")
    if prod.get("produce_point") is not None and src in ("consult", "interval"):
        done = prod.get("upgrades_done") or 0
        price = args.get("price") or (rules.service_price(done) if src == "consult" else rules.interval_upgrade_price(done))
        fields["produce.produce_point"] = expect(prod["produce_point"] - price, note=f"{src} 强化 {price}P（community_rules）")
        if src == "consult":
            fields["produce.upgrades_done"] = expect(done + 1)
    return _pred(seq, fields, due="any", basis="community")


def _card_delete(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """CARD_DELETE {card_id, upgrade, source: consult|event, price?}：牌组 -1；相談扣 P。"""
    from gakumas_produce import community_rules as rules

    cid, up = args.get("card_id"), args.get("upgrade") or 0
    fields = _deck_change(prod, remove=[(cid, up)], note="删卡") if cid else {}
    if prod.get("produce_point") is not None and args.get("source", "consult") == "consult":
        done = prod.get("deletes_done") or 0
        price = args.get("price") or rules.service_price(done)
        fields["produce.produce_point"] = expect(prod["produce_point"] - price, note=f"相談删卡 {price}P")
        fields["produce.deletes_done"] = expect(done + 1)
    return _pred(seq, fields, due="any", basis="community")


def _card_change(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """CARD_CHANGE {remove_card_id, remove_upgrade, card_id}：换出一张，换入的继承强化、定制清空 [wiki セレクトチェンジ]。"""
    rid, rup, cid = args.get("remove_card_id"), args.get("remove_upgrade") or 0, args.get("card_id")
    if not rid or not cid:
        return []
    return _pred(seq, _deck_change(prod, add=[(cid, rup)], remove=[(rid, rup)], note="セレクトチェンジ：强化继承"), due="any", basis="community")


def _card_auto_process(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """CARD_AUTO_PROCESS {exam: 1|2|3}：选拔①② 通过后随机删 2 张「基本」卡 [wiki]：只能预测张数，具体哪两张 pending。"""
    from gakumas_produce import community_rules as rules

    n = rules.SELECTION_BASIC_REMOVALS.get(args.get("exam"), 0)
    if not n or not _deck_known(prod):
        return []
    basics = [c for c in prod["deck"] if "基本" in catalog().card(_get(c, "card_id"), 0)["name"]]
    fields = {"produce.deck_size": expect(len(prod["deck"]) - min(n, len(basics)), note=GAIN_BASIS["auto_process"][1])}
    return _pred(seq, fields, due="DECK_LIST", basis="community", expires_after=None)


def _card_customize(prod: dict, args: dict, seq: int) -> list[Prediction]:
    fields = {}
    if args.get("price") and prod.get("produce_point") is not None:
        fields["produce.produce_point"] = expect(prod["produce_point"] - int(args["price"]), note="特別指導定制价格（画面读出）")
    i = args.get("deck_index")
    if i is not None and _deck_known(prod) and 0 <= i < len(prod["deck"]):
        fields[f"produce.deck.{i}.customized"] = expect(True)
    return _pred(seq, fields, due="any", basis="observe")


def _shop_buy(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """SHOP_BUY {kind: card|drink, card_id|drink_id, upgrade, price}：P - 实付价；物品进牌组 / 饮料。"""
    fields = {}
    if args.get("price") is not None and prod.get("produce_point") is not None:
        fields["produce.produce_point"] = expect(prod["produce_point"] - int(args["price"]), note="相談实付价（货架识别）")
    if args.get("kind") == "card" and args.get("card_id"):
        fields.update(_deck_change(prod, add=[(args["card_id"], args.get("upgrade") or 0)], note="相談买卡"))
        fields.update(_wappen_fields(prod))
    elif args.get("kind") == "drink" and args.get("drink_id"):
        fields.update(_drink_fields(prod, add=args["drink_id"]))
    return _pred(seq, fields, due="any", basis="observe")


def _drink_fields(prod: dict, add: str | None = None, remove: int | None = None) -> dict:
    drinks = list(prod.get("drinks") or [])
    if remove is not None:
        if not 0 <= remove < len(drinks):
            return {}
        drinks.pop(remove)
    if add is not None:
        cap = prod.get("drink_capacity") or 4  # H.I.F 培育页都是 4 格（识别离线统计）
        if len(drinks) >= cap:
            return {"produce.drinks": expect(sorted(drinks), note="饮料已满：会进 PDRINK_MAX，新饮料要另有 DRINK_DISCARD")}
        drinks.append(add)
    return {"produce.drinks": expect(sorted(drinks))}


def _drink_acquire(prod: dict, args: dict, seq: int) -> list[Prediction]:
    if not args.get("drink_id"):
        return []
    return _pred(seq, _drink_fields(prod, add=args["drink_id"]), due="any", basis="observe")


def _drink_use(prod: dict, args: dict, seq: int) -> list[Prediction]:
    idx = args.get("slot")
    drinks = list(prod.get("drinks") or [])
    if idx is None and args.get("drink_id") in drinks:
        idx = drinks.index(args["drink_id"])
    if idx is None:
        return []
    return _pred(seq, _drink_fields(prod, remove=idx), due="any", basis="action")


def _drink_discard(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """DRINK_DISCARD {discard: [i], receive_drink_id?}：丢掉第 i 瓶，收下新饮料（discard 为空 = 不收）。"""
    discard = args.get("discard") or []
    if not discard:
        return _pred(seq, {"produce.drinks": expect(sorted(prod.get("drinks") or []), note="不收新饮料")}, due="any", basis="action")
    fields = _drink_fields(prod, remove=discard[0])
    if args.get("receive_drink_id") and "produce.drinks" in fields:
        drinks = fields["produce.drinks"].value + [args["receive_drink_id"]]
        fields["produce.drinks"] = expect(sorted(drinks))
    return _pred(seq, fields, due="any", basis="action")


def _pitem(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """PITEM_ACQUIRE / PITEM_CUSTOMIZE {item_id, replaces?}：items 加入 / 替换（master ProduceItem / ProduceCustomizeItem 的 ID）。"""
    item = args.get("item_id")
    if not item:
        return []
    items = list(prod.get("items") or [])
    if args.get("replaces") in items:
        items.remove(args["replaces"])
    items.append(item)
    return _pred(seq, {"produce.items": expect(sorted(items))}, due="any", basis="master")


def _wappen(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """WAPPEN_TRIGGER {gain?}：次数 -1，星星 + gain（演出读出）或 +10×star_permil。"""
    if args.get("gain") is not None and prod.get("star") is not None:
        fields = {"produce.star": expect(prod["star"] + int(args["gain"]), note="ワッペン演出读出的星星")}
        if prod.get("wappen_left") is not None:
            fields["produce.wappen_left"] = expect(max(0, prod["wappen_left"] - 1))
        return _pred(seq, fields, due="any", basis="observe")
    return _pred(seq, _wappen_fields(prod), due="any", basis="community")


def _exam_start(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """EXAM_START {exam_label}：考试开始回复最大体力 50%（截断）[wiki]，本战 R2 不回复；剩余回合 = 本场回合数。"""
    hp, mx = prod.get("stamina"), prod.get("max_stamina")
    label = args.get("exam_label") or ""
    fields = {}
    if hp is not None and mx:
        fields["exam.hp"] = expect(hp if label == "r2" else min(mx, hp + mx // 2), note="考前回复 50%（R2 不回复）[wiki]")
    turns = args.get("turns") or {"r1": 9, "r2": 12}.get(label)
    if turns:
        fields["exam.remaining_turns"] = expect(turns, note="本场回合数（本战 R1 9 / R2 12 [wiki]；选拔按考试配置）")
    fields["exam.score"] = expect(0)
    return _pred(seq, fields, due="HIF_EXAM", basis="community", expires_after=2)


def _exam_result(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """EXAM_RESULT {exam: 1|2|3|"r1"|"r2", score, turn_scores?}：星星、属性奖励、R1 的 P 点（社区公式 / 样本）。"""
    from gakumas_produce import community_rules as rules

    exam, score = args.get("exam"), args.get("score")
    if score is None or exam is None:
        return []
    permil = prod.get("star_permil") or 1000
    fields = {}
    star = prod.get("star")
    if exam in (1, 2, 3):
        if star is not None:
            fields["produce.star"] = expect(star + rules.star_gain(rules.selection_star_base(exam, score), permil), note=GAIN_BASIS["exam.selection_star"][1])
        params = prod.get("params") or {}
        growth = prod.get("growth_permil") or {}
        if args.get("turn_scores"):
            split = rules.selection_param_split(exam, args["turn_scores"])
            gains = {a: _scale(v, growth.get(a, 1000)) for a, v in split.items()}
            fields.update(_params_fields(prod, gains, "exam", "选拔属性奖励（selection_param_split × 成长率）"))
        else:
            base = rules.selection_param_base(exam, score)
            cap = prod.get("param_cap") or 3000
            for a in ATTRS:
                if params.get(a) is not None:
                    hi = _cap_add(params[a], _scale(base, growth.get(a, 1000)) + SUPPORT_SLACK["exam"], cap)
                    fields[f"produce.params.{a}"] = expect(min=params[a], max=hi, exact=False,
                                                           note=f"选拔属性奖励合计 {base}，按回合得分分配（缺各属性回合分，给区间）")  # fmt: skip
    elif exam in ("r1", "r2"):
        rnd = 1 if exam == "r1" else 2
        if star is not None:
            base = rules.finals_star_base(rnd, score)
            fields["produce.star"] = expect(min=star + rules.star_gain(max(0, base - 3), permil), max=star + rules.star_gain(base + 3, permil), exact=False,
                                            note=GAIN_BASIS["exam.finals_star"][1])  # fmt: skip
        if rnd == 1 and prod.get("produce_point") is not None:
            pts = rules.r1_points(score)
            fields["produce.produce_point"] = expect(min=prod["produce_point"] + max(0, pts - 5), max=prod["produce_point"] + pts + 5, exact=False,
                                                     note=GAIN_BASIS["exam.r1_points"][1])  # fmt: skip
    return _pred(seq, fields, due="ACTION_SELECT", basis="community", expires_after=None)


def _interval_buy(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """ROUND_INTERVAL_BUY {service: recover|card|drink|upgrade, price?, hp_gain?, card_id?, drink_id?}。"""
    from gakumas_produce import community_rules as rules

    fields = {}
    p, hp, mx = prod.get("produce_point"), prod.get("stamina"), prod.get("max_stamina")
    service = args.get("service")
    if service == "recover" and hp is not None and mx:
        gain = args.get("hp_gain") or 2
        new_hp = min(mx, hp + gain)
        fields["produce.stamina"] = expect(new_hp, note="轮间回复：每 2 体力 10P [community_rules.interval_recover_price]")
        if p is not None:
            fields["produce.produce_point"] = expect(p - (args.get("price") or rules.interval_recover_price(new_hp - hp)))
    elif p is not None and args.get("price") is not None:
        fields["produce.produce_point"] = expect(p - int(args["price"]), note="轮间实付价（货架识别）")
    if service == "card" and args.get("card_id"):
        fields.update(_deck_change(prod, add=[(args["card_id"], args.get("upgrade") or 0)], note="轮间买卡"))
        fields.update(_wappen_fields(prod))
    if service == "drink" and args.get("drink_id"):
        fields.update(_drink_fields(prod, add=args["drink_id"]))
    return _pred(seq, fields, due="HIF_ROUND_INTERVAL", basis="community")


def _produce_start(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """PRODUCE_START {idol_card_id, produce_id, limit_rank, memories}：开局牌组（initial_deck），日程第 1 天，饮料空。"""
    idol = args.get("idol_card_id") or prod.get("idol_card_id")
    if not idol:
        return []
    deck = initial_deck(idol, args.get("produce_id") or prod.get("mode") or "produce-007", limit_rank=args.get("limit_rank", 6),
                        memories=args.get("memories"))  # fmt: skip
    fields = {"produce.deck": expect(deck_keys(deck), note="ExamInitialDeck + 固有卡 + 开局换卡 + 记忆（master）"),
              "produce.deck_size": expect(len(deck)), "produce.step": expect(1), "produce.drinks": expect([])}  # fmt: skip
    return _pred(seq, fields, due="any", basis="master", expires_after=None)


def _day_advance(prod: dict, args: dict, seq: int) -> list[Prediction]:
    if prod.get("step") is None:
        return []
    return _pred(seq, {"produce.step": expect(prod["step"] + 1)}, due="ACTION_SELECT", basis="master")


def _unmodeled(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """UNMODELED_CHANGE：互验反推出来的事件，以读数为准，不再预测。"""
    return []


def _deck_review(prod: dict, args: dict, seq: int) -> list[Prediction]:
    """DECK_REVIEW（主动查看牌组页，origin=review）：读到的牌组就是真值，不再预测。"""
    return []


PREDICTORS = {
    "PRODUCE_START": _produce_start, "DAY_ADVANCE": _day_advance, "LESSON": _lesson, "STUDY": _study, "OUTING": _outing,
    "GIFT": _gift, "ALLOWANCE": _allowance, "REST": _rest, "CARD_ACQUIRE": _card_acquire, "CARD_REROLL": _card_reroll,
    "CARD_EXCLUDE": _card_exclude, "CARD_UPGRADE": _card_upgrade, "CARD_DELETE": _card_delete, "CARD_CHANGE": _card_change,
    "CARD_AUTO_PROCESS": _card_auto_process, "CARD_CUSTOMIZE": _card_customize, "SHOP_BUY": _shop_buy,
    "DRINK_ACQUIRE": _drink_acquire, "DRINK_USE": _drink_use, "DRINK_DISCARD": _drink_discard,
    "PITEM_ACQUIRE": _pitem, "PITEM_CUSTOMIZE": _pitem, "WAPPEN_TRIGGER": _wappen, "EXAM_START": _exam_start,
    "EXAM_RESULT": _exam_result, "ROUND_INTERVAL_BUY": _interval_buy, "UNMODELED_CHANGE": _unmodeled, "DECK_REVIEW": _deck_review,
}  # fmt: skip
assert set(PREDICTORS) == set(EVENT_KINDS)


def predict(state, event) -> list[Prediction]:
    """事件之后下一个观测点的期望。state：GameState（或含 produce 的 dict / ProduceState）；event：Event 或 dict。
    推不出（字段缺、事件参数缺）的字段不给预测，交给不变量与观测。"""
    kind, args, seq = _event_parts(event)
    fn = PREDICTORS.get(kind)
    if fn is None:
        return []
    prod = _produce(state) if not isinstance(state, ProduceState) else state.to_dict()
    return fn(prod, args, seq)


def check_value(exp, observed) -> bool:
    """一个期望（Expect 或同字段的 dict）和读数是否一致。和 Expect.admits 的区别：读数为 None 时算不一致（审计用）。"""
    if observed is None:
        return False
    if isinstance(exp, dict):
        exp = Expect(**{k: v for k, v in exp.items() if k in Expect.__dataclass_fields__})
    if exp.exact and isinstance(exp.value, list) and isinstance(observed, list):
        return sorted(map(str, exp.value)) == sorted(map(str, observed))
    if exp.exact and exp.value is not None and not exp.tolerance and isinstance(observed, (int, float)):
        return observed == exp.value
    return exp.admits(observed)


__all__ = ["EVENT_KINDS", "GAIN_BASIS", "SUPPORT_SLACK", "check_value", "deck_keys", "expect", "initial_deck", "make_event", "predict",
           "support_bonus"]
