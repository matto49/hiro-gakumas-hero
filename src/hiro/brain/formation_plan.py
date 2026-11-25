"""开局编成方案：支援卡 + 回忆一起按社区规则选，写成 conf/formation-plan.json，开局时按它用编成号、做核对、告诉决策器回忆带哪些卡。

流程（用户 2026-11-06：选择 + 方案 + 开局核对；自动写编成位留到下一步）：
1. 读库存：支援卡 reports/live/support-inventory.json（tools.support_inventory），回忆 reports/live/memory-inventory.json
   （tools.memory_inventory）。
2. 按偶像卡定方案（プラン）、流派（memory_priority.profile_for_idol）和默认主属性，
   支援卡走 support_select.choose_main（三属性各组一次，ep02 逆练门槛），回忆走 formation.select_memories（流派表 + A/B 栏）。
3. 写方案：选了哪几张、理由、次优方案差多少，以及要存进的编成号（--support-set / --memory-set，用户在游戏里把方案存进这两个编成位）。
4. 开局：hiro.formation_env 读到方案就用它的编成号和回忆卡；kaa_patches 在第 2 步核对支援卡、第 3 步核对回忆编成号，对不上就停（不开局）。

租借格：库存里没有实机租借列表时，按「可租到的满级 SSR」（support_select.virtual_rentals：方案一致的全部 SSR，Lv60）
当候选，和自己的卡一起评估 6 格（主属性 4 张 [ep08]、最多 1 张租借；协调 11-17）。开局第 2 步按编成价值从实际租借列表挑（rental.py）。

用法：
    PYTHONPATH=src .venv/bin/python -m hiro.brain.formation_plan --idol i_card-skin-shro-3-007 --support-set 20 --memory-set 20
"""

from __future__ import annotations

import json
import time
from pathlib import Path

PLAN_PATH = "conf/formation-plan.json"
REPORT_PATH = "reports/live/formation-plan.md"
SUPPORT_INVENTORY = "reports/live/support-inventory.json"
MEMORY_INVENTORY = "reports/live/memory-inventory.json"
ATTRS = ("Vo", "Da", "Vi")
GROWTH_KEY = {
    "Vo": "produceVocalGrowthRatePermil",
    "Da": "produceDanceGrowthRatePermil",
    "Vi": "produceVisualGrowthRatePermil",
}
# 项目里已经定过的主属性（用户 / 决策-07：Campus 広 主 Da，虽然成长率 Vo 最高）；其他偶像默认按成长率最高的属性
DEFAULT_MAIN = {"i_card-shro-3-007": "Da"}
# 按模式的默认主属性，优先于 DEFAULT_MAIN（用户 11-20：Campus 広 在初里主 Vo，初 LEGEND 排行榜 89% 是 Vo 最高；
# 主 Da（决策-07）是 H.I.F 的决定，H.I.F 和 N.I.A 照旧，N.I.A 等用户再定）
DEFAULT_MAIN_BY_MODE = {("i_card-shro-3-007", "hajime"): "Vo"}


def default_main(idol_card: str, mode: str | None = None) -> str | None:
    """项目已定的默认主属性：按模式的（DEFAULT_MAIN_BY_MODE）> DEFAULT_MAIN；都没有 None。mode 是 produce id（produce-002）或家族名（hajime）。"""
    from hiro.state.modes import family

    cid = idol_card.replace("i_card-skin-", "i_card-")
    fam = family(mode) or mode
    return DEFAULT_MAIN_BY_MODE.get((cid, fam)) or DEFAULT_MAIN.get(cid)


# 回忆属性词条的作用基数（memory_priority.PARAM_GAINS / PARAM_FINALS 按主属性 / 第二 / 第三属性重排；数值取自 main8，粗估）
GAIN_BY_RANK = (2900, 1900, 950)
FINAL_BY_RANK = (3150, 2170, 1170)


