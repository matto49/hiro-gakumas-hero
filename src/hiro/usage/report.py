"""用量汇总与花费（设计稿 §2、§3、§4；逻辑参考 ccusage）。

- 读流水（ledger.read_rows，按 id 去重）→ 分组（日 / 月 / 局 / 对话）→ 每组 token 与花费。
- 花费：订阅（claude-cli / codex-cli）自带的 total_cost_usd 是 API 等价价，单列 subscription_usd，不进「花了多少钱」；
  其它行自带费用（cost_source=reported）照用；没有就按 prices.json 算（DeepSeek 分高峰 / 空闲时段，按北京时间判）；
  价格表没有 → 花费为空，计 unpriced；没有用量数据 → 计 no_usage。不做汇率换算，cost 按币种分开。
- 中国法定节假日不单独建模（按工作日算高峰，节假日会略多估）。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from hiro.usage import ledger

PRICES_FILE = Path(__file__).with_name("prices.json")
SUBSCRIPTION = {"claude-cli", "codex-cli"}
TOKEN_KEYS = ("input", "output", "cache_read", "cache_write")


@lru_cache(maxsize=1)
def load_prices() -> dict:
    return json.loads(PRICES_FILE.read_text(encoding="utf-8"))


def _price(prices: dict, provider: str | None, model: str | None) -> dict | None:
    return next((m for m in prices.get("models", []) if m["provider"] == provider and m["model"] == model), None)


def _multiplier(entry: dict, ts: str | None) -> float:
    peak = entry.get("peak")
    if not peak or not ts:
        return 1.0
    try:
        t = datetime.fromisoformat(ts).astimezone(ZoneInfo(peak["tz"]))
    except ValueError:
        return 1.0
    if t.weekday() in peak["weekdays"] and any(a <= t.hour < b for a, b in peak["hours"]):
        return float(peak["multiplier"])
    return 1.0


def has_usage(r: dict) -> bool:
    return any(r.get(k) is not None for k in TOKEN_KEYS)


def tokens(r: dict) -> int:
    return sum(r.get(k) or 0 for k in TOKEN_KEYS)


def row_cost(r: dict, prices: dict) -> tuple[float | None, str | None, str]:
    """一行的 (花费, 币种, 类别)：subscription / reported / priced / unpriced / no_usage。"""
    if r.get("provider") in SUBSCRIPTION:
        return r.get("cost"), ("USD" if r.get("cost") is not None else None), "subscription"
    if r.get("cost") is not None:
        return r["cost"], r.get("currency"), "reported"
    if not has_usage(r):
        return None, None, "no_usage"
    entry = _price(prices, r.get("provider"), r.get("model"))
    if entry is None:
        return None, None, "unpriced"
    rates = entry["rates"]
    cost = sum((r.get(k) or 0) * rates.get(k, 0.0) for k in TOKEN_KEYS) / 1_000_000 * _multiplier(entry, r.get("ts"))
    return cost, entry["currency"], "priced"


def _empty() -> dict:
    return {"calls": 0, **dict.fromkeys(TOKEN_KEYS, 0), "total": 0, "cost": {}, "subscription_usd": 0.0,
            "unpriced": 0, "no_usage": 0, "failed": 0}  # fmt: skip


def _add(g: dict, r: dict, prices: dict) -> None:
    g["calls"] += 1
    for k in TOKEN_KEYS:
        g[k] += r.get(k) or 0
    g["total"] += tokens(r)
    if r.get("ok") is False:
        g["failed"] += 1
    cost, cur, kind = row_cost(r, prices)
    if kind == "subscription":
        g["subscription_usd"] += cost or 0.0
        if not has_usage(r):
            g["no_usage"] += 1
    elif kind in ("unpriced", "no_usage"):
        g[kind] += 1
    elif cost is not None and cur:
        g["cost"][cur] = g["cost"].get(cur, 0.0) + cost


def _day(r: dict) -> str:
    return (r.get("ts") or "")[:10]


KEYS = {"day": _day, "month": lambda r: (r.get("ts") or "")[:7], "job": lambda r: r.get("job"),
        "thread": lambda r: r.get("thread"), None: lambda r: "all"}  # fmt: skip


def summarize(
    rows: list[dict], *, key: str | None = None, breakdown: bool = False, prices: dict | None = None
) -> list[dict]:
    """按 key 分组（day / month / job / thread / None=合成一组），每组 calls、四类 token、total、cost（按币种）、
    subscription_usd、unpriced、no_usage、failed；breakdown 时每组再带 models（按模型）。按 key 排序。"""
    prices = prices or load_prices()
    fn = KEYS[key]
    groups: dict = defaultdict(_empty)
    models: dict = defaultdict(lambda: defaultdict(_empty))
    for r in rows:
        k = fn(r)
        _add(groups[k], r, prices)
        if breakdown:
            _add(models[k][(r.get("provider"), r.get("model"))], r, prices)
    out = []
    for k in sorted(groups, key=lambda x: (x is None, str(x))):
        g = {"key": k, **groups[k]}
        if breakdown:
            g["models"] = [
                {"provider": p, "model": m, **v}
                for (p, m), v in sorted(models[k].items(), key=lambda kv: -kv[1]["total"])
            ]
        out.append(g)
    return out


def job_usage(job_id: str, rows: list[dict], prices: dict | None = None) -> dict:
    """一局的用量：合计 + by_source + by_model + items（每次调用一行：时间、来源、模型、token、花费）。没调用时 calls=0。"""
    prices = prices or load_prices()
    mine = [r for r in rows if r.get("job") == job_id]
    (total,) = summarize(mine, prices=prices) or [{"key": "all", **_empty()}]
    total.pop("key", None)
    items = []
    for r in mine:
        cost, cur, kind = row_cost(r, prices)
        items.append({"ts": r.get("ts"), "source": r.get("source"), "provider": r.get("provider"), "model": r.get("model"),
                      "tokens": tokens(r) if has_usage(r) else None, "cost": cost, "currency": cur, "kind": kind,
                      "ok": r.get("ok")})  # fmt: skip
    return {
        "job": job_id,
        **total,
        "by_source": {g["key"]: {k: v for k, v in g.items() if k != "key"} for g in _group_by(mine, "source", prices)},
        "by_model": summarize(mine, breakdown=True, prices=prices)[0]["models"] if mine else [],
        "items": items,
        "prices_checked": prices.get("checked"),
    }


def _group_by(rows: list[dict], field: str, prices: dict) -> list[dict]:
    groups: dict = defaultdict(_empty)
    for r in rows:
        _add(groups[r.get(field)], r, prices)
    return [{"key": k, **v} for k, v in groups.items()]


def write_job_usage(home: Path, job_id: str) -> dict:
    """任务结束时：这一局的用量写进 HIRO_HOME/jobs/<id>/usage.json（之后不变），返回同一份。"""
    u = job_usage(job_id, ledger.read_rows(Path(home) / "usage"))
    d = Path(home) / "jobs" / job_id
    if d.is_dir():
        (d / "usage.json").write_text(json.dumps(u, ensure_ascii=False, indent=1), encoding="utf-8")
    return u
