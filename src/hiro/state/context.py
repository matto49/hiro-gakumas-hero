"""给 LLM 的培育上下文：公共局面 + 页面预演。只算确定的事实，判断交给 LLM。

数据来源：主数据（AssetSource.master_db()，经 hiro.state.catalog）、日程（AssetSource.schedule()）、
考试要求 / 倍率 / 星星换算（AssetSource.exam_requirements()，开源默认实现没有 → 相关行写「未知」）、
公开攻略里的规则（hiro.brain.hif_rules）与社区攻略（hiro.state.knowledge）。
「当前属性/星星下的倍率」是按现在的数值算的，到考试时数值还会变，提示里会注明。
"""

import math
import threading

from hiro.brain import hif_rules as rules
from hiro.state.catalog import Catalog, CatalogUnavailable  # noqa: F401
from hiro.state.catalog import catalog as _catalog
from hiro.state.knowledge import SELECTION_TARGETS, TURN_SPLIT, YARUKI_CARDS, idol_trend
from hiro.state.produce import ATTRS, FINALS, SELECTION, LessonOption, ProduceState

TIER = {"Great": "◎", "Good": "○", "Normal": "△", "Bad": "×"}
ASSESS_PERMIL = 110  # ProduceSetting p_setting-7/8 produceAuditionTrendAssessmentPermilUpper/Lower
EXAMS = {
    SELECTION: {7: ("选拔①", "s1"), 13: ("选拔②", "s2"), 20: ("选拔③", "s3")},
    FINALS: {7: ("本战R1", "r1"), 9: ("本战R2", "r2")},
}
OPTION_CN = {"training": "公開レッスン", "class": "授業", "outing": "外出", "gift": "差し入れ", "shop": "相談",
             "special_guidance": "特別指導", "exam": "考试", "round1": "R1", "interval": "轮间", "round2": "R2"}  # fmt: skip


_LOCAL = threading.local()


def catalog() -> Catalog:
    """主数据目录（每个线程一份连接）。拿不到主数据时抛 CatalogUnavailable。"""
    return _catalog()


class ExamRequirementsUnavailable(RuntimeError):
    """没有考试要求 / 倍率换算（AssetSource.exam_requirements() 返回 None）：相关提示写「未知」。"""


def _requirements() -> dict:
    """AssetSource.exam_requirements("hif") 的结果，约定是一组函数：
    - preview(mode, position, params, idol_card_id) -> {"turns", "source": {"battle_config": {"vocal","dance","visual"}}, "parameter_bonus_permil": {Vo,Da,Vi}}
    - assessment(value, requirement) -> "Great" / "Good" / "Normal" / "Bad"
    - star_baseline(mode, position, idol_card_id) -> int；star_factor(star, baseline) -> float
    - exam_bonus(mode, position, params, star, idol_card_id) -> {Vo,Da,Vi: 千分比}
    拿不到时抛 ExamRequirementsUnavailable。"""
    cache = getattr(_LOCAL, "requirements", None)
    if cache is None:
        from hiro import plugins

        src = plugins.get("asset_source")
        fn = getattr(src, "exam_requirements", None) if src is not None else None
        cache = _LOCAL.requirements = (fn("hif") if fn else None) or {}
    if not cache:
        raise ExamRequirementsUnavailable("没有考试要求数据（AssetSource.exam_requirements 返回 None）")
    return cache


def hif_preview(mode: str, position: int, params: dict, *, idol_card_id: str | None = None, catalog=None) -> dict:
    return _requirements()["preview"](mode, position, params, idol_card_id)


def assessment(value: int, requirement: int, *_permil) -> str:
    return _requirements()["assessment"](value, requirement)


class _Multiplier:
    def star_baseline(self, mode, position, idol_card_id, catalog=None):
        return _requirements()["star_baseline"](mode, position, idol_card_id)

    def star_factor(self, star, baseline):
        return _requirements()["star_factor"](star, baseline)

    def exam_bonus(self, mode, position, params, star, idol_card_id):
        return _requirements()["exam_bonus"](mode, position, params, star, idol_card_id)


multiplier = _Multiplier()


class Schedule:
    """AssetSource.schedule(mode) 的结果：每个日程位置一项 {day, options, kind, training_focus?, open_lesson_ids?}（位置从 1 起）。"""

    def __init__(self, stages: list[dict]):
        self.stages = stages

    def stage(self, position: int) -> dict:
        return self.stages[position - 1]

    def export(self) -> dict:
        return {"stages": self.stages}


class ScheduleUnavailable(RuntimeError):
    pass


def schedule(mode: str) -> Schedule:
    cache = getattr(_LOCAL, "schedules", None)
    if cache is None:
        cache = _LOCAL.schedules = {}
    if mode not in cache:
        from hiro import plugins

        src = plugins.get("asset_source")
        stages = src.schedule(mode) if src is not None else None
        if not stages:
            raise ScheduleUnavailable(f"没有日程数据（AssetSource.schedule({mode!r}) 返回 None）")
        cache[mode] = Schedule(stages)
    return cache[mode]