def idol_info(idol: str, main_attr: str | None = None, mode: str | None = None) -> dict:
    """偶像卡（可以是 skin id）→ {idol_card_id, name, plan, effect, profile, profile_caveat, default_main, attr_order, main_why}。
    默认主属性：显式指定 > 偶像卡攻略的 formation.main_attr（mode 给了初 / N.I.A 的 produce id 时；广要 HIRO_GUIDES_HIRO=1）>
    DEFAULT_MAIN > 成长率最高。"""
    from hiro.brain.idol_know import main_attr as guide_main
    from hiro.state.context import catalog
    from hiro.state.memory_priority import profile_for_idol

    cid = idol.replace("i_card-skin-", "i_card-")
    row = catalog().one("IdolCard", cid)
    growth = {a: row.get(GROWTH_KEY[a]) or 0 for a in ATTRS}
    guided = None if main_attr else guide_main(mode, cid)
    fixed = default_main(cid, mode)
    default = main_attr or (guided[0] if guided else None) or fixed or max(ATTRS, key=lambda a: growth[a])
    main_why = (
        "指定" if main_attr else f"攻略 {guided[1]}" if guided else "项目已定（按模式）" if fixed else "成长率最高"
    )
    profile, caveat = profile_for_idol(cid)
    order = [default] + sorted((a for a in ATTRS if a != default), key=lambda a: -growth[a])
    return {"idol_card_id": cid, "name": row.get("name"), "plan": row["planType"], "effect": row.get("examEffectType"),
            "profile": profile, "profile_caveat": caveat, "default_main": default, "attr_order": order, "growth": growth, "main_why": main_why}  # fmt: skip


def guide_shares(mode: str | None, idol_card: str, decision: str) -> dict[str, float]:
    """攻略推荐 → {卡 id: 排行榜占比}（formation.support / formation.memory）。这张卡没有条目时用同流派的合并条目
    （guides.style_average）；写死权重的条目（written）按 weight / 100 折成占比。没给模式、HIRO_PLAN_GUIDES=0 返回 {}。"""
    import os

    if not mode or os.environ.get("HIRO_PLAN_GUIDES", "1") == "0":
        return {}
    from hiro.brain import guides
    from hiro.brain.idol_know import lookup

    out: dict[str, float] = {}
    for it in guides.merge(lookup(mode, idol_card, decision)):
        if it.side != "prefer":
            continue
        share = (it.fields.get("stat") or {}).get("share")
        if share is None and it.fields.get("weight") is not None:
            share = float(it.fields["weight"]) / 100
        if share:
            out[it.id] = round(float(share), 4)
    return out


def param_bases(attr_order: list[str]) -> tuple[dict, dict]:
    """主属性 / 第二 / 第三属性 → 回忆词条的作用基数与预计结束值。"""
    return dict(zip(attr_order, GAIN_BY_RANK)), dict(zip(attr_order, FINAL_BY_RANK))


