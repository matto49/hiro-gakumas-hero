# 来源：digital-hiro src/hiro/brain/formation.py @ c3fc27f（GPL-3.0-or-later）；支援卡技能改走 AssetSource.support_effects()
"""开局编成：从游戏里预存的支援卡编成 / 记忆编成里按社区规则选第几号（KAA prepare 的编成编号 1–20）。

用户 2026-10-31：记忆和支援卡不用 KAA 自动编成，改成显式配置，并作为决策的一部分。
- 支援卡：主属性 = 编成里张数最多的属性；目标主属性 4 张（4-2-0 / 4-1-1），其中尽量多张带 SP 课程发生率
  [seesaa][もりし][もなみ]。比较顺序：主属性张数（封顶 4）→ SP 发生率张数 → SP 结束奖励张数 → 成长率张数 → 等级合计。
  技能从 AssetSource.support_effects() 取（Lesson{属性}SpChangeRatePermilAddition 等）；取不到时只按属性张数和等级比较。
- 记忆：knowledge.MEMORY_WANT 里的卡按权重计分，带社区推荐的定制再加 1。
主属性随选中的支援卡编成确定，交给 ProduceDeciders.main_attr（不再需要 --main-attr）。

输入格式（GameState.page_fields 或直接传参，识别 / 总控按此提供）：
    support_sets: [{index, cards: [{support_card_id, level, limit_rank?}]}]
    memory_sets:  [{index, memories: [{card_id, upgrade, customizations: [customize_id]}]}]
"""

from functools import lru_cache

from hiro.state.knowledge import MEMORY_WANT, SUPPORT_MAIN_TARGET

ATTR_OF = {"SupportCardType_Vocal": "Vo", "SupportCardType_Dance": "Da", "SupportCardType_Visual": "Vi"}
EFFECT_ATTR = {"Vo": "Vocal", "Da": "Dance", "Vi": "Visual"}


def _support_skills(card_id: str, level: int, limit_rank: str | None = None, plan: str | None = None) -> list[dict]:
    """AssetSource.support_effects(card_id, level, limit_rank=, plan=) → [{"effect_type", "value", "trigger_id", "limit"}]；
    拿不到返回 []（编成 / 支援卡选择就只按稀有度、属性和等级排）。只接受两参数的老插件退回两参数调用。"""
    from hiro import plugins

    src = plugins.get("asset_source")
    fn = getattr(src, "support_effects", None) if src is not None else None
    if not fn:
        return []
    try:
        try:
            out = fn(card_id, level, limit_rank=limit_rank, plan=plan)
        except TypeError:
            out = fn(card_id, level)
        return list(out or [])
    except Exception:  # noqa: BLE001 素材源出错不能打断编成
        return []


@lru_cache(maxsize=None)
def _skills(card_id: str, level: int, limit_rank: int | None, plan: str) -> tuple:
    """支援卡在该等级生效的技能：((effect_type, trigger_id), ...)。素材源给不出来返回 ()。"""
    return tuple((s["effect_type"], s.get("trigger_id")) for s in _support_skills(card_id, level, limit_rank, plan))


def support_profile(cards: list[dict], plan: str) -> dict:
    """一套支援卡编成的属性分布与技能统计。"""
    from hiro.state.context import catalog

    counts, sp_rate, sp_end, growth, levels = {"Vo": 0, "Da": 0, "Vi": 0}, {}, {}, {}, 0
    for card in cards:
        try:
            row = catalog().one("SupportCard", card["support_card_id"])
        except Exception:
            continue
        attr = ATTR_OF.get(row["type"])
        if attr is None:
            continue
        counts[attr] += 1
        levels += card.get("level") or 0
        skills = _skills(card["support_card_id"], card.get("level") or 1, card.get("limit_rank"), plan)
        name = EFFECT_ATTR[attr]
        if any(e == f"ProduceEffectType_Lesson{name}SpChangeRatePermilAddition" for e, _ in skills):
            sp_rate[attr] = sp_rate.get(attr, 0) + 1
        if any(t.endswith(f"lesson_{name.lower()}_sp") for _, t in skills):
            sp_end[attr] = sp_end.get(attr, 0) + 1
        if any(e == f"ProduceEffectType_{name}GrowthRateAddition" for e, _ in skills):
            growth[attr] = growth.get(attr, 0) + 1
    main = max(counts, key=lambda a: (counts[a], sp_rate.get(a, 0)))
    return {"main_attr": main, "counts": counts, "sp_rate": sp_rate.get(main, 0), "sp_end": sp_end.get(main, 0),
            "growth": growth.get(main, 0), "levels": levels,
            "key": (min(counts[main], SUPPORT_MAIN_TARGET), sp_rate.get(main, 0), sp_end.get(main, 0), growth.get(main, 0), levels)}  # fmt: skip