def _scale(value: int, permil: int) -> int:
    return value * permil // 1000


def card_name(card_id: str, upgrade: int = 0) -> str:
    return catalog().card(card_id, 0)["name"] + "+" * upgrade


def drink_name(drink_id: str | None) -> str:
    """饮料名；None / 查不到的 id 返回「未知饮料」（第十八局选拔：持有饮料一格认不出，饮料溢出决策器在这里抛异常 → KAA 默认跳过新饮料）。"""
    if not drink_id:
        return "未知饮料"
    try:
        return catalog().one("ProduceDrink", drink_id)["name"]
    except Exception:
        return f"未知饮料（{drink_id}）"


# ---------------------------------------------------------------- 考试
def tier_gap(value: int, requirement: int) -> dict:
    """评定分档与升到上一档还差多少（客户端 GetAssessmentType 的阈值）。"""
    tier = assessment(value, requirement, ASSESS_PERMIL, ASSESS_PERMIL)
    great = math.ceil(requirement * (1000 + ASSESS_PERMIL) / 1000)
    normal = math.ceil(requirement * (1000 - ASSESS_PERMIL) / 1000)
    need = {"Good": great - value, "Normal": requirement + 1 - value, "Bad": normal - value}.get(tier)
    nxt = {"Good": "◎", "Normal": "○", "Bad": "△"}.get(tier)
    out = {"值": value, "基准": requirement, "评定": TIER[tier]}
    if need is not None:
        out[f"到{nxt}还差"] = max(1, need)
    return out


def params_complete(params: dict | None) -> bool:
    """三项属性都读到了（第十九局本战第 1 天：Da 2296 读成 3296 被不变量拦下，params 缺 Da → hif_preview 抛 Unsupported → 决策器 Wait）。"""
    return bool(params) and all(isinstance(params.get(a), (int, float)) for a in ATTRS)


def exam_brief(state: ProduceState, mode: str, position: int) -> dict:
    label, key = EXAMS[mode][position]
    if not params_complete(state.params):
        return {
            "场次": label,
            "日程位置": position,
            "说明": f"属性读数不全（{sorted(a for a in ATTRS if a not in (state.params or {}))} 没读到），跳过审查 / 倍率估算",
        }
    try:
        prev = hif_preview(mode, position, state.params, idol_card_id=state.idol_card_id, catalog=catalog())
    except ExamRequirementsUnavailable:
        return {
            "场次": label,
            "日程位置": position,
            "说明": "考试要求 / 倍率数据未知（没有接 exam_requirements 插件），不估审查评定和倍率",
        }
    cfg = prev["source"]["battle_config"]
    req = {"Vo": cfg["vocal"], "Da": cfg["dance"], "Vi": cfg["visual"]}
    kind, order = idol_trend(state.idol_card_id)
    split = dict(zip(order, TURN_SPLIT[kind][key]))
    baseline = multiplier.star_baseline(mode, position, state.idol_card_id, catalog())
    factor = multiplier.star_factor(state.star, baseline)
    final = multiplier.exam_bonus(mode, position, state.params, state.star, state.idol_card_id)
    out = {
        "场次": label,
        "日程位置": position,
        "回合": f"{prev['turns']} 回合（" + " / ".join(f"{a} {split[a]}" for a in order) + "）",
        "三项审查": {a: tier_gap(state.params[a], req[a]) for a in ATTRS},
        "星星": {"当前": state.star, "基准": baseline, "星星倍率": round(factor, 3)},
        "按当前数值的最终倍率": {a: f"{final[a] / 1000:.2f}×" for a in ATTRS},
    }
    if any(v["评定"] in ("△", "×") for v in out["三项审查"].values()):
        out["警告"] = "有一项 △/×，三项倍率都会被压低"
    if mode == SELECTION:
        n = int(key[1])
        param_full, star_full = SELECTION_TARGETS[n]
        out["目标分"] = {"属性奖励拿满": param_full, "星星奖励拿满": star_full}
        out["过线分"] = {1: 6100, 2: 50000, 3: 137000}[n]
    return out


def upcoming_exams(state: ProduceState) -> list[tuple[str, int]]:
    out = [(state.mode, p) for p in EXAMS[state.mode] if p >= state.step]
    if state.mode == SELECTION:
        out += [(FINALS, 7), (FINALS, 9)]
    return out


# ---------------------------------------------------------------- 公共局面
def deck_summary(state: ProduceState) -> dict:
    from hiro.brain.cards import parse_tags  # noqa: F401

    if not state.deck:  # 培育中牌组不可能为空：空 = 识别还没读到
        return {"状态": "牌组未识别（不是 0 张）：不要按张数、已持有或「必拿还缺」推理，只看候选本身和社区评级"}
    rows, missing = [], [v["name"] for k, v in YARUKI_CARDS.items() if v.get("must")]
    sleep = 0
    for c in state.deck:
        row = catalog().card(c.card_id, c.upgrade)
        if row["category"] == "ProduceCardCategory_Trouble":
            sleep += 1
        know = YARUKI_CARDS.get(c.card_id)
        if know and know["name"] in missing:
            missing.remove(know["name"])
        entry = card_name(c.card_id, c.upgrade) + ("（定制）" if c.customized else "")
        if know:
            entry += f"〔{know['tier'] or '—'}{'·必拿' if know.get('must') else ''}〕"
        rows.append(entry)
    return {"张数": len(state.deck), "眠気": sleep, "卡": rows, "必拿还缺": missing or None}


