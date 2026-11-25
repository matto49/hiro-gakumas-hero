"""数据来源插件（observation_source）接进观测（2026-11-25 插件架构 spec 4.3、4.8 第 3、4、8 条）。

Session.observe 处理完一张真截图（画面那份 Observation 已经 reduce）之后调 read_sources：按 strategy.json 的 sources 顺序问
声明覆盖这一页的插件，把它们读到的东西整理成 source="plugin" 的 Observation，由 Session 再走一遍 _reduce_checked。

- 时间：SourceRead.t 是跑 hiro 那台机器的本机时间；和截图时间差超过 TOLERANCE_S（不管早晚）就不用。
- 字段：画面上看得到的字段必须登记在 FIELD_CATALOG[页面]，否则丢掉并记一条说明；看不到的只能放 hidden。
- 档位：插件声明的 trust 高于 screen 时以插件为准；不高于时，画面这一帧已经读到的字段保留，插件只补没读到的。
  同一帧两边值不同时记一条冲突说明。多个来源同档位时，列表前面的优先。
- 出错、超时只记说明和计数，不影响画面读数。
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from hiro.contracts import FIELD_CATALOG, FieldMeta, Observation
from hiro.contracts.game_state import PRIORITY

logger = logging.getLogger(__name__)

TOLERANCE_S = 0.5
_started: set[str] = set()
_warned: set[str] = set()


def reset() -> None:
    """测试用。"""
    _started.clear()
    _warned.clear()


def fresh(t: float, frame_t: float, tol: float = TOLERANCE_S) -> bool:
    return abs(float(t) - float(frame_t)) <= tol


def _meta(raw: Any, key: str, lab: str) -> FieldMeta:
    m = (raw or {}).get(key)
    if isinstance(m, FieldMeta):
        return m
    if isinstance(m, dict):
        try:
            return FieldMeta(**m)
        except TypeError:
            pass
    return FieldMeta(method="plugin", note=lab)


def read_sources(
    page: str, frame_t: float, state: Any, screen_obs: Observation, seq: int
) -> tuple[list[Observation], list[str]]:
    """问数据来源插件，返回 (要并入状态的观测, 给 trace 的说明)。没配数据来源时什么都不做。"""
    from hiro import plugins, strategy
    from hiro.brain.plugin_gate import PluginTimeout, call_with_timeout
    from hiro.plugin_record import RECORD

    cfg = strategy.current()
    if not cfg.sources:
        return [], []
    allowed = set(FIELD_CATALOG.get(page, ()))
    screen_has = {k for k, v in screen_obs.fields.items() if v is not None}
    taken: dict[str, int] = {}  # 这一帧已经由前面的来源给了的字段 → 档位
    out: list[Observation] = []
    notes: list[str] = []
    for sid in cfg.sources:
        src = plugins.get_by_id("observation_source", sid)
        if src is None:
            if sid not in _warned:
                _warned.add(sid)
                text = f"strategy.json 配了数据来源 {sid!r}，但它没装或不能用，只用画面读数"
                logger.warning(text)
                RECORD.warn(text)
            continue
        pages = getattr(src, "pages", None)
        if pages is not None and page not in pages:
            continue
        lab = plugins.label(src)
        point = f"source.{sid}"
        if sid not in _started:
            _started.add(sid)
            RECORD.on_exit(src.stop)
            try:
                src.start({"page": page})
            except Exception as e:  # noqa: BLE001
                why = f"数据来源 {lab} start 出错：{type(e).__name__}: {e}"
                RECORD.error(point, lab, why)
                notes.append(why)
                continue
        try:
            view = copy.deepcopy(state)  # 插件拿副本，超时后也碰不到主循环的状态
            r = call_with_timeout(
                lambda src=src, view=view: src.read(page, frame_t, view),
                cfg.source_timeout_for(sid),
                key=f"source:{sid}",
            )
        except PluginTimeout as e:
            why = f"数据来源 {lab} {e}，这帧只用画面读数"
            RECORD.error(point, lab, why)
            notes.append(why)
            continue
        except Exception as e:  # noqa: BLE001
            why = f"数据来源 {lab} 出错（{type(e).__name__}: {e}），这帧只用画面读数"
            RECORD.error(point, lab, why)
            notes.append(why)
            continue
        if r is None:
            RECORD.deferred(point, lab)
            continue
        if not fresh(r.t, frame_t):
            RECORD.deferred(point, lab)
            notes.append(f"数据来源 {lab} 的数据和截图差 {float(r.t) - float(frame_t):+.2f} 秒，不用")
            continue
        trust = getattr(src, "trust", "screen")
        trust = trust if trust in PRIORITY else "screen"
        rank = PRIORITY[trust]
        fields: dict[str, Any] = {}
        dropped = []
        for k, v in (r.fields or {}).items():
            if v is None:
                continue
            if k not in allowed:
                dropped.append(k)
                continue
            if k in taken and taken[k] >= rank:
                continue  # 前面的来源同档位或更高，已经给了
            sv = screen_obs.fields.get(k)
            if k in screen_has and sv != v:
                winner = lab if rank > PRIORITY["screen"] else "画面"
                notes.append(f"数据来源冲突：{page}.{k} 画面={sv!r} vs {lab}={v!r}，采用{winner}")
            if k in screen_has and rank <= PRIORITY["screen"]:
                continue  # 同档位：画面已读到的保留
            fields[k] = copy.deepcopy(v)
        if dropped:
            notes.append(f"数据来源 {lab} 给了 {page} 没登记的字段 {sorted(dropped)}，丢掉（看不到的信息请放 hidden）")
        hidden = dict(r.hidden or {})
        if hidden:
            RECORD.hidden(set(hidden), lab)
        if not fields and not hidden:
            RECORD.deferred(point, lab)
            continue
        for k in fields:
            taken[k] = max(taken.get(k, 0), rank)
        RECORD.decided(point, lab)
        out.append(
            Observation(
                seq=seq,
                t=float(r.t),
                page=copy.deepcopy(screen_obs.page),
                fields=fields,
                field_meta={k: _meta(r.meta, k, lab) for k in fields},
                source="plugin",
                by=lab,
                trust=trust,
                hidden=hidden,
            )
        )
    return out, notes