def memory_profile(memories: list[dict]) -> dict:
    """一套记忆编成按 MEMORY_WANT 计分：带了这张卡记权重，带社区推荐的定制再 +1。"""
    from hiro.brain.deciders import _customize_text

    score, hits = 0, []
    swap = [m for m in memories if _swaps_hand(m.get("card_id"))]
    opening_core = [m for m in memories if m.get("card_id") in OPENING_CORE and any("initial_add" in _customize_text(cid) for cid in m.get("customizations") or [])]
    for card_id, name, weight, keys, _why in MEMORY_WANT:
        mem = next((m for m in memories if m.get("card_id") == card_id), None)
        if mem is None:
            continue
        text = "|".join(_customize_text(cid) for cid in mem.get("customizations") or [])
        custom = any(k in text for k in keys)
        score += weight + (1 if custom else 0)
        hits.append(name + ("（推荐定制）" if custom else ""))
    conflicts = []
    if swap and opening_core:
        # 开局入手的核心卡（デイドリーミング 要元気≥30）和换全部手牌的卡同在：第 1 回合容易把核心洗掉（第六局选拔③，协调 4a）
        score -= 2
        conflicts.append("仕切り直し 等换手牌卡与开局入手的 デイドリーミング 同在：开局容易把核心洗掉（-2）")
    return {"score": score, "hits": hits, "conflicts": conflicts}


OPENING_CORE = {"p_card-02-act-1_037"}  # デイドリーミング：开局入手定制时第 1 回合就在手，但要元気≥30 才能打


def _swaps_hand(card_id: str | None) -> bool:
    if not card_id:
        return False
    try:
        from hiro.brain.cards import card_view
        from hiro.brain.cards import kaa_cards as _kaa_cards

        kaa = _kaa_cards().get((card_id, 0))
        return bool(kaa) and "手札をすべて入れ替える" in card_view(0, kaa, available=True).text
    except Exception:
        return False


def star_rank(star: int | None) -> int | None:
    from hiro.state.knowledge import STAR_RANK_LINES

    if star is None:
        return None
    return max(i for i, line in enumerate(STAR_RANK_LINES) if star >= line)


def qualify_memory_profile(mem: dict, idol_card_id: str | None) -> dict:
    """一份选拔回忆能不能用、强不强（社区依据见 knowledge「本战用哪份选拔回忆」）。

    mem：{index, idol_card_id, event_mode?, ichibanboshi?, star, params{Vo,Da,Vi}, deck[{card_id, upgrade, customized?}], pitems?}
    比较顺序：能用 → 必拿卡（届いて！、せのびの魔法、デイドリーミング）几张 → 缺几个核心角色（终结 / 延迟 / 持续 / 循环，决策-07）→ 冲突牌几张
    → 一番星专属卡 → 星星 → 属性过本战审查线几项 → S/A 张数 → 眠気 → 牌组离 22 张多远 → 属性合计。"""
    from hiro.state.knowledge import (CORE_ROLES, FINALS_DECK_TARGET, FINALS_PARAM_LINES, FINALS_STAR_LINES, ROLES, YARUKI_CARDS, card_role,
                                      idol_trend, is_conflict)  # fmt: skip

    notes, problems = [], []
    same = idol_card_id is None or (mem.get("idol_card_id") or "").replace("i_card-skin-", "i_card-") == idol_card_id.replace("i_card-skin-", "i_card-")
    if not same:
        problems.append(f"不是同一张偶像卡（{mem.get('idol_card_id')}）")
    if mem.get("event_mode"):
        problems.append("活动模式下生成的回忆，只能打活动模式的本战 [wiki]")
    deck = mem.get("deck") or []
    ids = [c.get("card_id") for c in deck]
    musts = [k["name"] for cid, k in YARUKI_CARDS.items() if k.get("must") and cid in ids]
    missing = [k["name"] for cid, k in YARUKI_CARDS.items() if k.get("must") and cid not in ids]
    if missing:
        notes.append(f"缺必拿 {'、'.join(missing)}")
    s_a = sum(1 for cid in ids if (YARUKI_CARDS.get(cid) or {}).get("tier") in ("S", "A"))
    roles = {r: sum(1 for cid in ids if card_role(cid) == r) for r in CORE_ROLES}
    core_missing = [ROLES[r] for r in CORE_ROLES if not roles[r]]
    conflicts = [cid for cid in ids if is_conflict(cid)]
    if core_missing:
        notes.append(f"缺本战核心：{'、'.join(core_missing)}")
    if conflicts:
        notes.append(f"带了 {len(conflicts)} 张冲突牌（ハイテンション / 好印象系）")
    sleep = sum(1 for cid in ids if cid == "p_card-00-acc-0_002")
    star = mem.get("star")
    params = mem.get("params") or {}
    ok_params = 0
    try:
        kind, flow = idol_trend(idol_card_id or mem.get("idol_card_id") or "")
    except (KeyError, IndexError):
        kind, flow = None, ()
    lines = FINALS_PARAM_LINES.get(kind or "", {})
    for rank, attr in zip(("流1", "流2", "流3"), flow):
        tri, circle = lines.get(rank, (None, None))
        v = params.get(attr)
        line = circle or tri
        if v is None or line is None:
            continue
        if v >= line:
            ok_params += 1
        else:
            notes.append(f"{attr} {v} 低于本战{rank}审查线 {line}")
    if star is not None and star < FINALS_STAR_LINES["r1"]:
        notes.append(f"星星 {star} 未到 R1 审查线 {FINALS_STAR_LINES['r1']}（本战课程和考试还能再拿）")
    deck_gap = abs(len(deck) - FINALS_DECK_TARGET) if deck else 99
    key = (not problems, len(musts), -len(core_missing), -len(conflicts), bool(mem.get("ichibanboshi")), star or 0, ok_params, s_a, -sleep, -deck_gap,
           sum(v or 0 for v in params.values()))  # fmt: skip
    return {"index": mem.get("index"), "usable": not problems, "problems": problems, "musts": musts, "s_a": s_a, "sleep": sleep,
            "star": star, "star_rank": star_rank(star), "params_ok": ok_params, "deck_size": len(deck),
            "ichibanboshi": bool(mem.get("ichibanboshi")), "core": roles, "core_missing": core_missing, "conflicts": len(conflicts),
            "notes": notes, "key": key}  # fmt: skip


