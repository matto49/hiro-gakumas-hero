"""开局用的编成号和开局编成方案（新主循环 hiro.runtime 开局时读）。

原名 hiro.kaa_env：原来这里还给 KAA 主循环建 profile / 培育方案、设备后端（init_kaa 等），2026-11-24 随 KAA 主循环一起删了，
剩下的编成配置 2026-11-25 改成这个名字（去 KAA 第 2 批）。环境变量名不变。

环境变量：
- HIRO_SUPPORT_SET（new 必填）：支援卡编成号（1–20）。写 auto = 「おまかせ」（会确认列表第一张租借卡），只有明确想这样时才写。
- HIRO_MEMORY_SET（new 必填）：回忆（メモリー）编成号。写 auto = おまかせ（会改写当前编成位，可能带进和本局方向冲突的牌）。
- HIRO_MEMORY_CARDS：回忆编成带进培育的技能卡 ID，逗号分隔（如 p_card-02-men-2_004,p_card-02-act-1_037）。
  决策器把它们算作已有（三选一里不必再找），续跑记账也从它们起算；不设 = 空。
- HIRO_FORMATION_PLAN：开局编成方案文件（默认 conf/formation-plan.json，由 python -m hiro.brain.formation_plan 生成；off = 不读）。
  方案存在、且方案的偶像和这局一致时，上面三个变量没设的部分用方案里的值（支援卡 / 回忆编成号、回忆带的卡），
  主属性默认用方案的；开局第 2 / 3 步按方案核对编成，对不上就停（见 docs/formation-plan.md）。环境变量写了的以环境变量为准。
resume 不读编成号（培育已经开始）。
设备（HIRO_DEVICE / HIRO_ADB 等）见 hiro.devices。
"""

from __future__ import annotations

import logging
import os

AUTO = "auto"


def _set_env(name: str) -> int | str | None:
    """编成号：整数、"auto"（おまかせ）或 None（没设）。"""
    v = (os.environ.get(name) or "").strip()
    if not v:
        return None
    if v.lower() == AUTO:
        return AUTO
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"{name} 应为编成号（整数）或 auto，收到 {v!r}") from None


SUPPORT_SET = _set_env("HIRO_SUPPORT_SET")
MEMORY_SET = _set_env("HIRO_MEMORY_SET")
MEMORY_CARDS = tuple(c.strip() for c in (os.environ.get("HIRO_MEMORY_CARDS") or "").split(",") if c.strip())


logger = logging.getLogger(__name__)
ACTIVE_PLAN: dict | None = None  # 采用的开局编成方案（开局第 2 / 3 步按它核对）
PLAN_MAIN_ATTR: str | None = None


def apply_formation_plan(idol: str | None, path: str | None = None) -> dict | None:
    """读开局编成方案；环境变量没设的编成号 / 回忆卡用方案的。偶像对不上时不用（写日志）。返回采用的方案或 None。"""
    global SUPPORT_SET, MEMORY_SET, MEMORY_CARDS, ACTIVE_PLAN, PLAN_MAIN_ATTR
    from hiro.brain.formation_plan import PLAN_PATH, load

    env = os.environ.get("HIRO_FORMATION_PLAN")
    ACTIVE_PLAN, PLAN_MAIN_ATTR = None, None
    if env == "off":
        return None
    plan = load(path or env or PLAN_PATH)
    if not plan:
        return None
    want = (plan.get("idol") or {}).get("idol_card_id")
    if idol and want and idol.replace("i_card-skin-", "i_card-") != want:
        logger.warning("开局编成方案是给 %s 的，这局是 %s：不用方案", want, idol)
        return None
    sup, mem = plan.get("support") or {}, plan.get("memory") or {}
    if SUPPORT_SET is None and sup.get("set") and sup.get("own"):
        SUPPORT_SET = int(sup["set"])
    if MEMORY_SET is None and mem.get("set") and mem.get("cards"):
        MEMORY_SET = int(mem["set"])
    if not MEMORY_CARDS and mem.get("cards"):
        MEMORY_CARDS = tuple(mem["cards"])
    ACTIVE_PLAN, PLAN_MAIN_ATTR = plan, plan.get("main_attr")
    logger.info("开局编成方案（%s）：支援卡编成 %s、回忆编成 %s、主属性 %s、回忆带的卡 %s", plan.get("created"), SUPPORT_SET, MEMORY_SET,
                PLAN_MAIN_ATTR, "/".join(MEMORY_CARDS))  # fmt: skip
    return plan
