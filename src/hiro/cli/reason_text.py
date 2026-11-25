"""任务出错原因说白话（PR #79 CodeRabbit R6）：failed 事件的 reason 是从日志里挑的最后一条「停下：」/ ERROR，
里面有卡片编号（s_card-… / p_card-… / i_card-…）和程序原话（「new 开局要编成号」）。玩家在对话里看到的应该是卡名和能看懂的话。

在 CLI 产出 reason 的地方做（jobs.failure_reason）：这里能查本机资料库和支援卡资料，界面（Go）拿不到。
"""

from __future__ import annotations

import re

_CARD_ID = re.compile(r"(?<![\w-])[spi]_card-[A-Za-z0-9_\-]*[A-Za-z0-9_](?![\w-])")
_CJK = r"[^\x00-\x7f「」]"

_NEW_NEEDS_SETS = "开局没有定用游戏里的哪一套编成：先给方案选好支援卡和回忆的编成号，或开局时指定编成号"

# 程序原话 → 玩家看得懂的话（按顺序试，每条只改它匹配的那一段；匹配不上的原因原样保留）。卡号已经先换成「卡名」。
_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), r)
    for p, r in (
        (r"new 开局要编成号.*", _NEW_NEEDS_SETS),
        (r"没打开支援卡选择列表", "没能打开支援卡选择列表，游戏可能不在编成页面"),
        (r"(点了「[^」]*」)\s*但列表里没显示为选中", r"\1但游戏列表里没显示为选中，可能没点准，回编成页看看"),
        (
            r"支援卡选择列表里没找到\s*([^（]+?)\s*（库存里没有，或认不出）",
            r"支援卡列表里没找到\1：账号里可能没有这张卡，或没认出来，可以重扫支援卡再试",
        ),
        (r"翻了 (\d+) 轮仍没到 (\d+) 号编成", r"翻了 \1 轮还是没翻到第 \2 号编成"),
        (r"编成号读不出来（[^）]*）", "没读出当前是第几号编成"),
        (r"(支援卡|回忆)编成和方案对不上，不开局", r"游戏里的\1编成和编成方案对不上，没有开局"),
        (r"设备出错，停机：.*", "和游戏设备的连接出了问题，任务已停下，看看游戏或模拟器还开着没有"),
        (r"LIVE 页面和画面 (\d+)s 没变化，停机", r"LIVE 画面 \1 秒没变化，任务已停下"),
        (r"判页 / 处理器抛了异常（连续第 (\d+) 次，到 \d+ 次停机）", r"程序处理页面连续出错 \1 次，任务已停下"),
        (r"未接管的页面 / 钩子：.*?。已存帧，停止运行", "遇到程序还不会处理的页面，已存下画面并停下"),
        (r"编成格子不够放方案的卡：.*", "游戏编成里能换的格子不够放下方案里的卡"),
    )
)


def card_name(card_id: str) -> str | None:
    """卡号 → 卡名：支援卡读支援卡资料（先中文、中文为空用日文），其余读资料库（先中文再日文，偶像卡可带 skin）。查不到返回 None。"""
    from hiro.cli import catalog
    from hiro.cli.registry import CliError

    try:
        if card_id.startswith("s_card-"):
            name = ((catalog.load_supports().get("cards") or {}).get(card_id) or {}).get("name") or {}
            return name.get("zh") or name.get("ja") or None
        for lang in ("zh", "ja"):
            row = catalog.lookup([card_id], [], lang)["items"].get(card_id) or {}
            if row.get("name"):
                return row["name"]
    except (CliError, OSError, ValueError):
        return None
    return None


def plain(why: str) -> str:
    """把一条出错原因里的卡号换成卡名、常见程序原话换成白话。"""
    names: dict[str, str | None] = {}

    def sub(m: re.Match[str]) -> str:
        cid = m.group(0)
        if cid not in names:
            names[cid] = card_name(cid)
        return f"「{names[cid]}」" if names[cid] else cid

    out = _CARD_ID.sub(sub, why)
    out = re.sub(rf"({_CJK})\s+(「)", r"\1\2", out)  # 卡号两边原来的空格（「点了 s_card-… 但」）在换成「卡名」后不要了
    out = re.sub(rf"(」)\s+({_CJK})", r"\1\2", out)
    for pat, repl in _PHRASES:
        out = pat.sub(repl, out)
    return out
