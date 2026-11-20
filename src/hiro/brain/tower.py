"""偶像之路的编成决策（用户 11-14：编成「接入我们的 bench 算法去游玩」，不用おまかせ；设计稿
（作者本机，未发布））。

一层的编成 = 考哪种考试（mechanic，一层通常有几种）+ 主回忆（牌组、P 道具、饮料、体力、属性）+ 副回忆（只加属性：
每份副回忆三属性 × 0.2 向下取整，hiro.bench_bridge.tower.stage_params）。

- 副回忆：倍率公式（tower.score_bonus_permils）是确定的，按「倍率和最大」贪心挑，不用模拟；
- 考试 × 主回忆：每个组合换成 bench 题目的编成（gkms.exam_loadout.v1），用 bench 引擎 + UINet 按同一批种子打
  （tower.evaluate(detail=True)，每局给名次 / 星 / 条件），按「三星概率 → 拿第 1 的概率 → 平均分」排（用户 11-14：追求三星通关、
  在全部回忆里选最优组合）。全部回忆都进第一轮，逐轮淘汰（ROUNDS）。

UINet 只在 H.I.F 上训练过，偶像之路上只到上限的 33.7%（8a3a1e7）：排序只当依据之一，前两名接近时交 LLM。
"""

from __future__ import annotations

import math
import statistics

SUB_POOL = 12  # 副回忆候选最多看这么多份（按属性和）
ROUNDS = ((4, 8), (16, 3), (48, 1))  # 逐轮淘汰：累计打到这么多局，留前这么多个


def _vec(m) -> list[int]:
    p = m.get("params") if isinstance(m, dict) else m
    return [int(x or 0) for x in (p or (0, 0, 0))]


def bonus(lex: dict, main: dict, subs: list[dict]) -> list[int] | None:
    """这一层这一编成的考试倍率；battle_config 不知道时 None。"""
    from hiro.bench_bridge import tower as tw

    cfg = (lex.get("exam") or {}).get("produce_exam_battle_config_id")
    if not cfg:
        return None
    try:
        return tw.score_bonus_permils(cfg, tw.stage_params(_vec(main), [_vec(s) for s in subs]))
    except KeyError:
        return None


def choose_subs(lex: dict, main: dict, pool: list[dict], k: int) -> list[dict]:
    """副回忆：贪心挑 k 份，让本层倍率和（不知道倍率时用属性和）最大。主回忆不能同时当副回忆。"""
    from hiro.bench_bridge import tower as tw

    left = sorted((m for m in pool if m is not main), key=lambda m: -sum(_vec(m)))[:SUB_POOL]
    out: list[dict] = []
    for _ in range(max(0, k)):
        if not left:
            break

        def gain(m):
            b = bonus(lex, main, out + [m])
            return sum(b) if b is not None else sum(tw.stage_params(_vec(main), [_vec(s) for s in out + [m]]))

        best = max(left, key=gain)
        out.append(best)
        left.remove(best)
    # 两两交换一轮：倍率曲线有插值和审查基准罚分，贪心不一定最优
    total = lambda subs: sum(bonus(lex, main, subs) or tw.stage_params(_vec(main), [_vec(x) for x in subs]))  # noqa: E731
    improved = True
    while improved:
        improved = False
        for i in range(len(out)):
            for m in list(left):
                trial = out[:i] + [m] + out[i + 1:]
                if total(trial) > total(out):
                    left.append(out[i])
                    left.remove(m)
                    out = trial
                    improved = True
    return out


def signature(cands: list[dict]) -> str:
    """回忆库存签名（缓存键用）：回忆有增减、属性 / 牌组 / 道具（强化、定制）变了就变。"""
    import hashlib
    import json

    rows = sorted(json.dumps([c.get("memory_id"), _vec(c), c.get("max_stamina"), c.get("deck"), c.get("items"), c.get("drinks")],
                             ensure_ascii=False, sort_keys=True) for c in cands)  # fmt: skip
    return hashlib.sha1("\n".join(rows).encode()).hexdigest()[:16]


