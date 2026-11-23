"""`hiro catalog …`：游戏资料库（卡牌、P 道具、饮料、偶像卡、回忆技能）。

数据来自初星攻略站的公开接口（GKMS 主数据生成，中文取学マス汉化插件的翻译），下载到 HIRO_HOME/catalog，
不随仓库发布（AGENTS.md：不往仓库加游戏素材）：

    /api/catalog、/api/catalog?lang=zh   技能卡、饮料、P 道具、回忆、偶像卡（catalog.ja.json / catalog.zh.json）
    /api/supports                       支援卡（中日文名、类型、SP 发生率、各凸等级上限、能力、事件；supports.json）
    /sprites/<图名>.png 或 .webp         小图，按需下载、缓存（支援卡卡面、偶像卡是 webp，其余是 png）
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

DEFAULT_URL = "https://gakumas.wiki"  # 初星攻略站（旧地址 hiro.matto.top/gkbench 会 301 过来）
LANGS = ("ja", "zh")
KINDS = ("cards", "items", "drinks", "idols", "memories")
SEARCH_LIMIT = 50
SUPPORTS_FORMAT = "gkbench.supports.v1"
SPRITE_EXTS = (".png", ".webp")
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


def _write_atomic(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)


def load_supports() -> dict:
    """本机的支援卡资料（catalog sync 下载的 supports.json）。"""
    p = catalog_dir() / "supports.json"
    if not p.is_file():
        raise CliError("catalog_missing", "支援卡资料还没下载", "运行 hiro catalog sync")
    try:
        o = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        o = None
    if not isinstance(o, dict) or o.get("format") != SUPPORTS_FORMAT:
        raise CliError("bad_catalog", "支援卡资料文件坏了", "重新运行 hiro catalog sync")
    return o


def lookup(ids: list[str], texts: list[str], lang: str = "zh") -> dict:
    """按编号取记录的类别、名字、效果文字、稀有度和图名（查不到的不列）；偶像卡编号可以是 skin id。
    texts 是回忆能力的日文原文，按资料库里同一条回忆能力换成 lang 的文字（对不上的不列：多半是扫描时 OCR 读错了字）。"""
    cat = load(lang)
    trans = {}
    want = [t.strip() for t in texts if t.strip()]
    if want:
        mja, mto = load("ja").get("memories") or {}, cat.get("memories") or {}
        by_text: dict[str, str] = {}
        for mid, e in mja.items():
            t, to = (e or {}).get("text") or "", ((mto.get(mid) or {}).get("text")) or ""
            if t and to and t not in by_text:
                by_text[t] = to
        trans = {t: by_text[t] for t in want if t in by_text}
    items: dict[str, dict] = {}
    for raw in ids:
        eid = raw.strip()
        if not eid or eid in items:
            continue
        key = eid.replace("i_card-skin-", "i_card-", 1)
        for k in KINDS:
            e = (cat.get(k) or {}).get(key)
            if e is None:
                continue
            row = _brief(k, key, e)
            if k == "idols" and not row.get("asset"):
                row["asset"] = f"idol-{key}"  # 攻略站的偶像卡缩略图
            items[eid] = row
            break
    return {"lang": lang, "items": items, "texts": trans}


def idol_name(idol: str) -> str:
    """偶像卡编号（可以是 skin id）→ 卡名（给人看的摘要用，界面默认中文）：先中文、再日文；资料库没下载或查不到时原样返回编号。"""
    key = idol.replace("i_card-skin-", "i_card-")
    for lang in ("zh", "ja"):
        try:
            row = (load(lang).get("idols") or {}).get(key) or {}
        except (CliError, OSError, ValueError):
            continue
        if row.get("name"):
            return row["name"]
    return idol


def _texts(entry: dict) -> list[str]:
    out = [entry.get("name") or "", entry.get("text") or ""]
    for up in (entry.get("up") or {}).values():
        out += [up.get("name") or "", up.get("text") or ""]
    return out


def _brief(kind: str, eid: str, e: dict) -> dict:
    text = e.get("text")
    if text is None and e.get("up"):
        text = next(iter(e["up"].values())).get("text")
    return {
        "kind": kind,
        "id": eid,
        "name": e.get("name"),
        "rarity": e.get("rarity"),
        "plan": e.get("plan"),
        "text": text,
        "asset": e.get("asset"),
    }


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
        _write_atomic(d / f"catalog.{lang}.json", raw)
        if lang == "ja":
            counts = {k: len(cat.get(k) or {}) for k in KINDS}
    url = f"{base_url()}/api/supports"
    raw = _fetch(url)
    try:
        sup = json.loads(raw)
    except ValueError:
        sup = None
    if not isinstance(sup, dict) or sup.get("format") != SUPPORTS_FORMAT:
        raise CliError("bad_catalog", f"支援卡资料格式不对：{url}", "稍后重试；一直这样请反馈")
    _write_atomic(d / "supports.json", raw)
    counts["supports"] = len(sup.get("cards") or {})
    return {"langs": list(LANGS), "counts": counts, "dir": str(d)}


@command(
    "catalog supports",
    Risk.READ,
    "本机资料库里的支援卡资料（中日文名、SP 发生率、能力、事件），界面的支援卡页用。",
    agent=False,
)
def _supports(a: dict) -> dict:
    return load_supports()


@command(
    "catalog lookup",
    Risk.READ,
    "按编号一次取多条记录的名字、效果文字和图名（界面给信息页配中文名和图标用）。",
    args=(
        Arg("--ids", "编号，逗号分隔"),
        Arg("--texts", "回忆能力的日文原文，一行一条：给出 --lang 那种语言的同一条（对不上的不列）"),
        Arg("--lang", "语言", choices=LANGS, default="zh"),
    ),
    agent=False,
)
def _lookup(a: dict) -> dict:
    return lookup(str(a["ids"] or "").split(","), str(a["texts"] or "").split("\n"), a["lang"])


@command(
    "catalog search",
    Risk.READ,
    "按名字或效果文字搜资料库（最多 50 条）。",
    args=(
        Arg("query", "要搜的词，例如 エウレカ 或 好調"),
        Arg("--kind", "只搜这一类", choices=KINDS),
        Arg("--lang", "名字和效果文字用哪种语言", choices=LANGS, default="ja"),
    ),
)
def _search(a: dict) -> dict:
    items = search(a["query"], a["kind"], a["lang"])
    return {"query": a["query"], "items": items, "truncated": len(items) >= SEARCH_LIMIT}


@command(
    "catalog get",
    Risk.READ,
    "按编号取资料库里的一条完整记录。",
    args=(Arg("id", "编号，例如 p_card-01-ido-3_105"), Arg("--lang", "语言", choices=LANGS, default="ja")),
)
def _get(a: dict) -> dict:
    cat = load(a["lang"])
    for k in KINDS + ("enchants",):
        e = (cat.get(k) or {}).get(a["id"])
        if e is not None:
            return {"kind": k, "id": a["id"], "entry": e}
    raise CliError("not_found", f"资料库里没有 {a['id']}", "先用 hiro catalog search 查编号")


@command(
    "catalog sprite",
    Risk.READ,
    "取一张小图的本机路径（没有就下载）。",
    args=(Arg("asset", "图名，即记录里的 asset 字段"),),
    agent=False,
)
def _sprite(a: dict) -> dict:
    asset = a["asset"]
    if not _ASSET.match(asset):
        raise CliError("bad_args", f"图名不合法：{asset}", "用资料库记录里的 asset 字段")
    d = catalog_dir() / "sprites"
    for ext in SPRITE_EXTS:
        if (d / f"{asset}{ext}").is_file():
            return {"asset": asset, "path": str(d / f"{asset}{ext}")}
    last = None
    for ext in SPRITE_EXTS:
        try:
            data = _fetch(f"{base_url()}/sprites/{asset}{ext}", timeout=30)
        except CliError as e:
            last = e
            continue
        _write_atomic(d / f"{asset}{ext}", data)
        return {"asset": asset, "path": str(d / f"{asset}{ext}")}
    assert last is not None  # SPRITE_EXTS 不为空，走到这里一定是每种扩展名都失败了
    raise last
