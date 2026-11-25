"""任务出错时把原因带进 failed 事件（11-25 review：通知说「原因在最后几步里」，可原因只在日志事件里，22 个出错任务一个都看不到）。"""

from hiro.cli.jobs import failure_reason


def test_stop_line_wins_and_drops_screenshot_path():
    lines = [
        "[2026-11-24 16:15:17,792] [INFO] [hiro.produce.formation_writer] 编成修正：支援卡第 1 格",
        "停下：没打开支援卡选择列表（截图在 reports/live/calib/20261124-161509-编成菜单）",
        "[2026-11-24 16:15:40,000] [INFO] [x] 收尾",
    ]
    assert failure_reason(lines) == "没打开支援卡选择列表"


def test_last_error_line_without_log_prefix():
    lines = [
        "[2026-11-23 12:42:18,555] [ERROR] [hiro.runtime.loop] 判页 / 处理器抛了异常（连续第 2 次，到 10 次停机）",
        "[2026-11-23 12:51:48,730] [WARNING] [hiro.loop.landscape] 横屏 LIVE 黑屏 20s",
        "[2026-11-23 12:51:48,735] [ERROR] [__main__] LIVE 页面和画面 181s 没变化，停机",
        "LIVE 页面和画面 181s 没变化，停机",
    ]
    assert failure_reason(lines) == "LIVE 页面和画面 181s 没变化，停机"


def test_long_reason_is_cut_and_none_when_nothing():
    r = failure_reason(["[2026-11-23 12:00:00,000] [ERROR] [m] " + "很长" * 200])
    assert r is not None and len(r) <= 160 and r.endswith("…")
    assert failure_reason(["[2026-11-23 12:00:00,000] [INFO] [m] 一切正常"]) is None
