"""通用考试开局：编成（gkms.exam_loadout.v1 写法的 dict）→ 我们的引擎（gakumas_exam，Rust 后端）开一局，给搜索用。

偶像之路（tower_env）、初 / N.I.A 的试镜共用这一份（3940a9 11-17 同意：构造器由 8a3a1e7 写，实机出牌也用这一份）。
lo 的字段（缺的按缺省）：
  produce_cards       牌组，"p_card-…+1/p_card_custom-…" 或 dict（hiro.search.loadout.parse_deck_entry）
  produce_item_ids    P 道具（引擎按道具挂附魔）
  start_enchant_ids   另外的开考附魔（bench 题的 main_item_enchant_ids + lesson_start_enchant_ids）
  supports            支援卡 ["s_card-3-0010:64", …]（id:强化概率千分比）或引擎写法 {_supportCardId, …}（run_loadout 给的）
  skills              回忆 / 支援 / 偶像的考试附魔（ProduceSkill id，run_loadout 给的）
  idol_card_id / starting_stamina / max_stamina / drink_ids
  score_bonus_permils 三属性倍率，列表 [Vo, Da, Vi] 或字典 {"1": …}
  exam                {produce_exam_battle_config_id, produce_exam_gimmick_effect_group_id}；回合数和审查基准取 battle config，
                      每回合属性按审查基准随机（同 GKMS：试镜的回合属性按审查基准抽，不按偶像自己的属性）
  lesson              （初的课程）{parameter_type, kind, limit_turn, clear_score, perfect_score, step_id}：有它就按课程开局
                      （考试类型 Lesson：不乘回合倍率；回合数 = limitTurn + exam_extra_turn，每回合都是这节课的属性），不看 exam
bench 题（gkms.hif_loadout.v1，初 / N.I.A 的试镜、初的课程）先用 from_hif_loadout 换成上面这种写法。
课程：引擎没有「打到满分线就结束」，搜索目标里分数按满分线封顶（stage_objective 的 score_cap），效果相同（多出的分没用）。
没做：追い込み（HARD）课程到成功线后「课程类型」触发全部生效（GKMS oikomi_threshold），引擎按不满足处理。
"""

from __future__ import annotations

from functools import cache, lru_cache

STEP_TYPES = {
    "mid1": "ProduceStepType_AuditionMid1",
    "mid2": "ProduceStepType_AuditionMid2",
    "final": "ProduceStepType_AuditionFinal",
}
PLAN_TYPES = {"ProducePlanType_Plan1": 1, "ProducePlanType_Plan2": 2, "ProducePlanType_Plan3": 3}


# ProduceStepType（课程那几种）→ (这节课的属性 1 Vo / 2 Da / 3 Vi, 种类)。1–9 = LESSON_<属性>_<NORMAL|SP|HARD>，
# 29–34 = LEGEND_LESSON_<属性>_<NORMAL|SP>（同 GKMS scenario/hajime.py LESSON_STEP_TYPES）
LESSON_STEP_TYPES = {1: (1, "NORMAL"), 2: (1, "SP"), 3: (1, "HARD"), 4: (2, "NORMAL"), 5: (2, "SP"), 6: (2, "HARD"),
                     7: (3, "NORMAL"), 8: (3, "SP"), 9: (3, "HARD"), 29: (1, "NORMAL"), 30: (1, "SP"), 31: (2, "NORMAL"),
                     32: (2, "SP"), 33: (3, "NORMAL"), 34: (3, "SP")}  # fmt: skip


class ExamUnsupported(ValueError):
    """这种场次 / 写法我们的引擎开不了局（课程、查不到的试镜）。"""


def bonus_permils(b) -> dict:
    """考试倍率 → {1: Vo, 2: Da, 3: Vi}。题目文件写成列表 [Vo, Da, Vi]，bench 模板（problems.load_set(template=True)，实打时
    ctx.loadout 就是它）写成 {"1": Vo, "2": Da, "3": Vi}：两种都认（字典直接 zip 会拿到键，倍率变成 1 / 2 / 3）。"""
    if isinstance(b, dict):
        return {t: int(b[k]) for t in (1, 2, 3) for k in (t, str(t)) if k in b}
    return {t: int(v) for t, v in zip((1, 2, 3), b)}