# ---- 回忆编成选择器（docs/tasks/回忆编成选择系统.md §3，社区优先级见 hiro.state.memory_priority）----
#
# 库存格式（reports/live/memory-inventory.json，识别会话产出；字段都可缺，缺了按中性处理）：
#   {"memories": [{"id": 回忆编号或位置, "card_id"?: "p_card-…", "card_name"?: "届いて！+", "upgrade"?: 1,
#                  "customizations"?: [定制 ID], "custom_text"?: "定制效果文字", "customized"?: bool,
#                  "timing"?: "start" | "mid"（或含「中間試験」的文字）, "params"?: {Vo: {fixed, pct}} 或 "param_texts": [文字],
#                  "abilities"?: [能力文字], "rank"?: "SSS", "level"?: int}]}
# 评分 = 卡的社区优先度（weight，主导排序）+ 这份回忆的质量（定制、入手时机、强化、HIF 能力、属性词条）。
# 一套编成：4 格、同一张卡不重复；同类能力第二条只算一半；属性词条按「离上限还有多少」合并封顶；冲突组合扣分。

SLOTS = 4
CUSTOM_UNKNOWN = 0.5  # 定制过但读不出是哪项：按该卡最好定制的一半算
CONFLICT_PENALTY = 5
UPGRADE_BONUS = 3
FARM_MIN_WEIGHT = 68  # 值得刷的只列优先度前 8 的卡（到 アイドル魂）
PARAM_SCALE = 8.0  # 属性分（约 40–60）÷ 8 → 回忆质量里的 5–8 分；只在同一张卡的不同回忆之间起作用 [いおん01：先看带入的卡，再看词条]


def _resolve_card(m: dict) -> tuple[str | None, int | None]:
    if m.get("card_id"):
        return m["card_id"], m.get("upgrade")
    name = (m.get("card_name") or "").strip()
    if not name:
        return None, None
    base = name.rstrip("+＋")
    up = len(name) - len(base)
    from hiro.state.context import catalog

    for r in catalog().rows("ProduceCard"):
        if r["upgradeCount"] == 0 and r["name"] == base:
            return r["id"], m.get("upgrade", up)
    return None, m.get("upgrade", up)


def _timing(m: dict) -> str | None:
    t = m.get("timing")
    if t in ("start", "mid"):
        return t
    if isinstance(t, str) and t:
        return "mid" if ("中間" in t or "オーディション後" in t) else "start"
    return None


def _from_entry(e: dict) -> dict:
    """回忆页实读格式（reports/live/memory-inventory.json 的 entries，总控 2026-11-01）→ 选择器格式：
    {card: {card_id, name, upgrade}, timing, timing_text, abilities: [{text, disabled}], stats, page, cell}。
    属性词条（初期ボーカル上昇+20、パラメータボーナス+2.1%）也在 abilities 里；被禁用的能力不算。"""
    card = e.get("card") or {}
    texts = [a.get("text") or "" for a in e.get("abilities") or [] if not a.get("disabled")]
    return {"id": e.get("id") or (f"p{e.get('page')}-{'-'.join(map(str, e.get('cell') or []))}" if e.get("page") is not None else e.get("cell_hash")),
            "card_id": card.get("card_id"), "card_name": card.get("name"), "upgrade": card.get("upgrade"),
            "timing": e.get("timing") or e.get("timing_text"), "abilities": texts, "param_texts": texts,
            "custom_text": e.get("custom_text"), "customized": e.get("customized"), "rank": e.get("rank")}  # fmt: skip


