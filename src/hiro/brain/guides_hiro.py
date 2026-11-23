"""Campus 広 的手写知识 → 攻略条目（偶像卡层，gakumas-bench docs/guides.md 定稿 v1）。设计文档 4.6 / 4.9-4：迁移后行为不变，
接入点走 HIRO_GUIDES_HIRO（默认关）；打开时决策器从条目读，和手写表逐条一致（tests/test_guides_hiro.py 核对）。

迁了什么（初 / N.I.A 用到的）：
- card_pick：YARUKI_CARDS（必拿 / S / A / B → weight 100 / 90 / 75 / 60，role，keep）+ SMALL_DECK_CORE（可复用、要留张数的核心卡：keep）；
  avoid = CONFLICT_CARDS + OFF_PLAN_CARDS + CULL_AS_IMPRESSION。
- card_upgrade：CUSTOMIZE_PRIORITY（顺序、定制键）+ CUSTOMIZE_LEVELS（目标级数、是否必须做满）。
- formation.main_attr：formation_plan.DEFAULT_MAIN / DEFAULT_MAIN_BY_MODE（H.I.F、N.I.A Da；初 Vo，用户 11-20）。
没迁的：card_value（同档的先后，只是排序的细节）、H.I.F 专用的表（FINALS_DELETE、轮间、本战）——H.I.F 这次不接攻略。

导出给 bench 收进人写条目：python -m hiro.brain.guides_hiro > guides/entries/any/idol/i_card-shro-3-007.json
"""

from __future__ import annotations

import json
import re

HIRO = "i_card-shro-3-007"
TIER_WEIGHT = {"S": 90, "A": 75, "B": 60}
MUST_WEIGHT = 100
UPDATED = "2026-11-20"
_REF = re.compile(r"\[([^\]]+)\]")


