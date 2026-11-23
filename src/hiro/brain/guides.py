"""偶像卡攻略（gakumas-bench 发布的攻略包）的查询接口。数字小广是消费方，格式以 gakumas-bench `docs/guides.md`（定稿 v1，schema 1）为准。

给定（模式、难度、偶像卡、决策点、自己的卡池、局面）→ 按定稿第 5 节返回有序条目：
1. 收集 decision 相同、scope 每个已写字段都和当前局面相符的条目（plan / style 由偶像卡从主数据推出）；
2. 去掉 pool.requires 不满足的、unless.when 成立的（when 判不了或只有 text：条目照用，text 带进理由）；
3. 排序：依据级别（guide = decision > ranking > live，取 evidence 里最高的）→ 层（偶像卡 > 流派 > プラン > 难度 > 模式 > 通用）；
4. merge()：按 Item.id 去重，排序在前的条目说了算（同一张卡一条推荐、一条避开时，前面那条的意见生效）。

开关（设计文档 4.9-4）：HIRO_GUIDES=0 整个关掉（查询一律返回空，调用方退回现在的规则）；
Campus 広 的条目要 HIRO_GUIDES_HIRO=1 才用（默认关：广是已验证的夺冠配置，迁移过来的条目必须和手写表一致才打开）。

攻略包：~/.cache/hiro/guides（HIRO_GUIDES_DIR）下的 manifest.json + <mode>-<version>.json，`python -m hiro.brain.guides fetch` 从
https://hiro.matto.top/gkbench/api/guides/ 下载（核对 sha256；schema 比自己认得的新就不用）。没有包时查询返回空。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from functools import cache, lru_cache  # noqa: F401
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA = 1
BASE_URL = "https://hiro.matto.top/gkbench/api/guides/"
HIRO_IDOL = "i_card-shro-3-007"
LEVEL = {"guide": 3, "decision": 3, "ranking": 2, "live": 1}
LAYERS = ("idol_card", "style", "plan", "difficulty", "mode")  # 越靠前越具体
STYLE_OF_EFFECT = {
    "ProduceExamEffectType_ExamReview": "review",
    "ProduceExamEffectType_ExamCardPlayAggressive": "aggressive",
    "ProduceExamEffectType_ExamParameterBuff": "parameter_buff",
    "ProduceExamEffectType_ExamLessonBuff": "lesson_buff",
    "ProduceExamEffectType_ExamConcentration": "concentration",
    "ProduceExamEffectType_ExamFullPower": "full_power",
}
PLAN_OF = {"ProducePlanType_Plan1": "sense", "ProducePlanType_Plan2": "logic", "ProducePlanType_Plan3": "anomaly"}
# 数字小广选行动页的选项名 → 攻略的行动 ID（定稿第 1.1 节）
ACTION_ID = {"class": "class", "outing": "outing", "shop": "consult", "rest": "rest", "allowance": "allowance", "gift": "gift",
             "sales": "business", "special_guidance": "special_guidance"}  # fmt: skip


def enabled() -> bool:
    return os.environ.get("HIRO_GUIDES", "1") != "0"


def hiro_enabled() -> bool:
    return os.environ.get("HIRO_GUIDES_HIRO", "0") == "1"


def norm_idol(idol_card: str | None) -> str | None:
    return idol_card.replace("i_card-skin-", "i_card-") if idol_card else None


@cache
def idol_traits(idol_card: str | None) -> tuple[str | None, str | None]:
    """偶像卡 → (plan, style)。先查 KAA 主数据，没有这张卡（新偶像）再查模拟器主数据；都查不到 (None, None)。"""
    cid = norm_idol(idol_card)
    if not cid:
        return None, None
    row = None
    try:
        from hiro.state.context import catalog

        row = catalog().one("IdolCard", cid)
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        try:
            from gakumas_exam.catalog import Catalog

            cat = Catalog()
            try:
                row = cat.one("IdolCard", cid)
            finally:
                cat.db.close()
        except Exception:  # noqa: BLE001
            row = None
    if not row:
        return None, None
    return PLAN_OF.get(row.get("planType")), STYLE_OF_EFFECT.get(row.get("examEffectType"))


# ---------------------------------------------------------------- 条目
@dataclass
class Entry:
    raw: dict

    @property
    def id(self) -> str:
        return self.raw.get("id") or "?"

    @property
    def decision(self) -> str | None:
        return self.raw.get("decision")

    @property
    def scope(self) -> dict:
        return self.raw.get("scope") or {}

    @property
    def content(self) -> dict:
        return self.raw.get("content") or {}

    @property
    def level(self) -> int:
        return max((LEVEL.get((e or {}).get("kind"), 0) for e in self.raw.get("evidence") or []), default=0)

    @property
    def layer(self) -> int:
        """层的具体程度：偶像卡 5 > 流派 4 > プラン 3 > 难度 2 > 模式 1 > 通用 0（定稿 1.3）。"""
        for i, k in enumerate(LAYERS):
            if self.scope.get(k) is not None:
                return len(LAYERS) - i
        return 0

    def sources(self) -> str:
        """出处简写，拼进理由：guide / decision 写 ref，ranking 写样本数。"""
        out = []
        for e in self.raw.get("evidence") or []:
            if e.get("kind") in ("guide", "decision", "live") and e.get("ref"):
                out.append(str(e["ref"]))
            elif e.get("kind") == "ranking":
                out.append(f"排行榜 n={e.get('n')}")
        return "、".join(dict.fromkeys(out))


@dataclass
class Ctx:
    """unless 谓词要的局面。不知道的字段留 None：用到它的谓词按「判不了」处理。"""

    deck: list[str] | None = None  # 牌组的技能卡 id（基础 id，可重复）
    p_point: int | None = None
    stamina: int | None = None
    week: int | None = None
    pitems: list[str] | None = None
    next_exam: str | None = None


@dataclass
class Pool:
    """自己的卡池：支援卡 id → (凸数, 等级)；回忆 / 其它卡 id 集合。None 表示不知道。"""

    support: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)
    others: set[str] = field(default_factory=set)


def eval_when(cond, ctx: Ctx | None) -> bool | None:
    """结构化谓词 → True / False；判不了（不认识的谓词、局面缺字段）→ None。"""
    if not isinstance(cond, dict) or len(cond) != 1:
        return None
    ((k, v),) = cond.items()
    if k in ("all", "any"):
        vals = [eval_when(c, ctx) for c in v or []]
        if k == "all":
            return False if False in vals else (None if None in vals else True)
        return True if True in vals else (None if None in vals else False)
    if k == "not":
        r = eval_when(v, ctx)
        return None if r is None else not r
    if ctx is None:
        return None
    deck = ctx.deck
    if k == "deck_count":
        if deck is None:
            return None
        n = sum(1 for c in deck if c in set(v.get("ids") or []))
        return (v.get("gte") is None or n >= v["gte"]) and (v.get("lte") is None or n <= v["lte"])
    if k == "deck_has_any":
        return None if deck is None else any(c in deck for c in v or [])
    if k == "deck_has_all":
        return None if deck is None else all(c in deck for c in v or [])
    if k == "deck_size_gte":
        return None if deck is None else len(deck) >= v
    if k == "deck_size_lte":
        return None if deck is None else len(deck) <= v
    if k == "has_pitem":
        return None if ctx.pitems is None else any(p in ctx.pitems for p in v or [])
    if k == "p_point_lt":
        return None if ctx.p_point is None else ctx.p_point < v
    if k == "stamina_lt":
        return None if ctx.stamina is None else ctx.stamina < v
    if k == "week_gte":
        return None if ctx.week is None else ctx.week >= v
    if k == "next_exam":
        return None if ctx.next_exam is None else ctx.next_exam == v
    return None


def pool_ok(requires: list, pool: Pool | None) -> bool | None:
    """pool.requires 都满足 → True；有一项不满足 → False；不知道卡池 → None。"""
    if not requires:
        return True
    if pool is None:
        return None
    for r in requires:
        rid = r.get("id") if isinstance(r, dict) else r
        if rid in pool.support:
            lim, lvl = pool.support[rid]
            if isinstance(r, dict) and r.get("min_limit") is not None and (lim is None or lim < r["min_limit"]):
                return False
            if isinstance(r, dict) and r.get("min_level") is not None and (lvl is None or lvl < r["min_level"]):
                return False
        elif rid not in pool.others:
            return False
    return True


@dataclass
class Hit:
    entry: Entry
    note: str | None = None  # unless 判不了 / 只有文字时带进理由的提醒


def select(entries: list[Entry], *, mode: str | None, difficulty: str | None, idol_card: str | None, decision: str,
           pool: Pool | None = None, ctx: Ctx | None = None, layer: int | None = None) -> list[Hit]:  # fmt: skip
    """定稿第 5 节 1–3 步：过滤 + 排序。卡池不知道时，带 pool.requires 的条目不用（判不了是否满足，宁可不推荐要求卡池的东西）。"""
    plan, style = idol_traits(idol_card)
    want = {
        "mode": mode,
        "difficulty": difficulty,
        "plan": plan,
        "style": style,
        "idol_card": norm_idol(idol_card),
        "layer": layer,
    }
    hits = []
    for e in entries:
        if e.decision != decision:
            continue
        sc = e.scope
        if any(sc.get(k) is not None and sc.get(k) != want[k] for k in want):
            continue
        if pool_ok((sc.get("pool") or {}).get("requires") or [], pool) is not True:
            continue
        un = e.content.get("unless") or {}
        note = None
        if un:
            r = eval_when(un.get("when"), ctx) if un.get("when") is not None else None
            if r is True:
                continue
            note = un.get("text") if r is None else None
        hits.append(Hit(e, note))
    hits.sort(key=lambda h: (-h.entry.level, -h.entry.layer))
    return hits


@dataclass
class Item:
    id: str
    side: str  # prefer / avoid
    fields: dict
    entry: Entry


def merge(hits: list[Hit]) -> list[Item]:
    """定稿第 5 节第 4 步：按 Item.id 去重，排序在前的条目说了算（它把这张卡放 prefer 还是 avoid 就是哪边）。
    返回顺序：先按条目顺序，同一条目里按它自己的顺序（prefer 在前，avoid 在后）。"""
    seen: set[str] = set()
    out: list[Item] = []
    for h in hits:
        for side in ("prefer", "avoid"):
            for it in h.entry.content.get(side) or []:
                d = {"id": it} if isinstance(it, str) else dict(it)
                iid = d.get("id")
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                out.append(Item(iid, side, d, h.entry))
    return out


# ---------------------------------------------------------------- 攻略包
def cache_dir() -> Path:
    return Path(os.environ.get("HIRO_GUIDES_DIR") or Path.home() / ".cache" / "hiro" / "guides")


@cache
def _load_pack(mode: str, root: str) -> tuple[Entry, ...]:
    d = Path(root)
    try:
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        name = (man.get("packs") or {}).get(mode, {}).get("name")
        if not name:
            return ()
        pack = json.loads((d / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if int(pack.get("schema") or 0) > SCHEMA:
        logger.warning("攻略包 %s 的 schema %s 比数字小广认得的 %s 新，不用", name, pack.get("schema"), SCHEMA)
        return ()
    return tuple(Entry(e) for e in pack.get("entries") or [])


def entries(mode: str) -> tuple[Entry, ...]:
    return _load_pack(mode, str(cache_dir()))


def lookup(*, mode: str | None, difficulty: str | None, idol_card: str | None, decision: str, pool: Pool | None = None,
           ctx: Ctx | None = None, extra: list[Entry] | None = None) -> list[Hit]:  # fmt: skip
    """调用方用的入口。HIRO_GUIDES=0、广没开 HIRO_GUIDES_HIRO、没有攻略包时返回 []（调用方退回现在的规则）。
    extra：额外的条目（测试 / 本地调试用），和攻略包一起排序。"""
    if not enabled() or not mode:
        return []
    if norm_idol(idol_card) == HIRO_IDOL and not hiro_enabled():
        return []
    pool_entries = list(entries(mode)) + list(extra or [])
    return select(
        pool_entries, mode=mode, difficulty=difficulty, idol_card=idol_card, decision=decision, pool=pool, ctx=ctx
    )


def fetch(base: str = BASE_URL, modes=("hajime", "nia", "hif"), timeout: float = 30.0) -> dict:
    """下载 manifest 和各模式的发布包到缓存目录（版本没变就不下）。返回 {mode: version 或错误}。
    新版下好后删掉这个模式的旧版文件（prune_old）；新版没下成的模式旧版留着。"""
    import urllib.request

    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(base + "manifest", timeout=timeout) as r:  # noqa: S310  固定的 https 地址
        man_raw = r.read()
    man = json.loads(man_raw)
    if int(man.get("schema") or 0) > SCHEMA:
        return {"error": f"manifest schema {man.get('schema')} > {SCHEMA}"}
    out = {}
    for mode in modes:
        info = (man.get("packs") or {}).get(mode)
        if not info:
            out[mode] = "无"
            continue
        path = d / f"{info['name']}.json"
        if not path.exists():
            with urllib.request.urlopen(base + info["name"], timeout=timeout) as r:  # noqa: S310
                body = r.read()
            if info.get("sha256") and hashlib.sha256(body).hexdigest() != info["sha256"]:
                out[mode] = "sha256 不符，没存"
                continue
            path.write_bytes(body)
        out[mode] = info["name"]
    (d / "manifest.json").write_bytes(man_raw)
    _load_pack.cache_clear()
    removed = prune_old(
        d,
        {
            m: (man.get("packs") or {})[m]["name"]
            for m in modes
            if out.get(m) == ((man.get("packs") or {}).get(m) or {}).get("name")
        },
    )
    if removed:
        out["removed"] = removed
    return out


def prune_old(d: Path, current: dict) -> list[str]:
    """删掉缓存目录里 current 这些模式的旧版攻略包（文件名 <mode>-<版本>.json，不是 current[mode] 的）。返回删掉的文件名。
    只删认得出模式前缀、带数字版本号的 json，别的文件不碰（用户 11-21：攻略包每天更新，旧包会一直攒着）。"""
    import re

    removed = []
    for mode, name in current.items():
        pat = re.compile(rf"^{re.escape(mode)}-\d[\d-]*\.json$")
        for f in d.glob(f"{mode}-*.json"):
            if f.stem != name and pat.match(f.name):
                try:
                    f.unlink()
                    removed.append(f.name)
                except OSError as e:
                    logger.warning("删旧攻略包 %s 失败：%r", f.name, e)
    return sorted(removed)


FRESH_HOURS = 24
FETCH_TIMEOUT = 10.0  # 开局时自动下载：不让网络问题拖住开局


def refresh_if_stale(max_age_h: float = FRESH_HOURS, fetcher=None) -> str:
    """开局时调（初 / N.I.A / run_hif 的 new 和 resume，协调 11-20）：缓存的 manifest 没有或超过 max_age_h 小时就下载一次。
    失败只写日志，用旧缓存（没有就退回现在的规则），不抛异常、不阻塞开局。HIRO_GUIDES=0 或 HIRO_GUIDES_FETCH=0 时不联网。
    返回 off / fresh / fetched / failed。"""
    import time

    if not enabled() or os.environ.get("HIRO_GUIDES_FETCH", "1") == "0":
        return "off"
    man = cache_dir() / "manifest.json"
    try:
        if man.exists() and time.time() - man.stat().st_mtime < max_age_h * 3600:
            return "fresh"
    except OSError:
        pass
    try:
        got = (fetcher or (lambda: fetch(timeout=FETCH_TIMEOUT)))()
        logger.info("偶像卡攻略包已更新：%s", got)
        return "fetched"
    except Exception as e:  # noqa: BLE001  网络 / 服务器问题：用旧缓存或退回规则
        logger.warning("偶像卡攻略包下载失败（%s），%s", e, "用旧缓存" if man.exists() else "没有缓存，按现在的规则")
        return "failed"


def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m hiro.brain.guides")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="下载攻略包到缓存目录")
    f.add_argument("--base", default=BASE_URL)
    s = sub.add_parser("show", help="查一个决策点的条目")
    s.add_argument("--mode", required=True)
    s.add_argument("--difficulty")
    s.add_argument("--idol", required=True)
    s.add_argument("--decision", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        print(json.dumps(fetch(a.base), ensure_ascii=False))
        return 0
    for h in select(list(entries(a.mode)), mode=a.mode, difficulty=a.difficulty, idol_card=a.idol, decision=a.decision):
        print(
            f"[{h.entry.level}/{h.entry.layer}] {h.entry.id}：{h.entry.content.get('why') or ''}（{h.entry.sources()}）"
            + (f"；注意：{h.note}" if h.note else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