def _master():
    import hiro.sim  # noqa: F401  引擎路径
    from hiro.sim.engine import master_commit

    from gakumas_exam.master import shared

    return shared(master_commit())


def exam_env(lo: dict, seed: int, *, guid_prefix: str = "card-exam"):
    """编成 → 我们的引擎对局（FidelityBattle，Rust 后端时是 NativeBattle，.rx 是 gakumas_exam_rs.Exam）。"""
    import hiro.sim  # noqa: F401
    from hiro.bench_bridge.advisor import _customize_ids
    from hiro.search.loadout import parse_deck_entry

    from gakumas_exam.battle import FidelityBattle, _native
    from gakumas_exam.cards import ExamCard
    from gakumas_exam.setup import new_exam

    m = _master()
    cz = _customize_ids()
    deck = []
    for i, s in enumerate(lo["produce_cards"]):
        cid, up, custom = parse_deck_entry(s)
        ids = cz(cid, up)
        counts = [int(custom.get(k) or 0) for k in ids] if custom else None
        deck.append(ExamCard(m, cid, up, counts, guid=f"{guid_prefix}-{i:03d}"))
    plan = _idol_card(lo["idol_card_id"])["planType"]
    supports = [_support(m, s) for s in lo.get("supports") or ()]
    hp = int(lo.get("starting_stamina") or lo["max_stamina"])
    lesson = lo.get("lesson")
    if lesson:  # 课程：考试类型 Lesson（0），每回合都是这节课的属性，不乘倍率
        turns = {"turn_types": [int(lesson["parameter_type"])] * int(lesson["limit_turn"]), "exam_type": 0}
        gimmick = lo.get("gimmick_group_id")
    else:
        ex_ = lo["exam"]
        cfg = next(
            (r for r in m.table("ProduceExamBattleConfig") if r["id"] == ex_["produce_exam_battle_config_id"]), None
        )
        if cfg is None:
            raise ExamUnsupported(f"主数据里没有 battle config {ex_['produce_exam_battle_config_id']}")
        weights = {1: cfg["vocal"], 2: cfg["dance"], 3: cfg["visual"], "turns": int(cfg["turn"])}
        turns = {
            "randomize_turns": True,
            "config_weights": weights,
            "bonus_permil": bonus_permils(lo["score_bonus_permils"]),
        }
        gimmick = ex_.get("produce_exam_gimmick_effect_group_id")
    ex = new_exam(None, m, seed, deck=deck, items=list(lo.get("produce_item_ids") or []), stamina=hp, max_stamina=int(lo["max_stamina"]),
                  drinks=list(lo.get("drink_ids") or []), plan_type=PLAN_TYPES.get(plan, 0), gimmicks=[gimmick] if gimmick else None,
                  start_enchants=list(lo.get("start_enchant_ids") or []), supports=supports, skills=list(lo.get("skills") or []) or None,
                  **turns)  # fmt: skip
    attrs = [{1: "Vo", 2: "Da", 3: "Vi"}[t] for t in ex.turn_types]
    env = FidelityBattle(ex, FidelityBattle._turn_table(ex, attrs, 3), attrs, turn_offset=0, draw=3)
    env.seed = seed
    env._sync_turns()
    return _native(env)


