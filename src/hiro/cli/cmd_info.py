"""`hiro info …`：查本机信息库里的账号信息（最新一次扫描）。导入现有扫描工具的输出。"""

from __future__ import annotations

import json
from pathlib import Path

from hiro.cli.registry import Arg, CliError, Risk, command
from hiro.cli.store import KINDS, open_store

SCAN_HINT = {
    "account": "还没读过首页数值：运行 hiro run start scan-account",
    "idols": "还没扫描偶像卡：运行 hiro run start scan-idols",
    "supports": "还没扫描支援卡：运行 hiro run start scan-supports",
    "memories": "还没扫描回忆：运行 hiro run start scan-memories",
    "affection": "还没扫描好感度：运行 hiro run start scan-affection",
}


def _rarity(raw) -> str | None:
    """SupportCardRarity_Ssr / IdolCardRarity_Ssr / ssr → SSR。"""
    if not raw:
        return None
    return str(raw).rsplit("_", 1)[-1].upper()


def normalize_supports(inv: dict) -> list[dict]:
    """tools/support_inventory.py 的输出（cards: {id: {...}}）→ 每张卡一行。"""
    out = []
    for cid, c in (inv.get("cards") or {}).items():
        out.append({
            "id": c.get("support_card_id") or cid,
            "name": c.get("name"),
            "rarity": _rarity(c.get("rarity")),
            "attr": c.get("attr"),
            "type": c.get("type"),
            "level": c.get("level"),
            "breaks": c.get("breaks"),
        })
    return out


def normalize_memories(inv: dict) -> list[dict]:
    """hiro.memory_scan 的输出（entries: [...]）→ 每份回忆一行。"""
    out = []
    for e in inv.get("entries") or []:
        card = e.get("card") or {}
        out.append({
            "card_id": card.get("card_id"),
            "card": card.get("name") or e.get("card_text"),
            "timing": e.get("timing"),
            "timing_text": e.get("timing_text"),
            "stats": e.get("stats"),
            "abilities": [a.get("text") for a in e.get("abilities") or [] if not a.get("disabled")],
            "page": e.get("page"),
            "slot": e.get("slot"),
        })
    return out


NORMALIZE = {"supports": normalize_supports, "memories": normalize_memories}


def import_inventory(kind: str, inv: dict, source: str) -> dict:
    rows = NORMALIZE[kind](inv)
    complete = inv.get("complete")
    if complete is None and kind == "supports":
        complete = inv.get("source") == "list"  # 编成页扫描只含编成里出现过的卡
    sid = open_store().save_snapshot(kind, rows, source=source, scanned_at=inv.get("scanned_at"), complete=complete)
    return {"kind": kind, "snapshot": sid, "count": len(rows)}


def _listing(kind: str, keep=lambda r: True) -> dict:
    meta, rows = open_store().latest(kind)
    if meta is None:
        return {"kind": kind, "scanned_at": None, "complete": None, "items": [], "hint": SCAN_HINT[kind]}
    items = [r for r in rows if keep(r)]
    return {"kind": kind, "scanned_at": meta["scanned_at"], "complete": meta["complete"], "source": meta["source"],
            "total": meta["count"], "items": items}


def _match(value, want) -> bool:
    return want is None or (value is not None and str(value).lower() == str(want).lower())


def _contains(value, want) -> bool:
    return want is None or (value is not None and want.lower() in str(value).lower())


@command("info summary", Risk.READ, "账号概况：首页数值（PLv / AP / ジュエル）和每类信息的数量、上次扫描时间。")
def _summary(args: dict) -> dict:
    st = open_store()
    kinds = {}
    for k in KINDS:
        meta, rows = st.latest(k)
        kinds[k] = {"count": meta["count"] if meta else 0, "scanned_at": meta["scanned_at"] if meta else None,
                    "complete": meta["complete"] if meta else None}
    meta, rows = st.latest("account")
    return {"account": rows[0] if rows else None, "kinds": kinds}


@command("info supports", Risk.READ, "支援卡库存（最新一次扫描）：名字、稀有度、属性、等级、突破。",
         args=(Arg("--rarity", "只看这个稀有度", choices=("SSR", "SR", "R")),
               Arg("--attr", "只看这个属性", choices=("Vo", "Da", "Vi", "Assist")),
               Arg("--name", "名字包含这几个字")))
def _supports(a: dict) -> dict:
    return _listing("supports", lambda r: _match(r.get("rarity"), a["rarity"]) and _match(r.get("attr"), a["attr"])
                    and _contains(r.get("name"), a["name"]))


@command("info memories", Risk.READ, "回忆库存（最新一次扫描）：回忆技能卡、获得时机、参数、能力。",
         args=(Arg("--timing", "获得时机", choices=("start", "mid", "final")),
               Arg("--card", "回忆技能卡名字包含这几个字")))
def _memories(a: dict) -> dict:
    return _listing("memories", lambda r: _match(r.get("timing"), a["timing"]) and _contains(r.get("card"), a["card"]))


@command("info idols", Risk.READ, "偶像卡（最新一次扫描）：卡名、偶像、稀有度、突破、潜力。",
         args=(Arg("--idol", "偶像名字包含这几个字"), Arg("--rarity", "只看这个稀有度", choices=("SSR", "SR", "R"))))
def _idols(a: dict) -> dict:
    return _listing("idols", lambda r: _contains(r.get("idol"), a["idol"]) and _match(r.get("rarity"), a["rarity"]))


@command("info affection", Risk.READ, "每位偶像的好感度（親愛度，最新一次扫描）。")
def _affection(a: dict) -> dict:
    return _listing("affection")


@command("info import", Risk.READ, "把扫描工具的输出 JSON 导入信息库（扫描任务结束时自动调用）。",
         args=(Arg("--kind", "类别", choices=tuple(NORMALIZE), required=True), Arg("file", "扫描输出的 JSON 文件")),
         agent=False)
def _import(a: dict) -> dict:
    p = Path(a["file"])
    try:
        inv = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CliError("bad_file", f"读不了 {p}：{e}", "确认文件存在、是扫描工具写出的 JSON") from None
    return import_inventory(a["kind"], inv, source=f"import:{p.name}")
