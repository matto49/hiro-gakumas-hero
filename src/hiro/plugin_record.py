"""本进程（一段 trace）里插件的使用记录（2026-11-25 插件架构 spec 4.6）：每个决策点用了谁、定了几步、交回内置几步、出错几步，
加载警告，以及有没有收过画面上看不到的信息（uses_hidden）。写到 trace 目录的 plugins.json（Session 每记一步时有变化就写，进程退出时再写一次），每局报告读它。"""

from __future__ import annotations

import atexit
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
FILE = "plugins.json"


class Record:
    def __init__(self) -> None:
        self.trace_dir: Path | None = None
        self._closers: list[Callable[[], None]] = []
        self._atexit = False
        self.reset()

    def bind(self, trace_dir: Path | str) -> None:
        """这一段 trace 的目录（Session 创建时调）。换了目录就从零记。"""
        d = Path(trace_dir)
        if self.trace_dir == d:
            return
        if self.trace_dir is not None:
            self.reset()
        self.trace_dir = d
        self._load_previous(d / FILE)  # 续跑同一个 trace 目录：接着上一段的计数和 uses_hidden 累计，不整份覆盖
        self.dirty = True

    def _load_previous(self, path: Path) -> None:
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(old, dict):
            return
        for k, v in (old.get("points") or {}).items():
            if isinstance(v, dict):
                p = self.points[k]
                for n in ("decided", "deferred", "errors"):
                    p[n] += int(v.get(n) or 0)
                p["plugin"] = v.get("plugin") or p["plugin"]
                p["last_error"] = v.get("last_error") or p["last_error"]
        for w in old.get("warnings") or []:
            if w not in self.warnings:
                self.warnings.append(w)
        for lab, names in (old.get("hidden_fields") or {}).items():
            self.hidden(list(names or []), lab)
        if old.get("uses_hidden") and not self.uses_hidden:
            self.hidden_fields.setdefault("(上一段)", ["?"])  # 上一段只留了标记、没留字段名

    def on_exit(self, fn: Callable[[], None]) -> None:
        """进程结束时要调的收尾（育成插件 end、数据来源 stop）。"""
        self._closers.append(fn)
        if not self._atexit:
            self._atexit = True
            atexit.register(self.at_exit)

    def at_exit(self) -> None:
        for fn in self._closers:
            try:
                fn()
            except Exception as e:  # noqa: BLE001  插件收尾出错不影响退出
                logger.warning("插件收尾出错：%s: %s", type(e).__name__, e)
        self._closers.clear()
        self.flush()

    def flush(self) -> None:
        """写 trace 目录的 plugins.json。没配插件、也没记到任何东西时不写（老行为不多出文件）。"""
        self.dirty = False
        if self.trace_dir is None:
            return
        from hiro import strategy

        cfg = strategy.current()
        snap = self.snapshot()
        mapping = cfg.mapping()
        active = (
            bool(mapping.get("sources"))
            or len(mapping) > 1
            or snap["points"]
            or snap["warnings"]
            or snap["uses_hidden"]
        )
        if not active:
            return
        snap["mapping"] = mapping
        snap["config_warnings"] = list(cfg.warnings)
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            (self.trace_dir / FILE).write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            logger.warning("写 %s 失败：%s", FILE, e)

    def reset(self) -> None:
        self.dirty = False
        self.points: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"plugin": None, "decided": 0, "deferred": 0, "errors": 0, "last_error": None}
        )
        self.warnings: list[str] = []
        self.hidden_fields: dict[str, list[str]] = {}  # 插件标签 → 收到过的隐藏字段名

    def _point(self, point: str, label: str) -> dict[str, Any]:
        p = self.points[point]
        p["plugin"] = label
        return p

    def decided(self, point: str, label: str) -> None:
        self._point(point, label)["decided"] += 1
        self.dirty = True

    def deferred(self, point: str, label: str) -> None:
        self._point(point, label)["deferred"] += 1
        self.dirty = True

    def error(self, point: str, label: str, why: str) -> None:
        p = self._point(point, label)
        p["errors"] += 1
        p["last_error"] = why
        self.dirty = True

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)
            self.dirty = True

    def hidden(self, names: list[str] | set[str], label: str) -> None:
        if not names:
            return
        got = self.hidden_fields.setdefault(label, [])
        for n in sorted(names):
            if n not in got:
                got.append(n)
                self.dirty = True

    @property
    def uses_hidden(self) -> bool:
        return any(self.hidden_fields.values())

    def snapshot(self) -> dict[str, Any]:
        return {
            "points": {k: dict(v) for k, v in sorted(self.points.items())},
            "warnings": list(self.warnings),
            "uses_hidden": self.uses_hidden,
            "hidden_fields": {k: list(v) for k, v in self.hidden_fields.items()},
        }


RECORD = Record()