def _support(m, s) -> dict:
    """支援卡 → 引擎的 supportCardList 一项。bench 题写成 "s_card-3-0010:64" 或展开后的 {support_card_id, permil}：数字是考试内抽牌时的
    强化概率（千分比），不是等级（gkbench 展开模板时就叫 permil）；{id, level} 才按等级查。"""
    from gakumas_exam.loadout import _support as row_of
    from gakumas_exam.loadout import support_entry

    if isinstance(s, dict) and "_supportCardId" in s:  # 已经是引擎写法（hiro.search.loadout.run_loadout 给的）
        return dict(s)
    if isinstance(s, dict):
        sid, permil, level = s.get("support_card_id") or s.get("id"), s.get("permil"), s.get("level")
    else:
        sid, _, n = str(s).partition(":")
        permil, level = (int(n) if n else None), None
    if permil is None and level is not None:
        return support_entry(m, sid, int(level))
    row = row_of(m, sid)
    return {"_supportCardId": sid, "_filterParameterType": row["produceCardUpgradeLessonParameterType"],
            "_cardSearchId": row["upgradeProduceCardSearchId"], "_produceCardUpgradePermil": int(permil or 0)}  # fmt: skip


# ---------------------------------------------------------------- bench 题（gkms.hif_loadout.v1）


# 查表用引擎的主数据（gakumasu-diff 快照，hiro.bench_bridge.tower.master），不用 hiro.state 的 KAA game.db：后者旧，
# 新偶像卡（如 i_card-atbm-3-018）的试镜行没有（11-17 初 / N.I.A 29 题因此查不到）。


@cache
def _difficulty_rows() -> dict:
    from hiro.bench_bridge import tower as tw

    out: dict = {}
    for r in tw.master().rows("ProduceStepAuditionDifficulty"):
        out.setdefault((r["id"], r["produceId"], r["stepType"]), []).append(r)
    return out


@cache
def _idol_card(idol_card_id: str) -> dict:
    from hiro.bench_bridge import tower as tw

    row = next((r for r in tw.master().rows("IdolCard") if r.get("id") == idol_card_id), None)
    if row is None:
        raise ExamUnsupported(f"主数据里没有偶像卡 {idol_card_id}")
    return row


def audition_row(idol_card_id: str, produce_id: str, round_key: str, number: int | None = None) -> dict:
    """这场试镜的主数据行（ProduceStepAuditionDifficulty：偶像 × 培育 × 场次 × 编号）：battle config、对手组、gimmick、
    rankThreshold（过关名次）、baseScore（合格线）、forceEndScore（初中期封顶）、voteCount（N.I.A 票数）。
    number（N.I.A 选的是第几个试镜）不给时：只有一个就用它，有几个就报错。"""
    idol = _idol_card(idol_card_id)
    step = STEP_TYPES.get(str(round_key).lower())
    if step is None:
        raise ExamUnsupported(f"场次 {round_key} 不是试镜（课程走 from_hif_loadout 的课程写法）")
    rows = _difficulty_rows().get((idol["produceStepAuditionDifficultyId"], produce_id, step), [])
    if number:
        rows = [r for r in rows if int(r.get("number") or 0) == int(number)]
    if len(rows) != 1:
        raise ExamUnsupported(f"{idol_card_id} {produce_id} {step} 编号 {number}：主数据里找到 {len(rows)} 行")
    return rows[0]


def turn_weights(lo: dict) -> dict | None:
    """这场考试三属性回合的权重 {1: Vo, 2: Da, 3: Vi}（主数据 ProduceExamBattleConfig.vocal / dance / visual）；查不到 None。"""
    try:
        import hiro.sim  # noqa: F401  引擎路径
        from hiro.sim.engine import master_commit

        from gakumas_exam.master import shared

        cid = (lo.get("exam") or {}).get("produce_exam_battle_config_id")
        cfg = (
            next((r for r in shared(master_commit()).table("ProduceExamBattleConfig") if r["id"] == cid), None)
            if cid
            else None
        )
    except Exception:  # noqa: BLE001
        return None
    return {1: float(cfg["vocal"]), 2: float(cfg["dance"]), 3: float(cfg["visual"])} if cfg else None