def normalize_memory(m: dict) -> dict:
    from hiro.brain.deciders import _customize_text
    from hiro.state.memory_priority import CUSTOM_TEXT_KEYS, parse_param_texts

    if isinstance(m.get("card"), dict):
        m = _from_entry(m)

    card_id, up = _resolve_card(m)
    keys = "|".join(_customize_text(c) for c in m.get("customizations") or [])
    text = m.get("custom_text") or ""
    for key, words in CUSTOM_TEXT_KEYS.items():
        if any(w in text for w in words):
            keys += "|" + key
    customized = m.get("customized")
    if customized is None and (m.get("customizations") or text):
        customized = True
    params = m.get("params") or parse_param_texts(m.get("param_texts") or [])
    return {"id": m.get("id", m.get("index")), "card_id": card_id, "name": m.get("card_name") or card_id, "upgrade": up,
            "custom_keys": keys, "customized": customized, "timing": _timing(m), "params": params,
            "abilities": list(m.get("abilities") or []), "rank": m.get("rank"), "raw": m}  # fmt: skip


def memory_quality(mem: dict, gains: dict | None = None, finals: dict | None = None,
                   profile: str | None = None) -> tuple[float, list[str], list[tuple[str, float]]]:  # fmt: skip
    """一份回忆的质量分（同一张卡不同回忆之间比较）、说明、能力 [(去重键, 分)]。profile：流派（memory_priority.PROFILES），默认 やる気。"""
    from hiro.state.memory_priority import ability_value, card_entry, param_points

    e = card_entry(mem["card_id"], profile) or {}
    score, why, abil = 0.0, [], []
    customs = e.get("customs") or []
    hit = next(((k, b, n) for k, b, n in customs if k in mem["custom_keys"]), None)
    if hit:
        score += hit[1]
        why.append(f"推荐定制：{hit[2]}")
    elif mem["customized"] and customs:
        score += max(b for _, b, _ in customs) * CUSTOM_UNKNOWN
        why.append("定制过（读不出是哪项）")
    elif customs:
        why.append("未定制")
    t = mem["timing"]
    if e.get("timing") == "start" and t == "mid":
        score -= e.get("timing_penalty", 0)
        why.append(f"中间试验后才入手（这张要开局入手，-{e.get('timing_penalty', 0)}）")
    elif t == "start" and e.get("timing_bonus_start"):
        score += e["timing_bonus_start"]
        why.append("开局入手")
    elif t is None and e.get("timing") == "start":
        why.append("入手时机未读出")
    if (mem["upgrade"] or 0) >= 1:
        score += UPGRADE_BONUS + e.get("upgrade_bonus", 0)
        if e.get("upgrade_bonus"):
            why.append("已强化（这张要强化）")
    elif e.get("upgrade_bonus"):
        why.append("未强化（社区：这张要强化）")
    for a in mem["abilities"]:
        v, key = ability_value(a, profile)
        if v:
            abil.append((key or a, v))
            score += v
            if v >= 4:
                why.append(f"能力：{a}")
    pts = param_points(mem["params"], gains, finals)
    score += min(pts / PARAM_SCALE, 8)
    if pts:
        why.append(f"属性词条约 {pts:.0f} 分")
    return score, why, abil


def _formation_score(mems: list[dict], scored: dict, gains, finals, profile: str | None = None) -> tuple[float, list[str]]:
    from hiro.state.memory_priority import AB_BONUS, AB_PENALTY, PARAM_CAP, PARAM_FINALS, PARAM_GAINS, PROFILES, card_entry

    gains, finals = gains or PARAM_GAINS, finals or PARAM_FINALS
    total, notes, seen = 0.0, [], {}
    for m in mems:
        s = scored[id(m)]
        total += s["weight"] + s["quality"] - s["param_q"]
        for key, v in s["abilities"]:
            if key in seen:
                total -= v / 2
                notes.append(f"能力重复（{key}），第二条按一半")
            seen[key] = True
    # 属性词条合并封顶：几份回忆加在同一属性上，超过上限的部分没用
    raw: dict = {}
    for m in mems:
        for attr, v in (m["params"] or {}).items():
            if attr in gains:
                fixed, pct = (v.get("fixed") or 0, v.get("pct") or 0) if isinstance(v, dict) else (v or 0, 0)
                raw[attr] = raw.get(attr, 0) + fixed + pct / 100 * gains[attr]
    pts = sum(min(v, max(0, PARAM_CAP - finals.get(a, 0))) for a, v in raw.items())
    total += min(pts / PARAM_SCALE, 8 * SLOTS)
    ids = {m["card_id"] for m in mems}
    if "p_card-00-men-3_005" in ids and any(m["card_id"] == "p_card-02-act-1_037" and m["timing"] == "start" for m in mems):
        total -= CONFLICT_PENALTY
        notes.append(f"仕切り直し 与开局入手的 デイドリーミング 同在：开局元気不够打不出时会被换手牌洗掉（-{CONFLICT_PENALTY}）")
    target = PROFILES[profile]["ab_target"] if profile in PROFILES else None
    if target and len(mems) == SLOTS:
        # A 栏（一次性压缩 / 过牌）与 B 栏（循环核心）各 1–3 张，推荐配比见流派表 [いおん09][いおん10][いおん12]
        a = sum((card_entry(m["card_id"], profile) or {}).get("slot") == "A" for m in mems)
        b = sum((card_entry(m["card_id"], profile) or {}).get("slot") == "B" for m in mems)
        for col, n in (("A", a), ("B", b)):
            if not 1 <= n <= 3:
                total -= AB_PENALTY
                notes.append(f"{col} 栏 {n} 张，社区建议每栏 1–3 张（-{AB_PENALTY}）")
        if (a, b) == tuple(target):
            total += AB_BONUS
    return total, notes