def schedule_ahead(state: ProduceState, days: int = 7) -> list[dict] | str:
    try:
        sch = schedule(state.mode)
    except ScheduleUnavailable:
        return UNKNOWN
    out = []
    for pos in range(state.step, min(state.step + days, len(sch.export()["stages"]) + 1)):
        st = sch.stage(pos)
        row = {"位置": pos, "天": st["day"], "可选": [OPTION_CN.get(o, o) for o in st["options"]]}
        if st.get("training_focus"):
            row["课程"] = "星星重视" if st["training_focus"] == "star" else "普通"
            lessons = state.lessons.get(pos)
            if lessons:
                row["副属性"] = {o.main: o.sub for o in lessons}
        out.append(row)
        if st["kind"] in ("exam", "round1"):
            break
    return out


UNKNOWN = "未识别"


def _is_hajime_nia(mode: str | None) -> bool:
    from hiro.state.modes import HAJIME, NIA, family

    return family(mode) in (HAJIME, NIA)


def situation_hajime_nia(state: ProduceState, unknown: set[str] | frozenset = frozenset()) -> dict:
    """初 / N.I.A 的局面文本：只有这两个模式真有的东西。没有星星（用户 11-17：完全不用处理）、ワッペン、选拔 / 本战、H.I.F 的考试倍率。"""
    from hiro.state.modes import NIA
    from hiro.state.modes import mode as _mode
    from hiro.state.modes import steps as _steps

    try:
        m = _mode(state.mode)
        name = f"{'N.I.A' if m.family == NIA else '初'} {m.difficulty.upper()}"
    except KeyError:
        m, name = None, state.mode
    try:
        total = _steps(state.mode)
    except Exception:  # noqa: BLE001  主数据不可用
        total = UNKNOWN

    def val(key: str, value=None):
        return UNKNOWN if key in unknown else (getattr(state, key) if value is None else value)

    if not state.deck:
        deck = "牌组未识别（不是 0 张）：不要按张数推理"
    else:
        from hiro.state.knowledge import is_once

        counts: dict[str, int] = {}
        for c in state.deck:
            label = card_name(c.card_id, c.upgrade) + ("（定制）" if c.customized else "")
            counts[label] = counts.get(label, 0) + 1
        once = {
            card_name(c.card_id, c.upgrade) + ("（定制）" if c.customized else ""): is_once(c.card_id)
            for c in state.deck
        }
        deck = {
            "张数": len(state.deck),
            "卡": [
                f"{k}{' ×' + str(n) if n > 1 else ''}（{'一次性' if once[k] else '可复用'}）" for k, n in counts.items()
            ],
        }
    res = {"体力": UNKNOWN if "stamina" in unknown else f"{state.stamina}/{state.max_stamina}", "P点": val("produce_point"),
           "饮料": val("drinks", [_drink_entry(d) for d in state.drinks] + [f"空位 {state.drink_capacity - len(state.drinks)}"]),
           "禁用剩余": val("excludes_left"), "刷新剩余": val("rerolls_left")}  # fmt: skip
    if m is not None and m.family == NIA:
        res["投票数"] = UNKNOWN if state.votes is None else state.votes
        res["剩余休息次数"] = UNKNOWN if state.rest_left is None else state.rest_left
    out = {
        "进度": {"模式": name, "第几步": val("step"), "总步数": total},
        "资源": res,
        "属性": {a: (state.params[a] if isinstance(state.params.get(a), (int, float)) else "未读到") for a in ATTRS},
        "主属性": state.main_attr or UNKNOWN,
        "牌组": deck,
    }  # fmt: skip
    shown = {"drinks", "excludes_left", "rerolls_left", "deck", "step", "stamina", "produce_point"}
    hidden = {
        "lessons",
        "items",
        "excluded",
        "upgrades_done",
        "deletes_done",
        "drink_capacity",
        "star",
        "star_permil",
        "wappen_left",
        "param_cap",
    }
    if rest := sorted(set(unknown) - shown - hidden):
        out["未识别字段"] = rest
    return out


