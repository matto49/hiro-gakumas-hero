"""执行层（hiro.produce / hiro.exec）的等待走 hiro.devices.active.sleep：新主循环下每拍过 Runtime 检查点，录屏暂停能拦住执行中的操作
（2026-11-14 DMM 实机：删卡 / 强化执行中放了 pause.flag，执行层用 time.sleep 照样又点了一轮）。"""

import ast
from pathlib import Path
from types import SimpleNamespace as NS

SRC = Path(__file__).resolve().parents[1] / "src" / "hiro"


def test_exec_layer_has_no_raw_time_sleep():
    bad = []
    for p in sorted([*SRC.joinpath("produce").glob("*.py"), *SRC.joinpath("exec").glob("*.py")]):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (
                isinstance(n, ast.Attribute)
                and n.attr == "sleep"
                and isinstance(n.value, ast.Name)
                and n.value.id == "time"
            ):
                bad.append(f"{p.relative_to(SRC)}:{n.lineno}")
    assert bad == []


def test_exec_sleep_passes_runtime_checkpoint():
    from hiro.devices import active
    from hiro.produce import card_ops

    t, hits = [0.0], []
    rt = NS(
        checkpoint=lambda: hits.append("checkpoint"), clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)
    )
    active.use(rt)
    try:
        card_ops._sleep(0.5)  # 执行层模块里的等待
    finally:
        active.use(None)
    assert len(hits) >= 3 and t[0] >= 0.5  # 每 0.2 秒过一次检查点


def test_sleep_without_any_loop_is_plain_sleep(monkeypatch):
    """没接上新主循环（离线工具、测试）：sleep 退回 time.sleep。"""
    import time

    from hiro.devices import active

    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    active.use(None)
    active.sleep(0.01)
    assert slept == [0.01]
