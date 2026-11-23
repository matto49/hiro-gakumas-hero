"""`hiro plan …`：开局编成方案（hiro.brain.formation_plan）。用信息库里最新的支援卡 / 回忆库存，按社区规则选，
方案写到 HIRO_HOME/formation-plan.json；开局任务和写编成任务都读它（HIRO_FORMATION_PLAN）。只算、不碰游戏。"""

from __future__ import annotations

import json

from hiro.cli import paths
from hiro.cli.home import ensure_home
from hiro.cli.registry import Arg, CliError, Risk, command
from hiro.cli.store import open_store

ATTRS = ("Vo", "Da", "Vi")


def summarize(plan: dict) -> dict:
    """方案 → 界面编成卡和 agent 用的摘要。"""
    info, sup, mem = plan.get("idol") or {}, plan.get("support") or {}, plan.get("memory") or {}
    rental = sup.get("rental")
    return {
        "created": plan.get("created"),
        "idol": {
            "id": info.get("idol_card_id"),
            "name": info.get("name"),
            "plan": info.get("plan"),
            "profile": plan.get("profile"),
        },
        "main_attr": plan.get("main_attr"),
        "support": {
            "set": sup.get("set"),
            "error": sup.get("error"),
            "value": sup.get("value"),
            "counts": sup.get("counts"),
            "main_attr_why": sup.get("main_attr_why"),
            "own": [
                {
                    "id": o.get("support_card_id"),
                    "name": o.get("name"),
                    "attr": o.get("attr"),
                    "level": o.get("level"),
                    "value": o.get("value"),
                    "why": (o.get("why") or [])[:4],
                }
                for o in sup.get("own") or []
            ],
            "rental": None
            if not rental and not sup.get("rental_rule")
            else {
                "id": (rental or {}).get("support_card_id"),
                "name": (rental or {}).get("name"),
                "attr": (rental or {}).get("attr") or (sup.get("rental_rule") or {}).get("attr"),
                "value": (rental or {}).get("value"),
                "estimated": bool(sup.get("rental_estimated")),
            },
        },
        "memory": {
            "set": mem.get("set"),
            "error": mem.get("error"),
            "cards": mem.get("cards") or [],
            "chosen": [
                {
                    "id": c.get("id"),
                    "card_id": c.get("card_id"),
                    "name": c.get("name"),
                    "upgrade": c.get("upgrade") or 0,
                    "timing": c.get("timing"),
                    "slot": c.get("slot"),
                    "role": c.get("role"),
                }
                for c in mem.get("chosen") or []
            ],
        },
        "notes": plan.get("notes") or [],
    }


@command(
    "plan formation",
    Risk.READ,
    "按社区规则出开局编成方案：用信息库里最新的支援卡和回忆库存选支援卡 5 张 + 租借 1 张、回忆 4 份，写成方案文件"
    "（开局时按它核对，写编成任务按它写进游戏）。只计算，不碰游戏。",
    args=(
        Arg("--idol", "偶像卡编号（i_card-… 或 i_card-skin-…）", required=True),
        Arg("--support-set", "方案存进游戏里第几号支援卡编成（1–20）", type="int"),
        Arg("--memory-set", "方案存进第几号メモリー编成（1–20）", type="int"),
        Arg("--main-attr", "默认主属性（不给按偶像成长率）", choices=ATTRS),
        Arg("--fixed-main", "只用 --main-attr，不比较逆练", type="bool"),
    ),
)
def _formation(a: dict) -> dict:
    st = open_store()
    sup, mem = st.latest_raw("supports"), st.latest_raw("memories")
    if sup is None and mem is None:
        raise CliError(
            "no_inventory",
            "还没有支援卡和回忆的库存",
            "先扫描支援卡和回忆（hiro run start scan-supports / scan-memories）",
        )
    home = ensure_home()
    paths.apply_to_process(home)  # 主数据路径
    from hiro.brain import formation_plan as fp
    from hiro.state.catalog import CatalogUnavailable

    try:
        plan = fp.build(
            a["idol"],
            support_inventory=sup,
            memory_inventory=mem,
            support_set=a["support_set"],
            memory_set=a["memory_set"],
            main_attr=a["main_attr"],
            fixed_main=a["fixed_main"],
        )
    except CatalogUnavailable:
        raise CliError(
            "no_master_data", "没有游戏主数据，算不了编成", "设置 → 环境 → 重新安装（会下载主数据）"
        ) from None
    except (KeyError, ValueError) as e:
        raise CliError(
            "bad_idol", f"认不出这张偶像卡：{a['idol']}（{e}）", "用 hiro catalog search 查偶像卡编号"
        ) from None
    path = paths.plan_path(home)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return {**summarize(plan), "path": str(path), "report": fp.format_report(plan)}


@command("plan show", Risk.READ, "看现在的开局编成方案（上一次 hiro plan formation 出的）。")
def _show(a: dict) -> dict:
    path = paths.plan_path(ensure_home())
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CliError("no_plan", "还没有编成方案", "先出一份：hiro plan formation --idol <偶像卡编号>") from None
    return {**summarize(plan), "path": str(path)}