def select_memories(inventory: dict | list, *, gains: dict | None = None, finals: dict | None = None, plan: str = "ProducePlanType_Plan2",
                    profile: str | None = None) -> dict:  # fmt: skip
    """库存 → 推荐的 4 格编成、每格理由、第 2 方案与差距、值得刷的回忆。profile：流派（默认 やる気；见 memory_priority.profile_for_idol）。"""
    from itertools import combinations

    from hiro.state.context import catalog
    from hiro.state.memory_priority import MEMORY_PRIORITY, card_entry, fallback_weight, param_points

    rows = (inventory.get("memories") or inventory.get("entries") or []) if isinstance(inventory, dict) else inventory
    mems, skipped = [], []
    for raw in rows:
        m = normalize_memory(raw)
        if m["card_id"] is None:
            skipped.append(f"{raw.get('id')}：卡名读不出（{raw.get('card_name')}）")
            continue
        try:
            card_plan = catalog().card(m["card_id"], 0).get("planType")
        except Exception:
            card_plan = None
        if card_plan not in (None, plan, "ProducePlanType_Common"):
            continue  # 别的プラン的卡带不进来
        mems.append(m)
    scored = _score_memories(mems, gains, finals, profile)
    # 每张卡留质量最好的 3 份（同一张卡不重复带，候选数控制在组合可穷举的范围）
    by_card: dict = {}
    for m in sorted(mems, key=lambda x: -(scored[id(x)]["weight"] + scored[id(x)]["quality"])):
        by_card.setdefault(m["card_id"], []).append(m)
    top_cards = sorted(by_card, key=lambda c: -(scored[id(by_card[c][0])]["weight"] + scored[id(by_card[c][0])]["quality"]))[:10]
    pool = [m for c in top_cards for m in by_card[c][:3]]
    plans = []
    for combo in combinations(pool, min(SLOTS, len({m["card_id"] for m in pool}))):
        if len({m["card_id"] for m in combo}) < len(combo):
            continue
        total, notes = _formation_score(list(combo), scored, gains, finals, profile)
        plans.append((total, combo, notes))
    plans.sort(key=lambda p: -p[0])
    if not plans:
        return {"chosen": [], "notes": ["库存为空或没有可用的回忆"], "skipped": skipped, "farm": farm_list({}, scored, profile), "profile": profile or "yaruki"}
    best_total, best, best_notes = plans[0]
    best_ids = {m["card_id"] for m in best}
    runner = next((p for p in plans[1:] if {m["card_id"] for m in p[1]} != best_ids), None)
    same_cards = next((p for p in plans[1:] if {m["card_id"] for m in p[1]} == best_ids), None)

    def view(m):
        s = scored[id(m)]
        e = card_entry(m["card_id"], profile) or {}
        alts = [x for x in by_card[m["card_id"]] if x is not m]
        alt = alts[0] if alts else None
        return {"id": m["id"], "card_id": m["card_id"], "name": e.get("name") or m["name"], "upgrade": m["upgrade"],
                "timing": m["timing"], "weight": s["weight"], "quality": round(s["quality"], 1), "role": e.get("role"), "slot": e.get("slot"),
                "sources": e.get("sources", []), "why": s["why"],
                "vs_same_card": (f"同卡另一份 {alt['id']}：质量 {scored[id(alt)]['quality']:.1f}（{'；'.join(scored[id(alt)]['why']) or '无'}）"
                                 if alt else "库存里只有这一份")}  # fmt: skip

    chosen = [view(m) for m in sorted(best, key=lambda m: -scored[id(m)]["weight"])]
    left_out = [c for c in top_cards if c not in best_ids]
    out = {"chosen": chosen, "total": round(best_total, 1), "notes": best_notes, "skipped": skipped,
           "not_chosen": [{"card_id": c, "name": (card_entry(c, profile) or {}).get("name") or by_card[c][0]["name"],
                           "best": round(scored[id(by_card[c][0])]["weight"] + scored[id(by_card[c][0])]["quality"], 1)} for c in left_out[:5]]}  # fmt: skip
    if runner:
        diff_in = [m for m in runner[1] if m["card_id"] not in best_ids]
        diff_out = [m for m in best if m["card_id"] not in {x["card_id"] for x in runner[1]}]
        out["runner_up"] = {"total": round(runner[0], 1), "gap": round(best_total - runner[0], 1),
                            "swap": f"把 {'、'.join(view(m)['name'] for m in diff_out)} 换成 {'、'.join(view(m)['name'] for m in diff_in)}"}  # fmt: skip
    if same_cards:
        out["same_cards_other_copies"] = round(best_total - same_cards[0], 1)
    out["farm"] = farm_list(by_card, scored, profile)
    out["profile"] = profile or "yaruki"
    return out