def situation(state: ProduceState, unknown: set[str] | frozenset = frozenset()) -> dict:
    """unknown：从未被观测到、ProduceState 用默认值补上的字段（GameState.unobserved_produce_fields()），显示为「未识别」，不当事实。
    初 / N.I.A 走 situation_hajime_nia（没有星星、选拔 / 本战）。"""
    if _is_hajime_nia(state.mode):
        return situation_hajime_nia(state, unknown)
    exams = upcoming_exams(state)

    def val(key: str, value=None):
        return UNKNOWN if key in unknown else (getattr(state, key) if value is None else value)

    drinks = [_drink_entry(d) for d in state.drinks] + [f"空位 {state.drink_capacity - len(state.drinks)}"]
    out = {
        "进度": {"模式": "选拔" if state.mode == SELECTION else "本战", "日程位置": state.step,
                 "接下来": schedule_ahead(state)},
        "P点用途": ("选拔的 P 点和饮料都不带进本战，不要无目的存钱 [ep07]；" if state.mode == SELECTION else "本战第 6 天相談要给轮间留 P 点；")
                   + f"从今天起还能花钱（相談 / 特別指導）的日子：{spending_days(state) or '没有了'}",
        "资源": {"体力": f"{state.stamina}/{state.max_stamina}", "P点": state.produce_point, "饮料": val("drinks", drinks),
                 "ワッペン剩余": val("wappen_left"), "禁用剩余": val("excludes_left"), "刷新剩余": val("rerolls_left")},
        "属性": {a: (f"{state.params[a]}（成长率 {state.growth_permil[a] / 10:.1f}%）" if isinstance(state.params.get(a), (int, float)) else "未读到")
                 for a in ATTRS} | {"上限": val("param_cap")},
        "主属性": "{0}（{1}；社区：课程只上这一项，赌 SP）".format(*main_attr_basis(state)),  # noqa: UP030
        "星星": {"当前": state.star,
                 "获得倍率": f"{UNKNOWN}（预演里的星星按 1× 估，实际多半更高）" if "star_permil" in unknown else f"{state.star_permil / 1000:g}×",
                 "离 R2 基准 800": max(0, 800 - state.star) or "已达到（继续拿，最多到 3 倍）",
                 "说明": "星星越多本战倍率越高；选拔考试的星星倍率封顶不代表星星没用"},
        "下一场考试": exam_brief(state, *exams[0]) if exams else None,
        "本战（按当前数值）": [exam_brief(state, FINALS, p) for p in (7, 9)] if state.mode == SELECTION else None,
        "评定的优先级": "本战 R1/R2 的评定决定最终倍率；选拔考试只需过线并拿满奖励分（见目标分），不必为选拔的 ◎ 偏离主属性" if state.mode == SELECTION else None,
        "牌组": deck_summary(state),
    }  # fmt: skip
    shown = {"drinks", "wappen_left", "excludes_left", "rerolls_left", "param_cap", "star_permil", "deck"}
    if rest := sorted(
        set(unknown) - shown - {"lessons", "items", "excluded", "upgrades_done", "deletes_done", "drink_capacity"}
    ):
        out["未识别字段"] = rest
    return out


SUPPORT_TYPE = {"SupportCardType_Vocal": "Vo", "SupportCardType_Dance": "Da", "SupportCardType_Visual": "Vi"}


def support_counts(state: ProduceState) -> dict | None:
    """支援卡编成的属性分布（Assist 不计）；未识别时 None。"""
    if not state.supports:
        return None
    counts = dict.fromkeys(ATTRS, 0)
    for s in state.supports:
        try:
            row = catalog().one("SupportCard", s.support_card_id)
        except Exception:  # master 快照里没有（新卡）：不计入，避免整个上下文失败
            continue
        if row["type"] in SUPPORT_TYPE:
            counts[SUPPORT_TYPE[row["type"]]] += 1
    return counts


def main_attr_basis(state: ProduceState) -> tuple[str, str]:
    """主属性与依据：显式指定 > 支援卡编成里最多的属性（同数看成长率）> 成长率最高（推测）。
    社区：主属性由支援卡编成决定（4-2 / 4-1-1 编成里 4 张的那项），课程只上这一项。"""
    if state.main_attr in ATTRS:
        return state.main_attr, "运行配置指定"
    counts = support_counts(state)
    if counts and any(counts.values()):
        best = max(ATTRS, key=lambda a: (counts[a], state.growth_permil[a]))
        return best, "支援卡编成 " + " / ".join(f"{a} {counts[a]}" for a in ATTRS)
    best = max(ATTRS, key=lambda a: state.growth_permil[a])
    return best, "支援卡未识别，按成长率最高推测，可能不准"


def main_attr(state: ProduceState) -> str:
    return main_attr_basis(state)[0]


def _drink_entry(drink_id: str) -> str:
    from hiro.state.knowledge import drink_label

    return drink_name(drink_id) + f"〔{drink_label(drink_id)}〕"


# ---------------------------------------------------------------- 选行动预演
def _apply_params(state: ProduceState, gains: dict) -> tuple[dict, dict]:
    after = dict(state.params)
    real = {}
    for a, g in gains.items():
        if not isinstance(state.params.get(a), (int, float)):
            continue  # 这一项没读到（被不变量拦下）：不估它的增量
        after[a] = min(state.param_cap, after[a] + g)
        real[a] = after[a] - state.params[a]
    return after, real


