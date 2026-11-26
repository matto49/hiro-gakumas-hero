"""饮料持有决策：拿 / 换哪瓶 / 不拿 / 买（用户 2026-11-02 第十六局复盘，经总控 / 协调转达，用户已 review；同日更正）。

总原则（用户更正）：社区规则是「优先参考的优质社区概念」，给默认优先级、默认选择和兜底；完整对局模拟确实算出更好
（同一批种子配对，差距 > 2 个标准误）时可以调整，跨优先档也可以，并在决策记录里写明偏离了哪条社区规则、模拟差多少。
1. 每瓶饮料按主数据效果归类（knowledge.drink_class），类别给出默认档位和同档内的社区默认顺序（knowledge.DRINK_CLASSES）。
2. 默认档位按「持有组合」算：
   - 第一瓶复制 → 0 档（高价值：复制哪张、什么时候喝由考试里的预演决定，せのび / 紙吹雪 / 約束 / デイドリ 都可能）；
   - 前 BURST_MUST 瓶爆发 → 0 档（兑现回合；R2 两瓶）；
   - 抽牌类（抽牌 / 换手）超过 DRAW_CAP 的 → 4 档（默认限量：喝早会转乱牌堆，用户批注）；
   - 空位算 3.5 档：比回复 / 元気 差，比好印象 / 超限抽牌 好。
3. 候选持有组合按「档位从好到坏排序后的列表」比较，取规则默认。
4. 模拟：规则默认和其余候选（下一场考试的合成开局，同一批种子配对打完整局，hiro.sim.resource，和轮间 B3 同一套方法）。
   某个候选比规则默认好 > 2 个标准误 → 改选它，记录偏离（reports/deviations.jsonl，定期汇总给协调由用户 review）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from hiro.state.knowledge import BURST_MUST, CLASS_BUFFS, DRAW_CAP, DRAW_CLASSES, DRINK_CLASSES, drink_class

logger = logging.getLogger(__name__)

EMPTY_TIER = 3.5
FLOW_MATCH_TIER = 2  # 加偶像拿手 buff 的饮料（D12，同 やる気 之于广）
FLOW_CONFLICT_TIER = 4  # 加冲突 buff 的饮料（同一种プラン里另一种 buff，flows.OTHER_BUFFS；同 好印象 之于广）
OVERRIDE_SE = 2.0  # 模拟要好过规则默认几个标准误才改（用户更正 2026-11-02：建议 > 2 个标准误，跨档也可以）
MAX_SIM_OPTIONS = 8
NOOP = ("keep", "skip", "none")  # 「不动」的候选键  # 一次最多模拟几个候选（按规则排名取前几个）
SIM = True  # 测试 / 兜底开关：False 时只按规则


def stage_of(mode: str | None, *, interval: bool = False, after_r1: bool = False) -> str:
    """下一场考试是哪一段：选拔 / R1 / R2。"""
    from hiro.state.produce import FINALS

    if mode != FINALS:
        return "selection"
    return "r2" if interval or after_r1 else "r1"


def class_tier(k: str, buff: str | None = None) -> int | float:
    """一类饮料的默认档位。buff：偶像拿手 buff（D12，初 / N.I.A 按流派，同换卡 hiro.brain.flows）——加拿手 buff 的提到 2 档，
    加冲突 buff 的降到 4 档；None 时用 DRINK_CLASSES 原表（原表是按广的 やる気 定的：やる気 2 档、好印象 4 档）。"""
    tier = DRINK_CLASSES[k]["tier"]
    if buff and k in CLASS_BUFFS:
        from hiro.brain.flows import OTHER_BUFFS

        if buff in CLASS_BUFFS[k]:
            return min(tier, FLOW_MATCH_TIER)
        if any(b in OTHER_BUFFS.get(buff, ()) for b in CLASS_BUFFS[k]):
            return FLOW_CONFLICT_TIER
    return tier


def tag(drinks: list, stage: str, buff: str | None = None) -> list[tuple]:
    """每瓶饮料的 (档位, 同档顺序, 类别)，与输入顺序对齐。必留 / 超限按社区默认顺序先到先得。buff 见 class_tier。"""
    cls = [drink_class(d) for d in drinks]
    order = sorted(range(len(drinks)), key=lambda i: (class_tier(cls[i], buff), DRINK_CLASSES[cls[i]]["order"], i))
    out: list = [None] * len(drinks)
    copies = bursts = draws = 0
    for i in order:
        k = cls[i]
        info = DRINK_CLASSES[k]
        tier = class_tier(k, buff)
        if k == "copy":
            tier = 0 if copies == 0 else tier
            copies += 1
        elif k == "burst":
            tier = 0 if bursts < BURST_MUST[stage] else tier
            bursts += 1
        if k in DRAW_CLASSES:
            draws += 1
            if draws > DRAW_CAP[stage]:
                tier = 4
        out[i] = (tier, info["order"], k)
    return out


def holding_key(drinks: list, stage: str, capacity: int, buff: str | None = None) -> tuple:
    """持有组合的比较键（小的好）：档位从好到坏排序的 (档位, 顺序) 列表，空位补 3.5 档。"""
    keys = sorted((t, o) for t, o, _ in tag(list(drinks), stage, buff))  # None（没认出）按「未知」类算，不当空位
    keys += [(EMPTY_TIER, 0)] * max(0, capacity - len(keys))
    return tuple(keys)


def tiers_of(key: tuple) -> tuple:
    return tuple(t for t, _ in key)


def describe(drinks: list, stage: str, buff: str | None = None) -> str:
    from hiro.state.context import drink_name

    parts = []
    for d, (t, _, k) in zip(drinks, tag(drinks, stage, buff)):
        parts.append(
            f"{drink_name(d)}〔{DRINK_CLASSES[k]['label']}·{'优先' if t == 0 else ('超限' if t == 4 and k in DRAW_CLASSES else f'{t:g}档')}〕"
        )
    return "、".join(parts) or "空"


@dataclass
class Option:
    """一个候选：做完之后的持有饮料。action 是对应的契约动作（由调用方给）。"""

    key: str
    label: str
    drinks: list
    action: object = None
    cost: int = 0
    extra: dict = field(default_factory=dict)


def choose(
    options: list[Option],
    stage: str,
    capacity: int,
    *,
    sim_context: dict | None = None,
    seeds=range(100),
    where: str = "",
    take_on_tie: bool = False,
    buff: str | None = None,
    decide_ties: bool = False,
) -> dict:
    """规则默认 + 模拟。返回 {pick: Option, rule: Option, rows: [...], why: str, sim: bool, deviation: dict | None, unsure: str | None}。
    sim_context：{deck, hp, max_hp, permil, exam}（hiro.sim.resource.setup_for 的参数）；None 或 SIM=False 时只按规则。
    unsure：规则 / 模拟拿不准的原因（决策点补全：交 LLM 在候选里选）——没有模拟时同档打平、模拟失败、模拟偏向别的候选但在噪声内
    或输入是参考值不许跨档；社区默认明显最好（别的候选都不如它 / 不同档且没有模拟）时为 None。
    buff：偶像拿手 buff，按流派定档（class_tier）。decide_ties：没有模拟时同档打平也按同档内的社区顺序直接定，不交 LLM
    （D12：初 / N.I.A 没有饮料模拟，原来同档一打平就交 LLM；持有 / 候选里有没认出的饮料时仍交 LLM）。"""
    keyed = [(holding_key(o.drinks, stage, capacity, buff), i, o) for i, o in enumerate(options)]
    if take_on_tie:
        # 档位打平时收新饮料（用户 11-17：有的卡 / P 道具拿到饮料就得分）：先比档位，同档位时「不动」排后面，再按同档内的社区顺序
        keyed.sort(key=lambda x: (tiers_of(x[0]), x[2].key in NOOP, x[0], x[1]))
    else:
        keyed.sort(key=lambda x: (x[0], x[2].key not in NOOP, x[1]))  # 组合打平时不动（不收 / 不拿 / 不买）
    best_key, _, rule = keyed[0]
    tiers_txt = lambda k: "/".join(f"{t:g}" for t in tiers_of(k))  # noqa: E731
    rows = [
        {"key": o.key, "label": o.label, "tiers": tiers_txt(k), "rule_rank": n + 1, "cost": o.cost}
        for n, (k, _, o) in enumerate(keyed)
    ]
    out = {
        "pick": rule,
        "rule": rule,
        "rows": rows,
        "sim": False,
        "deviation": None,
        "unsure": None,
        "buff": buff,
        "why": f"社区默认：{rule.label}（档位 {tiers_txt(best_key)}）"
        + (f"（按偶像拿手 {buff} 定档）" if buff else ""),
    }
    if (
        take_on_tie
        and rule.key not in NOOP
        and any(o.key in NOOP and tiers_of(k) == tiers_of(best_key) for k, _, o in keyed)
    ):
        # 和「不动」档位打平：直接收，不交模拟 / LLM 改回不收（模拟算不到「拿到饮料就得分」）
        out["why"] += "；档位和不收打平，默认收新饮料（拿到饮料就得分的卡 / P 道具）"
        return out
    seen, others = {tuple(sorted(d for d in rule.drinks if d))}, []
    for k, _, o in keyed[1:]:
        sig = tuple(sorted(d for d in o.drinks if d))
        if sig not in seen:
            seen.add(sig)
            others.append((k, o))
    others = others[:MAX_SIM_OPTIONS]
    if not others:
        return out
    if not SIM or sim_context is None:
        if tiers_of(others[0][0]) == tiers_of(best_key):
            unknown = any(not d for o in options for d in o.drinks)
            if decide_ties and not unknown:
                out["why"] += f"；{others[0][1].label} 同档，按同档内的社区顺序定（没有饮料模拟）"
            else:
                out["unsure"] = f"{others[0][1].label} 和社区默认同档（{tiers_txt(best_key)}），没有模拟分开"
        return out
    try:
        from hiro.sim import interval as iv
        from hiro.sim import resource as rv

        ctx = dict(sim_context)
        exam = ctx.pop("exam")
        base, notes = rv.setup_for(exam, drinks=[d for d in rule.drinks if d], **ctx)
        if notes:
            # 输入是参考值（牌组没记账 / 倍率没有）时不许跨档偏离（第十八局本战 22:20），跨档候选就不必模拟（协调 2026-11-03：
            # 第二十一局选拔 16 次饮料模拟 5.2 分钟，多数是这种白算）。只模拟同档候选；没有就直接按社区默认
            others = [(k, o) for k, o in others if tiers_of(k) == tiers_of(best_key)]
            if not others:
                out["why"] += f"；{'、'.join(notes)}：不跨档偏离，同档没有别的候选，不模拟"
                return out  # 别的候选都不同档：按社区档位就能定
        setups = [base] + [iv._with(base, drinks=[d for d in o.drinks if d]) for _, o in others]
        res = rv.adaptive_eval(setups, threshold=OVERRIDE_SE, workers=4)
    except Exception as e:  # 模拟器不支持本场牌组等：按规则
        logger.warning("饮料模拟失败，按社区默认：%s", e)
        out["why"] += f"；模拟失败（{e}），按社区默认"
        out["unsure"] = f"模拟失败（{e}）"
        return out
    out["sim"] = True
    by_key = {rule.key: res[0]} | {o.key: r for (_, o), r in zip(others, res[1:])}
    for row in rows:
        r = by_key.get(row["key"])
        if r is not None:
            row.update(sim_mean=r["mean"], sim_gain=r["gain"], sim_gain_se=r["gain_se"])
    (k, o), r = max(zip(others, res[1:]), key=lambda x: x[1]["gain"])
    note = f"（模拟 {rv.POLICY} 续打 {res[0]['n']} 局配对（自适应，最多 {rv.BATCHES[-1]}），下一场 {exam}{'；' + '、'.join(notes) if notes else ''}）"
    cross = tiers_of(k) != tiers_of(best_key)
    if cross and notes:
        # 牌组 / 倍率用的是参考值（第十八局本战 22:20：「牌组没记账：用参考牌组」却跨档拿了 おしゃれハーブティー）：输入是假的，不允许跨档偏离
        out["why"] += (
            f"；模拟里 {o.label} 比它 {r['gain']:+,} ± {r['gain_se']:,}，但{('、'.join(notes))}，不跨档偏离{note}"
        )
        if r["gain"] > 0:
            out["unsure"] = (
                f"模拟偏向 {o.label}（{r['gain']:+,} ± {r['gain_se']:,}），但{'、'.join(notes)}，输入是参考值"
            )
        return out
    if r["gain"] > OVERRIDE_SE * max(r["gain_se"], 1):
        out["pick"] = o
        out["deviation"] = {"where": where, "stage": stage, "rule": rule.label, "rule_tiers": tiers_txt(best_key), "pick": o.label,
                            "pick_tiers": tiers_txt(k), "gain": r["gain"], "gain_se": r["gain_se"], "cross_tier": cross,
                            "policy": rv.POLICY, "seeds": res[0]["n"], "notes": notes}  # fmt: skip
        out["why"] = (f"偏离社区默认（{'跨档' if cross else '同档'}）：选 {o.label}，比社区默认 {rule.label} {r['gain']:+,} ± {r['gain_se']:,}"
                      f"（> {OVERRIDE_SE:g} 个标准误）{note}")  # fmt: skip
        log_deviation(out["deviation"])
    else:
        out["why"] += (
            f"；模拟里最好的其他候选 {o.label} 比它 {r['gain']:+,} ± {r['gain_se']:,}，不到 {OVERRIDE_SE:g} 个标准误，不改{note}"
        )
        if r["gain"] > 0:
            out["unsure"] = f"模拟里 {o.label} 比社区默认 {r['gain']:+,} ± {r['gain_se']:,}，在噪声内"
    return out


DEVIATIONS = "reports/deviations.jsonl"


def log_deviation(rec: dict) -> None:
    """偏离社区默认的记录：定期汇总给协调，由用户 review（用户更正 2026-11-02）。写不了（只读环境 / 测试）不影响决策。"""
    import json
    import time
    from pathlib import Path

    try:
        path = Path(DEVIATIONS)
        if not path.parent.is_dir():
            return
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "kind": "drink", **rec}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def accident(drinks: list, stage: str, capacity: int) -> list[str]:
    """饮料「事故」（社区：事故时才在相談 / 本战相談补）。选拔：没有复制、爆发不够、有用的（0–1 档）不到 2 瓶；
    本战：只看有用的不到 2 瓶（もなみ：最后的相談基本不花，よほど事故ったら才买）。返回缺什么。"""
    tags = tag([d for d in drinks if d], stage)
    out = []
    if stage == "selection":  # 本战按もなみ「よほど事故ったら」：只看有用的饮料够不够（她只缺 ブースト 时忍住没买）
        if not any(k == "copy" for _, _, k in tags):
            out.append("复制")
        if sum(k == "burst" for _, _, k in tags) < BURST_MUST[stage]:
            out.append("爆发")
    if sum(t <= 1 for t, _, _ in tags) < 2:
        out.append("有用的饮料不到 2 瓶")
    return out


def best_incoming(
    cands: list[dict], held: list | None, stage: str, capacity: int, buff: str | None = None
) -> dict | None:
    """栏满时三选一先挑哪瓶去和持有的比（hif 的「所持上限」流程）：每个候选取「丢掉持有里某一瓶换它」能得到的最好持有组合，
    取组合最好的那个候选（之后 PDRINK_MAX 决定丢不丢、丢哪瓶）。held 未知时按单瓶默认档位。"""
    known = [c for c in cands or [] if c.get("drink_id")]
    if not known:
        return None
    hold = [h.get("drink_id") for h in held or []]

    def best_key(c: dict) -> tuple:
        if not hold:
            t, o, _ = tag([c["drink_id"]], stage, buff)[0]
            return ((t, o),)
        return min(
            holding_key(hold[:j] + [c["drink_id"]] + hold[j + 1 :], stage, capacity, buff) for j in range(len(hold))
        )

    return min(known, key=best_key)
