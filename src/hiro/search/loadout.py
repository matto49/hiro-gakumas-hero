"""这一局的编成 → 合成开局要的 P 道具 / 支援卡 / 回忆附魔（任务 ② 第 1 部分；设计稿 2026-11-12-rust-exam-search-design.md）。

来源（不读存档、不读种子）：
- P 道具：状态机记账 produce.items（PITEM_ACQUIRE / PITEM_CUSTOMIZE）；
- 支援卡：开局编成方案（conf/formation-plan.json）的 support.own + rental，等级 → 考试内强化概率（gakumas_exam.loadout.support_entry）；
- 回忆附魔：开局编成方案 memory.chosen 每份回忆的能力文字，对主数据 H.I.F 回忆技能的说明（「〇〇使用後、…眠気を除外…」按卡名对）。
拿不到的项给 None 以外的空值并写进 notes——不退回模板那一局的（任务 ① 查到的污染）。
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache

HIF_TRIGGER = "p_trigger-start_audition-for_hif_memory"


@lru_cache(maxsize=1)
def hif_memory_skills() -> dict[str, str]:
    """H.I.F 回忆技能（考试附魔）→ 说明文字。"""
    from hiro.brain.cards import description_text
    from hiro.state.context import catalog

    rows = catalog().db.execute(
        "SELECT DISTINCT s.id, s.produceDescriptions FROM MemoryAbility a JOIN ProduceSkill s ON s.id = a.skillId AND s.level = a.level "
        "WHERE s.produceTriggerId1 = ?", (HIF_TRIGGER,)).fetchall()  # fmt: skip
    return {sid: description_text(d) for sid, d in rows}


_KEY = re.compile(r"以降\d+回まで、(.+?)使用後")


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "").replace("＋", "+")


def skills_from_abilities(texts: list[str]) -> list[str]:
    """回忆能力文字（OCR）→ H.I.F 回忆技能 ID：先按说明里的卡名找（文字里出现这张卡的名字且是「使用後」那类），
    再按整段相似度（≥ 0.85）。属性词条等对不上的忽略。同技能只算一次（主数据 isUniqueActivation）。"""
    skills = hif_memory_skills()
    out: list[str] = []
    for raw in texts:
        t = _norm(raw)
        if "使用後" not in t and "除外" not in t:
            continue
        hit = None
        best = 0
        for sid, desc in skills.items():
            m = _KEY.search(desc)
            if m and m.group(1) in t and len(m.group(1)) > best:
                hit, best = sid, len(m.group(1))
        if hit is None:
            scored = max(
                skills.items(), key=lambda kv: difflib.SequenceMatcher(None, _norm(kv[1]), t).ratio(), default=None
            )
            if scored and difflib.SequenceMatcher(None, _norm(scored[1]), t).ratio() >= 0.85:
                hit = scored[0]
        if hit and hit not in out:
            out.append(hit)
    return out


_CZ = None


def customize_counts(card_id: str, upgrade: int, customize: dict | None) -> list[int] | None:
    """一张卡的定制 {定制 ID: 次数} → 按主数据这张卡 produceCardCustomizeIds 顺序的次数列表（R2Setup 牌组项的第 3 位）。
    没有定制返回 None；定制 ID 不在这张卡的列表里的忽略（任务 ② 验证 2：デイドリーミング 定制后打出挂的是 enc03，不是 enc01）。"""
    global _CZ
    if not customize:
        return None
    if _CZ is None:
        from hiro.bench_bridge.advisor import _customize_ids

        _CZ = _customize_ids()
    ids = _CZ(card_id, int(upgrade or 0))
    counts = [int(customize.get(i) or 0) for i in ids]
    return counts if any(counts) else None


def parse_deck_entry(s: str) -> tuple:
    """「卡ID+强化/定制ID*次数」（gakumas-bench 题目写法）或 {id, upgrade_count, customizes}（bench 公开编成）→ (card_id, upgrade, {定制 ID: 次数})。"""
    if isinstance(s, dict):  # bench 交给算法的公开编成：{id, upgrade_count?, customizes?: [{id, customize_count}]}
        cz = {}
        for c in s.get("customizes") or []:
            cz[c["id"]] = cz.get(c["id"], 0) + int(c.get("customize_count") or 1)
        if (
            "produce_card_id" in s
        ):  # bench 模板展开后的 GKMS 写法（初 / N.I.A 题）：{produce_card_id, current_upgrade_count, customizes}
            up = s.get("current_upgrade_count", s.get("base_upgrade_count"))
            return s["produce_card_id"], int(up or 0), cz
        return s["id"], int(s.get("upgrade_count") or 0), cz
    head, _, cz = str(s).partition("/")
    cid, _, u = head.partition("+")
    out: dict = {}
    # 一张卡可以带多个定制，用「/」（bench 题目）或「,」连起来：卡ID+1/定制A/定制B（偶像之路对拍：只认逗号时两个定制都没生效，
    # 「強気に変更」→「強気 2 段」那个定制丢了，集中机制分数偏差就是它）
    for part in filter(None, re.split(r"[/,]", cz)):
        k, _, n = part.partition("*")
        out[k] = out.get(k, 0) + int(n or 1)
    return cid, int(u or 0), out


def supports_from_plan(plan: dict) -> list[dict]:
    """开局编成方案的支援卡（自有 + 租借）→ supportCardList 项。"""
    from hiro.sim.engine import master_commit

    from gakumas_exam.loadout import support_entry
    from gakumas_exam.master import shared

    m = shared(master_commit())
    sup = plan.get("support") or {}
    cards = list(sup.get("own") or []) + ([sup["rental"]] if sup.get("rental") else [])
    return [support_entry(m, c["support_card_id"], int(c.get("level") or 1)) for c in cards if c.get("support_card_id")]


def run_loadout(produce: dict | None, plan: dict | None) -> tuple[dict, list[str]]:
    """{items, supports, skills}（都是 tuple，给 R2Setup）+ 说明。"""
    notes: list[str] = []
    items = tuple((produce or {}).get("items") or ())
    if not items:
        notes.append("P 道具没记账：按没有 P 道具搜")
    supports: tuple = ()
    skills: tuple = ()
    if not plan:
        notes.append("没有开局编成方案：支援卡 / 回忆附魔按没有搜")
    else:
        try:
            supports = tuple(supports_from_plan(plan))
        except Exception as e:  # noqa: BLE001
            notes.append(f"支援卡换算失败（{type(e).__name__}: {e}）：按没有搜")
        chosen = (plan.get("memory") or {}).get("chosen") or []
        if chosen and not any("abilities" in c for c in chosen):
            notes.append("编成方案里的回忆没存能力文字（旧方案）：回忆附魔按没有搜")
        texts = [t for c in chosen for t in c.get("abilities") or []]
        skills = tuple(skills_from_abilities(texts))
    return {"items": items, "supports": supports, "skills": skills}, notes