# 没有倍率换算数据（开源版：exam_requirements 不可用）时，R1 倍率增量用「R1 各属性回合数 × 属性增量」近似（协调 2026-11-07）。
# 依据：考前说明页 属性 → 倍率 基本线性（同属性斜率近乎恒定，Vo≈1.41 / Da≈1.20 %/点），属性间斜率差约 20%，比回合数 4:3:2 的差小；
# 只用于同一步各选项之间比大小。Win2 选课两次选了主属性 Da，R1（Vo 4 回合）倍率比 A5b 低 15%。行为变化，默认关；HIRO_R1_TURN_PROXY=1 打开
R1_TURN_PROXY = __import__("os").environ.get("HIRO_R1_TURN_PROXY") == "1"


def r1_turn_weighted_gain(state: ProduceState, delta: dict) -> int | None:
    """本战：Σ R1 该属性回合数 × 属性增量（到上限为止）。不是本战、属性读数不全、偶像没有流行顺序数据时 None。"""
    gains = {a: delta[a] for a in ATTRS if delta.get(a)}
    if state.mode != FINALS or not gains or not params_complete(state.params):
        return None
    try:
        kind, order = idol_trend(state.idol_card_id)
    except (KeyError, IndexError, AttributeError, TypeError):
        return None
    turns = dict(zip(order, TURN_SPLIT[kind]["r1"]))
    return sum(turns[a] * (min(state.param_cap, state.params[a] + g) - state.params[a]) for a, g in gains.items())


def r1_bonus_gain(state: ProduceState, delta: dict) -> int | None:
    """本战：加上 delta（属性增量）后，R1「每回合属性倍率 × 该属性回合数」总和的增量（hif_preview 的 parameter_bonus_permil，
    回合数按偶像流行顺序 × TURN_SPLIT；只算属性，不含星星）。本战课程 / 授業选属性用（协调 2026-11-04：和数据确认审计共用）。
    不是本战、属性读数不全、没有属性增量时 None。"""
    gains = {a: delta[a] for a in ATTRS if delta.get(a)}
    if state.mode != FINALS or not gains or not params_complete(state.params):
        return None
    kind, order = idol_trend(state.idol_card_id)
    turns = dict(zip(order, TURN_SPLIT[kind]["r1"]))

    def total(params: dict) -> int:
        b = hif_preview(FINALS, 7, params, idol_card_id=state.idol_card_id, catalog=catalog())["parameter_bonus_permil"]
        return sum(b[a] * turns[a] for a in ATTRS)

    after = {a: min(state.param_cap, state.params[a] + gains.get(a, 0)) for a in ATTRS}
    try:
        return total(after) - total(state.params)
    except ExamRequirementsUnavailable:
        return None  # 没有倍率换算：本战选课退回主属性 / 流行顺序规则


def _tier_changes(state: ProduceState, after: dict) -> list[str]:
    """属性变化带来的评定变化（下一场考试与本战 R1）。属性读数不全时跳过（返回空），不抛。"""
    out = []
    if not params_complete(state.params) or not params_complete(after):
        return out
    try:
        _requirements()
    except ExamRequirementsUnavailable:
        return out
    for mode, pos in upcoming_exams(state)[:1] + ([(FINALS, 7)] if state.mode == SELECTION else []):
        label = EXAMS[mode][pos][0]
        cfg = hif_preview(mode, pos, state.params, idol_card_id=state.idol_card_id, catalog=catalog())["source"][
            "battle_config"
        ]
        req = {"Vo": cfg["vocal"], "Da": cfg["dance"], "Vi": cfg["visual"]}
        for a in ATTRS:
            t0 = TIER[assessment(state.params[a], req[a], ASSESS_PERMIL, ASSESS_PERMIL)]
            t1 = TIER[assessment(after[a], req[a], ASSESS_PERMIL, ASSESS_PERMIL)]
            if t0 != t1:
                out.append(f"{label} {a} {t0}→{t1}")
    return out


def _hp_note(state: ProduceState, hp_after: int) -> str:
    exams = upcoming_exams(state)
    if not exams or exams[0][0] != state.mode:
        return f"体力 {hp_after}/{state.max_stamina}"
    mode, pos = exams[0]
    if mode == FINALS and pos == 9:
        return f"体力 {hp_after}/{state.max_stamina}"
    recover = state.max_stamina // 2  # 考前回复最大体力 50%（H.I.F 加成另算）
    days = pos - state.step - 1
    return f"体力 {hp_after}/{state.max_stamina}；距{EXAMS[mode][pos][0]}还有 {days} 天行动，考前回复约 +{recover}"


def exam_tomorrow(state: ProduceState) -> bool:
    """明天（下一个日程位置）就是有考前回复的考试：选拔①②③、本战 R1（R2 没有考前回复）。"""
    nxt = state.step + 1
    return nxt in EXAMS.get(state.mode, {}) and not (state.mode == FINALS and nxt == 9)


def rest_real_gain(state: ProduceState) -> tuple[int, int, int]:
    """休息的实际收益：(今天回复量, 不休息时开考体力, 休息时开考体力)。明天不是考试时后两项为今天之后的体力。
    考试开始自动回复最大体力 50%（小数切り捨て，wiki）：考前一天体力已过半时，休息多回的体力会被封顶吃掉。"""
    half = state.max_stamina // 2
    rested = min(state.max_stamina, state.stamina + half)
    if not exam_tomorrow(state):
        return rested - state.stamina, state.stamina, rested
    return rested - state.stamina, min(state.max_stamina, state.stamina + half), min(state.max_stamina, rested + half)