def _load(path: str | Path | None):
    if not path:
        return None
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def build(idol: str, *, support_inventory: dict | None, memory_inventory: dict | list | None, support_set: int | None = None,
          memory_set: int | None = None, main_attr: str | None = None, fixed_main: bool = False, mode: str | None = None) -> dict:  # fmt: skip
    """库存 → 编成方案（dict，写进 conf/formation-plan.json 的就是它）。main_attr：默认主属性（fixed_main 时不比较逆练）。
    mode：初 / N.I.A 的 produce id（给了就查偶像卡攻略：默认主属性 + 支援卡 / 回忆推荐对照，见 guide_check）。"""
    from hiro.brain import support_select as ss
    from hiro.brain.formation import select_memories

    info = idol_info(idol, main_attr, mode)
    out = {
        "version": PLAN_VERSION,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "idol": info,
        "profile": info["profile"],
        "mode": mode,
        "notes": [],
    }
    if info["profile_caveat"]:
        out["notes"].append(info["profile_caveat"])

    # ---- 支援卡
    sup: dict = {"set": support_set}
    if support_inventory:
        inv = ss.normalize_inventory(support_inventory)
        has_rentals = bool(inv.get("rentals"))
        if not has_rentals:  # 没有实机租借列表：按可租到的满级 SSR 估租借格，一起评估 6 格（协调 11-17）
            inv = dict(inv, rentals=ss.virtual_rentals())
        size, main_count = 6, 4
        # 主属性来自攻略站（formation.main_attr 是前 100 名的统计，已经含成长率 / 审查基准）时不逆练：
        # 11-24 標 麻央 攻略 Vo，按支援卡价值逆练成 Da（Da 卡价值高 309），但麻央成长率 Vo 22% / Vi 18% / Da 6%、
        # 选拔审查基准 Vo 45% / Vi 30% / Da 25%，Da 是最差的主属性，选拔第 3 场只打到过线的 57%
        guided = str(info.get("main_why") or "").startswith("攻略")
        sup_guide = guide_shares(mode, info["idol_card_id"], "formation.support")
        if sup_guide:
            out["notes"].append(f"支援卡按攻略推荐加分（{len(sup_guide)} 张有排行榜占比，占比 × {ss.GUIDE_WEIGHT:g}）")
        if fixed_main or guided:
            with ss.using(info["default_main"], info["plan"], main_count, guide=sup_guide):
                r = ss.choose(inv, size=size)
            r = dict(
                r,
                main_attr_why=f"{'指定' if fixed_main else info['main_why']} 主属性 {info['default_main']}（不比较逆练）",
            )
        else:
            with ss.using(guide=sup_guide):
                r = ss.choose_main(
                    inv,
                    plan=info["plan"],
                    default_attr=info["default_main"],
                    size=size,
                    main_count=main_count,
                    growth=info.get("growth"),
                )
        if "error" in r:
            sup["error"] = r["error"]
            sup["main_attr_candidates"] = r.get("main_attr_candidates")
        else:
            sup.update(main_attr=r["main_attr"], value=r["value"], own=r["own"], rental=r.get("rental"), counts=r["counts"],
                       sp_lessons=r.get("sp_lessons"), runner_up=r.get("runner_up"), excluded=r.get("excluded"),
                       main_attr_candidates=r.get("main_attr_candidates"), main_attr_why=r.get("main_attr_why"))  # fmt: skip
            if not has_rentals:
                sup["rental_estimated"] = True
                sup["rental_rule"] = {"attr": (r.get("rental") or {}).get("attr") or r["main_attr"],
                                      "why": "租借格按「可租到的满级 SSR」估（Lv60，偏乐观）；开局第 2 步按编成价值从实际租借列表挑 [ep08]"}  # fmt: skip
            out["notes"].extend(r.get("notes") or [])
            if all(all(w.startswith("事件") for w in o.get("why") or []) for o in r["own"]):
                out["notes"].append(
                    "支援卡技能数据缺失（只算到支援事件收益）：支援卡方案只按事件、属性、等级排，不可靠，建议手动挑"
                )
    else:
        sup["error"] = "没有支援卡库存（先跑 tools.support_inventory --list）"
    out["support"] = sup
    out["main_attr"] = sup.get("main_attr") or info["default_main"]

    # ---- 回忆（词条基数按最终主属性排）
    order = [out["main_attr"]] + [a for a in info["attr_order"] if a != out["main_attr"]]
    gains, finals = param_bases(order)
    mem: dict = {"set": memory_set}
    if memory_inventory:
        mem_guide = guide_shares(mode, info["idol_card_id"], "formation.memory")
        r = select_memories(
            memory_inventory, gains=gains, finals=finals, plan=info["plan"], profile=info["profile"], guide=mem_guide
        )
        mem.update(chosen=r.get("chosen") or [], total=r.get("total"), runner_up=r.get("runner_up"), notes=r.get("notes") or [],
                   farm=r.get("farm") or [], skipped=r.get("skipped") or [], not_chosen=r.get("not_chosen") or [])  # fmt: skip
        mem["cards"] = [c["card_id"] for c in mem["chosen"]]
        if not mem["chosen"]:
            mem["error"] = "回忆库存里没有能用的回忆"
    else:
        mem["error"] = "没有回忆库存（先跑 tools.memory_inventory --live）"
        mem["cards"] = []
    out["memory"] = mem
    if mode:
        out["guide_check"] = guide_check(mode, info["idol_card_id"], sup, mem, support_inventory, memory_inventory)
        out["notes"].extend(out["guide_check"].get("notes") or [])
    return out


def guide_check(mode: str, idol_card: str, sup: dict, mem: dict, support_inventory, memory_inventory) -> dict:
    """偶像卡攻略的 formation.support / formation.memory 推荐 和 这份方案对照（设计文档 4.9-3：具体选哪张由数字小广按库存和估值算，
    攻略只给推荐、卡池要求和依据）。列出：推荐里库存有、方案也选了的；推荐里库存有、方案没选的（写进 notes 提醒人看）；推荐里库存没有的。"""
    from hiro.brain import guides
    from hiro.brain import support_select as ss
    from hiro.brain.idol_know import lookup

    chosen_sup = {o.get("support_card_id") for o in (sup.get("own") or []) if isinstance(o, dict)} - {None}
    owned_sup = {
        c.get("support_card_id") for c in ss.normalize_inventory(support_inventory or {}).get("cards") or []
    } - {None}
    chosen_mem = set(mem.get("cards") or [])
    out = {"notes": []}
    for decision, chosen, owned in (
        ("formation.support", chosen_sup, owned_sup),
        ("formation.memory", chosen_mem, None),
    ):
        rows = []
        for it in guides.merge(lookup(mode, idol_card, decision)):
            if it.side != "prefer":
                continue
            have = None if owned is None else it.id in owned
            rows.append({"id": it.id, "chosen": it.id in chosen, "owned": have, "role": it.fields.get("role"), "entry": it.entry.id,
                         "share": (it.fields.get("stat") or {}).get("share")})  # fmt: skip
        out[decision] = rows
        miss = [r["id"] for r in rows if r["owned"] and not r["chosen"]][:5]
        if miss:
            out["notes"].append(
                f"{decision}：攻略推荐、库存里有但方案没选的：{'、'.join(miss)}（按估值没选，人看一下）"
            )
    return out