def from_hif_loadout(doc: dict) -> dict:
    """bench 题（初 / N.I.A 的试镜，gkms.hif_loadout.v1）→ exam_env 要的写法，附 audition（主数据行，objective 要用）。
    倍率：题里有 score_bonus_permils（N.I.A 抓到的服务器倍率，已含各种加成）就用，没有按 parameters 和 battle config 算
    （同 hiro.bench_bridge.tower.score_bonus_permils 的曲线插值 + 审查基准不足扣分）。"""
    from hiro.bench_bridge import tower as tw

    if str(doc.get("round") or "") == "lesson":
        return _lesson_loadout(doc)
    number = doc.get("audition_number") or doc.get("step_select_number")
    row = audition_row(doc["idol_card_id"], doc["produce_id"], doc.get("round") or "final", number)
    permils = doc.get("score_bonus_permils")
    if not permils:
        params = doc["parameters"]
        params = [int(params[k]) for k in ("1", "2", "3")] if isinstance(params, dict) else [int(v) for v in params]
        permils = tw.score_bonus_permils(row["produceExamBattleConfigId"], params)
    return {
        "produce_id": doc["produce_id"],
        "round": doc.get("round"),
        "idol_card_id": doc["idol_card_id"],
        "produce_cards": list(doc["deck_cards"]),
        "produce_item_ids": list(doc.get("produce_item_ids") or []),
        "start_enchant_ids": list(doc.get("main_item_enchant_ids") or []) + list(doc.get("lesson_start_enchant_ids") or []),
        "supports": list(doc.get("support_cards") or []),
        "drink_ids": list(doc.get("drink_ids") or []),
        "starting_stamina": int(doc.get("starting_stamina") or doc["max_stamina"]),
        "max_stamina": int(doc["max_stamina"]),
        "score_bonus_permils": permils,
        "exam": {"produce_exam_battle_config_id": row["produceExamBattleConfigId"],
                 "produce_exam_gimmick_effect_group_id": row.get("produceExamGimmickEffectGroupId") or None,
                 "produce_exam_battle_npc_group_id": row["produceExamBattleNpcGroupId"]},
        "audition": {k: row.get(k) for k in ("rankThreshold", "baseScore", "forceEndScore", "voteCount", "isStaticNpcScore", "number")},
    }  # fmt: skip


def lesson_level(step_id: str) -> tuple[dict, str | None]:
    """课程步骤 id → (ProduceStepLessonLevel 行, 课程名)：limitTurn 回合数、successThreshold 合格线、resultTargetValueLimit 满分线。"""
    from hiro.bench_bridge import tower as tw

    m = tw.master()
    step = next((r for r in m.rows("ProduceStepLesson") if r.get("id") == step_id), None)
    if step is None:
        raise ExamUnsupported(f"主数据里没有这节课程：{step_id}")
    level = next((r for r in m.rows("ProduceStepLessonLevel") if r.get("id") == step["produceStepLessonLevelId"]), None)
    if level is None:
        raise ExamUnsupported(f"主数据里没有课程等级 {step['produceStepLessonLevelId']}")
    return level, step.get("name")


def _lesson_loadout(doc: dict) -> dict:
    """bench 的初课程题 → exam_env 写法（同 GKMS scenario/hajime.build_lesson_session，30 节实录课程逐回合分数对过）：
    回合 = limitTurn + exam_extra_turn；满分线 = resultTargetValueLimit + lesson_limit_up_score（打到就结束）；合格线 = successThreshold。"""
    st = int(doc.get("step_type") or 0)
    if st not in LESSON_STEP_TYPES:
        raise ExamUnsupported(f"不是课程步骤：step_type {st}")
    if not doc.get("step_id"):
        raise ExamUnsupported("课程题缺 step_id")
    pt, kind = LESSON_STEP_TYPES[st]
    level, name = lesson_level(doc["step_id"])
    return {
        "produce_id": doc["produce_id"],
        "round": "lesson",
        "idol_card_id": doc["idol_card_id"],
        "produce_cards": list(doc["deck_cards"]),
        "produce_item_ids": list(doc.get("produce_item_ids") or []),
        "start_enchant_ids": list(doc.get("main_item_enchant_ids") or []) + list(doc.get("lesson_start_enchant_ids") or []),
        "supports": list(doc.get("support_cards") or []),
        "drink_ids": list(doc.get("drink_ids") or []),
        "starting_stamina": int(doc.get("starting_stamina") or doc["max_stamina"]),
        "max_stamina": int(doc["max_stamina"]),
        "gimmick_group_id": doc.get("gimmick_group_id") or None,
        "lesson": {"parameter_type": pt, "kind": kind, "name": name, "step_id": doc["step_id"],
                   "limit_turn": int(level["limitTurn"]) + int(doc.get("exam_extra_turn") or 0),
                   "clear_score": int(level.get("successThreshold") or 0),
                   "perfect_score": int(level["resultTargetValueLimit"]) + int(doc.get("lesson_limit_up_score") or 0)},
    }  # fmt: skip