def _evidence(text: str) -> list[dict]:
    """说明里的出处 → evidence：[game8] [もりし] 这类是攻略；「决策-N」「用户 YYYY-MM-DD」「协调」「总控」是用户 / 总控定的规则；都没有时记成 decision（广的手写表）。"""
    ev = []
    for ref in _REF.findall(text or ""):
        for r in re.split(r"[\]\[、,]", ref):
            r = r.strip()
            if not r:
                continue
            ev.append({"kind": "decision" if r.startswith("决策") else "guide", "ref": r})
    for m in re.findall(r"(决策-\d+|用户 ?20\d\d-\d\d-\d\d|协调[^，；）]*|总控[^，；）]*)", text or ""):
        ev.append({"kind": "decision", "ref": m.strip()})
    if not ev:
        ev.append({"kind": "decision", "ref": "广手写知识（hiro.state.knowledge）"})
    seen, out = set(), []
    for e in ev:
        key = (e["kind"], e["ref"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def card_pick_entry() -> dict:
    from hiro.state.knowledge import (
        CONFLICT_CARDS,
        CULL_AS_IMPRESSION,
        OFF_PLAN_CARDS,
        SMALL_DECK_CORE,
        YARUKI_CARDS,
        core_keep,
    )

    prefer, ev_text = [], []
    conflicts = set(CONFLICT_CARDS) | set(OFF_PLAN_CARDS) | set(CULL_AS_IMPRESSION)
    for cid, k in YARUKI_CARDS.items():
        if cid in conflicts:
            continue  # 卡表里有、但同时是冲突卡（夢色リップ：CULL_AS_IMPRESSION）：只放 avoid，不然合并时推荐先到、冲突不生效
        it = {"id": cid}
        w = MUST_WEIGHT if k.get("must") else TIER_WEIGHT.get(k.get("tier"))
        if w is not None:
            it["weight"] = w
        if k.get("role"):
            it["role"] = k["role"]
        if cid in SMALL_DECK_CORE or k.get("keep"):
            it["keep"] = core_keep(cid)
        it["note"] = (k.get("name") or "") + "：" + (k.get("note") or "")
        prefer.append(it)
        ev_text.append(k.get("note") or "")
    for cid, why in SMALL_DECK_CORE.items():
        if cid not in YARUKI_CARDS:
            prefer.append({"id": cid, "keep": core_keep(cid), "note": why})
            ev_text.append(why)
    avoid = [
        {"id": cid, "role": "conflict", "note": why}
        for table in (CONFLICT_CARDS, OFF_PLAN_CARDS, CULL_AS_IMPRESSION)
        for cid, why in table.items()
    ]
    ev_text += [it["note"] for it in avoid]
    prefer.sort(key=lambda it: -(it.get("weight") or 0))
    return {"id": f"any.idol.{HIRO}.card_pick.yaruki-cards", "kind": "written", "scope": {"idol_card": HIRO}, "decision": "card_pick",
            "content": {"prefer": prefer, "avoid": avoid,
                        "why": "やる気 Campus 広 的卡评级：必拿 / S / A 优先；可复用的只留循环和道具触发的核心卡（keep 张）；好印象系、元気增加无效的卡不拿"},
            "evidence": _merge_evidence(ev_text), "updated": UPDATED, "by": "3940a9"}  # fmt: skip


def card_upgrade_entry() -> dict:
    from hiro.state.knowledge import CUSTOMIZE_LEVELS, CUSTOMIZE_PRIORITY

    prefer, ev_text = [], []
    for cid, name, keys, why in CUSTOMIZE_PRIORITY:
        levels, full = CUSTOMIZE_LEVELS.get(cid, (1, False))
        prefer.append({"id": cid, "custom": list(keys), "levels": levels, "full": full, "note": f"{name}：{why}"})
        ev_text.append(why)
    return {"id": f"any.idol.{HIRO}.card_upgrade.customize", "kind": "written", "scope": {"idol_card": HIRO}, "decision": "card_upgrade",
            "content": {"prefer": prefer, "why": "定制按顺序做；えいえいおー 的 やる気+ 只有做满 3 级才值"},
            "evidence": _merge_evidence(ev_text), "updated": UPDATED, "by": "3940a9"}  # fmt: skip


MAIN_ATTR_WHY = {
    "hif": (
        "开局编成的默认主属性：H.I.F 主 Da（成长率 Vo 最高，但项目定的是 Da）",
        [{"kind": "decision", "ref": "决策-07"}],
    ),
    "nia": ("开局编成的默认主属性：N.I.A 暂时沿用 H.I.F 的 Da，等用户定", [{"kind": "decision", "ref": "决策-07"}]),
    "hajime": (
        "开局编成的默认主属性：初主 Vo（初 LEGEND 排行榜 89% 是 Vo 最高）",
        [
            {"kind": "decision", "ref": "用户 2026-11-20"},
            {"kind": "ranking", "mode": "hajime", "n": 100, "share": 0.89, "fetched": "2026-11-19"},
        ],
    ),
}


def main_attr_entries() -> list[dict]:
    """按模式各一条（用户 11-20：广在初里主 Vo，H.I.F 主 Da；N.I.A 先照旧 Da）。"""
    from hiro.brain.formation_plan import default_main

    out = []
    for mode, (why, ev) in MAIN_ATTR_WHY.items():
        attr = default_main(HIRO, mode)
        if attr:
            out.append({"id": f"{mode}.idol.{HIRO}.formation.main_attr.{attr.lower()}", "kind": "written", "scope": {"idol_card": HIRO, "mode": mode},
                        "decision": "formation.main_attr", "content": {"prefer": [attr.lower()], "why": why}, "evidence": ev,
                        "updated": "2026-11-20", "by": "3940a9"})  # fmt: skip
    return out


def _merge_evidence(texts) -> list[dict]:
    seen, out = set(), []
    for t in texts:
        for e in _evidence(t):
            key = (e["kind"], e["ref"])
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


def entries() -> list[dict]:
    return [card_pick_entry(), card_upgrade_entry(), *main_attr_entries()]


if __name__ == "__main__":
    print(json.dumps(entries(), ensure_ascii=False, indent=1))