def format_report(plan: dict) -> str:
    """方案 → 给人看的 markdown（编成截图确认时对照用）。"""
    from hiro.state.memory_priority import PROFILES

    info, sup, mem = plan["idol"], plan["support"], plan["memory"]
    prof = PROFILES[plan["profile"]]
    lines = [f"# 开局编成方案：{info['name']}（{info['idol_card_id']}）", "",
             f"- 方案 {info['plan'].replace('ProducePlanType_', '')}，流派 {prof['name']}（社区依据 {'、'.join(prof['sources'])}）",
             f"- 主属性 {plan['main_attr']}" + (f"（{sup['main_attr_why']}）" if sup.get("main_attr_why") else ""), ""]  # fmt: skip
    lines += ["## 支援卡" + (f"（存进编成 {sup['set']}）" if sup.get("set") else "（编成号未指定）"), ""]
    if sup.get("error"):
        lines.append(f"没有方案：{sup['error']}")
    else:
        for i, o in enumerate(sup["own"], 1):
            lines.append(
                f"{i}. {o['attr']} **{o['name']}** Lv{o['level']}，价值 {o['value']}：{'；'.join(o['why'][:4])}"
            )
        if sup.get("rental"):
            est = "（估：按满级 SSR，开局按实际列表挑）" if sup.get("rental_estimated") else ""
            lines.append(f"6. 租借 {sup['rental']['name']}（价值 {sup['rental']['value']}）{est}")
        elif sup.get("rental_rule"):
            lines.append(f"6. 租借格：{sup['rental_rule']['why']}")
        lines.append(
            f"\n总价值 {sup['value']}，属性分布 {sup['counts']}，主属性课程走 SP 约 {sup.get('sp_lessons')} 次"
        )
        if sup.get("main_attr_candidates"):
            lines.append("各属性当主属性：" + "，".join(f"{a} {v}" for a, v in sup["main_attr_candidates"].items()))
        if sup.get("runner_up"):
            ru = sup["runner_up"]
            lines.append(f"次优：不带 {ru['without']}、换 {ru['replace_with']}，低 {ru['gap']}")
        if sup.get("excluded"):
            lines.append("プラン不一致、不能带：" + "、".join(str(e["support_card_id"]) for e in sup["excluded"]))
    lines += ["", "## 回忆" + (f"（存进メモリー編成 {mem['set']}）" if mem.get("set") else "（编成号未指定）"), ""]
    if mem.get("error"):
        lines.append(f"没有方案：{mem['error']}")
    else:
        target = prof["ab_target"]
        if target:
            lines.append(
                f"社区配比：A 栏（一次性压缩 / 过牌）{target[0]} 张、B 栏（循环核心，要强化 / 定制）{target[1]} 张\n"
            )
        for i, c in enumerate(mem["chosen"], 1):
            col = f"{c['slot']} 栏，" if c.get("slot") else ""
            when = "开局" if c["timing"] == "start" else "中间试验后" if c["timing"] == "mid" else "时机未读出"
            lines.append(
                f"{i}. **{c['name']}{'+' * (c['upgrade'] or 0)}**（回忆 {c['id']}，{col}{c.get('role') or '表外卡'}；{when}入手）"
            )
            lines.append(
                f"   - 社区 {'、'.join(c['sources']) or '—'}；优先度 {c['weight']}，回忆质量 {c['quality']}；{'；'.join(c['why']) or '无定制 / 能力信息'}"
            )
        if mem.get("runner_up"):
            ru = mem["runner_up"]
            lines.append(f"\n次优：{ru['swap']}，总分低 {ru['gap']}")
        if mem.get("notes"):
            lines.append("注意：" + "；".join(mem["notes"]))
        if mem.get("farm"):
            lines += [
                "",
                "值得刷的回忆：",
                *[f"- {f['name']}（现有 {f['owned']} 份）：{'；'.join(f['want'])}" for f in mem["farm"][:6]],
            ]
    if plan.get("notes"):
        lines += ["", "## 说明", "", *[f"- {n}" for n in plan["notes"]]]
    lines += ["", "## 开局前", "",
              "1. 在游戏里把上面的支援卡、回忆存进对应编成位（不要覆盖自己常用的编成），截图确认。",
              "2. 开局时程序读 conf/formation-plan.json：用这两个编成号，回忆带的卡交给决策器；第 2 步核对支援卡、第 3 步核对回忆编成号，对不上就停。"]  # fmt: skip
    return "\n".join(lines) + "\n"


