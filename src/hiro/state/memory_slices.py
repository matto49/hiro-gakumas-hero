"""回忆分片（资料库扫描设计 5.1）：一片 = 一个流派（技能卡的プラン）× 一个获得模式（プロデュース），共 12 片。
游戏回忆选择页的「ソート・フィルタ」能按这两组筛选；扫描一次只扫一片，资料库按片记录。"""

from __future__ import annotations

PLANS = ("sense", "logic", "anomaly", "common")
PRODUCES = ("hajime", "nia", "hif")
SLICES = tuple(f"{p}:{m}" for p in PLANS for m in PRODUCES)


def slice_key(plan: str, produce: str) -> str:
    if plan not in PLANS or produce not in PRODUCES:
        raise ValueError(f"没有这一片：{plan} × {produce}")
    return f"{plan}:{produce}"


def slices_for_plan(plan: str | None) -> list[str]:
    """一局要用的片：偶像卡流派的 3 片 + 共通的 3 片；流派不知道（None）时 12 片全要。"""
    if plan is None:
        return list(SLICES)
    return [slice_key(plan, m) for m in PRODUCES] + [slice_key("common", m) for m in PRODUCES]


# 游戏「ソート・フィルタ」弹窗里的复选框文字（按包含找；11-26 实机）
PLAN_LABELS = {"sense": "センス", "logic": "ロジック", "anomaly": "アノマリー", "common": "共通"}
PRODUCE_LABELS = {"hajime": "定期公演", "nia": "AUDITION", "hif": "FESTIVAL"}


SWITCH = "HIRO_MEMORY_SLICES"


def enabled() -> bool:
    """「回忆按片扫描」开关（D10 用户 11-26 18:14 定：缺省开）：环境变量 HIRO_MEMORY_SLICES 设了以它为准；没设看初星助手设置
    （HIRO_HOME/settings.json 的 memory_slices_off，玩家在设置里关了才是 true）。关着时扫描回忆照旧整表滚动扫、出方案照旧用整表快照。"""
    import json
    import os

    v = os.environ.get(SWITCH)
    if v is not None and v.strip():
        return v.strip().lower() in ("1", "on", "true", "yes")
    try:
        from hiro.cli.home import hiro_home

        return not json.loads((hiro_home() / "settings.json").read_text(encoding="utf-8")).get("memory_slices_off")
    except (OSError, ValueError, AttributeError):
        return True  # 没有设置文件（命令行直接用、初星助手还没存过设置）：按缺省开