def spending_days(state: ProduceState) -> list[int]:
    """本模式里从今天起还能花 P 点的日子（日程上有相談 / 特別指導 / 外出付费档的位置）。"""
    try:
        sch = schedule(state.mode)
    except ScheduleUnavailable:
        return []
    out = []
    for pos in range(state.step, len(sch.export()["stages"]) + 1):
        if {"shop", "special_guidance"} & set(sch.stage(pos)["options"]):
            out.append(pos)
    return out


def _likely_sub(state: ProduceState, main: str) -> tuple[str, str]:
    """副属性没提供时，按社区统计取最可能的一项（剩余两项里流行顺序靠前的约 75%）。"""
    _, order = idol_trend(state.idol_card_id)
    weights = rules.sub_parameter_weights(main, order)
    sub = max(weights, key=weights.get)
    return sub, f"副属性未读到，按社区统计最可能是 {sub}（约 {weights[sub]:.0%}）"


def preview_lessons(
    state: ProduceState, lessons: list[LessonOption], unknown: set[str] | frozenset = frozenset()
) -> list[dict]:
    st = schedule(state.mode).stage(state.step)
    rows = [catalog().one("ProduceStepOpenLesson", k) for k in st["open_lesson_ids"]]
    out = []
    for o in lessons:
        sub, sub_note = (o.sub, None) if o.sub else _likely_sub(state, o.main)
        for sp in [o.sp] if o.sp is not None else [True, False]:
            row = next(r for r in rows if r["subParameterType"].endswith(_ENUM[sub]) and ("-sp-" in r["id"]) == sp)
            gains = {o.main: _scale(row["mainParameter"], state.growth_permil[o.main]),
                     sub: _scale(row["subParameter"], state.growth_permil[sub])}  # fmt: skip
            after, real = _apply_params(state, gains)
            star = _scale(row["star"], state.star_permil)
            hp = state.stamina - row["stamina"]
            entry = {
                "option": "training", "attr": o.main, "sp": sp,
                "选项": f"{o.main} 课程（副 {sub}，{'SP' if sp else '非 SP'}）" + ("〔当天才知道是否 SP，两种都列出〕" if o.sp is None else ""),
                "属性": " ".join(f"{a}+{v}" for a, v in real.items()),
                "星星": f"+{star} → {state.star + star}" + ("（获得倍率未识别，按 1× 估，实际多半更高）" if "star_permil" in unknown else ""),
                "P点": f"+{st['base_p_points']}",
                "体力": f"-{row['stamina']}" + ("（不够）" if hp < 0 else ""),
                "之后": _hp_note(state, hp),
                "delta": {**real, "star": star, "stamina": -row["stamina"], "produce_point": st["base_p_points"]},
            }  # fmt: skip
            if sub_note:
                entry["副属性"] = sub_note
            if any(real[a] < gains[a] for a in real):
                entry["注意"] = (
                    "可能撞到上限（属性上限未识别，按 3000 算）"
                    if "param_cap" in unknown
                    else "有属性撞到上限，溢出部分浪费"
                )
            if changes := _tier_changes(state, after):
                entry["评定变化"] = changes
            out.append(entry)
    return out


_ENUM = {"Vo": "Vocal", "Da": "Dance", "Vi": "Visual"}
LESSON_HP = {8: True, 6: False}  # 公開レッスン 体力消耗：SP 8、普通 6 [seesaa]


def _class_entry(state: ProduceState, st: dict, a: str, unknown: set[str] | frozenset = frozenset()) -> dict:
    value = rules.CLASS_PARAMETER[(state.mode, st["day"])]
    gain = (state.mode, st["day"]) in rules.CLASS_GAIN_DAYS
    after, real = _apply_params(state, {a: value})
    entry = {"option": "class", "attr": a, "sp": None, "选项": f"授業 {a}", "属性": f"{a}+{real[a]}（授業不套成长率）",
             "卡": "之后三选一：A/B 两档体力 -5 选卡，眠気档不扣体力但加眠気"
                   + ("（选择获得，必为未强化）" if gain else "（セレクトチェンジ：换掉一张卡，强化继承、定制清空）"),
             "之后": _hp_note(state, state.stamina - 5), "delta": {a: real[a], "stamina": -5}}  # fmt: skip
    if changes := _tier_changes(state, after):
        entry["评定变化"] = changes
    return entry


def _wappen_note(state: ProduceState, unknown: set[str] | frozenset) -> tuple[int, str]:
    """获得一张卡时ワッペン给的星星，以及说明（次数 / 获得倍率未识别时注明）。"""
    star = _scale(10, state.star_permil) if ("wappen_left" in unknown or state.wappen_left) else 0
    note = f"ワッペン 星星 +{star}"
    if "wappen_left" in unknown:
        note += "（剩余次数未识别，以画面为准）"
    if "star_permil" in unknown:
        note += "（获得倍率未识别，按 1× 估）"
    return star, note