def write(plan: dict, path: str | Path = PLAN_PATH, report: str | Path | None = REPORT_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    if report:
        r = Path(report)
        r.parent.mkdir(parents=True, exist_ok=True)
        r.write_text(format_report(plan), encoding="utf-8")


def load(path: str | Path | None = None) -> dict | None:
    """读方案；没有文件、坏文件返回 None（开局沿用原来的编成号）。"""
    try:
        return _load(path or PLAN_PATH)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 按「偶像卡 × 模式」存方案（2026-11-26）
# 方案存在 <目录>/plans/<模式>/<偶像卡>.json；<目录>/formation-plan.json 是「当前方案」（开局核对、写编成都读它），
# 开局前把这一局用的那份复制过去。version 2 的方案要写进游戏后（mark_written）才放行开局；以前的方案（没有 version 2）照旧。
PLAN_VERSION = 2
CURRENT = "formation-plan.json"
PRODUCE_OF = {("hajime", None): "produce-001", ("hajime", "regular"): "produce-001", ("hajime", "pro"): "produce-002",
              ("hajime", "master"): "produce-003", ("hajime", "legend"): "produce-006", ("nia", None): "produce-004",
              ("nia", "pro"): "produce-004", ("nia", "master"): "produce-005", ("hif", None): "produce-007"}  # fmt: skip


def produce_of(family: str, difficulty: str | None = None) -> str | None:
    """模式 + 难度 → produce id（查攻略用）。H.I.F 不分难度，按选拔。"""
    return PRODUCE_OF.get((family, None if family == "hif" else difficulty)) or PRODUCE_OF.get((family, None))


def plan_family(plan: dict) -> str | None:
    """方案是给哪个模式出的（plan["family"]，旧方案没写时按 plan["mode"] 的 produce id 推）。"""
    from hiro.state.modes import family

    return (
        plan.get("family")
        or family(plan.get("mode"))
        or (plan.get("mode") if plan.get("mode") in ("hajime", "nia", "hif") else None)
    )


def plan_key_path(root: Path, idol: str, fam: str) -> Path:
    return Path(root) / "plans" / fam / f"{idol.replace('i_card-skin-', 'i_card-')}.json"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def activate(root: Path, plan: dict) -> None:
    """设为当前方案。当前方案原来是别的偶像的、又没有按模式存过的副本（旧格式，或不带 --mode 出的）时，
    先收进 plans/_legacy/<偶像卡>.json 免得丢（_legacy 里已有同一张卡的旧文件就用这份更新的覆盖）。"""
    root = Path(root)
    cur = load(root / CURRENT)
    old = ((cur or {}).get("idol") or {}).get("idol_card_id")
    if cur and old and old != (plan.get("idol") or {}).get("idol_card_id"):
        fam = cur.get("family")
        if not fam or not plan_key_path(root, old, fam).is_file():
            _write_json(plan_key_path(root, old, "_legacy"), cur)
    _write_json(root / CURRENT, plan)


def save_plan(root: Path, plan: dict, fam: str) -> Path:
    """存进 plans/<模式>/<偶像卡>.json 并设为当前方案。"""
    plan = dict(plan, family=fam)
    path = plan_key_path(Path(root), (plan.get("idol") or {}).get("idol_card_id") or "", fam)
    _write_json(path, plan)
    activate(root, plan)
    return path


def find_plan(root: Path, idol: str, fam: str) -> tuple[dict, Path] | None:
    """这张卡 × 这个模式的方案：plans/<模式>/<偶像卡>.json；没有时看当前方案 / 收起来的旧方案（旧格式、同一张卡、没标模式或同模式）。"""
    root, cid = Path(root), idol.replace("i_card-skin-", "i_card-")
    path = plan_key_path(root, cid, fam)
    if (plan := load(path)) is not None:
        return plan, path
    for p in (root / CURRENT, plan_key_path(root, cid, "_legacy")):
        plan = load(p)
        if plan and (plan.get("idol") or {}).get("idol_card_id") == cid and plan_family(plan) in (None, fam):
            return plan, p
    return None


def inherited_sets(root: Path, fam: str) -> tuple[int | None, int | None]:
    """新方案的编成位号：这个模式最近一份方案用的；没有时用旧格式的当前方案（它没标模式）的。都没有 (None, None)。"""
    root = Path(root)
    cands = list((root / "plans" / fam).glob("*.json")) if (root / "plans" / fam).is_dir() else []
    plans = [x for x in (load(p) for p in cands) if x]
    cur = load(root / CURRENT)
    if cur and plan_family(cur) in (None, fam):
        plans.append(cur)
    plans = [x for x in plans if (x.get("support") or {}).get("set") and (x.get("memory") or {}).get("set")]
    if not plans:
        return None, None
    best = max(plans, key=lambda x: x.get("created") or "")
    return int(best["support"]["set"]), int(best["memory"]["set"])


def needs_write(plan: dict, fam: str, parts=("support", "memory")) -> bool:
    """新格式的方案还没把 parts（缺省支援卡和回忆都算）写进这个模式的游戏编成位（旧方案按以前的做法：认为玩家自己写好了）。"""
    done = set(((plan.get("written") or {}).get(fam) or {}).get("what") or ())
    return plan.get("version") == PLAN_VERSION and not set(parts) <= done


def unwritten(plan: dict, fam: str, parts=("support", "memory")) -> list[str]:
    """parts 里还没写进游戏的那几样（按 support、memory 的顺序）。旧方案不算。"""
    if plan.get("version") != PLAN_VERSION:
        return []
    done = set(((plan.get("written") or {}).get(fam) or {}).get("what") or ())
    return [w for w in ("support", "memory") if w in parts and w not in done]


def mark_written(current: str | Path, fam: str, what) -> None:
    """写编成任务写完、回读核对通过后调：当前方案和 plans/<模式>/<偶像卡>.json 都记上 written[模式]。"""
    current = Path(current)
    plan = load(current)
    if not plan:
        return
    w = dict(plan.get("written") or {})
    prev = set((w.get(fam) or {}).get("what") or ())  # 分次写（先支援卡、后回忆）：合并写过的部分
    w[fam] = {"what": sorted(prev | set(what)), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "support_set": (plan.get("support") or {}).get("set"), "memory_set": (plan.get("memory") or {}).get("set")}  # fmt: skip
    plan["written"] = w
    _write_json(current, plan)
    cid = (plan.get("idol") or {}).get("idol_card_id")
    if cid and plan.get("version") == PLAN_VERSION:
        key = plan_key_path(current.parent, cid, plan_family(plan) or fam)
        if key.is_file():
            _write_json(key, plan)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="按社区规则选支援卡和回忆，写开局编成方案")
    ap.add_argument("--idol", required=True, help="偶像卡 id 或 skin id")
    ap.add_argument("--support-inventory", default=SUPPORT_INVENTORY)
    ap.add_argument("--memory-inventory", default=MEMORY_INVENTORY)
    ap.add_argument("--support-set", type=int, default=None, help="方案要存进的支援卡编成号（1–20）")
    ap.add_argument("--memory-set", type=int, default=None, help="方案要存进的メモリー编成号（1–20）")
    ap.add_argument("--main-attr", choices=ATTRS, default=None, help="默认主属性（不给按项目已定 / 成长率最高）")
    ap.add_argument("--fixed-main", action="store_true", help="只用 --main-attr，不比较逆练")
    ap.add_argument(
        "--mode",
        default=None,
        help="初 / N.I.A 的 produce id（produce-002 等）：查偶像卡攻略（默认主属性、支援卡 / 回忆推荐对照）",
    )
    ap.add_argument("--out", default=PLAN_PATH)
    ap.add_argument("--report", default=REPORT_PATH)
    args = ap.parse_args(argv)
    plan = build(args.idol, support_inventory=_load(args.support_inventory), memory_inventory=_load(args.memory_inventory),
                 support_set=args.support_set, memory_set=args.memory_set, main_attr=args.main_attr, fixed_main=args.fixed_main, mode=args.mode)  # fmt: skip
    write(plan, args.out, args.report)
    print(format_report(plan))
    print(f"→ {args.out}\n→ {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
