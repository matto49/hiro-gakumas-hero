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


def prior_trace_dirs(cur: Path) -> list[Path]:
    """同一局之前的 trace 段：<基名>、<基名>-rN（不含 cur），再加上 cur 里已有的步骤（--trace 同名续写，D43 第三十三局本战）。
    按续打编号的数值排（基名段 = 0；名字字典序会把 -r10 排在 -r9 前，局面打底取到旧段，#128 review），cur 排最后。"""
    cur = Path(cur)
    base = re.sub(r"-r\d+$", "", cur.name)
    dirs = sorted(
        (d for d in cur.parent.glob(base + "*") if d != cur and re.fullmatch(re.escape(base) + r"(-r\d+)?", d.name)),
        key=lambda d: int(m.group(1)) if (m := re.search(r"-r(\d+)$", d.name)) else 0,
    )
    steps = cur / "steps.jsonl"
    if steps.exists() and steps.stat().st_size > 0:
        dirs.append(cur)
    return dirs


def last_produce_mode(cur: Path) -> str | None:
    """之前 trace 段里最后记下的 produce.mode（produce-00N）；没有 None。续跑没给难度时用它接回原来的难度：
    原来缺省按 REGULAR / PRO 覆盖，初 LEGEND / MASTER 续跑后课程档位、考试回合数、属性上限都按低难度算（11-28）。"""
    import json

    mode = None
    for d in prior_trace_dirs(cur):
        try:
            with (d / "steps.jsonl").open(encoding="utf-8") as f:
                for line in f:
                    try:
                        step = json.loads(line)
                    except ValueError:
                        continue
                    st = step.get("state_after") or step.get("state") or {}
                    m = (st.get("produce") or {}).get("mode")
                    if m:
                        mode = m
        except OSError:
            continue
    return mode


def resume_difficulty(cur: Path, modes: dict[str, str], fallback: str) -> str:
    """续跑的难度：上一段 trace 的 produce.mode 在 modes（难度 → produce id）里就用它对应的难度；读不到按 fallback 并记警告。"""
    mode = last_produce_mode(cur)
    for difficulty, pid in modes.items():
        if pid == mode:
            logger.info("续跑：按上一段 trace 的 %s 接着打 %s", mode, difficulty)
            return difficulty
    logger.warning(
        "续跑：上一段 trace 里没读到这个模式的难度（%s），按 %s 处理；不对的话用 --difficulty 指定", mode, fallback
    )
    return fallback
