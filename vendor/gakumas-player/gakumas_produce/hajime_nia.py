"""初（produce-001/002/003/006）与 N.I.A（produce-004/005）的培育规则和确定性状态模拟。

和 H.I.F 的 `schedule.py` 一样，只做「基础收益」：主数据里写死的数（课程过关 / 满分线、自主课和营业的属性与投票、
休息回复比例、考试档位、评价阈值）按规则算；随机的东西（授業属性、SP 是否出现、事件、支援卡触发、考试得分）由调用方
给出（实机读数或计划假设），这里不编造。每个公式的来源写在说明文档 `docs/hajime-nia-mechanics.md`。

    from gakumas_produce.hajime_nia import rules, Sim
    r = rules("produce-004", "i_card-skin-shro-3-007")
    s = Sim(r, params={"Vo": 155, "Da": 191, "Vi": 159}, growth_permil={"Vo": 1272, "Da": 1472, "Vi": 1135}, max_stamina=30)
    s.self_lesson("Da", sp=True)
    s.business("produce_point", "Da", excellent=True)
    s.audition("mid1", tier=2, score=9000, rank=1, votes=4680, params={"Vo": 33, "Da": 74, "Vi": 63})
    s.evaluation()
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from gakumas_exam.catalog import Catalog
from gakumas_exam.errors import Unsupported

ATTRS = ("Vo", "Da", "Vi")
ATTR_KEY = {"vo": "Vo", "da": "Da", "vi": "Vi", "vocal": "Vo", "dance": "Da", "visual": "Vi"}
HAJIME = ("produce-001", "produce-002", "produce-003", "produce-006")
NIA = ("produce-004", "produce-005")
STAGES = ("mid1", "mid2", "final")
STAGE_STEP = {"ProduceStepType_AuditionMid1": "mid1", "ProduceStepType_AuditionMid2": "mid2", "ProduceStepType_AuditionFinal": "final"}
PHASE_OF_STAGE = {"mid1": "before_1st", "mid2": "before_2nd", "final": "before_3rd"}
VENUES = ("produce_card", "produce_drink", "produce_point", "stamina")  # 紫 商業 / 青 企業 / 黄 自治体 / 绿 リゾート

# 初的最终评价（社区公式，用 wikiwiki「初_マスター育成」的 A+ / S / S+ 分数线表反推核对，见说明文档）
HAJIME_RANK_BONUS = {1: 1700, 2: 900, 3: 500}
HAJIME_FIRST_PARAM_BONUS = 30
HAJIME_SCORE_SLOPES = ((5000, 0.30), (10000, 0.15), (20000, 0.08), (30000, 0.04), (40000, 0.02), (None, 0.01))
# N.I.A 的最终评价投票项（学マスwiki「最終プロデュース評価」N.I.A 表；GKMSProject 用 9 局录制结算逐分对上）
NIA_VOTE_TIERS = {
    "produce-004": ((20000, 0.100, 0), (40000, 0.085, 300), (60000, 0.070, 900), (80000, 0.065, 1200),
                    (100000, 0.060, 1600), (None, 0.055, 2100)),
    "produce-005": ((20000, 0.100, 0), (40000, 0.085, 300), (60000, 0.070, 900), (80000, 0.065, 1200),
                    (100000, 0.060, 1600), (120000, 0.050, 2600), (140000, 0.040, 3800), (None, 0.030, 5200)),
}
PARAM_FACTOR = 2.3
# N.I.A 自主课附带的 P 点（wikiwiki「N.I.A立ち回り」表，プロ；主数据里没有）
NIA_SELF_LESSON_PP = {1: 20, 2: 25, 3: 30}
# 营业每次扣的体力（GKMSProject 实测 business_list，多数选项 5；主数据 ProduceStepEventSuggestion.stamina 为 0）
BUSINESS_STAMINA = 5


def family(produce_id):
    if produce_id in HAJIME:
        return "hajime"
    if produce_id in NIA:
        return "nia"
    raise Unsupported(f"不是初或 N.I.A：{produce_id}")


def attr(name):
    a = ATTR_KEY.get(str(name).lower(), name)
    if a not in ATTRS:
        raise Unsupported(f"属性写法不认识：{name}")
    return a


# ------------------------------------------------------------------ 主数据规则


@dataclass(frozen=True)
class Lesson:
    """一节课（ProduceStepLessonLevel）：回合数、过关线、满分线（满分线也是这节课属性收益的上限）。"""

    level_id: str
    name: str
    turns: int
    clear: int
    perfect: int


@dataclass(frozen=True)
class Tier:
    number: int
    audition_type: str
    base_score: int
    vote_unlock: int  # 选这一档要先有的投票数（N.I.A）
    rank_threshold: int  # 要到的名次
    force_end_score: int  # 到这个分考试就结束（0 = 不封顶）


@dataclass
class Rules:
    produce_id: str
    family: str
    name: str
    steps: int
    action_points: int
    param_cap: int
    setting: dict
    lessons: dict = field(default_factory=dict)  # (kind, attr) -> [Lesson]，按这节课是第几次排
    self_lessons: dict = field(default_factory=dict)  # (phase 1..3, sp) -> (stamina, parameter)
    business: dict = field(default_factory=dict)  # (phase, venue) -> (votes, excellent_votes, parameter, extras)
    auditions: dict = field(default_factory=dict)  # stage -> [Tier]
    grades: tuple = ()  # ((threshold, grade), ...) 从低到高
    plan: int = 0  # 偶像卡的 planType 号（1 / 2 / 3）

    @property
    def drink_limit(self):
        return int(self.setting["produceDrinkPossessLimit"])

    @property
    def retries(self):
        return int(self.setting.get("continueCount") or 0)

    def grade(self, value):
        out = self.grades[0][1] if self.grades else None
        for t, g in self.grades:
            if value >= t:
                out = g
        return out


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _lesson_rows(c, produce_id, idol):
    """这个偶像卡的课程，按（种类, 属性）→ 第几次排好。主数据三种写法：
    REGULAR / PRO 按角色和属性（p_step_lesson_level-001-shro-sp-da-002）；MASTER 只有追い込み、按考试流派
    （p_step_lesson-produce-003-exam_card_play_aggressive_01-hard-01）；LEGEND 按流派、三属性共用（…-produce_006-card_play_aggressive-sp-03）。"""
    levels = {r["id"]: r for r in c.rows("ProduceStepLessonLevel")}
    num = produce_id.split("-")[1]
    style = _snake(str(idol.get("examEffectType", "")).replace("ProduceExamEffectType_Exam", ""))
    pats = {
        "001": rf"^p_step_lesson_level-001-{idol['characterId']}-(normal|sp|hard)-(vo|da|vi)-(\d+)$",
        "002": rf"^p_step_lesson_level-002-{idol['characterId']}-(normal|sp|hard)-(vo|da|vi)-(\d+)$",
        "003": rf"^p_step_lesson-produce-003-exam_{style}_01-(hard)()-(\d+)$",
        "006": rf"^p_step_lesson-produce_006-{style}-(normal|sp)()-(\d+)$",
    }
    pat = re.compile(pats[num])
    out = {}
    for r in c.rows("ProduceStepLesson"):
        m = pat.match(r["id"])
        if not m:
            continue
        L = levels[r["produceStepLessonLevelId"]]
        lesson = Lesson(L["id"], r["name"], int(L["limitTurn"]), int(L["successThreshold"]), int(L["resultTargetValueLimit"]))
        for a in ([attr(m[2])] if m[2] else ATTRS):
            out.setdefault((m[1], a), []).append((int(m[3]), lesson))
    return {k: [x for _, x in sorted(v)] for k, v in out.items()}


def _business(c, produce_id):
    num = produce_id.replace("-", "_")
    votes = {}
    for r in c.rows("ProduceStepEventDetail"):
        m = re.match(rf"^event-detail-business-{num}-plan\d-({'|'.join(VENUES)})-(before_\w+?)-\d+-\d+(-ex)?$", r["id"])
        if not m:
            continue
        v = [e for e in r.get("produceEffectIds") or [] if e.startswith("p_effect-vote_count_addition-")]
        if v:
            votes.setdefault((m[2], m[1]), {})[bool(m[3])] = int(v[0].rsplit("-", 1)[1].split("_")[0])
    eff = {r["id"]: r for r in c.rows("ProduceEffect")}
    params, extras = {}, {}
    for r in c.rows("ProduceStepEventSuggestion"):
        m = re.match(rf"^p_s_e_s-event-detail-business-{num}-(before_\w+?)-({'|'.join(VENUES)})-", r["id"])
        if not m:
            continue
        for e in r.get("produceEffectIds") or []:
            E = eff.get(e) or {}
            t = str(E.get("produceEffectType", "")).replace("ProduceEffectType_", "")
            if t in ("VocalAddition", "DanceAddition", "VisualAddition"):
                params[(m[1], m[2])] = int(E["effectValueMin"])
            elif t in ("ProducePointAddition", "StaminaRecoverFix"):
                extras.setdefault((m[1], m[2]), {})[t] = int(E["effectValueMin"])
    out = {}
    for k, v in votes.items():
        out[k] = (v.get(False, 0), v.get(True, v.get(False, 0)), params.get(k, 0), dict(extras.get(k, {})))
    return out


def rules(produce_id, idol_card_id, catalog=None) -> Rules:
    """读主数据拼出这个偶像卡在这个培育里的规则。"""
    c = catalog or Catalog()
    fam = family(produce_id)
    p = c.one("Produce", produce_id)
    idol = c.one("IdolCard", idol_card_id.replace("i_card-skin-", "i_card-"))  # 换装（skin）共用原卡的配置
    r = Rules(produce_id=produce_id, family=fam, name=p["name"], steps=int(p["steps"]), action_points=int(p["actionPointQuantity"]),
              param_cap=int(p["idolCardParameterGrowthLimit"]), setting=dict(c.one("ProduceSetting", p["produceSettingId"])),
              plan=int(str(idol.get("planType", "")).rsplit("Plan", 1)[-1] or 0) if "Plan" in str(idol.get("planType", "")) else 0)
    if fam == "hajime":
        r.lessons = _lesson_rows(c, produce_id, idol)
    else:
        # 主数据只有 produce_004 的 6 行自主课（MASTER 共用，GKMSProject 同样处理）
        for row in c.rows("ProduceStepSelfLesson"):
            m = re.match(r"^self_lesson-produce_00\d-(\d+)-(normal|sp)$", row["id"])
            if m:
                r.self_lessons[(int(m[1]), m[2] == "sp")] = (int(row["stamina"]), int(row["parameter"]))
        r.business = _business(c, produce_id)
    ref = idol.get("produceStepAuditionDifficultyId")
    for row in c.rows("ProduceStepAuditionDifficulty"):
        if row.get("id") != ref or row.get("produceId") != produce_id or row.get("stepType") not in STAGE_STEP:
            continue
        r.auditions.setdefault(STAGE_STEP[row["stepType"]], []).append(Tier(
            int(row["number"]), str(row.get("auditionType", "")).replace("ProduceStepAuditionType_", ""), int(row.get("baseScore") or 0),
            int(row.get("voteCount") or 0), int(row.get("rankThreshold") or 0), int(row.get("forceEndScore") or 0)))
    for k in r.auditions:
        r.auditions[k].sort(key=lambda t: t.number)
    group = next(g["id"] for g in c.rows("ProduceGroup") if produce_id in g["produceIds"])
    r.grades = tuple(sorted((int(g["threshold"]), g["grade"].replace("ResultGrade_", "")) for g in c.rows("ProduceGrade")
                            if g["produceGroupId"] == group))
    return r


def lesson_by_clear(kind, clear, *, plan=0, catalog=None) -> Lesson:
    """按画面过关线在初（produce-001/002/003）的全部课程档位里反查。plan 给了就先按 plan 过滤（plan3 的课程表和 plan2 共用时退回不过滤）。
    唯一就返回，不唯一 / 没有就抛 Unsupported（不猜）。"""
    c = catalog or Catalog()
    pat = re.compile(rf"^p_step_lesson_level-00[123]-plan(\d)(?:-aggressive)?-{kind}-\d+$|^p_step_lesson_level-003-plan(\d)-{kind}-\d+$")
    rows = []
    for r in c.rows("ProduceStepLessonLevel"):
        m = pat.match(r["id"])
        if m and int(r["successThreshold"]) == clear:
            rows.append((int(m[1] or m[2]), r))
    if plan and any(p == plan for p, _ in rows):
        rows = [x for x in rows if x[0] == plan]
    shapes = {(int(r["limitTurn"]), int(r["resultTargetValueLimit"])) for _, r in rows}
    if len(shapes) != 1:
        raise Unsupported(f"{kind} 课过关线 {clear} 在主数据里对不上唯一一档（{sorted(shapes)}）")
    r = rows[0][1]
    return Lesson(r["id"], kind, int(r["limitTurn"]), int(r["successThreshold"]), int(r["resultTargetValueLimit"]))


# ------------------------------------------------------------------ 评价


def hajime_evaluation(params, final_score, rank, cap):
    """初的最终评价：floor(属性和 × 2.3) + 名次分 + 最终考试分的分段折算。第 1 名每项属性先 +30（再按上限截）。

    params 传最終考试**之前**的属性：结算画面 / 培育履历上显示的属性已经含了这 +30，用那组数要先每项减 30。
    实机核对：2026-11-17 初 REGULAR 篠澤広，履历 995 / 996 / 478、最終 14314 第 1 → 9973（游戏显示 9,973）。"""
    bonus = HAJIME_FIRST_PARAM_BONUS if rank == 1 else 0
    total = sum(min(cap, max(0, int(params.get(a, 0))) + bonus) for a in ATTRS)
    part, lo = 0.0, 0
    for hi, slope in HAJIME_SCORE_SLOPES:
        top = final_score if hi is None else min(final_score, hi)
        if top > lo:
            part += (top - lo) * slope
        if hi is None or final_score <= hi:
            break
        lo = hi
    return math.floor(total * PARAM_FACTOR + 1e-9) + HAJIME_RANK_BONUS.get(rank, 0) + math.floor(part + 1e-9)


def nia_vote_eval(votes, produce_id):
    for top, m, c in NIA_VOTE_TIERS[produce_id]:
        if top is None or votes <= top:
            return math.floor(max(0, votes) * m + c + 1e-9)
    return 0


def nia_evaluation(params, votes, produce_id, cap):
    return math.floor(sum(min(cap, max(0, int(params.get(a, 0)))) for a in ATTRS) * PARAM_FACTOR + 1e-9) + nia_vote_eval(votes, produce_id)


def grow(base, growth_permil):
    """课程 / 自主课的属性收益：基础值 + floor(基础值 × 加成)。growth_permil 按画面读数写法（1472 = +47.2%）。"""
    return base + math.floor(base * (growth_permil - 1000) / 1000 + 1e-9)


# ------------------------------------------------------------------ 状态模拟


@dataclass
class Event:
    week: int
    action: str
    detail: dict


class Sim:
    """一局培育的状态：体力、P 点、三项属性、投票数、已上课次数、休息次数。每个动作推进一周（考试 / 试镜也算一周）。

    动作只做主数据能确定的部分；需要实机或计划给出的值都是必填参数，不猜。违反规则（体力不够、档位票数不够、
    超过周数）抛 Unsupported。"""

    def __init__(self, rules: Rules, *, params, growth_permil, max_stamina, stamina=None, produce_point=None, votes=0):
        self.r = rules
        self.params = {a: int(params.get(a, 0)) for a in ATTRS}
        self.growth = {a: int(growth_permil.get(a, 1000)) for a in ATTRS}
        self.max_stamina = int(max_stamina)
        self.stamina = self.max_stamina if stamina is None else int(stamina)
        self.pp = int(rules.setting.get("initialProducePoint") or 0) if produce_point is None else int(produce_point)
        self.votes = int(votes)
        self.week = 0
        self.lesson_count = {}
        self.rests = 0
        self.results = {}  # stage -> dict(score, rank, tier)
        self.log: list[Event] = []

    # ---- 内部
    def _step(self, action, **detail):
        if self.week >= self.r.steps:
            raise Unsupported(f"已经走完 {self.r.steps} 周")
        self.week += 1
        self.log.append(Event(self.week, action, detail))

    def _add(self, a, n):
        self.params[a] = min(self.r.param_cap, self.params[a] + int(n))

    def _stamina(self, delta):
        self.stamina = max(0, min(self.max_stamina, self.stamina + int(delta)))

    def phase(self):
        """N.I.A 当前在哪一段（1 = 1 次试镜前，2 = 2 次前，3 = 最终前）：按已经打完的试镜数。"""
        return 1 + sum(1 for s in ("mid1", "mid2") if s in self.results)

    # ---- 初
    def lesson(self, a, kind="normal", *, level=None, clear=None, score=None, stamina_used, oikomi_extra=0):
        """初的课程。kind = normal / sp / hard（追い込み）。score 默认按满分线；收益不超过满分线。
        用哪一档（回合 / 过关线 / 满分线）由日程逐周决定（客户端逆向：等级 = baseStepLevel + 日程 AddStepLevel），
        **不是**「同种同属性第几次」（实机：REGULAR 第一节 SP 就是 7 回合 / 过关 70）。所以必须给其一：
          level：这一档在 rules.lessons[(kind, attr)] 里的序号（1 起）；
          clear：画面上的过关线（CLEARまで 的初值），按它在初的全部课程档位里反查（REGULAR 的追い込み实机是 9 / 90、11 / 165，
                 落在 produce-002 的档位上，所以不限本培育）。
        stamina_used：这节课实际扣掉的体力（出牌决定，必填）。oikomi_extra：追い込みボーナス里额外涨的量，下课时平分到三项。"""
        if self.r.family != "hajime":
            raise Unsupported("课程（打牌的レッスン）只在初里；N.I.A 用 self_lesson")
        a = attr(a)
        if level is not None:
            seq = self.r.lessons.get((kind, a)) or []
            if not 1 <= level <= len(seq):
                raise Unsupported(f"{kind} {a} 课没有第 {level} 档（主数据 {len(seq)} 档）")
            L = seq[level - 1]
        elif clear is not None:
            L = lesson_by_clear(kind, int(clear), plan=self.r.plan)
        else:
            raise Unsupported("课程档位按日程定：要给 level 或画面过关线 clear")
        if stamina_used > self.stamina:
            raise Unsupported(f"体力 {self.stamina} 不够这节课用的 {stamina_used}")
        self.lesson_count[(kind, a)] = self.lesson_count.get((kind, a), 0) + 1
        base = L.perfect if score is None else max(0, min(int(score), L.perfect))
        gain = grow(base, self.growth[a])
        self._stamina(-stamina_used)
        self._add(a, gain)
        if oikomi_extra:
            each = int(oikomi_extra) // 3
            for b in ATTRS:
                self._add(b, each)
        self._step("lesson", attr=a, kind=kind, level=L.level_id, base=base, gain=gain, cleared=base >= L.clear)
        return gain

    def study(self, gains, *, stamina_used):
        """授業：涨哪项、涨多少是随机的（g8：20 / 30 / 40 / 55 / 80 / 110），由调用方给出。"""
        for a, n in gains.items():
            self._add(attr(a), n)
        self._stamina(-stamina_used)
        self._step("study", gains=dict(gains), stamina=stamina_used)

    def outing(self, *, pp_cost, recover_permil, extras=None):
        """外出：花 P 点回体力（按最大体力的千分比），附带的饮料 / 卡由调用方记在 extras。"""
        if pp_cost > self.pp:
            raise Unsupported(f"P 点 {self.pp} 不够外出要的 {pp_cost}")
        self.pp -= pp_cost
        self._stamina(math.floor(self.max_stamina * recover_permil / 1000))
        self._step("outing", pp_cost=pp_cost, recover_permil=recover_permil, extras=extras or {})

    # ---- N.I.A
    def self_lesson(self, a, *, sp=False, stamina_permil=0):
        """N.I.A 自主课：不打牌，扣 6 / 8 体力，涨 80–150（按段）。
        体力门槛按客户端逆向（ScheduleScreenPresenter.IsSelfLessonStaminaSufficient）：体力 < 消耗时不能上，等于可以上。
        实际消耗 = 基础 ×（1000 + stamina_permil）/ 1000（UserProduceProgress.SelfLessonTypeStaminaPermils，支援卡 / 道具的
        SelfLessonStaminaUp / Down 给的；取整方式未逆向，这里向下取整）。"""
        if self.r.family != "nia":
            raise Unsupported("自主レッスン只在 N.I.A")
        a = attr(a)
        ph = self.phase()
        cost, base = self.r.self_lessons[(ph, bool(sp))]
        cost = math.floor(cost * (1000 + stamina_permil) / 1000 + 1e-9)
        if self.stamina < cost:
            raise Unsupported(f"体力 {self.stamina} 低于消耗 {cost}，自主课上不了")
        gain = grow(base, self.growth[a])
        self._stamina(-cost)
        self._add(a, gain)
        self.pp += NIA_SELF_LESSON_PP[ph]
        self._step("self_lesson", attr=a, sp=bool(sp), gain=gain)
        return gain

    def business(self, venue, a, *, excellent=False, vote_permil=0, stamina_cost=BUSINESS_STAMINA, pp_cost=0):
        """营业：去哪个场所（venue = produce_card 紫 / produce_drink 青 / produce_point 黄 / stamina 绿）、选项涨哪项属性。
        投票 = 主数据票数（大成功取 -ex 行，等于 1.3 倍）×（1000 + 支援卡营业票数加成‰）/ 1000；属性不吃加成。
        每个营业的体力消耗和 P 点消耗是服务器逐个下发的（UserProduceProgressBusiness.Stamina / ProducePoint）；
        客户端（ScheduleBusinessScreenView.CheckIfGoBusiness）要求 P 点 ≥ P 点消耗且体力 ≥ 体力消耗才能去。"""
        if self.r.family != "nia":
            raise Unsupported("営業只在 N.I.A")
        if venue not in VENUES:
            raise Unsupported(f"营业场所不认识：{venue}")
        key = (PHASE_OF_STAGE[STAGES[self.phase() - 1]], venue)
        plain, ex, param, extras = self.r.business[key]
        votes = math.floor((ex if excellent else plain) * (1000 + vote_permil) / 1000 + 1e-9)
        if self.stamina < stamina_cost or self.pp < pp_cost:
            raise Unsupported(f"体力 {self.stamina} / P 点 {self.pp} 不够营业的 {stamina_cost} / {pp_cost}")
        self._stamina(-stamina_cost)
        self.pp -= pp_cost
        self.votes += votes
        self._add(attr(a), param)
        self.pp += extras.get("ProducePointAddition", 0)
        self._stamina(extras.get("StaminaRecoverFix", 0))
        self._step("business", venue=venue, attr=attr(a), votes=votes, excellent=excellent, param=param)
        return votes

    # ---- 共用
    def rest(self, *, before_audition=False):
        """休息：回最大体力 × refreshStaminaRecoveryPermil（700）；N.I.A 试镜前一周按 beforeAudition…（500）。"""
        if self.stamina >= self.max_stamina:
            raise Unsupported("体力满了不能休息（g8-休）")
        key = "beforeAuditionRefreshStaminaRecoveryPermil" if before_audition else "refreshStaminaRecoveryPermil"
        self._stamina(math.floor(self.max_stamina * int(self.r.setting[key]) / 1000))
        self.rests += 1
        self._step("rest", before_audition=before_audition)

    def other(self, action, *, stamina=0, pp=0, params=None, votes=0):
        """活動支給 / 差し入れ / 相談 / 特別指導 / 商店等：收益由调用方给出（随机或看画面）。"""
        self._stamina(stamina)
        self.pp += pp
        self.votes += votes
        for a, n in (params or {}).items():
            self._add(attr(a), n)
        self._step(action, stamina=stamina, pp=pp, params=dict(params or {}), votes=votes)

    def audition(self, stage, *, tier=1, score, rank, params=None, votes=0, stamina_used=0):
        """考试 / 试镜。tier：N.I.A 选的档（票数要够）；初每场只有 1 档。params / votes 是结算画面上的属性和投票奖励。"""
        tiers = self.r.auditions.get(stage) or []
        t = next((x for x in tiers if x.number == tier), None)
        if t is None:
            raise Unsupported(f"{stage} 没有第 {tier} 档")
        if self.votes < t.vote_unlock:
            raise Unsupported(f"{stage} 第 {tier} 档要 {t.vote_unlock} 票，现在 {self.votes}")
        if t.force_end_score:
            score = min(score, t.force_end_score)
        for a, n in (params or {}).items():
            self._add(attr(a), n)
        self.votes += int(votes)
        self._stamina(-stamina_used)
        passed = rank <= t.rank_threshold
        self.results[stage] = dict(score=score, rank=rank, tier=tier, passed=passed)
        self._step("audition", stage=stage, tier=tier, score=score, rank=rank, passed=passed)
        return passed

    def evaluation(self):
        """最终评价和档位（ProduceGrade）。初要先打完 final。"""
        if self.r.family == "hajime":
            f = self.results.get("final")
            if not f:
                raise Unsupported("初的评价要最终考试的分数和名次")
            value = hajime_evaluation(self.params, f["score"], f["rank"], self.r.param_cap)
        else:
            value = nia_evaluation(self.params, self.votes, self.r.produce_id, self.r.param_cap)
        return dict(value=value, grade=self.r.grade(value))


# ------------------------------------------------------------------ 周表


def calendar_document(catalog=None):
    import json

    from gakumas_exam.catalog import ROOT

    doc = json.loads((ROOT / "data/schedules/hajime_nia.json").read_text(encoding="utf-8"))
    c = catalog or Catalog()
    if doc["master_commit"] != c.info["commit"]:
        raise Unsupported("周表和主数据版本不一致")
    return doc


def calendar(produce_id, pattern=None, *, catalog=None):
    """完整周表：每周一个列表，列出那周能选的行动种类（不含休む）。只有 complete=true 的模式能用（目前只有初 MASTER 三种）；
    其它模式抛 Unsupported，要看已知部分用 calendar_document()。"""
    mode = calendar_document(catalog)["modes"].get(produce_id)
    if not mode or not mode.get("complete"):
        raise Unsupported(f"{produce_id} 没有有出处的完整周表")
    pats = mode["patterns"]
    if pattern is None:
        if len(pats) != 1:
            raise Unsupported(f"{produce_id} 有 {len(pats)} 种周表，要指定 pattern（1 起）")
        pattern = 1
    weeks = pats[pattern - 1]
    steps = int((catalog or Catalog()).one("Produce", produce_id)["steps"])
    if len(weeks) != steps:
        raise Unsupported(f"{produce_id} 周表 {len(weeks)} 周，主数据 {steps} 周")
    return [w.split("/") for w in weeks]
