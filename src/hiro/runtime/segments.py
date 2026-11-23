"""初 / N.I.A 新主循环的 trace 起名：同一局的各段 trace 连起来，续跑时 HifCore._seed_produce_from_trace 能找到上一段。

- new：<前缀>-<时间戳>（hajime-20261117-202931）
- resume 没给 --trace：接最近一段同前缀的 trace，起名 <基名>-rN（N 接着往上数）。HifCore._prior_trace_dirs 按「<基名>、<基名>-rN」
  找之前的段，体力 / P 点 / 属性 / step 在续跑首帧读不到时用上一段最后的读数打底（来源 infer，画面一读到就覆盖）。
  原来初 / N.I.A 入口默认按时间戳起名，续跑找不到上一段，决策层 produce_state() 构造不出（协调 11-17，3940a9 查出）。
- 给了 --trace 就用给的。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def trace_name(
    prefix: str, command: str, given: str | None = None, *, root: Path | None = None, now: datetime | None = None
) -> str:
    if given:
        return given
    stamp = f"{prefix}-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    if command != "resume":
        return stamp
    from hiro.contracts.trace import TRACES

    root = Path(root or TRACES)
    pat = re.compile(rf"({re.escape(prefix)}-\d{{8}}-\d{{6}})(?:-r(\d+))?")
    segs = [
        (m.group(1), int(m.group(2) or 0), d)
        for d in (root.iterdir() if root.is_dir() else ())
        if d.is_dir() and (m := pat.fullmatch(d.name))
    ]
    if not segs:
        logger.info("续跑：traces 里没有 %s-* 的上一段，新起 %s（续跑首帧读不到的字段留空）", prefix, stamp)
        return stamp
    base = max(segs, key=lambda s: s[2].stat().st_mtime)[0]
    n = max(k for b, k, _ in segs if b == base) + 1
    name = f"{base}-r{n}"
    logger.info("续跑：接上一段 trace %s，这一段 %s", base, name)
    return name
