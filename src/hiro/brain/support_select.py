# 来源：digital-hiro src/hiro/brain/support_select.py @ c3fc27f（GPL-3.0-or-later）；支援卡技能改走 AssetSource.support_effects()
"""支援卡编成选择（用户 2026-11-02：支援卡、回忆都不用おまかせ）。自己 5 格 + 租借 1 格，由这里选。

方法（社区 いおん ep02 / 05 / 08，docs/hif-playbook.md）：
- 一张支援卡的价值 = 它在**我们这条路线**上每个词条的效果 × 实际触发次数，加上支援事件给的东西（属性 / 卡 / P 道具）。
  触发次数取自我们的实机日志（第十四、十五局选拔 + 本战，ROUTE），不是理论最大值（ep02：少触发一次就扣掉）。
- SP 发生率词条不直接给属性，而是决定主属性课程有多少次走 SP（SP 课程本身多给属性、还触发所有「SP 课程结束时」词条），
  所以在编成层面算：主属性 SP 率合计 → 主属性课程走 SP 的概率 → 所有卡的 SP 触发次数（ep08：集中 SP，4-2-0 / 4-1-1）。
- 支援事件给的技能卡：牌组用得上的加分，污染循环的扣分（ep05：送卡污染可能抵消属性收益）。查过 master：没有支援事件给
  せのびの魔法 / 届いて / デイドリ 等核心卡（决策-11 的缺口只能靠选拔里拿），所以这一项只看 夏夜 / 焼き芋 这类卡。
- 约束：主属性 4 张，其余 2 张（4-2-0 或 4-1-1）；租借最多 1 张；同一张卡不重复。
- 主属性（choose_main）：三个属性各当一次主属性组编成，默认用偶像的主属性；别的属性当主属性总价值高出 REVERSE_MIN_GAIN（100）
  以上才「逆练」（ep02：作者的经验线是 100 分，逆练后被弱化的属性要靠课程 / 考试补，也会限制拿卡）。
  ep08 的稳妥属性线（一属性约 2000、二属性 1400、三属性 1100）写进方案说明，提醒副属性要靠课程和考试补齐。

数值单位：属性点（Vo / Da / Vi 合计）。P 点、体力等按 VALUE_PER 折算（粗估，写在下面）。离线没有培育阶段模拟器，
这里的分数只用于编成之间比较；绝对值不是预测。

输入（识屏读库存，reports/live/support-inventory.json）：
    {"cards": [{"support_card_id", "level", "limit_rank"（"SupportCardLevelLimitRank__4" 等，可省略 = 按等级推）}],
     "rentals": [{"support_card_id", "level", "limit_rank", "slot"（租借列表里的位置）, "owner"?}]}
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache

ATTR_OF = {"SupportCardType_Vocal": "Vo", "SupportCardType_Dance": "Da", "SupportCardType_Visual": "Vi", "SupportCardType_Assist": None}
NAME = {"Vo": "vocal", "Da": "dance", "Vi": "visual"}
MAIN_ATTR = "Da"  # Campus 広：主 Da（用户 / 决策-07）
MAIN_COUNT = 4  # 4-2-0 / 4-1-1 [ep08][playbook]
PLAN = "ProducePlanType_Plan2"  # 広 是 ロジック（Plan2，やる気）；master IdolCard i_card-shro-3-007.planType
# 识屏 / 库存里的 plan 字段（游戏卡面写法或 master 枚举）→ master 枚举。总控 2026-11-02：编成里有「プラン不一致」的卡开不了局，必须硬过滤
PLAN_ALIASES = {"センス": "ProducePlanType_Plan1", "sense": "ProducePlanType_Plan1", "plan1": "ProducePlanType_Plan1",
                "ロジック": "ProducePlanType_Plan2", "logic": "ProducePlanType_Plan2", "plan2": "ProducePlanType_Plan2",
                "アノマリー": "ProducePlanType_Plan3", "anomaly": "ProducePlanType_Plan3", "plan3": "ProducePlanType_Plan3",
                "フリー": "ProducePlanType_Common", "free": "ProducePlanType_Common", "common": "ProducePlanType_Common"}  # fmt: skip

# 每局（选拔 + 本战）各类事件的次数，第十四 / 十五局日志统计 + 本战按日程补（docs/tasks/决策-12-支援卡编成.md §2）
ROUTE = {
    "lessons": {"Da": 7, "Vo": 0, "Vi": 0},  # 只上主属性课程：选拔 Da 6（SP 5 + 普通 1），本战 1
    "school": 5,  # 授業（end_step_event_school；选拔 4–5）
    "activity": 2,  # 活動支給（end_step_event_activity；粗估）
    "present": 3,  # 差し入れ（start_present；选拔 2 + 本战 1）
    "refresh": 4,  # 外出（start_refresh；选拔 3 + 本战 1）
    "shop": 2,  # 相談 / 本战商店（start_shop）
    "audition": 5,  # 考试结束（选拔 3 + 本战 2）
    "customize": 4,  # 特別指導定制级数（第十五局起每次最多 2 张 × 若干级；粗估）
    "upgrade": 4, "upgrade_mental": 2,  # 强化（相談 / 外出 / 事件）
    "delete": 2, "delete_mental": 1, "change": 2,
    "get_card": 14, "get_card_mental": 7, "get_card_active": 7, "get_card_aggressive": 6, "get_card_review": 1, "get_card_block": 2, "get_ssr": 1,
    "get_item": 3, "get_drink": 20, "buy_drink": 1,
}
SP_BASE = 0.25  # 没有 SP 率词条时主属性课程走 SP 的概率（粗估，待实机统计）
SP_LESSON_EXTRA = 45  # SP 课程比普通课程多给的主属性（第十四局：SP Da+135 / 普通约 +90）
GROWTH_BASE = {"Da": 1100, "Vo": 450, "Vi": 450}  # 成长率作用的基数：一局里课程 / 授業给的属性（粗估）
MAIN_LESSONS, GROWTH_MAIN, GROWTH_OTHER = 7, 1100, 450  # 换主属性时 ROUTE["lessons"] / GROWTH_BASE 按这几个数重排（同上，粗估）
REVERSE_MIN_GAIN = 100  # 逆练门槛 [ep02]：别的属性当主属性总价值至少高这么多才换
SAFE_LINES = (2000, 1400, 1100)  # 稳妥属性线（一 / 二 / 三属性）[ep08]
VALUE_PER = {"p": 0.4, "hp": 1.5, "max_hp": 3.0, "exam_upgrade_pct": 0.08, "drink_discount": 30.0}  # 折算到属性点（粗估）
LESSON_P_BASE = 40  # 一次课程奖励的 P 点（粗估）
EVENT_CARD_GOOD, EVENT_CARD_BAD = 20.0, -25.0  # 事件送卡：用得上 / 污染循环（ep02：一张卡约 20 点；ep05：污染可能抵消属性）


def _trigger_count(tid: str, sp: dict) -> float:
    """触发 ID → 每局次数。sp：各属性课程走 SP 的次数。"""
    for a, n in NAME.items():
        if f"lesson_{n}_sp" in tid:
            return sp[a]
        if f"lesson_{n}_normal" in tid:
            return ROUTE["lessons"][a] - sp[a]
        if f"lesson_{n}" in tid:
            return ROUTE["lessons"][a]
    rules = [
        ("produce_start", 1), ("end_lesson-lesson_sp", sum(sp.values())), ("end_lesson_before_present-lesson", sum(ROUTE["lessons"].values())),
        ("end_lesson-lesson", sum(ROUTE["lessons"].values())), ("start_present", ROUTE["present"]), ("end_step_event_activity", ROUTE["activity"]),
        ("end_step_event_school", ROUTE["school"]), ("end_audition", ROUTE["audition"]), ("start_shop", ROUTE["shop"]),
        ("start_refresh", ROUTE["refresh"]), ("customize_produce_card", ROUTE["customize"]), ("start_customize", 2),
        ("upgrade_produce_card-0000_0000-p_card_search-mental", ROUTE["upgrade_mental"]), ("upgrade_produce_card", ROUTE["upgrade"]),
        ("delete_produce_card-0000_0000-p_card_search-mental", ROUTE["delete_mental"]), ("delete_produce_card", ROUTE["delete"]),
        ("change_produce_card", ROUTE["change"]), ("exam_card_play_aggressive", ROUTE["get_card_aggressive"]),
        ("exam_review", ROUTE["get_card_review"]), ("exam_block", ROUTE["get_card_block"]), ("p_card_search-ssr", ROUTE["get_ssr"]),
        ("p_card_search-mental_skill", ROUTE["get_card_mental"]), ("p_card_search-active_skill", ROUTE["get_card_active"]),
        ("get_produce_card", ROUTE["get_card"]), ("get_produce_item", ROUTE["get_item"]), ("buy_shop_item_produce_drink", ROUTE["buy_drink"]),
        ("get_produce_drink", ROUTE["get_drink"]),
    ]  # fmt: skip
    for key, n in rules:
        if key in tid:
            return n
    return 0.0


@lru_cache(maxsize=None)
def _compiled(card_id: str, level: int, limit_rank: str | None, plan: str | None = None) -> tuple:
    """(属性, ((效果类型, 效果值, 触发 ID, 次数上限), ...))。素材源给不出技能时返回 (属性, ())。"""
    from hiro.brain.formation import _support_skills
    from hiro.state.context import catalog

    card = catalog().one("SupportCard", card_id)
    attr = ATTR_OF.get(card["type"])
    rank = limit_rank or None  # 和主仓库一样：空当成没给（突破段是 SupportCardLevelLimitRank__N 这样的枚举名）
    skills = _support_skills(card_id, level, rank, plan or PLAN)
    return attr, tuple((s["effect_type"], s.get("value"), s.get("trigger_id"), s.get("limit")) for s in skills)


@lru_cache(maxsize=None)
def _events(card_id: str, level: int) -> tuple:
    """支援事件（等级够了才有）给的东西：(("param", 属性, 值) | ("card", card_id) | ("pitem", id) | ("upgrade",) | ("p", 值), ...)。"""
    from hiro.state.context import catalog

    c = catalog()
    out = []
    for ev in c.rows("ProduceEventSupportCard"):
        if ev["supportCardId"] != card_id or ev["supportCardLevel"] > level:
            continue
        for d in c.by_id("ProduceStepEventDetail", ev["produceStepEventDetailId"]):
            for e in d.get("produceEffectIds") or []:
                if m := re.search(r"produce_card-(p_card-[\w-]+?)-\d$", e):
                    out.append(("card", m.group(1)))
                elif m := re.search(r"(vocal|dance|visual)_addition-(\d+)_", e):
                    out.append(("param", {v: k for k, v in NAME.items()}[m.group(1)], int(m.group(2))))
                elif "produce_item" in e:
                    out.append(("pitem", e))
                elif "produce_card_upgrade" in e:
                    out.append(("upgrade",))
                elif m := re.search(r"produce_point_addition-(\d+)", e):
                    out.append(("p", int(m.group(1))))
    return tuple(out)


def event_card_value(card_id: str) -> float:
    """支援事件送的技能卡：社区高评价 / 小牌组核心加分；冲突、小牌组不该拿（可复用的非核心）扣分；一次性的中性。"""
    from hiro.brain.deciders import TIER_RANK, _tier_rank, is_conflict
    from hiro.state.knowledge import SMALL_DECK_CORE, small_deck_reject

    if is_conflict(card_id) or small_deck_reject(card_id):
        return EVENT_CARD_BAD
    if card_id in SMALL_DECK_CORE or _tier_rank(card_id) >= TIER_RANK["A"]:
        return EVENT_CARD_GOOD
    return 0.0


@dataclass
class Slot:
    card_id: str
    level: int
    limit_rank: str | None = None
    rental: bool = False
    rental_slot: int | None = None
    name: str = ""
    attr: str | None = None
    skills: tuple = ()
    events: tuple = ()
    sp_rate: dict = field(default_factory=dict)  # 属性 → SP 发生率（千分比）


def _norm_plan(v) -> str | None:
    if not v:
        return None
    v = str(v)
    if v.startswith("ProducePlanType_"):
        return v
    return PLAN_ALIASES.get(v.strip().lower()) or PLAN_ALIASES.get(v.strip()) or "?" + v


def plan_mismatch(entry: dict) -> str | None:
    """这张卡和当前方案（PLAN）不一致的原因；一致返回 None。master 的 planType 和库存里的 plan 字段（识屏读的）任一不一致都算：
    游戏在开局第 2 步给这些卡标「プラン不一致」，编成里有就开不了局（总控 2026-11-02，第一次选的 9 号编成就是这样）。
    master 查不到这张卡也算不一致（无法确认方案）。"""
    from hiro.state.context import catalog

    ok = ("ProducePlanType_Common", PLAN)
    try:
        master = catalog().one("SupportCard", entry.get("support_card_id"))["planType"]
    except Exception:
        return "master 查不到这张卡，无法确认方案"
    if master not in ok:
        return f"プラン不一致（master {master}）"
    seen = _norm_plan(entry.get("plan"))
    if seen is not None and seen not in ok:
        return f"プラン不一致（库存标 {entry.get('plan')}）"
    if entry.get("plan_mismatch"):
        return "プラン不一致（识屏看到标记）"
    return None


def normalize_inventory(inventory: dict) -> dict:
    """tools.support_inventory 写的是 {cards: {卡 ID: {...}}}；这里统一成 {cards: [{support_card_id, ...}], rentals: [...]}。"""
    out = dict(inventory or {})
    for k in ("cards", "rentals"):
        v = out.get(k)
        if isinstance(v, dict):
            out[k] = [{"support_card_id": cid, **(e or {})} for cid, e in v.items()]
    return out


def make_slot(entry: dict, rental: bool = False) -> Slot | None:
    from hiro.state.context import catalog

    if plan_mismatch(entry):
        return None  # 硬过滤：别的路线专用卡进编成开不了局
    cid = entry.get("support_card_id")
    card = catalog().one("SupportCard", cid)
    level = int(entry.get("level") or 1)
    attr, skills = _compiled(cid, level, entry.get("limit_rank"), PLAN)
    sp = {}
    for etype, value, _tid, _lim in skills:
        for a, n in NAME.items():
            if etype == f"ProduceEffectType_Lesson{n.capitalize()}SpChangeRatePermilAddition":
                sp[a] = sp.get(a, 0) + value
        if etype == "ProduceEffectType_LessonSpChangeRatePermilAddition":
            for a in NAME:
                sp[a] = sp.get(a, 0) + value
    return Slot(cid, level, entry.get("limit_rank"), rental, entry.get("slot"), card["name"], attr, skills, _events(cid, level), sp)


def sp_counts(slots: list[Slot]) -> dict:
    """编成 → 各属性课程走 SP 的次数（SP 率合计封顶 100%）。"""
    out = {}
    for a, n in ROUTE["lessons"].items():
        rate = min(1.0, SP_BASE + sum(s.sp_rate.get(a, 0) for s in slots) / 1000)
        out[a] = n * rate
    return out


def card_value(slot: Slot, sp: dict) -> tuple[float, list[str]]:
    """一张卡在给定 SP 次数下的价值与明细。"""
    total, notes = 0.0, []

    def add(v: float, why: str):
        nonlocal total
        if v:
            total += v
            notes.append(f"{why} {v:+.0f}")

    for etype, value, tid, limit in slot.skills:
        n = _trigger_count(tid, sp)
        if limit:
            n = min(n, limit)
        if m := re.fullmatch(r"ProduceEffectType_(Vocal|Dance|Visual)Addition", etype):
            add(value * n, f"{m.group(1)}+{value}×{n:g}")
        elif m := re.fullmatch(r"ProduceEffectType_(Vocal|Dance|Visual)GrowthRateAddition", etype):
            a = {"Vocal": "Vo", "Dance": "Da", "Visual": "Vi"}[m.group(1)]
            add(value / 1000 * GROWTH_BASE[a], f"{a} 成长率 +{value / 10:g}%")
        elif etype == "ProduceEffectType_SupportCardEventParameterAdditionValueUp":
            base = sum(e[2] for e in slot.events if e[0] == "param")
            add(base * value / 1000, f"事件属性 +{value / 10:g}%")
        elif etype == "ProduceEffectType_SupportCardProduceCardUpgradeProbabilityUp":
            add(value / 10 * VALUE_PER["exam_upgrade_pct"], "考试手牌强化率")
        elif etype == "ProduceEffectType_LessonPresentProducePointUp":
            # 千分比：课程奖励的 P 点 +value‰（课程奖励基数 LESSON_P_BASE，粗估）
            add(value / 1000 * LESSON_P_BASE * n * VALUE_PER["p"], f"课程 P +{value / 10:g}%×{n:g}")
        elif etype == "ProduceEffectType_ProducePointAdditionDisableTrigger":
            add(value * n * VALUE_PER["p"], f"P+{value}×{n:g}")
        elif etype == "ProduceEffectType_StaminaRecoverFix":
            add(value * n * VALUE_PER["hp"], f"体力+{value}×{n:g}")
        elif etype == "ProduceEffectType_MaxStaminaAddition":
            add(value * VALUE_PER["max_hp"], f"体力上限+{value}")
        elif etype == "ProduceEffectType_ShopProduceDrinkPriceDiscountMultiple":
            add(VALUE_PER["drink_discount"], "饮料折扣")
    for e in slot.events:
        if e[0] == "param":
            add(e[2], f"事件 {e[1]}+{e[2]}")
        elif e[0] == "card":
            add(event_card_value(e[1]), f"事件送卡 {e[1]}")
        elif e[0] == "p":
            add(e[1] * VALUE_PER["p"], f"事件 P+{e[1]}")
    return total, notes


def formation_value(slots: list[Slot]) -> tuple[float, dict]:
    sp = sp_counts(slots)
    total = sum(card_value(s, sp)[0] for s in slots)
    # SP 课程本身多给的主属性
    total += sum(sp[a] for a in sp) * SP_LESSON_EXTRA
    return total, sp


def _valid(slots: list[Slot]) -> bool:
    ids = [s.card_id for s in slots]
    return len(set(ids)) == len(ids) and sum(s.rental for s in slots) <= 1 and sum(s.attr == MAIN_ATTR for s in slots) == MAIN_COUNT


def choose(inventory: dict, *, size: int = 6) -> dict:
    """库存 + 租借列表 → 编成方案：{own: [...], rental: {...} | None, value, sp, notes, runner_up}。
    先按单卡价值贪心组一个合法编成（主属性 4 张），再做单张互换的局部搜索直到不再提升（SP 率在编成层面计价，单卡排序会低估 SP 卡）。"""
    inventory = normalize_inventory(inventory)
    own = [s for s in (make_slot(e) for e in inventory.get("cards") or []) if s]
    rent = [s for s in (make_slot(e, rental=True) for e in inventory.get("rentals") or []) if s]
    pool = own + rent
    excluded = [{"support_card_id": e.get("support_card_id"), "rental": k == "rentals", "why": plan_mismatch(e)}
                for k in ("cards", "rentals") for e in inventory.get(k) or [] if plan_mismatch(e)]  # fmt: skip
    if not pool:
        return {"error": "库存为空", "excluded": excluded}
    sp0 = sp_counts([])
    single = {id(s): card_value(s, sp0)[0] + (sum(s.sp_rate.values()) / 1000 * ROUTE["lessons"][MAIN_ATTR] * SP_LESSON_EXTRA) for s in pool}
    main = sorted((s for s in pool if s.attr == MAIN_ATTR), key=lambda s: -single[id(s)])
    other = sorted((s for s in pool if s.attr != MAIN_ATTR), key=lambda s: -single[id(s)])

    def greedy() -> list[Slot]:
        pick: list[Slot] = []
        for group, want in ((main, MAIN_COUNT), (other, size - MAIN_COUNT)):
            for s in group:
                if sum(x.attr == MAIN_ATTR for x in pick) >= MAIN_COUNT and s.attr == MAIN_ATTR:
                    break
                if len([x for x in pick if (x.attr == MAIN_ATTR) == (s.attr == MAIN_ATTR)]) >= want:
                    break
                trial = pick + [s]
                if len({x.card_id for x in trial}) == len(trial) and sum(x.rental for x in trial) <= 1:
                    pick = trial
        return pick

    best = greedy()
    if len(best) < size or not _valid(best):
        return {"error": f"凑不出 {MAIN_COUNT}-{size - MAIN_COUNT} 编成（主属性 {MAIN_ATTR} {sum(s.attr == MAIN_ATTR for s in pool)} 张）",
                "partial": [s.name for s in best]}  # fmt: skip
    best_v, _ = formation_value(best)
    improved = True
    while improved:
        improved = False
        for i in range(size):
            for cand in pool:
                if cand in best:
                    continue
                trial = best[:i] + [cand] + best[i + 1:]
                if not _valid(trial):
                    continue
                v, _ = formation_value(trial)
                if v > best_v + 1e-6:
                    best, best_v, improved = trial, v, True
    # 第二方案：去掉第一方案价值贡献最大的那张后重新局部搜索的结果（给「差多少」）
    runner = None
    sp = sp_counts(best)
    vals = {id(s): card_value(s, sp)[0] for s in best}
    top = max(best, key=lambda s: vals[id(s)])
    alt_pool = [s for s in pool if s is not top]
    alt = [s for s in best if s is not top]
    for cand in sorted(alt_pool, key=lambda s: -single[id(s)]):
        if cand not in alt and _valid(alt + [cand]):
            runner_v, _ = formation_value(alt + [cand])
            runner = {"without": top.name, "replace_with": cand.name, "value": round(runner_v), "gap": round(best_v - runner_v)}
            break
    return {
        "main_attr": MAIN_ATTR, "value": round(best_v), "sp_lessons": {a: round(v, 1) for a, v in sp.items()},
        "own": [_describe(s, sp) for s in best if not s.rental],
        "rental": next((_describe(s, sp) for s in best if s.rental), None),
        "counts": {a: sum(s.attr == a for s in best) for a in ("Vo", "Da", "Vi")},
        "runner_up": runner, "excluded": excluded,
    }  # fmt: skip


@contextmanager
def using(main_attr: str | None = None, plan: str | None = None, main_count: int | None = None):
    """临时换主属性 / 方案（换偶像、比较逆练）：ROUTE["lessons"] 和 GROWTH_BASE 按主属性重排，结束后还原。
    默认值（Da / Plan2）下重排结果与原表相同。main_count：编成里主属性要几张（只选自己 5 张、租借格开局再租主属性时用 3）。"""
    global MAIN_ATTR, PLAN, GROWTH_BASE, MAIN_COUNT
    saved = (MAIN_ATTR, PLAN, GROWTH_BASE, ROUTE["lessons"], MAIN_COUNT)
    MAIN_ATTR, PLAN = main_attr or MAIN_ATTR, _norm_plan(plan) or PLAN
    MAIN_COUNT = main_count or MAIN_COUNT
    ROUTE["lessons"] = {a: (MAIN_LESSONS if a == MAIN_ATTR else 0) for a in NAME}
    GROWTH_BASE = {a: (GROWTH_MAIN if a == MAIN_ATTR else GROWTH_OTHER) for a in NAME}
    try:
        yield
    finally:
        MAIN_ATTR, PLAN, GROWTH_BASE, ROUTE["lessons"], MAIN_COUNT = saved


def choose_main(inventory: dict, *, plan: str | None = None, default_attr: str | None = None, size: int = 6,
                main_count: int | None = None, min_gain: float = REVERSE_MIN_GAIN) -> dict:  # fmt: skip
    """三个属性各当一次主属性组编成，按 ep02 的逆练门槛选主属性。返回 choose 的结果，另带
    main_attr_candidates（各属性的价值 / 凑不出的原因）与 main_attr_why。default_attr 不给时取价值最高的。"""
    results = {}
    for a in NAME:
        with using(a, plan, main_count):
            results[a] = choose(inventory, size=size)
    ok = {a: r for a, r in results.items() if "error" not in r}
    cands = {a: (r["value"] if "error" not in r else r["error"]) for a, r in results.items()}
    if not ok:
        return {"error": "三个属性都凑不出 4-2 编成", "main_attr_candidates": cands}
    best = max(ok, key=lambda a: ok[a]["value"])
    if default_attr in ok and best != default_attr and ok[best]["value"] - ok[default_attr]["value"] < min_gain:
        pick, why = default_attr, (f"不逆练：{best} 当主属性只高 {ok[best]['value'] - ok[default_attr]['value']}，"
                                   f"不到门槛 {min_gain:g} [ep02]")  # fmt: skip
    elif default_attr in ok and best != default_attr:
        pick, why = best, f"逆练 {best}：比默认 {default_attr} 高 {ok[best]['value'] - ok[default_attr]['value']}（门槛 {min_gain:g} [ep02]）"
    elif default_attr and default_attr not in ok:
        pick, why = best, f"默认主属性 {default_attr} 凑不出 4 张（{cands[default_attr]}），改用价值最高的 {best}"
    else:
        pick, why = best, f"主属性 {best}：价值最高"
    out = dict(ok[pick], main_attr_candidates=cands, main_attr_why=why)
    lines = dict(zip(("一", "二", "三"), SAFE_LINES))
    out["notes"] = [f"4 张集中在 {pick}，其余属性靠课程和考试补：稳妥线 一属性 {lines['一']} / 二属性 {lines['二']} / 三属性 {lines['三']} [ep08]"]
    return out


def _describe(s: Slot, sp: dict) -> dict:
    v, notes = card_value(s, sp)
    return {"support_card_id": s.card_id, "name": s.name, "attr": s.attr, "level": s.level, "limit_rank": s.limit_rank, "rental": s.rental,
            "rental_slot": s.rental_slot, "sp_rate": s.sp_rate, "value": round(v), "why": notes[:8]}  # fmt: skip


def choose_rental(rentals: list[dict], own_plan: list[dict]) -> dict | None:
    """租借列表里挑一张：在已定的自己 5 张上，加哪张租借让编成价值最高（主属性凑满 4 张）。给执行侧（租借列表页）用。"""
    own = [s for s in (make_slot(e) for e in own_plan) if s]
    best, best_v = None, None
    for e in rentals:
        s = make_slot(e, rental=True)
        if s is None or not _valid(own + [s]):
            continue
        v, _ = formation_value(own + [s])
        if best_v is None or v > best_v:
            best, best_v = s, v
    if best is None:
        return None
    return {**_describe(best, sp_counts(own + [best])), "formation_value": round(best_v)}


def rental_scorer(own_entries: list[dict], main_attr: str | None = None, plan: str | None = None):
    """给租借列表执行侧用的打分函数：row（read_rental_list 的一行）→ 加上这张租借后整个编成的价值；
    认不出卡、方案不一致、凑不成 4-2（不是主属性）时返回 None。同一张卡同一等级只算一次。"""
    cache: dict = {}

    def score(row: dict) -> float | None:
        card = row.get("card") or {}
        cid, level = card.get("support_card_id"), card.get("level")
        if not cid or not level:
            return None
        key = (cid, level)
        if key not in cache:
            with using(main_attr, plan):
                own = [s for s in (make_slot(e) for e in own_entries) if s]
                slot = make_slot({"support_card_id": cid, "level": level}, rental=True)
                cache[key] = formation_value(own + [slot])[0] if slot is not None and _valid(own + [slot]) else None
        return cache[key]

    return score


def evaluate(cards: list[dict]) -> dict:
    """一套现成编成（例如用户原有的编成 1）的价值，和 choose 的结果同一刻度，用来比较「现在 vs 方案」。"""
    bad = [{"support_card_id": e.get("support_card_id"), "why": plan_mismatch(e)} for e in cards if plan_mismatch(e)]
    slots = [s for s in (make_slot(e, rental=bool(e.get("rental"))) for e in cards) if s]
    v, sp = formation_value(slots)
    # 有不一致的卡：这套编成开不了局，标 valid=False（价值只按剩下的卡算，仅供参考）
    return {"value": round(v), "valid": not bad, "plan_mismatch": bad, "sp_lessons": {a: round(x, 1) for a, x in sp.items()},
            "counts": {a: sum(s.attr == a for s in slots) for a in ("Vo", "Da", "Vi")},
            "cards": [_describe(s, sp) for s in slots]}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    """python -m hiro.brain.support_select [库存.json] [方案.json] [--current 编成号]：读库存，写编成方案（含理由、第二方案差距；
    库存里有 sets[] 时同时评估用户现有编成）。"""
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser()
    ap.add_argument("inventory", nargs="?", default="reports/live/support-inventory.json")
    ap.add_argument("out", nargs="?", default="reports/live/support-plan.json")
    args = ap.parse_args(argv)
    inv = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    plan = choose(inv)
    plan["current_sets"] = {str(s.get("index")): evaluate(s.get("cards") or []) for s in inv.get("sets") or []}
    Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in plan.items() if k not in ("own", "current_sets")}, ensure_ascii=False))
    for o in plan.get("own") or []:
        print(f"  {o['attr']} {o['name']} Lv{o['level']} 价值 {o['value']}：{'；'.join(o['why'][:4])}")
    if plan.get("rental"):
        print(f"  租借 {plan['rental']['name']}（第 {plan['rental']['rental_slot']} 格）价值 {plan['rental']['value']}")
    for k, v in plan["current_sets"].items():
        print(f"  现有编成 {k}：价值 {v['value']}（{v['counts']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
