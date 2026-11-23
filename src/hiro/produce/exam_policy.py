"""考试出牌的决策源（reports/analysis/expert-preview-pilot.md）。

    llm     现状：规则 → 本回合计划 → LLM → 硬约束 → 兜底（BattleBrain.decide）
    expert  模拟器能还原本场时，直接执行预演期望终局分最高的合法动作；还原不了 / 预演不可用时退回 llm
    shadow  仍由 llm 出牌；同时算出专家的选择，写进记录供对照（零风险的第一步）

开关：run_hif --exam-policy {llm,expert,shadow}，或环境变量 HIRO_EXAM_POLICY；默认 llm。
专家模式下 rules.violations 不拦截（预演按游戏规则模拟，已包含这些经验要防的后果），只记录。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from hiro.brain.rules import Decision, violations

POLICIES = ("llm", "expert", "shadow")
# E1：饮料比最好的非饮料动作多不到 1 个标准误时不喝。bench（专家 + 社区续打，32 局配对）第九局 R1 +5.1 万 ± 1.8 万、R2 +10.4 万 ± 3.4 万、
# 第十局 R1 +1.9 万 ± 1.3 万，详见 reports/analysis/p1-community-rollout.md
EXPERT_DRINK_SE = True
EXPERT_DRINK_RULE = (
    "R1"  # 饮料阶段预算：剩余 >4 回合时每回合最多喝 1 瓶（None 关闭；R1d = 抽牌类不受限）。见 drink_budget_blocks
)
# 核心兑现牌最早在剩几回合时打（剩余回合 > 它时专家不打，有别的动作可选时）。第十局本战 R1：剩 9 回合、元気 0 时打出
# せのびの魔法+，R1 只有 46,570。续打（当时的 heuristic；2026-11-02 起一律社区打法）不会在后段安排 せのび，「留着」被估得一文不值（和饮料是同一类偏差，长期靠 P1）
# **默认关闭**：bench（第十局本战牌组 R1 / R2 合成开局，专家 32 局配对）里，留到剩 ≤5（せのび）/ ≤2（届いて）/ ≤3（約束）
# R1 −8.4 万 ± 2.6 万、R2 −7.9 万 ± 2.9 万，各种阈值都显著为负：R1 只有 9 回合，留着的牌进弃牌堆后常常来不及再抽回来。
# 第十局 R1 的主因是存档还原出的行动数为 0、专家在还有行动时结束了回合（battle._sim_state 已修正），不是 せのび 打早了。
# 例：{"p_card-02-act-3_186": 5, "p_card-02-act-3_039": 2, "p_card-02-act-3_045": 3}
# E3（用户 2026-11-01）：届いて 留到剩 ≤2 回合（bench 在噪声内，按用户决定合入）。E2（せのび 留到剩 ≤5）两轮 bench 都显著为负，不合入
EXPERT_CASH_HOLD: dict = {"p_card-02-act-3_039": 2}
# E3 的方式（总控 2026-11-02 第十八局本战复盘）：hold 现状（剩 > 2 一律不打）/ off 关掉 / cycle 按抽回概率（抽不回来就现在兑现）
EXPERT_CASH_MODE = (
    "cycle"  # 2026-11-03 重测（32 局 × 4 考题，去泄漏）：cycle 合并 +2.2k ± 2.2k、选拔③ +13.0k ± 5.2k，不更差 → 默认
)
CASH_GENKI_GROWTH = 1.3  # 终结牌留到后面时元気的大致增长
# 用户 q11 批注归纳的打法硬规则（datasets/annotations/notes.jsonl；docs/tasks/决策-09-q11批注.md）。名字在集合里才生效；
# 合入标准（用户 2026-11-01）：bench 显著为正或在噪声内才开，显著为负不开、报数据。各条 bench 结果见 docs/tasks/决策-09-q11批注.md §3
#   R1 同回合先叠元気 / やる気 再兑现     R2 不耗行动的牌先打          R3 饮料按用途留（加分 ≤4 回合 / 回血末回合或没血 / 抽牌没牌可打时）
#   R6 有可打的有用牌时不结束回合；没有就打眠気；有用的牌优先于眠気      R8 偏离路线的牌（オトメゴコロ、好印象系）有别的有用牌时不打
#   R9 同回合先叠 やる気 再打加元気的牌（用户 2026-11-03：止められない想い+ 先于元気弹，先叠やる気再加元気可以多 4 点元気）
RULES_ALL = ("R3", "R6", "R8", "R2", "R9", "R1")  # 应用顺序：先筛饮料，再定「打不打」，再定先打哪张
EXPERT_RULES: set = set(RULES_ALL)  # 2026-11-01 bench 至今没有显著为负的（决策-09 §3）；显著为负的从这里去掉
# S0（用户 2026-11-03，第十八、十九局本战复盘）：纯加数值的饮料开局就喝——「这种数值越早叠，总是越正向的」。
# 纯加数值 = knowledge.STAT_DRINK_CLASSES（やる気 / 元気 / 好印象 / 平加：永久叠加、没有时机依赖；ブレンド、コーヒー、烏龍茶、ハーブティー、ルイボス、初星水）。
# 手里有、能喝时直接喝（多瓶就连喝），drink_budget（每回合最多 1 瓶）和 E1（不到 1 个标准误不喝）都不拦它。
STAT_DRINK_FIRST = True
# D37（协调 2026-11-04，第三十二局 #236：R2 剩 12、手里只有 せのび+、已喝 コーヒー，drink_budget 拦下 パワフル漢方，预演 +9.4 万 ≈ 5 SE）：
# 「卡手时抽牌类不拦」的两个缺口。行为变化，都默认关，由协调 / 总控决定：
#   STUCK_FETCH_EXEMPT：豁免的类别再加检索（knowledge 的 fetch：漢方 把固有卡从山札 / 捨札拿到手里，卡手时作用和抽牌一样）；
#   STUCK_BY_PREVIEW：「卡手」再加一种——手里能打的牌在预演里都不如结束回合（#236 せのび+ 能打但比结束回合低 5.3 万，现规则不算卡手）。
STUCK_FETCH_EXEMPT = False  # 协调 2026-11-04 定不开：#236 实测 T1 喝 − 实机路径 −2.5 万 ± 1.6 万
STUCK_BY_PREVIEW = False
# 只作数据（协调 2026-11-04）：空手豁免只在剩余回合 ≤ N 时放行（None = 现行，不限）。空手豁免是用户规则，没有用户同意不改默认
STUCK_EMPTY_MAX_REM: int | None = None
PREVIEW_BUDGET_S = (
    7.5  # 专家 / shadow 模式下每步预演的时间预算（preview_actions 的 deadline）；p90 约为它 +0.2 s（离线验收）
)

# 退回 llm 的原因（记 exam_policy_fallback）
FALLBACK_REASONS = ("sim_unsupported", "save_unavailable", "save_mismatch", "no_previews", "blocked_or_unmapped")
_LIVE_REASON = {"sim_unsupported": "sim_unsupported", "sim_error": "sim_unsupported", "save_error": "save_unavailable", "save_missing": "save_unavailable",
                "save_changing": "save_unavailable", "save_playing": "save_unavailable", "save_unreadable": "save_unavailable",
                "remaining": "save_mismatch", "hand_count": "save_mismatch", "hand_cards": "save_mismatch", "save_stale": "save_mismatch", "hand_count_1": "save_mismatch"}  # fmt: skip


def policy_from_env(value: str | None = None) -> str:
    p = (value or os.environ.get("HIRO_EXAM_POLICY") or "llm").strip().lower()
    if p not in POLICIES:
        raise ValueError(f"exam policy {p!r} 不在 {POLICIES}")
    return p


@dataclass
class ExpertPick:
    decision: Decision | None  # 专家的选择；None 表示这一步专家不可用
    reason: str | None  # 不可用的原因（FALLBACK_REASONS）
    expected: float | None = None
    gap: float | None = None  # 与次优的期望差
    violations: list[str] | None = None  # 专家动作违反的规则（只记录，不拦截）
    hard_rules: list | None = None  # 这一步被硬规则拦下 / 改选的记录


def expert_pick(state, live_why: str | None = None) -> ExpertPick:
    """state：BattleState（存档路径带 action_previews）。live_why：_live_state 给的存档 / 模拟器状态（None = 正常）。"""
    if live_why is not None and not state.action_previews:
        return ExpertPick(None, _LIVE_REASON.get(live_why, "save_unavailable"))
    rows = state.action_previews or []
    if not rows:
        return ExpertPick(None, "no_previews")
    legal = set(state.legal_actions())
    cands = sorted((r for r in rows if r.get("legal_action") in legal), key=lambda r: -r["期望终局分"])
    fired: list[dict] = []  # 硬规则拦下 / 改选的记录（实机 rec.log("hard_rule")，总控本战后统计）

    def apply(rule: str, keep, why: str) -> None:
        nonlocal cands
        before = cands[0]["legal_action"] if cands else None
        kept = [r for r in cands if keep(r)]
        if kept and len(kept) < len(cands):
            cands = kept
            if cands[0]["legal_action"] != before:
                fired.append({"rule": rule, "remaining": state.remaining_turns, "blocked": before, "chosen": cands[0]["legal_action"],
                              "why": why})  # fmt: skip

    stat_rows = [r for r in cands if _is_stat_drink(state, r["legal_action"])] if STAT_DRINK_FIRST else []
    blocked = drink_budget_blocks(state)
    if blocked:
        rows_now = list(cands)
        apply(
            "drink_budget",
            lambda r: not r["legal_action"].startswith("drink") or _budget_exempt(state, r["legal_action"], rows_now),
            blocked,
        )
    if EXPERT_DRINK_SE and cands and cands[0]["legal_action"].startswith("drink"):
        # E1：饮料只比最好的非饮料动作多不到 1 个标准误：不喝（协调 4a / 用户：噪声里的差不能作为喝的理由）
        alt = next((r for r in cands if not r["legal_action"].startswith("drink")), None)
        if alt is not None and cands[0]["期望终局分"] - alt["期望终局分"] < (alt.get("比最佳标准误") or 0):
            apply("E1_drink_se", lambda r: not r["legal_action"].startswith("drink"),
                  f"饮料只比 {alt.get('动作')} 多 {cands[0]['期望终局分'] - alt['期望终局分']:,}，不到 1 个标准误 {alt.get('比最佳标准误')}")  # fmt: skip
    if any(cash_hold_blocks(state, r["legal_action"]) for r in cands):
        why = next(w for r in cands if (w := cash_hold_blocks(state, r["legal_action"])))
        apply("E3_cash_hold", lambda r: not cash_hold_blocks(state, r["legal_action"]), why)
    for rule in RULES_ALL:
        if rule in EXPERT_RULES and cands:
            got = R_RULES[rule](state, cands)
            if got is not None:
                apply(rule, got[0], got[1])
    if stat_rows:
        top = max(stat_rows, key=lambda r: r["期望终局分"])
        if not cands or cands[0]["legal_action"] != top["legal_action"]:
            fired.append({"rule": "S0_stat_drink", "remaining": state.remaining_turns, "blocked": cands[0]["legal_action"] if cands else None,
                          "chosen": top["legal_action"], "why": f"纯加数值的饮料 {top.get('动作')} 开局就喝（用户 2026-11-03）"})  # fmt: skip
        cands = [top] + [r for r in cands if r is not top]
    if not cands:
        return ExpertPick(None, "blocked_or_unmapped")  # 预演里的动作都被本回合禁用（画面定位不到）或不合法
    best = cands[0]
    gap = best["期望终局分"] - cands[1]["期望终局分"] if len(cands) > 1 else None
    reason = (f"预演专家：{best.get('动作')} 期望终局分 {best['期望终局分']:,}"
              + (f"，比次优 {cands[1].get('动作')} 高 {gap:,.0f}" if gap is not None else "（唯一合法动作）"))  # fmt: skip
    d = Decision(best["legal_action"], reason, "expert")
    if fired:
        d.reason += "；硬规则：" + "；".join(f"{f['rule']}（{f['why']}）" for f in fired)
    return ExpertPick(d, None, best["期望终局分"], gap, violations(state, d), fired)


def drink_budget_blocks(state) -> str | None:
    """专家的饮料阶段预算：回合内搜索对饮料有乐观偏差，续打（当时的 heuristic；2026-11-02 起一律社区打法）又不会在关键回合用饮料，「留着」被低估，专家会在前期连喝
    （第十局选拔③ 第 1 回合连喝 ホエイ、特製、ブースト）。bench（第九局牌组 R2 合成开局，专家 32 局配对）：
    剩余 >4 回合时每回合最多 1 瓶，改进牌组 +11.5 万 ± 3.1 万，实际牌组 +2.2 万 ± 2.9 万。返回拦截原因；不拦时 None。"""
    if EXPERT_DRINK_RULE not in ("R1", "R1d") or state.remaining_turns is None or state.remaining_turns <= 4:
        return None
    drunk = sum(1 for x in state.this_turn or [] if str(x).startswith("喝"))
    return f"饮料预算：剩 {state.remaining_turns} 回合，本回合已喝 {drunk} 瓶" if drunk >= 1 else None


def _drink_of(state, action: str):
    if not action.startswith("drink:"):
        return None
    try:
        slot = int(action.split(":")[1])
    except ValueError:
        return None
    return next((x for x in state.drinks or [] if x.slot == slot), None)


def _is_stat_drink(state, action: str) -> bool:
    """纯加数值的饮料（S0，knowledge.STAT_DRINK_CLASSES）。"""
    from hiro.state.knowledge import STAT_DRINK_CLASSES, drink_class

    d = _drink_of(state, action)
    return d is not None and drink_class(d.drink_id) in STAT_DRINK_CLASSES


def _budget_exempt(state, action: str, rows: list | None = None) -> bool:
    """drink_budget（每回合最多 1 瓶）不拦的饮料：
    - 卡手时的抽牌类饮料（knowledge.DRAW_CLASSES：抽牌 / 换手）：手里没有能打的牌（空手 / 只剩トラブル 或不可用的牌）、还有行动。
      用户 2026-11-02「抽牌药在手头没有可打的牌的时候用」；时机曲线「卡手时喝」+3.5–6.9 万，高于社区旧规则（决策-15 §2）。
      第二十一局 R2 #140：剩 9、空手、还有 1 次行动，drink_budget 拦下 センブリソーダ，只能结束回合（预演 −10.9 万）；
    - R1d（总控 2026-11-02 重测用）：抽牌类一律不拦。
    D37 开关：STUCK_FETCH_EXEMPT 把检索类也算进来；STUCK_BY_PREVIEW 时 rows（这一步的预演行）里能打的牌都不如结束回合也算卡手。"""
    if not action.startswith("drink:"):
        return False
    d = _drink_of(state, action)
    if d is None or not _stuck_exempt_class(d.drink_id):
        return False
    if EXPERT_DRINK_RULE == "R1d":
        return True
    if _stuck(state) and (STUCK_EMPTY_MAX_REM is None or (state.remaining_turns or 0) <= STUCK_EMPTY_MAX_REM):
        return True
    return STUCK_BY_PREVIEW and _preview_stuck(rows)


def _stuck_exempt_class(drink_id: str | None) -> bool:
    from hiro.state.knowledge import DRAW_CLASSES, drink_class

    k = drink_class(drink_id)
    return k in DRAW_CLASSES or (STUCK_FETCH_EXEMPT and k == "fetch")


def _preview_stuck(rows: list | None) -> bool:
    """预演意义上的卡手：有结束回合这一行，且每张能打的牌的期望终局分都不高于它（手里没有能打的牌也算）。"""
    if not rows:
        return False
    end = next((r["期望终局分"] for r in rows if r.get("legal_action") == "skip"), None)
    return end is not None and all(
        r["期望终局分"] <= end for r in rows if str(r.get("legal_action", "")).startswith("play:")
    )


def _stuck(state) -> bool:
    """卡手：手里没有能打的非トラブル牌。"""
    return not any(c.available and c.category != "trouble" for c in state.hand or [])


def cash_hold_blocks(state, action: str) -> str | None:
    """核心兑现牌（せのび / 届いて / 約束）在剩余回合大于 EXPERT_CASH_HOLD 时不打。返回原因；不拦时 None。"""
    if not action.startswith("play:") or state.remaining_turns is None:
        return None
    try:
        card = next(c for c in state.hand if c.slot == int(action.split(":")[1]))
    except (StopIteration, ValueError):
        return None
    limit = EXPERT_CASH_HOLD.get(card.card_id or "")
    if limit is None or state.remaining_turns <= limit or EXPERT_CASH_MODE == "off":
        return None
    if EXPERT_CASH_MODE == "cycle" and state.piles is not None:
        # 牌库循环（第十八局本战 R2 第 3 次 #43：E3 拦下 届いて，预演说少 13.9 万；选拔③ probe：抽回概率低就该兑现）：
        # 不打的话回合末进弃牌堆，之后还能抽回的概率 P（redraw_chance，只用公开牌堆）；「P × 元気增长 × 之后最高倍率」不如现在的倍率就放行
        from hiro.brain.state import redraw_chance

        p_back = redraw_chance(
            state.piles, card.name, [(c.name, c.tags.once) for c in state.hand], state.remaining_turns - 1
        )
        now = state.now_turn_permil or state.multiplier_permil
        future = max([state.final_turn_permil or 0] + list((state.attr_permils or {}).values()))
        if now and future and p_back * CASH_GENKI_GROWTH * future < now:
            return None
        return (f"{card.name} 留到剩 {limit} 回合以内再打（现在剩 {state.remaining_turns}；不打的话之后抽回概率 {p_back:.0%}，"
                f"× {CASH_GENKI_GROWTH} × 之后最高倍率 {future} ≥ 现在 {now}）")  # fmt: skip
    return f"{card.name} 留到剩 {limit} 回合以内再打（现在剩 {state.remaining_turns}）"


def _card(state, action: str):
    if not action.startswith("play:"):
        return None
    try:
        return next(c for c in state.hand if c.slot == int(action.split(":")[1]))
    except (StopIteration, ValueError):
        return None


def _drink(state, action: str):
    if not action.startswith("drink:"):
        return None
    try:
        return next(d for d in state.drinks if d.slot == int(action.split(":")[1]))
    except (StopIteration, ValueError):
        return None


def _actions_after(row) -> int | None:
    """预演即时结果里的「之后剩余行动」（随机时取最小）。"""
    imm = row.get("即时结果")
    v = imm.get("之后剩余行动") if isinstance(imm, dict) else None
    if isinstance(v, str):
        try:
            return int(v.split("~")[0])
        except ValueError:
            return None
    return v


_BUILD = re.compile(r"(元気|やる気)\+\d")
_CASH = re.compile(r"(元気|やる気|好印象)の\d+%分パラメータ上昇")


def is_builder(card) -> bool:
    """叠资源的牌：加元気 / やる気、本身不按资源得分（距離感の基本、えいえいおー、イメトレ、ひなたぼっこ…）。"""
    return card is not None and card.category == "mental" and bool(_BUILD.search(card.text)) and not is_cash(card)


def is_cash(card) -> bool:
    """按元気 / やる気 / 好印象 得分的牌（仕草の基本、紙吹雪、ありがとうの言葉、約束、届いて…）；持续 / 延迟的不算。"""
    if card is None or card.category == "trouble":
        return False
    t = card.tags
    return bool(t.genki_score_pct or t.impression_score_pct or t.clears_genki) or any(
        not any(k in card.text[max(0, m.start() - 25): m.start()] for k in ("ターン開始時", "ターン後", "スキルカード使用時"))
        for m in _CASH.finditer(card.text))  # fmt: skip


def is_useful(state, row) -> bool:
    """R6 的「有用的牌」：不是眠気等麻烦牌、不是清空元気的终结牌、不被 E3 留着、打出去有正面效果。"""
    card = _card(state, row["legal_action"])
    if card is None or card.category == "trouble" or card.tags.is_sleep or card.tags.clears_genki:
        return False
    if cash_hold_blocks(state, row["legal_action"]):
        return False
    imm = row.get("即时结果")
    if not isinstance(imm, dict):
        return False
    gains = [v for k, v in imm.items() if k in ("分数", "元气", "干劲", "好印象") and isinstance(v, str)]
    return any(not v.startswith("-") for v in gains) or bool(card.tags.extra_actions or card.tags.draws)


def _is_skip(row) -> bool:
    return row["legal_action"] == "skip"


def rule_r1(state, cands):
    """R1：最好的是兑现牌、手里有叠元気 / やる気 的牌且打完还剩行动（兑现牌仍能打）时，先打叠资源的牌。"""
    best = cands[0]
    if not is_cash(_card(state, best["legal_action"])):
        return None
    builders = [r for r in cands if is_builder(_card(state, r["legal_action"])) and (_actions_after(r) or 0) >= 1]
    if not builders:
        return None
    keep = {r["legal_action"] for r in builders}
    return (lambda r: r["legal_action"] in keep,
            f"先叠资源再兑现：{builders[0].get('动作')} 打完还剩行动，{best.get('动作')} 放后面")  # fmt: skip


def rule_r2(state, cands):
    """R2：最好的动作会用掉行动（打完剩 0 / 结束回合）、手里有不耗行动的有用牌时，先打不耗行动的牌。"""
    best = cands[0]
    if best["legal_action"].startswith("drink") or (not _is_skip(best) and (_actions_after(best) or 0) >= 1):
        return None
    from hiro.state.knowledge import is_conflict

    free = [r for r in cands if r["legal_action"].startswith("play") and (_card(state, r["legal_action"]).tags.extra_actions or 0) >= 1
            and (_actions_after(r) or 0) >= 1 and is_useful(state, r) and not is_conflict(_card(state, r["legal_action"]).card_id)]  # fmt: skip
    if not free:
        return None
    keep = {r["legal_action"] for r in free}
    return (
        lambda r: r["legal_action"] in keep,
        f"不耗行动的 {free[0].get('动作')} 先打（{best.get('动作')} 会用掉行动）",
    )


def _gives_genki(card) -> bool:
    """打出时加元気（元気+N 按 やる気 放大）。"""
    return card is not None and card.category != "trouble" and "元気+" in card.text and not is_cash(card)


def _pure_yaruki(card) -> bool:
    """只叠 やる気、不加元気的牌（止められない想い、ゆめみごこち、ワクワクが止まらない、思い出し笑い）。"""
    return (
        card is not None
        and card.category != "trouble"
        and "やる気+" in card.text
        and "元気+" not in card.text
        and not is_cash(card)
    )


def rule_r9(state, cands):
    """R9（用户 2026-11-03）：本回合要打的几张牌按回合内收益排——最好的是加元気的牌、手里有只叠 やる気 的牌且打完还剩行动（加元気的牌仍能打）时，
    先打 やる気 牌：元気增量按 やる気 放大，先叠 やる気 多出来的元気是确定的（不依赖抽牌），不交给续打噪声。
    第十八、十九局本战 114 个回合里 9 次反了（えいえいおー / デイドリーミング 先于 ワクワク / 止められない想い+ / ゆめみごこち+）。"""
    best = cands[0]
    if not _gives_genki(_card(state, best["legal_action"])):
        return None
    from hiro.state.knowledge import is_conflict

    first = [r for r in cands if r["legal_action"].startswith("play") and _pure_yaruki(c := _card(state, r["legal_action"])) and c.available
             and (_actions_after(r) or 0) >= 1 and not is_conflict(c.card_id)]  # fmt: skip
    if not first:
        return None
    keep = {r["legal_action"] for r in first}
    return (
        lambda r: r["legal_action"] in keep,
        f"先叠やる気：{first[0].get('动作')} 打完还剩行动，{best.get('动作')}（加元気）放后面",
    )


BOOST_DRINK_TURNS = 4  # 加分饮料（パラメータ上昇量増加N%（Nターン））留到剩这么多回合以内
RECOVER_HP_FRAC = 0.25  # 回血饮料：体力不高于最大体力的这个比例才喝（「完全没体力」）；末回合不限
R3_DRAW_DRINK = False  # R3「手里有有用牌时不喝抽牌饮料」：bench（本局饮料 ブースト/センブリ/おしゃれ/特製，六组 24 局配对）关掉合计 +3.9 万 ± 1.5 万，显著为负，不开（复盘-main13-R1.md）


def drink_purpose(d) -> str | None:
    t = d.text
    if "手札をすべて入れ替える" in t:
        return "swap"
    if "スキルカードを" in t and "引く" in t:
        return "draw"
    if re.search(r"パラメータ上昇量増加\d+%（\d+ターン）", t) and "引く" not in t:
        return "boost"
    if re.fullmatch(r"体力回復\d+", t.strip()):
        return "recover"
    return None


def rule_r3(state, cands):
    """R3：饮料按用途留。加分（ブーストエキス）剩 ≤4 回合才喝；回血（リカバリドリンク）末回合或体力很低才喝；
    抽牌（センブリソーダ）手里没有有用的牌可打时才喝。"""
    rem = state.remaining_turns
    useful = any(is_useful(state, r) for r in cands if r["legal_action"].startswith("play"))
    why: dict = {}
    for r in cands:
        d = _drink(state, r["legal_action"])
        if d is None:
            continue
        kind = drink_purpose(d)
        if kind == "boost" and rem is not None and rem > BOOST_DRINK_TURNS:
            why[r["legal_action"]] = f"{d.name} 留到剩 {BOOST_DRINK_TURNS} 回合以内（现在剩 {rem}）"
        elif (
            kind == "recover"
            and rem is not None
            and rem > 1
            and state.hp is not None
            and state.max_hp
            and state.hp > state.max_hp * RECOVER_HP_FRAC
        ):
            why[r["legal_action"]] = (
                f"{d.name} 留到末回合 / 体力 ≤{RECOVER_HP_FRAC:.0%}（现在 {state.hp}/{state.max_hp}）"
            )
        elif kind == "draw" and useful and R3_DRAW_DRINK:
            why[r["legal_action"]] = f"{d.name} 手里还有能打的牌，先不喝"
    if not why or cands[0]["legal_action"] not in why:
        return None
    return lambda r: r["legal_action"] not in why, why[cands[0]["legal_action"]]


def rule_r6(state, cands):
    """R6：最好的是结束回合 / 打眠気、手里有有用的牌可打时，打有用的牌；结束回合但只有眠気可打时，打眠気（除外、压缩牌堆）。"""
    best = cands[0]
    best_card = _card(state, best["legal_action"])
    trouble = best_card is not None and (best_card.category == "trouble" or best_card.tags.is_sleep)
    if not (_is_skip(best) or trouble):
        return None
    useful = [r for r in cands if r["legal_action"].startswith("play") and is_useful(state, r)]
    # 不空过时先打叠资源的牌（R1 的思路：兑现牌留到元気 / 倍率更高时）；没有叠资源的才打兑现牌（q11 选拔③ #35：イメトレ 而不是 約束）
    builders = [r for r in useful if is_builder(_card(state, r["legal_action"]))]
    useful = builders or useful
    if useful:
        keep = {r["legal_action"] for r in useful}
        return (
            lambda r: r["legal_action"] in keep,
            f"有能打的 {useful[0].get('动作')}，不{'结束回合' if _is_skip(best) else '打眠気'}",
        )
    if _is_skip(best):
        sleep = [r for r in cands if (c := _card(state, r["legal_action"])) is not None and c.tags.is_sleep]
        if sleep:
            keep = {r["legal_action"] for r in sleep}
            return lambda r: r["legal_action"] in keep, "没有有用的牌：打掉眠気（除外）而不是空过"
    return None


def rule_r8(state, cands):
    """R8：最好的是偏离路线的牌（knowledge.is_conflict：好印象系、オトメゴコロ 等）、手里有别的有用牌时，打有用的牌。"""
    from hiro.state.knowledge import is_conflict

    card = _card(state, cands[0]["legal_action"])
    if card is None or not is_conflict(card.card_id):
        return None
    other = [r for r in cands if r["legal_action"].startswith("play") and is_useful(state, r)
             and not is_conflict((_card(state, r["legal_action"]) or card).card_id)]  # fmt: skip
    if not other:
        return None
    keep = {r["legal_action"] for r in other}
    return lambda r: r["legal_action"] in keep, f"{card.name} 偏离路线，先打 {other[0].get('动作')}"


R_RULES = {"R1": rule_r1, "R2": rule_r2, "R3": rule_r3, "R6": rule_r6, "R8": rule_r8, "R9": rule_r9}


def shadow_record(pick: ExpertPick, chosen: Decision, state) -> dict:
    """shadow 模式的对照记录：专家选择、LLM（或规则）的选择、两者在预演里的期望差。"""
    rows = {r.get("legal_action"): r for r in state.action_previews or []}
    mine = rows.get(chosen.action, {}).get("期望终局分")
    return {
        "expert_action": pick.decision.action if pick.decision else None,
        "expert_expected": pick.expected,
        "expert_unavailable": pick.reason,
        "chosen_action": chosen.action,
        "chosen_source": chosen.source,
        "chosen_expected": mine,
        "agree": bool(pick.decision and pick.decision.action == chosen.action),
        "expected_gap": (pick.expected - mine) if pick.expected is not None and mine is not None else None,
        "expert_violations": pick.violations or None,
    }


def decide_with_policy(
    policy: str, state, memory, brain, live_off: str | None = None
) -> tuple[Decision, ExpertPick | None]:
    """按决策源出一步：expert 且专家可用 → 专家；否则 BattleBrain.decide（llm / shadow / 专家不可用时退回）。
    返回 (决策, 专家的选择或 None)；调用方据此记 exam_policy_fallback / exam_shadow。"""
    pick = expert_pick(state, live_off) if policy != "llm" else None
    if policy == "expert" and pick.decision is not None:
        d = pick.decision
        d.warnings = [f"专家动作违反规则（不拦截，只记录）：{v}" for v in pick.violations or []]
        return d, pick
    d = brain.decide(state, memory)
    return guard_llm(state, memory, brain, d), pick


def llm_blocks(state, action: str) -> str | None:
    """LLM / 规则层（没有预演）选的动作是否被硬规则禁止：只用看局面就能判的那几条——饮料预算、E3（届いて 留到剩 ≤2）、
    R3 的加分 / 回血饮料（以及开着时的追加行动饮料）。需要预演即时结果的（R1 R2 R6 R8、R3 抽牌饮料）判不了，不拦。返回原因或 None。"""
    legal = list(state.legal_actions())
    if action not in legal:
        return None
    # 没有预演：把它当成期望最高、其余并列，跑一遍专家的规则链，看规则会不会改选（E1 在这些占位行上不会触发：差 1 分、标准误 0）
    rows = [
        {"legal_action": a, "动作": a, "期望终局分": 1 if a == action else 0, "即时结果": "无变化", "比最佳标准误": 0}
        for a in legal
    ]
    saved = state.action_previews
    state.action_previews = rows
    try:
        pick = expert_pick(state)
    finally:
        state.action_previews = saved
    if pick.decision is None or pick.decision.action == action:
        return None
    return "；".join(f"{f['rule']}：{f['why']}" for f in pick.hard_rules or []) or "硬规则禁止"


FORCING_RULES = ("S0_stat_drink",)  # 指定了「必须做哪个动作」的硬规则（不是只禁止）：LLM 被它拦下时直接执行它选的动作


def llm_forced(state, action: str) -> tuple[str, str] | None:
    """LLM / 规则层的选择被「指定动作」类硬规则（S0：纯加数值饮料开局就喝）改选时，返回 (该执行的动作, 原因)；否则 None。"""
    legal = list(state.legal_actions())
    if action not in legal:
        return None
    rows = [
        {"legal_action": a, "动作": a, "期望终局分": 1 if a == action else 0, "即时结果": "无变化", "比最佳标准误": 0}
        for a in legal
    ]
    saved = state.action_previews
    state.action_previews = rows
    try:
        pick = expert_pick(state)
    finally:
        state.action_previews = saved
    hit = next((f for f in reversed(pick.hard_rules or []) if f["rule"] in FORCING_RULES), None)
    if hit is None or pick.decision is None or pick.decision.action == action:
        return None
    return pick.decision.action, f"{hit['rule']}：{hit['why']}"


def guard_llm(state, memory, brain, d, *, retry: bool = True):
    """总控 2026-11-02（main13 R1 #138：退回 LLM 时第 9 回合打了 届いて，E3 只在专家路径生效）：LLM / 规则层的选择也过同一套硬规则。
    被禁时把这个动作当本回合禁用、重新决定一次；还被禁就用兜底启发式在剩下的动作里选。"""
    why = llm_blocks(state, d.action)
    if why is None:
        return d
    from hiro.brain.rules import fallback

    forced = llm_forced(state, d.action)
    if forced is not None:
        # 第二十局 R2 再挑戦第 1 步：S0 拦下 LLM 的 play:1 后，兜底启发式打了 play:0、没喝饮料。指定动作的规则直接执行它选的动作
        alt = Decision(forced[0], f"硬规则 {forced[1]}", "rule")
        alt.warnings = [f"{d.source} 选了 {d.action}，被硬规则拦下（{why}），改为 {alt.action}"]
        if memory is not None:
            memory.plan = []
        return alt

    blocked = list(state.blocked)
    state.blocked = blocked + [d.action]
    try:
        alt = brain.decide(state, memory) if retry else None
        if alt is None or llm_blocks(state, alt.action) is not None or alt.action == d.action:
            alt = fallback(state)
    finally:
        state.blocked = blocked
    alt.warnings = list(alt.warnings or []) + [f"{d.source} 选了 {d.action}，被硬规则拦下（{why}），改为 {alt.action}"]
    alt.plan = []
    if memory is not None:
        memory.plan = []  # 被拦的计划不再沿用
    return alt