def loadout(lex: dict, main: dict, subs: list[dict], idol_card_id: str | None = None) -> dict:
    """（本层考试设置, 主回忆, 副回忆）→ gkms.exam_loadout.v1（同 idolroad 题目的写法）。"""
    exam = dict(lex.get("exam") or {})
    layer_items = list(exam.get("produce_item_ids") or [])
    hp = int(main.get("max_stamina") or 0) or None
    out = {
        "schema": "gkms.exam_loadout.v1", "produce_id": "tower", "round": "final",
        "tower_id": lex["tower_id"], "layer": int(lex["layer"]), "mechanic": lex["mechanic"],
        "idol_card_id": idol_card_id or main.get("idol_card_id"),
        "max_stamina": hp, "starting_stamina": hp,
        "score_bonus_permils": bonus(lex, main, subs),
        "produce_cards": list(main.get("deck") or []),
        "produce_item_ids": list(main.get("items") or []) + [i for i in layer_items if i not in (main.get("items") or [])],
        "exam": exam, "drink_ids": list(main.get("drinks") or []),
    }  # fmt: skip
    if lex.get("open_rounds") is not None:
        out["open_rounds"] = lex["open_rounds"]
    return out


def lost_ssr_count(deck: list) -> int | None:
    """牌组里「SSR、打出后进除外区」的张数（主数据 rarity = Ssr、playMovePositionType = Lost）。27 层 ★3 要「クリア時に除外にある
    スキルカード(SSR)が5枚以下」：这类卡多了，条件和拿第 1 兼顾不了（8a3a1e7 11-14 筛选）。读不到主数据时 None。"""
    try:
        from hiro.state.context import catalog
    except Exception:  # noqa: BLE001
        return None
    n = 0
    for s in deck or []:
        head = str(s).split("/", 1)[0]
        cid, _, u = head.partition("+")
        try:
            row = catalog().card(cid, int(u or 0))
        except Exception:  # noqa: BLE001
            continue
        if row and row.get("rarity") == "ProduceCardRarity_Ssr" and row.get("playMovePositionType") == "ProduceCardMovePositionType_Lost":
            n += 1
    return n


LOST_SSR_SLACK = 1  # ok 的余地（只用于提示，不筛）：牌组里的卡不一定都打出（27 层 AS-EGTW6Z2W 牌组 6 张、实际打完除外 5–6 张，拿过三星）


def static_check(lo: dict, conds: list | None) -> dict:
    """编成本身能不能留出条件余量（不模拟）：{ok, margin}。现在只认「除外 SSR ≤ N」：牌组里打出即除外的 SSR 不超过
    N + LOST_SSR_SLACK 时 ok；margin = N − 张数，排序时作软惩罚（rank_key 的 static_margin），不筛编成（协调 11-14）。
    没有这类条件时 {ok: None, margin: None}。"""
    for c in conds or []:
        if c.get("field") == "lost_ssr" and c.get("op") == "<=" and c.get("value") is not None:
            n = lost_ssr_count(lo.get("produce_cards") or [])
            if n is None:
                return {"ok": None, "margin": None, "lost_ssr": None}
            return {"ok": n <= c["value"] + LOST_SSR_SLACK, "margin": c["value"] - n, "lost_ssr": n}
    return {"ok": None, "margin": None, "lost_ssr": None}


def evaluable(lo: dict) -> str | None:
    """编成能不能拿去模拟：不能返回原因。"""
    ex = lo.get("exam") or {}
    if not lo.get("produce_cards"):
        return "主回忆的牌组没读到"
    if lo.get("score_bonus_permils") is None or not ex.get("produce_exam_battle_config_id"):
        return "本层考试设置（审查基准 / 回合数）不知道"
    if not ex.get("produce_exam_gimmick_effect_group_id"):
        return "本层机关不知道"
    if not lo.get("max_stamina"):
        return "主回忆的体力没读到"
    return None


def _rate(k: int, n: int) -> tuple[float, float]:
    p = k / n
    return p, math.sqrt(max(p * (1 - p), 1 / n) / n)