LESSON_KINDS = {"normal": "NORMAL", "sp": "SP", "hard": "HARD"}


def lesson_levels_by_clear(
    produce_id: str, idol_card_id: str | None, kind: str, clear: int, turns: int | None = None
) -> list[dict]:
    """按画面的合格线（CLEARまで 开课时的 N）反查这节课的档位（ProduceStepLessonLevel）：同培育、同种类、successThreshold = clear；
    带 plan 的档位只要偶像的 plan。档位按日程位置定，不是同种同属性第几次（平台 11-18 核两局实机），所以不按次数查。
    初 REGULAR（produce-001）的追い込み用的是 produce-002 的 hard 档（bench 5 节实录：p_step_lesson-produce-002-…-hard-01 / 02）。
    turns：画面开课时的总回合数，档位 limitTurn 不能比它多（加减回合只会 −1 / +N，这里只当上限用）。"""
    from hiro.bench_bridge import tower as tw

    num = str(produce_id or "").split("-")[-1]
    k = str(kind or "normal").lower()
    nums = {num, "002"} if (num == "001" and k == "hard") else {num}
    plan = ""
    if idol_card_id:
        try:
            plan = "plan" + str(_idol_card(idol_card_id)["planType"]).rsplit("Plan", 1)[-1]
        except ExamUnsupported:
            plan = ""
    out = []
    for r in tw.master().rows("ProduceStepLessonLevel"):
        rid = str(r.get("id") or "")
        if int(r.get("successThreshold") or -1) != int(clear) or f"-{k}-" not in rid:
            continue
        if not any(rid.startswith((f"p_step_lesson_level-{n}-", f"p_step_lesson_level-produce_{n}-")) for n in nums):
            continue
        if "-plan" in rid and plan and f"-{plan}-" not in rid:
            continue
        out.append(r)
    return out


def lesson_from_screen(
    produce_id: str, idol_card_id: str | None, kind: str, attr: str, screen: dict
) -> tuple[dict, list[str]]:
    """实机课程（平台的课程画面读数 state.exam_session.lesson：turns_total / clear_target / perfect_target / level_ids /
    lesson_limit_up_score）→ lesson 写法 + 说明。回合数、合格线用画面的；满分线画面读到（过了合格线才出现 PERFECTまで）就用，
    没读到按档位 resultTargetValueLimit + lesson_limit_up_score（档位按合格线反查，候选的满分线不唯一就不封顶）。"""
    from hiro.bench_bridge import tower as tw

    notes: list[str] = []
    k = str(kind or "normal").lower()
    a = {"vo": 1, "da": 2, "vi": 3}.get(str(attr).lower())
    if a is None or k not in LESSON_KINDS:
        raise ExamUnsupported(f"课程种类 / 属性不认识：{kind} {attr}")
    turns, clear, perfect = screen.get("turns_total"), screen.get("clear_target"), screen.get("perfect_target")
    if not turns or clear is None:
        raise ExamUnsupported(f"课程画面读数不全（回合 {turns}、合格线 {clear}）")
    if perfect is None:
        ids = list(screen.get("level_ids") or ())
        rows = [r for r in tw.master().rows("ProduceStepLessonLevel") if r.get("id") in ids] if ids else []
        rows = rows or lesson_levels_by_clear(produce_id, idol_card_id, k, int(clear), int(turns))
        caps = {int(r["resultTargetValueLimit"]) for r in rows}
        if len(caps) == 1:
            up = screen.get("lesson_limit_up_score")
            perfect = caps.pop() + int(up or 0)
            notes.append(
                f"满分线按档位 {sorted(r['id'] for r in rows)} 算 {perfect}"
                + ("" if up is not None else "（满分线加成没读到，按 0）")
            )
        else:
            notes.append(f"满分线没读到、档位对不上（候选 {len(rows)} 个），不封顶")
    return {"parameter_type": a, "kind": LESSON_KINDS[k], "name": None, "limit_turn": int(turns), "clear_score": int(clear),
            "perfect_score": int(perfect) if perfect is not None else None, "source": "screen"}, notes  # fmt: skip