def _simple_entry(state: ProduceState, opt: str, unknown: set[str] | frozenset = frozenset()) -> dict:
    if opt == "outing":
        rec = (
            min(state.max_stamina, state.stamina + _scale(state.max_stamina, rules.OUTING_RECOVER_PERMIL))
            - state.stamina
        )
        star, note = _wappen_note(state, unknown)
        return {"option": "outing", "选项": "外出", "体力": f"+{rec}", "获得": f"选一瓶饮料 + 选一张主效果卡（{note}）",
                "加码": f"付 50P 或加 1 张眠気：再选一张强化过的主效果卡（再 +{star} 星星）",
                "之后": _hp_note(state, state.stamina + rec), "delta": {"stamina": rec, "star": star}}  # fmt: skip
    if opt == "gift":
        star, note = _wappen_note(state, unknown)
        return {"option": "gift", "选项": "差し入れ", "P点": f"+{rules.PRESENT_POINTS}", "获得": f"SR/SSR 饮料 + 技能卡（{note}）",
                "delta": {"produce_point": rules.PRESENT_POINTS, "star": star}}  # fmt: skip
    if opt == "shop":
        return {"option": "shop", "选项": "相談", "P点": state.produce_point,
                "价格": f"强化 {rules.service_price(state.upgrades_done)}P、删除 {rules.service_price(state.deletes_done)}P；"
                        "卡 R+100/SR 50/SR+110/SSR 100/SSR+150，饮料 50/75/100，最左一格 7 折", "delta": {}}  # fmt: skip
    if opt == "special_guidance":
        return {"option": "special_guidance", "选项": "特別指導", "说明": "定制卡牌，价格以画面为准", "delta": {}}
    if opt == "rest":
        rec, no_rest, rest = rest_real_gain(state)
        row = {
            "option": "rest",
            "选项": "休息",
            "体力": f"+{rec}",
            "代价": "这一天不获得任何属性、星星、卡",
            "delta": {"stamina": rec},
        }
        if exam_tomorrow(state):
            row["实际收益"] = (f"明天考试开考时自动回复 50%：不休息开考体力 {no_rest}，休息开考体力 {rest}，实际只多 {rest - no_rest}"
                             + ("（零收益）" if rest == no_rest else ""))  # fmt: skip
        return row
    return {"option": opt, "选项": OPTION_CN.get(opt, opt), "delta": {}}


def preview_action_select(
    state: ProduceState, available: list[dict] | None = None, unknown: set[str] | frozenset = frozenset()
) -> list[dict]:
    """选行动页：每个可选行动的即时结果，带 index（LLM 按它回答）与 option/attr/sp（转 Action.produce_next）。

    available：画面上的选项（GameState.page_fields["options"]：{option, attr, hp_cost, ...}）；None 时按日程表列出当天的选项。
    课程没有 SP 标记时按体力消耗推断（SP 8 / 普通 6）。"""
    st = schedule(state.mode).stage(state.step)
    lessons = {o.main: o for o in state.lessons.get(state.step) or []}
    out = []
    if available is None:
        available = [{"option": "training", "attr": a} for a in ATTRS] if "training" in st["options"] else []
        available += [{"option": "class", "attr": a} for a in ATTRS] if "class" in st["options"] else []
        available += [{"option": o} for o in st["options"] if o not in ("training", "class")]
        if state.stamina < state.max_stamina and st["kind"] == "choice":
            available.append({"option": "rest"})
    for o in available:
        opt, attr = o.get("option"), o.get("attr")
        if opt == "training" and attr:
            base = lessons.get(attr)
            sp = LESSON_HP.get(o.get("hp_cost")) if o.get("hp_cost") is not None else (base.sp if base else None)
            out += preview_lessons(state, [LessonOption(attr, base.sub if base else None, sp)], unknown)
        elif opt == "class" and attr and "class" in st["options"]:
            out.append(_class_entry(state, st, attr, unknown))
        elif opt in ("exam", "round1", "round2"):
            out.append({"option": opt, "选项": OPTION_CN[opt], "说明": "进入考试", "delta": {}})
        else:
            out.append(_simple_entry(state, opt, unknown))
    for row in out:
        if (
            row.get("option") in ("training", "class")
            and (g := r1_bonus_gain(state, row.get("delta") or {})) is not None
        ):
            row["R1倍率增量"] = g  # Σ 每回合倍率(‰) × 回合数 的增量，越大越好（本战课程 / 授業补哪项看它）
        elif (
            R1_TURN_PROXY
            and row.get("option") in ("training", "class")
            and (g := r1_turn_weighted_gain(state, row.get("delta") or {})) is not None
        ):
            row["R1倍率增量"] = g
            row["R1倍率增量说明"] = "近似：R1 各属性回合数 × 属性增量（缺倍率换算数据；只用于和同一步其他选项比大小）"
    for i, row in enumerate(out):
        row["index"] = i
    return out


