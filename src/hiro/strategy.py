"""选用哪些插件：strategy.json（2026-11-25 插件架构 spec 4.2）。

文件由 HIRO_STRATEGY_FILE 指定（初星助手开局时把 HIRO_HOME/strategy.json 和本局参数合成一份放进任务目录）；没设 = 全部用内置，
和引入插件前完全一样。和 HIRO_STRATEGY（KAA 培育流程类）无关。

    {
      "version": 1,
      "sources": ["gkms_hook"],              数据来源，按顺序问，同档位前面的优先
      "exam": "gnrpa_pro",                   考试出牌；"exam.hif" / "exam.hajime" / "exam.nia" / "exam.tower" 按模式覆盖
      "produce": {"ACTION_SELECT": "uinet"},  育成：页面名 → 插件 id；没写的页面用内置；写 "builtin" 也是内置
      "timeouts": {"uinet": 60}               单步超时（秒）
    }

写错的键、不认识的页面只记进 warnings，不报错（开局照常，对应决策点用内置）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiro.plugins import BUILTIN

ENV = "HIRO_STRATEGY_FILE"
DEFAULT_TIMEOUT_S = 60.0
SOURCE_TIMEOUT_S = 1.0  # 数据来源每帧同步问，超过这个就不等（数据本来也只认 0.5 秒内的）
EXAM_MODES = ("hif", "hajime", "nia", "tower")
# 育成决策点 = ProduceDeciders.decide 的分派表（tests/test_strategy.py 核对两边一致）
PRODUCE_PAGES = (
    "ACTION_SELECT",
    "SELECT_CARD",
    "SELECT_DRINK",
    "CONSULT",
    "STUDY",
    "OUTING",
    "HIF_ROUND_INTERVAL",
    "SELECT_PITEM",
    "EVENT_CHOICE",
    "EXAM_CARD_MOVE",
    "PDRINK_MAX",
    "SPECIAL_GUIDANCE",
    "FORMATION",
    "TOWER_FORMATION",
    "TOWER_RETRY",
    "QUALIFY_MEMORY",
    "SKILL_CARD_CHANGE_1",
    "SKILL_CARD_CHANGE_2",
    "SKILL_CARD_REMOVAL",
    "SKILL_CARD_ENHANCE",
    "PDRINK_MAX_CONFIRM",
    "NIA_SALES_PLACE",
    "NIA_SALES_EVENT",
    "NIA_AUDITION_SELECT",
    "EXAM_RETRY",
)
_KEYS = {"version", "sources", "exam", "produce", "timeouts"} | {f"exam.{m}" for m in EXAM_MODES}


def _id(v: Any) -> str | None:
    if not isinstance(v, str) or not v.strip() or v.strip() == BUILTIN:
        return None
    return v.strip()


@dataclass
class Strategy:
    sources: list[str] = field(default_factory=list)
    exam: dict[str, str] = field(default_factory=dict)  # "" = 所有模式；"hif" 等 = 按模式覆盖
    produce: dict[str, str] = field(default_factory=dict)
    timeouts: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # 合成后的原样配置（写进 plugins.json / plugins list）

    def produce_for(self, page: str) -> str | None:
        return self.produce.get(page)

    def exam_for(self, mode: str | None) -> str | None:
        """这个模式的考试出招插件 id；没配或写了 builtin 为 None。按模式写的 builtin 能盖掉全局的 exam。"""
        pid = self.exam[mode] if mode and mode in self.exam else self.exam.get("")
        return None if pid in (None, BUILTIN) else pid

    def timeout_for(self, plugin_id: str) -> float:
        return float(self.timeouts.get(plugin_id, DEFAULT_TIMEOUT_S))

    def source_timeout_for(self, plugin_id: str) -> float:
        return float(self.timeouts.get(plugin_id, SOURCE_TIMEOUT_S))

    def mapping(self) -> dict[str, Any]:
        """生效对应表：决策点 → 插件 id（只列配了插件的）。"""
        out: dict[str, Any] = {"sources": list(self.sources)}
        for k, v in self.exam.items():
            out["exam" + (f".{k}" if k else "")] = v
        for k, v in self.produce.items():
            out[f"produce.{k}"] = v
        return out


def _exam_id(v: Any) -> str | None:
    """exam / exam.<模式> 的值：插件 id，或显式的 builtin（按模式写 builtin 要能盖掉全局）。"""
    if isinstance(v, str) and v.strip() == BUILTIN:
        return BUILTIN
    return _id(v)


def from_dict(d: Any) -> Strategy:
    """配置 → Strategy。任何写错（键、类型）都只记进 warnings，不抛异常（开局照常，对应决策点用内置）。"""
    if d is not None and not isinstance(d, dict):
        return Strategy(warnings=[f"strategy.json 应该是 JSON 对象，收到 {type(d).__name__}，全部用内置"])
    s = Strategy(raw=dict(d or {}))
    if not d:
        return s
    for w in d.get("_warnings", []) if isinstance(d.get("_warnings"), list) else []:
        s.warnings.append(str(w))
    for k in d:
        if k not in _KEYS and k != "_warnings":
            s.warnings.append(f"strategy.json 里不认识的键 {k!r}，忽略")
    src = d.get("sources") if d.get("sources") is not None else []
    if isinstance(src, str):
        src = [x.strip() for x in src.split(",")]
    if not isinstance(src, list):
        s.warnings.append(f"strategy.json 的 sources 应该是 id 列表，收到 {src!r}，不用数据来源")
        src = []
    s.sources = [x for x in (_id(v) for v in src) if x]
    for key, mode in [("exam", "")] + [(f"exam.{m}", m) for m in EXAM_MODES]:
        v = d.get(key)
        if v is None:
            continue
        if (e := _exam_id(v)) is not None:
            s.exam[mode] = e
        else:
            s.warnings.append(f"strategy.json 的 {key} 应该是插件 id，收到 {v!r}，忽略")
    produce = d.get("produce") if d.get("produce") is not None else {}
    if not isinstance(produce, dict):
        s.warnings.append(f"strategy.json 的 produce 应该是「页面 → 插件 id」，收到 {produce!r}，育成全部用内置")
        produce = {}
    for page, pid in produce.items():
        if page not in PRODUCE_PAGES:
            s.warnings.append(f"strategy.json 里不认识的育成页面 {page!r}，忽略（可选：{', '.join(PRODUCE_PAGES)}）")
            continue
        if (p := _id(pid)) is not None:
            s.produce[page] = p
        elif not (isinstance(pid, str) and pid.strip() == BUILTIN):
            s.warnings.append(f"strategy.json 的 produce.{page} 应该是插件 id，收到 {pid!r}，这一页用内置")
    timeouts = d.get("timeouts") if d.get("timeouts") is not None else {}
    if not isinstance(timeouts, dict):
        s.warnings.append(f"strategy.json 的 timeouts 应该是「插件 id → 秒」，收到 {timeouts!r}，用默认超时")
        timeouts = {}
    for pid, sec in timeouts.items():
        try:
            s.timeouts[str(pid)] = float(sec)
        except (TypeError, ValueError):
            s.warnings.append(f"strategy.json 里 {pid} 的超时 {sec!r} 不是数字，按默认 {DEFAULT_TIMEOUT_S:g} 秒")
    return s


def read_file(path: Path | str | None) -> dict[str, Any]:
    """读配置文件；没有 / 读不了返回 {}（读不了的原因放进 _warnings）。"""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"_warnings": [f"读不了 {p}：{e}"]}
    return d if isinstance(d, dict) else {"_warnings": [f"{p} 不是 JSON 对象"]}


def load(path: Path | str | None) -> Strategy:
    return from_dict(read_file(path))


def parse_overrides(plugins: str | None, sources: str | None) -> dict[str, Any]:
    """开局参数 → 覆盖用的配置片段：plugins 写 "produce.ACTION_SELECT=uinet,exam=x,exam.tower=y"，sources 写 "a,b"。"""
    out: dict[str, Any] = {"_warnings": []}
    for part in (plugins or "").split(","):
        part = part.strip()
        if not part:
            continue
        key, eq, val = part.partition("=")
        key, val = key.strip(), val.strip()
        if not eq or not key or not val:
            out["_warnings"].append(f"开局参数 {part!r} 应写成 决策点=插件 id")
            continue
        if key.startswith("produce."):
            out.setdefault("produce", {})[key[len("produce.") :]] = val
        elif key == "exam" or key.startswith("exam."):
            out[key] = val
        else:
            out["_warnings"].append(f"开局参数 {part!r} 的决策点不认识（produce.<页面> / exam / exam.<模式>）")
    if sources is not None and sources.strip():
        out["sources"] = [x.strip() for x in sources.split(",") if x.strip()]
    return out


def merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """开局参数覆盖配置文件：produce 逐页覆盖，其余键整体覆盖；warnings 合在一起。"""
    out = dict(base) if isinstance(base, dict) else {}
    out.setdefault("version", 1)
    for k, v in (overrides or {}).items():
        if k == "produce":
            base_p = out.get("produce")
            out["produce"] = {**(base_p if isinstance(base_p, dict) else {}), **v}
        elif k == "_warnings":
            out["_warnings"] = list(out.get("_warnings") or []) + list(v)
        else:
            out[k] = v
    if not out.get("_warnings"):
        out.pop("_warnings", None)
    return out


_current: Strategy | None = None


def current() -> Strategy:
    """本进程生效的配置（按 HIRO_STRATEGY_FILE 读一次）。"""
    global _current
    if _current is None:
        _current = load(os.environ.get(ENV))
    return _current


def reset() -> None:
    """测试用：下次 current() 重新读。"""
    global _current
    _current = None
