"""偶像之路（tower）：编成 → 我们的引擎（gakumas_exam，Rust 后端）开局，和搜索的终局目标（用户 11-14：偶像之塔用 GNRPA 搜、追求三星）。

编成写法同 bench 题目 / HIRO_TOWER_LOADOUT（gkms.exam_loadout.v1）：produce_cards（「卡ID+强化/定制ID*次数」）、produce_item_ids、
starting_stamina / max_stamina、score_bonus_permils、exam（gimmick / battle_config / npc_group，缺了用 hiro.bench_bridge.tower.layer_exam 补）。
离线对拍（设计稿「偶像之路第 1 步」）：机关、回合属性（按 battle_config 的三属性权重随机，和 GKMS 逐种子一致）、倍率都是现有能力；
随机出牌 200 局的分布，集中 / やる気 和 GKMS 一致，好印象还差约 2 万（待查）。
"""

from __future__ import annotations

from hiro.search.exam_env import bonus_permils  # noqa: F401  原在这里，别处按 hiro.search.tower.bonus_permils 引用


def complete_loadout(lo: dict) -> dict:
    """编成缺的考试设置（机关 / battle_config / 对手组 / 层道具）用 layer_exam 补；已有的不动。"""
    from hiro.bench_bridge import tower as tw

    lo = dict(lo)
    ex = dict(lo.get("exam") or {})
    if not (ex.get("produce_exam_battle_config_id") and ex.get("produce_exam_gimmick_effect_group_id")):
        lex = tw.layer_exam(lo["tower_id"], int(lo["layer"]), lo.get("mechanic"))
        for k, v in ((lex or {}).get("exam") or {}).items():
            if v is not None and not ex.get(k):
                ex[k] = v
        items = list(lo.get("produce_item_ids") or [])
        for i in (lex or {}).get("exam", {}).get("produce_item_ids") or []:
            if i not in items:
                items.append(i)
        lo["produce_item_ids"] = items
    lo["exam"] = ex
    lo["produce_cards"] = with_initial_cards(list(lo.get("produce_cards") or []), lo.get("idol_card_id"))
    return lo


def with_initial_cards(cards: list, idol: str | None) -> list:
    """编成页读到的牌组补上游戏开局另加的 2 张基本卡（tower.Master.idol_initial_cards，同 bench_bridge.screen._with_initial_cards）：
    已经在里面的不重复加（bench 题本来就带）。没有偶像 id / 主数据时原样返回。"""
    if not cards or not idol:
        return cards
    try:
        from hiro.bench_bridge.tower import master

        extra = master().idol_initial_cards(idol)
    except Exception:  # noqa: BLE001
        return cards
    from hiro.search.loadout import parse_deck_entry

    have = {parse_deck_entry(c)[0] for c in cards}
    return cards + [c for c in extra if c not in have]


def tower_env(lo: dict, seed: int):
    """编成 → 我们的引擎对局（FidelityBattle，Rust 后端时是 NativeBattle，.rx 是 gakumas_exam_rs.Exam）。通用部分在 exam_env。"""
    from hiro.search.exam_env import exam_env

    return exam_env(lo, seed, guid_prefix="card-tower")


def objective(lo: dict, rivals: list | None = None) -> dict:
    """search / plan_many 的目标参数：rivals（对手终局分，优先用画面读到的；没有用主数据区间的上限，保守）、通关条件。"""
    from hiro.bench_bridge import tower as tw

    out: dict = {"rivals": [], "lost_ssr_max": None, "debuffs_min": None}
    if rivals:
        out["rivals"] = sorted((int(x) for x in rivals), reverse=True)
    else:
        lex = tw.layer_exam(lo["tower_id"], int(lo["layer"]), lo.get("mechanic")) or {}
        out["rivals"] = sorted((int(hi) for _, hi in lex.get("rivals") or []), reverse=True)
    for c in tw.star_conditions(int(lo["layer"])):
        if c.get("field") == "lost_ssr" and c.get("op") == "<=":
            out["lost_ssr_max"] = int(c["value"])
        elif c.get("field") == "debuffs" and c.get("op") == ">=":
            out["debuffs_min"] = int(c["value"])
    return out


EVAL_SECONDS = 1.0  # 编成评估：每个世界搜多少秒（协调 11-14 同意：16 个世界 × GNRPA）


def rust_available() -> bool:
    try:
        import gakumas_exam_rs as rs
    except ImportError:
        return False
    return hasattr(rs, "plan_many")


def evaluate_rust(loadouts: list[dict], seeds: list[int], *, budget_s: float | None = None, detail: bool = True, workers: int | None = None,
                  seconds: float | None = None, **_) -> list[list]:  # fmt: skip
    """编成评估（接口同 hiro.bench_bridge.tower.evaluate）：每个编成按每个种子开一局，plan_many 在每个世界里从开局搜一整局
    （世界内牌序当真，偏乐观但对所有编成一样）。detail=True 时每局 {score, first, stars, conditions}，否则终局分。
    不依赖 GKMS（用户 11-14：编成评估不绑死 uinet / GKMS）。budget_s 不够跑完时，按时间缩每个世界的秒数。"""
    import gakumas_exam_rs as rs

    secs = EVAL_SECONDS if seconds is None else float(seconds)
    n = max(1, len(loadouts) * len(seeds))
    threads = 0 if not workers else int(workers)
    if budget_s is not None:
        import os

        cores = max(1, (os.cpu_count() or 2) - 1) if not threads else threads  # noqa: SIM212
        secs = max(0.1, min(secs, float(budget_s) * cores / n * 0.9))
    out = []
    for lo in loadouts:
        lo = complete_loadout(lo)
        obj = objective(lo, lo.get("rivals"))
        rxs = [tower_env(lo, int(s)).rx for s in seeds]
        res = rs.plan_many(rxs, seconds=secs, threads=threads, seed=int(seeds[0]) if seeds else 0, **obj)
        top = max(obj["rivals"]) if obj["rivals"] else None
        has_cond = obj["lost_ssr_max"] is not None or obj["debuffs_min"] is not None
        row = []
        for _value, raw, stars in res:
            if not detail:
                row.append(int(raw))
                continue
            first = top is not None and raw > top
            conds = (
                [{"text": "通关条件", "field": None, "got": None, "ok": (stars >= 3) if first else None}]
                if has_cond
                else []
            )
            row.append({"score": int(raw), "first": bool(first), "stars": int(stars), "conditions": conds})
        out.append(row)
    return out