# ---------------------------------------------------------------- 选卡预演
def preview_card_pick(state: ProduceState, candidates: list[dict], *, change: bool = False,
                      unknown: set[str] | frozenset = frozenset()) -> dict:  # fmt: skip
    """三选一：candidates = [{card_id, upgrade}]；change=True 为セレクトチェンジ（不算获得，不触发ワッペン）。
    unknown 里的字段（禁用 / 刷新次数、ワッペン、牌组）显示为「未识别」，不按默认值 0 推理。"""
    from hiro import master
    from hiro.brain.cards import card_view

    other = _is_hajime_nia(state.mode)  # 初 / N.I.A：没有 H.I.F ワッペン、没有星星
    wappen = (
        _scale(10, state.star_permil)
        if (not other and ("wappen_left" in unknown or state.wappen_left) and not change)
        else 0
    )
    owned = [c.card_id for c in state.deck]
    rows = []
    for i, c in enumerate(candidates):
        row = catalog().card(c["card_id"], c.get("upgrade", 0))
        kaa = master.card(c["card_id"], c.get("upgrade", 0))
        v = card_view(i, kaa, available=True) if kaa else None
        know = YARUKI_CARDS.get(c["card_id"])
        entry = {
            "index": i,
            "卡": card_name(c["card_id"], c.get("upgrade", 0)),
            "效果": v.text if v else "",
            "费用": f"{row['stamina']}（先扣元气，不够扣体力）"
            + (f"，另直接扣体力 {row['forceStamina']}" if row.get("forceStamina") else ""),
            "社区评级": (
                f"{know['tier'] or '—'}{'·必拿' if know.get('must') else ''}：{know['note']}" if know else "攻略未提及"
            ),
        }
        if v and v.tags.extra_actions:
            entry["压缩"] = "带使用数追加（社区：R1 前牌组只留这类卡和得分必需卡）"
        if v and v.tags.once:
            entry["用后除外"] = True
        if c["card_id"] in owned:
            entry["已持有"] = owned.count(c["card_id"])
        entry["delta"] = {"deck_size": 0 if change else 1} | ({} if other else {"star": wappen})
        rows.append(entry)
    out = {
        "方式": "セレクトチェンジ（换掉一张，不触发获得）" if change else "获得",
        "拿后牌组张数": (len(state.deck) + (0 if change else 1)) if state.deck else "未知（牌组未识别）",
        "ワッペン星星": (f"{UNKNOWN}（还有次数的话 +{wappen}）" if "wappen_left" in unknown else f"+{wappen}") if wappen else "无",
        "候选": rows,
        "禁用剩余": UNKNOWN if "excludes_left" in unknown else state.excludes_left,
        "刷新剩余": UNKNOWN if "rerolls_left" in unknown else state.rerolls_left,
        "也可以": [x for x, ok in (("禁用一张（补新候选）" + ("：次数未识别，以画面为准" if "excludes_left" in unknown else ""),
                                     "excludes_left" in unknown or state.excludes_left),
                                    ("刷新全部" + ("：次数未识别，以画面为准" if "rerolls_left" in unknown else ""),
                                     "rerolls_left" in unknown or state.rerolls_left),
                                    ("不拿", not change)) if ok],
    }  # fmt: skip
    if other:
        out.pop("ワッペン星星")
    return out


MACHINE_KEYS = ("option", "attr", "sp", "delta")


def for_llm(rows: list[dict]) -> list[dict]:
    """去掉给机器用的键（option/attr/sp/delta），LLM 只看中文说明和 index。"""
    return [{k: v for k, v in r.items() if k not in MACHINE_KEYS} for r in rows]


def to_previews(rows: list[dict], page: str = "action_select") -> list:
    """preview_action_select / preview_card_pick 的行 → hiro.contracts.Preview（engine=formula）。"""
    from hiro.contracts import Action, Preview

    out = []
    for r in rows:
        if page == "action_select":
            action = Action.produce_next(r["option"], attr=r.get("attr"), sp=r.get("sp"))
        else:
            action = Action.activate_effect(r["index"])
        summary = {k: v for k, v in r.items() if k not in MACHINE_KEYS}
        out.append(Preview(action=action, immediate=dict(r.get("delta") or {}), summary=summary, engine="formula"))
    return out


def payload(state: ProduceState, page: str, **kw) -> dict:
    """LLM 输入：公共局面 + 页面预演。page：action_select（kw: available）/ card_pick（kw: candidates, change）；
    kw unknown：未观测字段（GameState.unobserved_produce_fields()），显示为「未识别」。"""
    unknown = frozenset(kw.get("unknown") or ())
    out = {"局面": situation(state, unknown)}
    if page == "action_select":
        out["选项预演"] = for_llm(preview_action_select(state, kw.get("available"), unknown))
    elif page == "card_pick":
        pick = preview_card_pick(state, kw["candidates"], change=kw.get("change", False), unknown=unknown)
        out["选卡"] = pick | {"候选": for_llm(pick["候选"])}
    return out
