"""G1 §8 错误文案：游戏没开时任务直接抛 DeviceUnavailable 结束，日志里没有「停下：」也没有 ERROR，
failure_reason 返回空，对话里看不到原因。现在读 traceback 最后一行的异常，设备类换成白话。"""

from hiro.cli.jobs import failure_reason

import pytest

TB = ["Traceback (most recent call last):", '  File "x.py", line 1, in <module>', "    main()"]


@pytest.mark.parametrize(
    ("last", "want"),
    [
        (
            "hiro.devices.base.DeviceUnavailable: MaaTools 端口 7458 连不上（PlayCover 里开了 maaTools 吗？）：[Errno 61] Connection refused",
            "连不上游戏：游戏没开",
        ),
        (
            "hiro.devices.base.DeviceUnavailable: 找不到游戏窗口（标题 'gakumas'、类 UnityWndClass）：先从 DMM Game Player 启动学マス",
            "找不到游戏窗口：先从 DMM Game Player 启动学マス",
        ),
        ("hiro.devices.base.DeviceGone: MaaTools 断开：[Errno 54] Connection reset by peer", "和游戏的连接断了"),
        ("hiro.devices.base.DeviceGone: MaaTools 连续 91s 返回空帧", "游戏画面一直截不到"),
        (
            "hiro.devices.base.DeviceUnavailable: 截图方式 printwindow 截出来全黑：游戏窗口最小化了？",
            "游戏画面截出来是全黑的",
        ),
        ("hiro.devices.base.DeviceGone: adb 设备 127.0.0.1:5555 不在了：offline", "和游戏设备的连接出了问题"),
        ("hiro.runtime.loop.ResumeBlocked: 回首页：60s 没完成", "回首页：60s 没完成"),
        ("KeyError: 'step'", "程序出错停下了：'step'"),
        ("Exception: 任务执行失败", "程序出错停下了：任务执行失败"),
        ("StopIteration", "程序出错停下了（StopIteration）"),
        ("AssertionError", "程序出错停下了（AssertionError）"),
        ("StopIteration: 没有下一页了", "程序出错停下了（StopIteration）：没有下一页了"),
        ("OSError: [Errno 28] No space left on device", "磁盘满了"),
    ],
)
def test_uncaught_exception_becomes_reason(last, want):
    why = failure_reason([*TB, last])
    assert why is not None and why.startswith(want), why
    assert "Device" not in why and "Errno" not in why and "Traceback" not in why


def test_stop_and_error_lines_still_win():
    lines = ["停下：游戏不在首页", *TB, "hiro.devices.base.DeviceGone: MaaTools 断开：x"]
    assert failure_reason(lines) == "游戏不在首页"
    lines = ["[2026-11-26 10:00:00,000] [ERROR] [m] 读不到体力", *TB, "ValueError: x"]
    assert failure_reason(lines) == "读不到体力"
    assert failure_reason(["正常日志", "扫描完成"]) is None


def _run_job(tmp_path, script: str):
    """真起一个子进程任务（走 Job._pump 的日志收集），等它结束，返回任务。"""
    import sys
    import time

    from hiro.cli import jobs

    kind = jobs.Kind("t", "测试", lambda p, d: [sys.executable, "-c", script], lambda p: "测试任务")
    home = tmp_path / "home"
    home.mkdir()
    j = jobs.Job(home, kind, {})
    j.start()
    deadline = time.time() + 20
    while j.state == "running" and time.time() < deadline:
        time.sleep(0.05)
    return j


def test_pump_keeps_traceback_tail(tmp_path):
    """#130 review：_pump 原来只把「停下：」/ ERROR 行交给 failure_reason，traceback 尾行进不去。"""
    script = (
        "print('[2026-11-26 11:04:40,149] [INFO] [hiro.produce.hif] 开始', flush=True)\n"
        "class DeviceUnavailable(Exception): pass\n"
        "raise DeviceUnavailable('MaaTools 端口 7458 连不上（PlayCover 里开了 maaTools 吗？）：[Errno 61] Connection refused')\n"
    )
    j = _run_job(tmp_path, script)
    assert j.state == "failed", j.state
    assert j.reason is not None and j.reason.startswith("连不上游戏：游戏没开"), j.reason


def test_pump_keeps_trailing_stop(tmp_path):
    """G1-F2：工具把「停下」写在句尾（扫偶像卡「没进到偶像详情页（…），停下」），原来也进不了 failure_reason。"""
    script = "import sys\nprint('没进到偶像详情页（找不到「一覧」），停下', flush=True)\nsys.exit(3)\n"
    j = _run_job(tmp_path, script)
    assert j.state == "failed" and j.reason == "没进到偶像详情页（找不到「一覧」）", j.reason