RENT_MIN_GAIN = 2.0  # 租借要让编成总分至少高这么多才租（低于这个就是噪声级的差别，不值得用掉一次租借）


def _score_memories(mems: list[dict], gains, finals, profile: str | None = None) -> dict:
    from hiro.state.memory_priority import card_entry, fallback_weight, param_points

    scored = {}
    for m in mems:
        q, why, abil = memory_quality(m, gains, finals, profile)
        e = card_entry(m["card_id"], profile)
        w = e["weight"] if e else fallback_weight(m["card_id"], profile)
        scored[id(m)] = {"weight": w, "quality": q, "why": why, "abilities": abil,
                         "param_q": min(param_points(m["params"], gains, finals) / PARAM_SCALE, 8)}  # fmt: skip
    return scored


def _plan_problem(raw: dict, m: dict, plan: str) -> str | None:
    """租借回忆的方案检查：卡的 planType（master）或租借行的 plan 字段（识屏读的）不是 共通 / 本方案 → 不能带。"""
    from hiro.brain.support_select import _norm_plan
    from hiro.state.context import catalog

    ok = (plan, "ProducePlanType_Common")
    try:
        card_plan = catalog().card(m["card_id"], 0).get("planType")
    except Exception:
        return "master 查不到这张卡"
    if card_plan not in ok:
        return f"プラン不一致（master {card_plan}）"
    seen = _norm_plan(raw.get("plan"))
    if seen is not None and seen not in ok:
        return f"プラン不一致（列表标 {raw.get('plan')}）"
    if raw.get("plan_mismatch"):
        return "プラン不一致（识屏看到标记）"
    return None


# 消耗白名单：用户明确授权、可以直接花的资源。
# 「マニー」（游戏币）：用户 2026-11-02 经协调授权（原话「允许，随便租」），用于租借回忆（目前每份 1500）。
# ジュエル 永远不在白名单里（用户规则：一律不花）。
SPEND_ALLOWED = {"マニー"}
SPEND_FORBIDDEN = ("ジュエル", "jewel")


def cost_allowed(cost) -> bool:
    """cost：识屏读到的消耗提示（如「必要マニー 1500」/ {"item": "マニー", "amount": 1500}）。只有白名单资源、且不含禁用字样才放行。"""
    text = cost if isinstance(cost, str) else " ".join(str(v) for v in (cost.values() if isinstance(cost, dict) else [cost]))
    if any(f in text for f in SPEND_FORBIDDEN):
        return False
    return any(a in text for a in SPEND_ALLOWED)