def _margin(checks: list, conds: list | None) -> float | None:
    """一局各条件的余量之和（满足越多越大）：「≤ v」是 v − 局末值，「≥ v」是局末值 − v。读不到的条件不算。"""
    by_text = {c.get("text"): c for c in conds or []}
    total, seen = 0.0, False
    for x in checks or []:
        c = by_text.get(x.get("text"))
        if c is None or x.get("got") is None or c.get("value") is None:
            continue
        total += (c["value"] - x["got"]) if c.get("op") == "<=" else (x["got"] - c["value"])
        seen = True
    return total if seen else None


def summarize(rows: list, border: int | None, conds: list | None = None) -> dict:
    """K 局结果 → {n, mean, se, p3, p3_se, est3, est3_se, first, first_se, cond, margin, clear}（出错 / 没跑到的局不算）。
    rows 是 evaluate(detail=True) 的判定（{score, first, stars, conditions, …}），也接只有分数的旧写法（这时按过关线算 first、没有三星）。
    est3 = 拿第 1 的概率 × 通关条件满足率：评估用的 UINet 不会刻意满足条件（27 层「除外 SSR ≤ 5」实测 0/8，8a3a1e7），
    两样分开估再相乘，才看得出哪份编成更容易满足条件；条件余量 margin 再细分（例如除外区 SSR 平均比上限少几张）。"""
    got = [r for r in rows if r is not None]
    n = len(got)
    out = {"n": n, "mean": None, "se": None, "p3": None, "p3_se": None, "est3": None, "est3_se": None, "first": None, "first_se": None,
           "cond": None, "margin": None, "clear": None}  # fmt: skip
    if not n:
        return out
    if isinstance(got[0], dict):
        xs = [int(r.get("score") or 0) for r in got]
        firsts = sum(bool(r.get("first")) for r in got)
        stars = sum(int(r.get("stars") or 0) >= 3 for r in got)
        out["p3"], out["p3_se"] = _rate(stars, n)
        with_cond = [r for r in got if r.get("conditions")]
        if with_cond:
            ok = sum(all(x.get("ok") is True for x in r["conditions"]) for r in with_cond)
            out["cond"] = ok / len(with_cond)
            ms = [m for m in (_margin(r["conditions"], conds) for r in with_cond) if m is not None]
            out["margin"] = statistics.fmean(ms) if ms else None
        cond = out["cond"] if out["cond"] is not None else 1.0
        out["est3"] = firsts / n * cond
        out["est3_se"] = math.sqrt(max(out["est3"] * (1 - out["est3"]), 1 / n) / n)
    else:
        xs = [int(x) for x in got]
        firsts = sum(x >= border for x in xs) if border is not None else None
    out["mean"] = round(statistics.fmean(xs))
    out["se"] = round(statistics.stdev(xs) / math.sqrt(n)) if n > 1 else 0
    if firsts is not None:
        out["first"], out["first_se"] = _rate(firsts, n)
        out["clear"] = out["first"]
    return out


RANK = ("p3", "est3", "first", "margin", "static_margin", "mean")


def rank_key(s: dict) -> tuple:
    """排序键（大的好）：实测三星概率 → 估计三星概率（第 1 × 条件满足）→ 拿第 1 的概率 → 条件余量 → 平均分
    （用户 11-14：追求三星通关）。不知道的项按最低。"""
    return tuple(-1e18 if s.get(k) is None else s[k] for k in RANK)


def clearly_better(a: dict, b: dict) -> bool:
    """第一名是否明显好过第二名（> 2 个标准误），按 三星概率 → 拿第 1 的概率 → 平均分 依次比：前一项差在噪声内才比下一项。"""
    for k, kse, floor in (("p3", "p3_se", 1e-9), ("est3", "est3_se", 1e-9), ("first", "first_se", 1e-9), ("mean", "se", 1.0)):
        if a.get(k) is None or b.get(k) is None:
            continue
        se = math.hypot(a.get(kse) or 0, b.get(kse) or 0)
        d = a[k] - b[k]
        if abs(d) > 2 * max(se, floor):
            return d > 0
    return False
