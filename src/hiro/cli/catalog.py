"""`hiro catalog …`：游戏资料库（卡牌、P 道具、饮料、偶像卡、回忆技能）。

数据来自 gakumas-bench 的公开接口（GKMS 主数据生成，中文取学マス汉化插件的翻译），下载到 HIRO_HOME/catalog，
不随仓库发布（AGENTS.md：不往仓库加游戏素材）。小图按需下载、缓存。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from hiro.cli.home import ensure_home
from hiro.cli.registry import Arg, CliError, Risk, command

DEFAULT_URL = "https://hiro.matto.top/gkbench"
LANGS = ("ja", "zh")
KINDS = ("cards", "items", "drinks", "idols", "memories")
SEARCH_LIMIT = 50
_ASSET = re.compile(r"^[A-Za-z0-9_.\-]+$")


def base_url() -> str:
    return os.environ.get("HIRO_CATALOG_URL", DEFAULT_URL).rstrip("/")


def catalog_dir() -> Path:
    return ensure_home() / "catalog"


def _fetch(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "hiro-console"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 固定的 http(s) 地址
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise CliError("network", f"下载失败：{url}（{e}）", "检查网络；国内网络不通时稍后重试") from None


def load(lang: str = "ja") -> dict:
    p = catalog_dir() / f"catalog.{lang}.json"
    if not p.is_file():
        raise CliError("catalog_missing", "资料库还没下载", "运行 hiro catalog sync")
    return json.loads(p.read_text(encoding="utf-8"))


def idol_name(idol: str) -> str:
    """偶像卡编号（可以是 skin id）→ 卡名（给人看的摘要用）；资料库没下载或查不到时原样返回编号。"""
    try:
        row = (load("ja").get("idols") or {}).get(idol.replace("i_card-skin-", "i_card-")) or {}
    except (CliError, OSError, ValueError):
        return idol
    return row.get("name") or idol


def _texts(entry: dict) -> list[str]:
    out = [entry.get("name") or "", entry.get("text") or ""]
    for up in (entry.get("up") or {}).values():
        out += [up.get("name") or "", up.get("text") or ""]
    return out


def _brief(kind: str, eid: str, e: dict) -> dict:
    text = e.get("text")
    if text is None and e.get("up"):
        text = next(iter(e["up"].values())).get("text")
    return {"kind": kind, "id": eid, "name": e.get("name"), "rarity": e.get("rarity"), "plan": e.get("plan"),
            "text": text, "asset": e.get("asset")}


def search(q: str, kind: str | None = None, lang: str = "ja") -> list[dict]:
    cat = load(lang)
    ql = q.lower()
    hits = []
    for k in (kind,) if kind else KINDS:
        for eid, e in (cat.get(k) or {}).items():
            if ql in eid.lower() or any(ql in t.lower() for t in _texts(e)):
                hits.append(_brief(k, eid, e))
                if len(hits) >= SEARCH_LIMIT:
                    return hits
    return hits


@command("catalog sync", Risk.READ, "下载 / 更新游戏资料库（日文和中文两份）。第一次用资料库前运行。")
def _sync(a: dict) -> dict:
    d = catalog_dir()
    d.mkdir(parents=True, exist_ok=True)
    counts = {}
    for lang in LANGS:
        url = f"{base_url()}/api/catalog" + ("?lang=zh" if lang == "zh" else "")
        raw = _fetch(url)
        try:
            cat = json.loads(raw)
        except ValueError:
            raise CliError("bad_catalog", f"资料库数据不是 JSON：{url}", "稍后重试；一直这样请反馈") from None
        tmp = d / f"catalog.{lang}.json.tmp"
        tmp.write_bytes(raw)
        tmp.replace(d / f"catalog.{lang}.json")
        if lang == "ja":
            counts = {k: len(cat.get(k) or {}) for k in KINDS}
    return {"langs": list(LANGS), "counts": counts, "dir": str(d)}


@command("catalog search", Risk.READ, "按名字或效果文字搜资料库（最多 50 条）。",
         args=(Arg("query", "要搜的词，例如 エウレカ 或 好調"),
               Arg("--kind", "只搜这一类", choices=KINDS),
               Arg("--lang", "名字和效果文字用哪种语言", choices=LANGS, default="ja")))
def _search(a: dict) -> dict:
    items = search(a["query"], a["kind"], a["lang"])
    return {"query": a["query"], "items": items, "truncated": len(items) >= SEARCH_LIMIT}


@command("catalog get", Risk.READ, "按编号取资料库里的一条完整记录。",
         args=(Arg("id", "编号，例如 p_card-01-ido-3_105"), Arg("--lang", "语言", choices=LANGS, default="ja")))
def _get(a: dict) -> dict:
    cat = load(a["lang"])
    for k in KINDS + ("enchants",):
        e = (cat.get(k) or {}).get(a["id"])
        if e is not None:
            return {"kind": k, "id": a["id"], "entry": e}
    raise CliError("not_found", f"资料库里没有 {a['id']}", "先用 hiro catalog search 查编号")


@command("catalog sprite", Risk.READ, "取一张小图的本机路径（没有就下载）。",
         args=(Arg("asset", "图名，即记录里的 asset 字段"),), agent=False)
def _sprite(a: dict) -> dict:
    asset = a["asset"]
    if not _ASSET.match(asset):
        raise CliError("bad_args", f"图名不合法：{asset}", "用资料库记录里的 asset 字段")
    p = catalog_dir() / "sprites" / f"{asset}.png"
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _fetch(f"{base_url()}/sprites/{asset}.png", timeout=30)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
    return {"asset": asset, "path": str(p)}