def choose_memory_rental(rental_rows: list[dict], current_formation: list[dict], inventory: dict | list | None = None, *,
                         rentals_left: int | None = None, gains: dict | None = None, finals: dict | None = None,
                         plan: str = "ProducePlanType_Plan2", profile: str | None = None) -> dict:  # fmt: skip
    """回忆租借（用户 2026-11-02 同意，总控派单）：自己库存缺关键卡（せのびの魔法 等）时，从租借列表里租一份替换编成里的一格。

    - rental_rows：租借列表（开局第 3 步），每行和回忆库存同一格式（card_id / card_name、upgrade、timing、abilities、customizations、
      param_texts…），可带 plan / plan_mismatch（识屏读到的方案）、slot（列表位置）、owner、cost（任何消耗提示）。
    - current_formation：当前编成（编成 5）的 4 格，同一格式；inventory：自己的回忆库存（可选，用来判断「自己有没有更好的替代」）。
    - 评分和 select_memories 同一套（卡的社区优先度 + 定制 / 入手时机 / 能力 / 属性词条，能力重复按一半、词条合并封顶）。
    - 每份租借试换编成里的每一格（同一张卡不重复带：租的卡编成里已有时只能换那一格），取总分提升最大的；提升 < RENT_MIN_GAIN 不租。
    - 只选プラン適正的；带 cost 的行：白名单资源（マニー，用户 2026-11-02 授权）照常选；其他消耗（ジュエル / 道具）不选，在 need_user 里列出交用户请示。
    - 自己库存里有同一张卡、换上去不比租的差（差 < RENT_MIN_GAIN）：不租，建议换自己的（use_own）。
    - 租借次数用完（rentals_left == 0）或找不到值得租的：rent=None，按现有编成开局，不卡住。

    返回 {rent: row | None, replace_slot: int | None（current_formation 的下标）, replace: 被换掉那格的说明, gain, reason,
          rows: 每份租借的最好替换与提升, use_own, need_user, current_total}。"""
    gains_, finals_ = gains, finals
    cur = [normalize_memory(r) for r in current_formation or []]
    cur = [m for m in cur if m["card_id"]]
    out = {"rent": None, "replace_slot": None, "replace": None, "gain": 0.0, "rows": [], "use_own": None, "need_user": [],
           "current_total": None}  # fmt: skip
    if rentals_left is not None and rentals_left <= 0:
        out["reason"] = "今日租借次数已用完：按现有编成开局"
        return out
    rentals = []
    for raw in rental_rows or []:
        m = normalize_memory(raw)
        label = m["name"] or raw.get("card_name") or raw.get("card_id")
        if m["card_id"] is None:
            out["rows"].append({"slot": raw.get("slot"), "name": label, "skip": "卡名读不出"})
            continue
        if raw.get("cost") and not cost_allowed(raw.get("cost")):
            out["need_user"].append({"slot": raw.get("slot"), "name": label, "cost": raw.get("cost")})
            out["rows"].append({"slot": raw.get("slot"), "name": label, "skip": f"有消耗提示（{raw.get('cost')}）：不选，交用户请示"})
            continue
        why = _plan_problem(raw, m, plan)
        if why:
            out["rows"].append({"slot": raw.get("slot"), "name": label, "skip": why})
            continue
        rentals.append((raw, m))
    own_rows = ((inventory.get("memories") or inventory.get("entries") or []) if isinstance(inventory, dict) else inventory) or []
    own = [m for m in (normalize_memory(r) for r in own_rows) if m["card_id"]]
    scored = _score_memories(cur + [m for _, m in rentals] + own, gains_, finals_, profile)
    base, _ = _formation_score(cur, scored, gains_, finals_, profile) if cur else (0.0, [])
    out["current_total"] = round(base, 1)

    def best_swap(m: dict) -> tuple[float, int | None, list[str]]:
        slots = [i for i, c in enumerate(cur) if c["card_id"] == m["card_id"]] or list(range(len(cur)))
        if len(cur) < SLOTS and not any(c["card_id"] == m["card_id"] for c in cur):
            total, notes = _formation_score(cur + [m], scored, gains_, finals_, profile)
            return total - base, None, notes  # 有空格：直接放进去
        best = (float("-inf"), None, [])
        for i in slots:
            total, notes = _formation_score(cur[:i] + [m] + cur[i + 1:], scored, gains_, finals_, profile)
            if total - base > best[0]:
                best = (total - base, i, notes)
        return best

    from hiro.state.memory_priority import card_entry

    def name_of(x: dict) -> str:
        if (card_entry(x["card_id"], profile) or {}).get("name"):
            return card_entry(x["card_id"], profile)["name"]
        try:
            from hiro.state.context import catalog

            return catalog().card(x["card_id"], 0)["name"]
        except Exception:
            return x["name"] or x["card_id"]

    best = None
    for raw, m in rentals:
        gain, slot, notes = best_swap(m)
        s = scored[id(m)]
        row = {"slot": raw.get("slot"), "name": name_of(m), "card_id": m["card_id"],
               "owner": raw.get("owner"), "weight": s["weight"], "quality": round(s["quality"], 1), "why": s["why"],
               "replace_slot": slot, "replace": name_of(cur[slot]) if slot is not None else "空格", "gain": round(gain, 1), "notes": notes}  # fmt: skip
        out["rows"].append(row)
        if best is None or gain > best[0]:
            best = (gain, raw, m, slot, row)
    if best is None:
        out["reason"] = "租借列表里没有能用的回忆（读不出 / プラン不一致 / 有消耗提示）：按现有编成开局"
        return out
    gain, raw, m, slot, row = best
    if gain < RENT_MIN_GAIN:
        out["reason"] = f"最好的租借 {row['name']} 只让编成总分 {gain:+.1f}（< {RENT_MIN_GAIN}）：不租，按现有编成开局"
        return out
    # 自己库存里同一张卡的回忆换上去不比租的差：用自己的，不占租借次数
    mine = [x for x in own if x["card_id"] == m["card_id"] and not any(x["id"] == c["id"] for c in cur)]
    if mine:
        own_best = max(mine, key=lambda x: best_swap(x)[0])
        own_gain, own_slot, _ = best_swap(own_best)
        if own_gain >= gain - RENT_MIN_GAIN:
            out["use_own"] = {"id": own_best["id"], "name": row["name"], "replace_slot": own_slot, "gain": round(own_gain, 1)}
            out["reason"] = f"自己库存有 {row['name']} 的回忆（{own_best['id']}，换上 {own_gain:+.1f}），不比租的（{gain:+.1f}）差：不租，换自己的"
            return out
    out.update(rent=raw, replace_slot=slot, replace=row["replace"], gain=round(gain, 1))
    out["reason"] = (f"租 {row['name']}（列表第 {raw.get('slot')} 格{'，' + str(raw.get('owner')) if raw.get('owner') else ''}；"
                     f"{'；'.join(row['why']) or '无定制 / 能力信息'}），换掉编成第 {slot + 1 if slot is not None else '空'} 格 {row['replace']}："
                     f"编成总分 {base:.1f} → {base + gain:.1f}（{gain:+.1f}）")  # fmt: skip
    return out


