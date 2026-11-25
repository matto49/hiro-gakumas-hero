"""`hiro plan …`：开局编成方案（hiro.brain.formation_plan）。用信息库里最新的支援卡 / 回忆库存，按社区规则选，
方案写到 HIRO_HOME/formation-plan.json；开局任务和写编成任务都读它（HIRO_FORMATION_PLAN）。只算、不碰游戏。"""

from __future__ import annotations

import json

from hiro.cli import paths
from hiro.cli.home import ensure_home
from hiro.cli.registry import Arg, CliError, Risk, command
from hiro.cli.store import open_store

ATTRS = ("Vo", "Da", "Vi")


_DECISION_LABEL = {"formation.support": "支援卡", "formation.memory": "回忆"}


def _plain_note(note: str) -> str:
    """方案备注给人看：内部决策键换成「支援卡 / 回忆」，卡号换成卡名（旧方案文件里存的备注也一样换）。"""
    from hiro.cli import reason_text

    for key, label in _DECISION_LABEL.items():
        if note.startswith(key + "："):
            note = label + note[len(key) :]
    return reason_text.plain(note)


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
        "notes": [_plain_note(n) for n in plan.get("notes") or []],
    }


MODES = ("hajime", "nia", "hif")


def memory_inventory(st, style: str | None):
    """出方案用的回忆库存：开了「回忆按片扫描」按流派取片（D16）；缺省（关）照旧用最新的整表扫描快照（#116 审查重要 5）。"""
    from hiro.state import memory_slices

    return st.memories_for_plan(style) if memory_slices.enabled() else st.latest_raw("memories")


def _plan_style(fp, idol: str) -> str | None:
    """偶像卡的流派（sense / logic / anomaly），决定出方案要哪几片回忆（设计 D16）；认不出（主数据缺、编号不对）时 None = 12 片全要。"""
    from hiro.brain.guides import PLAN_OF

    try:
        return PLAN_OF.get(fp.idol_info(idol).get("plan"))
    except Exception:  # noqa: BLE001  认不出交给后面 build 报 bad_idol
        return None


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
        Arg(
            "--mode",
            "给哪个模式出（hajime / nia / hif）：按这个模式查社区攻略，方案按「偶像卡 × 模式」存",
            choices=MODES,
        ),
        Arg("--difficulty", "难度（初 regular / pro / master / legend，N.I.A pro / master），查攻略用；不给按默认难度"),
    ),
)
def _formation(a: dict) -> dict:
    home = ensure_home()
    plan, path = make_plan(home, a["idol"], a.get("mode"), difficulty=a.get("difficulty"), support_set=a["support_set"],
                           memory_set=a["memory_set"], main_attr=a["main_attr"], fixed_main=a["fixed_main"])  # fmt: skip
    from hiro.brain import formation_plan as fp

    return {**summarize(plan), "path": str(path), "report": fp.format_report(plan)}


def make_plan(home, idol: str, fam: str | None, *, difficulty: str | None = None, support_set: int | None = None,
              memory_set: int | None = None, main_attr: str | None = None, fixed_main: bool = False,
              source: str | None = None) -> tuple[dict, object]:  # fmt: skip
    """读信息库里最新的库存 → 出方案 → 存（给了模式按「偶像卡 × 模式」存并设为当前方案，没给只写当前方案）。
    plan formation 和开局前自动出方案（hiro run start）共用。"""
    st = open_store()
    paths.apply_to_process(home)  # 主数据路径
    from hiro.brain import formation_plan as fp
    from hiro.state.catalog import CatalogUnavailable

    sup, mem = st.latest_raw("supports"), memory_inventory(st, _plan_style(fp, idol))
    if sup is None and mem is None:
        raise CliError(
            "no_inventory",
            "还没有支援卡和回忆的库存",
            "先扫描支援卡和回忆（hiro run start scan-supports / scan-memories）",
        )

    try:
        plan = fp.build(
            idol,
            support_inventory=sup,
            memory_inventory=mem,
            support_set=support_set,
            memory_set=memory_set,
            main_attr=main_attr,
            fixed_main=fixed_main,
            mode=fp.produce_of(fam, difficulty) if fam else None,
        )
    except CatalogUnavailable:
        raise CliError(
            "no_master_data", "没有游戏主数据，算不了编成", "设置 → 环境 → 重新安装（会下载主数据）"
        ) from None
    except (KeyError, ValueError) as e:
        raise CliError("bad_idol", f"认不出这张偶像卡：{idol}（{e}）", "用 hiro catalog search 查偶像卡编号") from None
    if source:
        plan = dict(plan, source=source)
    if fam:
        path = fp.save_plan(home, plan, fam)
        plan = fp.load(path) or plan
    else:  # 没给模式：照以前只写当前方案
        path = paths.plan_path(home)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return plan, path


@command(
    "plan show",
    Risk.READ,
    "看开局编成方案：缺省是当前方案（上一次出的 / 上一局用的）；给 --idol 和 --mode 看那张卡在那个模式的方案。",
    args=(
        Arg("--idol", "偶像卡编号"),
        Arg("--mode", "模式（hajime / nia / hif）", choices=MODES),
    ),
)
def _show(a: dict) -> dict:
    from hiro.brain import formation_plan as fp

    home = ensure_home()
    if a.get("idol") and a.get("mode"):
        found = fp.find_plan(home, a["idol"], a["mode"])
        if found is None:
            raise CliError(
                "no_plan",
                f"{a['idol']} 在 {a['mode']} 还没有编成方案",
                f"出一份：hiro plan formation --idol {a['idol']} --mode {a['mode']}",
            )
        plan, path = found
        return {**summarize(plan), "path": str(path)}
    path = paths.plan_path(home)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CliError("no_plan", "还没有编成方案", "先出一份：hiro plan formation --idol <偶像卡编号>") from None
    return {**summarize(plan), "path": str(path)}