# 课程开局只给弱饮料（协调 11-18，按 3940a9 摘的攻略折中 [g8-試験]：强饮料留到考试）。强 = hiro.state.knowledge.DRINK_CLASSES 档位 1
# （复制 / 抽牌 / 加行动（初星ホエイプロテイン）/ 爆发）。
STRONG_DRINK_TIER = 1


def strong_drink(drink_id: str | None) -> bool:
    from hiro.state.knowledge import DRINK_CLASSES, drink_class

    return bool(drink_id) and DRINK_CLASSES.get(drink_class(drink_id), {}).get("tier") == STRONG_DRINK_TIER


# ---------------------------------------------------------------- 对手分增减（AuditionNpcEnhancePermil）
# 引擎（gakumas_exam）没实现对手分的增减；客户端是这么算的（2026-11-17 反汇编 11-14 客户端
# ExamParameterModel.CalculateAuditionNpc @0x36df15c）：permil ≠ 0 时
#   f = 1 + FloatFromPermil(permil)（float32，permil / 1000.0f）
#   新下限 = Math.Round(f × scoreMin)，新区间 = Math.Round(f × (scoreMax − scoreMin))（float32 乘，四舍六入五取偶）
# 之后按新下限 / 区间逐回合生成对手分（GetScoreList）。permil = UserProduceProgress.AuditionNpcEnhancePermil，服务器按持有的 P 道具算：
#   Σ 道具的 AuditionNpcEnhance（+）/ AuditionNpcWeaken（−）值 × 该道具已发动次数（fire_count；没发动过算 0）
# 对过 GKMS 抓包里 7 组（100 / 1010 / 1250 / −800），全对。N.I.Aキー（黄緑，N.I.A PRO 简单模式开局给，开局就发动）= −800。


NIA_EASY_KEY = "pitem_00-3-330-0"  # N.I.Aキー（黄緑）：N.I.A PRO 简单模式开局给，对手分 −800‰


def _f32(x: float) -> float:
    import struct

    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def scale_npc_range(score_min: int, score_max: int, permil: int | None) -> tuple[int, int]:
    """对手分区间按 AuditionNpcEnhancePermil 缩放（客户端算法，见上）。"""
    if not permil:
        return int(score_min), int(score_max)
    f = _f32(_f32(_f32(int(permil)) / _f32(1000.0)) + 1.0)
    lo = round(_f32(f * _f32(int(score_min))))  # Python round：五取偶，同 C# Math.Round
    rg = round(_f32(f * _f32(int(score_max) - int(score_min))))
    return lo, lo + rg


@lru_cache(maxsize=1)
def _npc_item_values() -> dict:
    """P 道具 id → 它的对手分增减值（千分比，增 +、减 −）；没有这种效果的不在表里。"""
    from hiro.bench_bridge import tower as tw

    m = tw.master()
    pe = {r["id"]: r for r in m.rows("ProduceEffect")}
    ie = {r["id"]: r for r in m.rows("ProduceItemEffect")}
    out: dict = {}
    for it in m.rows("ProduceItem"):
        v = 0
        for e in it.get("produceItemEffectIds") or ():
            q = pe.get((ie.get(e) or {}).get("produceEffectId")) or {}
            t = str(q.get("produceEffectType") or "")
            if t.endswith("AuditionNpcEnhance"):
                v += int(q.get("effectValueMin") or 0)
            elif t.endswith("AuditionNpcWeaken"):
                v -= int(q.get("effectValueMin") or 0)
        if v:
            out[it["id"]] = v
    return out