def farm_list(by_card: dict, scored: dict, profile: str | None = None) -> list[dict]:
    """值得刷的回忆（§5）：优先级表里的卡，库存没有，或最好的一份缺推荐定制 / 入手时机不对 / 没有好能力。按卡优先度排。"""
    from hiro.state.memory_priority import MEMORY_PRIORITY, PROFILES

    out = []
    for e in PROFILES[profile]["priority"] if profile in PROFILES else MEMORY_PRIORITY:
        if e["weight"] < FARM_MIN_WEIGHT:
            continue
        have = by_card.get(e["card_id"]) or []
        best = have[0] if have else None
        want = []
        if best is None:
            want.append("库存没有这张卡的回忆")
        else:
            s = scored[id(best)]
            if e.get("customs") and not any(k in best["custom_keys"] for k, _, _ in e["customs"]):
                want.append(f"推荐定制：{e['customs'][0][2]}")
            if e.get("timing") == "start" and best["timing"] != "start":
                want.append("开局入手" + ("（现有的是中间试验后入手）" if best["timing"] == "mid" else "（入手时机未读出）"))
            if e.get("upgrade_bonus") and not (best["upgrade"] or 0):
                want.append("强化过的（社区：这张要强化）")
            if not any(v >= 4 for _, v in s["abilities"]):
                react = sorted(PROFILES[profile]["react"].items(), key=lambda kv: -kv[1])[:2] if profile in PROFILES else []
                want.append(f"HIF 能力：{' / '.join(k for k, _ in react)} 再动" if react and profile != "yaruki"
                            else "HIF 能力：紙吹雪 / Wkwk 再动（[いおん15] 优先 紙吹雪）")  # fmt: skip
        if want:
            out.append({"card_id": e["card_id"], "name": e["name"], "weight": e["weight"], "owned": len(have),
                        "want": want, "sources": e["sources"]})  # fmt: skip
    return out


def format_memory_report(r: dict) -> str:
    from hiro.state.memory_priority import PROFILES

    prof = PROFILES.get(r.get("profile") or "yaruki", PROFILES["yaruki"])
    lines = [f"# 回忆编成推荐（{prof['name']}）", ""]
    for i, c in enumerate(r.get("chosen", []), 1):
        col = f"{c['slot']} 栏，" if c.get("slot") else ""
        lines.append(f"{i}. **{c['name']}{'+' * (c['upgrade'] or 0)}**（回忆 {c['id']}，{col}{c['role']}；"
                     f"{'开局' if c['timing'] == 'start' else '中间试验后' if c['timing'] == 'mid' else '时机未读出'}入手）")  # fmt: skip
        lines.append(f"   - 社区：{'、'.join(c['sources'])}；优先度 {c['weight']}，回忆质量 {c['quality']}")
        lines.append(f"   - {'；'.join(c['why']) or '无定制 / 能力信息'}")
        lines.append(f"   - 为什么是这份：{c['vs_same_card']}")
    if r.get("runner_up"):
        ru = r["runner_up"]
        lines += ["", f"第 2 方案：{ru['swap']}，总分低 {ru['gap']}（{r['total']} 对 {ru['total']}）。"]
    if r.get("notes"):
        lines += ["", "注意：" + "；".join(r["notes"])]
    if r.get("not_chosen"):
        lines += ["", "没选上的高优先卡：" + "、".join(f"{x['name']}（{x['best']}）" for x in r["not_chosen"])]
    if r.get("farm"):
        lines += ["", "## 值得刷的回忆", ""]
        for f in r["farm"]:
            lines.append(f"- {f['name']}（优先度 {f['weight']}，现有 {f['owned']} 份）：{'；'.join(f['want'])}")
    if r.get("skipped"):
        lines += ["", "读不出的回忆：" + "；".join(r["skipped"])]
    return "\n".join(lines) + "\n"


def main(argv=None):
    """python -m hiro.brain.formation reports/live/memory-inventory.json [输出.md]"""
    import json
    import sys
    from pathlib import Path

    argv = argv if argv is not None else sys.argv[1:]
    r = select_memories(json.loads(Path(argv[0]).read_text(encoding="utf-8")))
    text = format_memory_report(r)
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
        Path(argv[1]).with_suffix(".json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
