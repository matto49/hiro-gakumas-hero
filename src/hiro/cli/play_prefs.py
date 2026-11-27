"""初星助手设置页「培育」那一节 → 任务的环境变量（11-27 用户：LIVE / 选照片等开关原来只写在启动脚本里，界面上找不到）。

设置页是准的：每一项都按 settings.json 设，没设过的项按程序缺省（下表括号里的），一律盖过启动环境里的同名变量。
设置页上看到的就是任务实际用的（CodeRabbit #197 / #198：原来没设过的项沿用启动环境，页面显示「跳过」、任务却可能停下等人）。

| settings.json | 环境变量 |
|---|---|
| live：play（缺省）/ play_pick / watch / pick / auto | HIRO_LIVE + HIRO_PHOTO_PICK（play_pick = 照常播 LIVE、照片自己选） |
| unread_story：skip（缺省）/ stop | HIRO_UNREAD_STORY |
| retry_max："0"–"3"（缺省 3） | HIRO_RETRY_MAX |
| retry_handoff：true / false | HIRO_RETRY_HANDOFF |
| hif_guides：true / false | HIRO_HIF_GUIDES |
| exam_schedule_probe：true / false | HIRO_EXAM_SCHEDULE_PROBE |
"""

from __future__ import annotations

import json
from pathlib import Path

LIVE = {
    "play": {"HIRO_LIVE": "play", "HIRO_PHOTO_PICK": "auto"},
    "play_pick": {"HIRO_LIVE": "play", "HIRO_PHOTO_PICK": "stop"},
    "watch": {"HIRO_LIVE": "watch"},
    "pick": {"HIRO_LIVE": "pick"},
    "auto": {"HIRO_LIVE": "auto", "HIRO_PHOTO_PICK": "auto"},
}


def _flag(v) -> str:
    return "1" if v is True or str(v).lower() in ("1", "true", "on") else "0"


def env_from_settings(home: Path) -> dict[str, str]:
    """settings.json 里「培育」那几项对应的环境变量；没有 settings.json、某项没设或写了认不出的值时按缺省。"""
    try:
        s = json.loads((Path(home) / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    if not isinstance(s, dict):
        s = {}
    env: dict[str, str] = {}
    env.update(LIVE.get(str(s.get("live") or "play"), LIVE["play"]))
    env["HIRO_UNREAD_STORY"] = u if (u := s.get("unread_story")) in ("skip", "stop") else "skip"
    env["HIRO_RETRY_MAX"] = r if (r := str(s.get("retry_max", ""))) in ("0", "1", "2", "3") else "3"
    for key, var in (
        ("retry_handoff", "HIRO_RETRY_HANDOFF"),
        ("hif_guides", "HIRO_HIF_GUIDES"),
        ("exam_schedule_probe", "HIRO_EXAM_SCHEDULE_PROBE"),
    ):
        env[var] = _flag(s.get(key, False))
    return env