def npc_enhance_permil(items) -> tuple[int, list[str]]:
    """持有的 P 道具 → (对手分增减千分比, 不确定的道具)。items 写成 id 或 {produce_item_id / id, fire_count}。
    给了 fire_count 就按它；只给 id（我们的 P 道具账本不记发动次数）时：N.I.Aキー 这类开局就发动的按 1 次，
    チャレンジP道具 这类按条件累计发动的按 1 次并记进「不确定」（实际可能 3–5 次，见抓包）。"""
    values = _npc_item_values()
    total, unsure = 0, []
    for it in items or ():
        iid = it if isinstance(it, str) else (it.get("produce_item_id") or it.get("id"))
        v = values.get(iid)
        if not v:
            continue
        if isinstance(it, dict) and "fire_count" in it:
            total += v * int(it.get("fire_count") or 0)
        else:
            total += v
            if "challenge" in str(iid):
                unsure.append(iid)
    return total, unsure


def loadout_npc_permil(lo: dict) -> int:
    """这一场对手分增减千分比：lo 里显式给了 npc_enhance_permil（画面 / 抓包读到的）就用，否则按 P 道具推。"""
    if lo.get("npc_enhance_permil") is not None:
        return int(lo["npc_enhance_permil"])
    return npc_enhance_permil(lo.get("produce_item_ids") or ())[0]


def objective(lo: dict, rivals: list | None = None) -> dict:
    """search / plan_many 的终局目标（rivals：对手终局分，从高到低）：画面 / ctx.meta 给的优先，没有用主数据对手组的 scoreMax（保守）；
    再加按场次的目标（stage_objective：过关名次、分数封顶、课程合格线）。"""
    from hiro.bench_bridge import tower as tw

    if rivals:
        top = sorted((int(x) for x in rivals), reverse=True)
    else:
        npc = (lo.get("exam") or {}).get("produce_exam_battle_npc_group_id")
        rows = [r for r in tw.master().rows("ProduceExamBattleNpcGroup") if r.get("id") == npc] if npc else []
        permil = loadout_npc_permil(
            lo
        )  # 主数据区间没算对手分增减（N.I.Aキー −80%、チャレンジP道具 +…%）：按客户端算法缩放
        top = sorted((scale_npc_range(int(r["scoreMin"]), int(r["scoreMax"]), permil)[1] for r in rows), reverse=True)
    out = {"rivals": top, "lost_ssr_max": None, "debuffs_min": None}
    out.update(stage_objective(lo))
    return out


def stage_objective(lo: dict) -> dict:
    """按场次的目标（3940a9，设计稿第五节）：只给有值的键（旧 Rust 包不认这几个参数）。
    - 过关名次 pass_rank：主数据 rankThreshold（初 = 3，N.I.A = 1），名次在线外一律 0 档；
    - 分数封顶 score_cap：forceEndScore > 0 时（初 REGULAR 中間 900，到分考试就结束，多的分没用）；
    - 课程（以后）：clear_score = 合格线、score_cap = 满分线。"""
    aud = lo.get("audition") or {}
    out: dict = {}
    if aud.get("rankThreshold"):
        out["pass_rank"] = int(aud["rankThreshold"])
    if int(aud.get("forceEndScore") or 0) > 0:
        out["score_cap"] = int(aud["forceEndScore"])
    lesson = lo.get("lesson") or {}
    if lesson.get("clear_score") is not None:
        out["clear_score"] = int(lesson["clear_score"])
        if lesson.get("perfect_score"):
            out["score_cap"] = int(lesson["perfect_score"])
        else:
            out.pop("score_cap", None)
    return out
