"""G1-F1 / F2（11-26 Mac 小白验收）：首次自动扫描里，扫回忆读完停在「メモリー編成」页没回首页；
下一个扫偶像卡开头盲点首页底栏，12 秒就失败，对话里还只说「日志里没写原因」。

- 扫支援卡 / 回忆（--from-home）读完回首页；扫偶像卡开头先回首页，回不去就说清楚停下。
- 工具写「…，停下」在句尾时，任务失败原因也要认出来。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from hiro.cli.jobs import failure_reason
from hiro.runtime import nav

import cv2
import pytest

SHOT = Path(__file__).parent / "fixtures" / "g1" / "memory-formation-page.png"  # 私有截图（游戏画面不随仓库发布）


def test_failure_reason_reads_trailing_stop():
    assert failure_reason(["没进到偶像详情页（找不到「一覧」），停下"]) == "没进到偶像详情页（找不到「一覧」）"
    assert failure_reason(["停下：游戏不在首页"]) == "游戏不在首页"
    assert failure_reason(["正常日志", "扫描完成"]) is None


@pytest.mark.skipif(not SHOT.exists(), reason="没有截图样例 tests/fixtures（游戏画面不随仓库发布）")
def test_toolbar_home_found_on_memory_formation_page():
    """G1 05:13 实机帧：メモリー編成 页左下角只有房子图标没有字，模板能认出来（goto_home 靠它回首页）。"""
    f = SimpleNamespace(img=cv2.imread(str(SHOT)), texts=[])
    assert not nav.at_home(f)
    m = nav.home_button(f)
    assert m is not None and m.rect.x1 < 150 and m.rect.y1 > 1150


def test_back_home_returns_false_instead_of_raising(monkeypatch):
    from hiro.runtime.loop import ResumeBlocked

    seen = {}

    def fake_goto(rt, timeout=nav.HOME_TIMEOUT):
        seen["device"] = rt.raw_device
        raise ResumeBlocked("回首页：60s 没完成")

    monkeypatch.setattr(nav, "goto_home", fake_goto)
    raw = SimpleNamespace(name="fake", screenshot=lambda: None, tap=lambda x, y: None)
    wrapped = SimpleNamespace(_device=raw)
    assert nav.back_home(wrapped) is False
    assert seen["device"] is raw  # 包过的 ToolDevice 取原始设备给 Runtime

    monkeypatch.setattr(nav, "goto_home", lambda rt, timeout=nav.HOME_TIMEOUT: object())
    assert nav.back_home(raw) is True


def test_idol_scan_goes_home_first_and_stops_with_reason(monkeypatch, capsys, tmp_path):
    import hiro.tool_device
    from hiro.produce import idol_scan

    taps = []
    dev = SimpleNamespace(screenshot=lambda: None, tap=lambda x, y: taps.append((x, y)))
    monkeypatch.setattr(hiro.tool_device, "open_tool_device", lambda: dev)
    monkeypatch.setattr(nav, "back_home", lambda d, timeout=nav.HOME_TIMEOUT: False)
    monkeypatch.setattr(sys, "argv", ["idol_scan", "--out", str(tmp_path / "o.json")])
    assert idol_scan.main() == 3
    assert taps == []  # 回不了首页就不盲点底栏
    out = capsys.readouterr().out
    assert failure_reason(out.splitlines()).startswith("游戏不在首页")


def test_memory_scan_from_home_goes_back_home(monkeypatch, tmp_path):
    import hiro.memory_scan as ms
    from hiro.produce import formation_menu

    calls = []
    dev = SimpleNamespace(screenshot=lambda: None, tap=lambda x, y: None, swipe=lambda *a, **k: None)
    monkeypatch.setattr(ms, "open_tool_device", lambda: dev)
    monkeypatch.setattr(ms, "Picker", lambda *a, **k: SimpleNamespace(ocr=lambda img: []))
    monkeypatch.setattr(formation_menu, "open_picker", lambda *a, **k: calls.append("open"))
    monkeypatch.setattr(formation_menu, "tool_device", lambda d: d)
    monkeypatch.setattr(
        ms, "scan", lambda picker, out, max_pages: calls.append("scan") or {"entries": [], "complete": True}
    )
    monkeypatch.setattr(nav, "back_home", lambda d, timeout=nav.HOME_TIMEOUT: calls.append("home") or True)
    monkeypatch.setattr(
        sys, "argv", ["memory_scan", "scan", "--from-home", "--no-calib", "--out", str(tmp_path / "o.json")]
    )
    ms.main()
    assert calls == ["open", "scan", "home"]


def test_memory_scan_goes_home_even_when_scan_fails(monkeypatch, tmp_path):
    import hiro.memory_scan as ms
    from hiro.produce import formation_menu

    calls = []
    dev = SimpleNamespace(screenshot=lambda: None, tap=lambda x, y: None, swipe=lambda *a, **k: None)
    monkeypatch.setattr(ms, "open_tool_device", lambda: dev)
    monkeypatch.setattr(ms, "Picker", lambda *a, **k: SimpleNamespace(ocr=lambda img: []))
    monkeypatch.setattr(formation_menu, "open_picker", lambda *a, **k: None)
    monkeypatch.setattr(formation_menu, "tool_device", lambda d: d)

    def boom(*a, **k):
        raise RuntimeError("列表读不下去")

    monkeypatch.setattr(ms, "scan", boom)
    monkeypatch.setattr(nav, "back_home", lambda d, timeout=nav.HOME_TIMEOUT: calls.append("home") or True)
    monkeypatch.setattr(
        sys, "argv", ["memory_scan", "scan", "--from-home", "--no-calib", "--out", str(tmp_path / "o.json")]
    )
    with pytest.raises(RuntimeError):
        ms.main()
    assert calls == ["home"]


def test_memory_scan_keeps_scene_when_formation_may_have_changed(monkeypatch, tmp_path):
    """#108 review：选择页在扫描中关了（多半点成了「決定」）时 scan 要求停手让人核对编成，收尾不能再点回首页。"""
    import hiro.memory_scan as ms
    from hiro.produce import formation_menu

    calls = []
    dev = SimpleNamespace(screenshot=lambda: None, tap=lambda x, y: None, swipe=lambda *a, **k: None)
    monkeypatch.setattr(ms, "open_tool_device", lambda: dev)
    monkeypatch.setattr(ms, "Picker", lambda *a, **k: SimpleNamespace(ocr=lambda img: []))
    monkeypatch.setattr(formation_menu, "open_picker", lambda *a, **k: None)
    monkeypatch.setattr(formation_menu, "tool_device", lambda d: d)
    monkeypatch.setattr(
        ms,
        "scan",
        lambda picker, out, max_pages: {"entries": [], "complete": False, "formation_may_have_changed": True},
    )
    monkeypatch.setattr(nav, "back_home", lambda d, timeout=nav.HOME_TIMEOUT: calls.append("home") or True)
    monkeypatch.setattr(
        sys, "argv", ["memory_scan", "scan", "--from-home", "--no-calib", "--out", str(tmp_path / "o.json")]
    )
    ms.main()
    assert calls == []


def test_back_home_swallows_device_error(monkeypatch):
    from hiro.devices.base import DeviceError

    def boom(rt, timeout=nav.HOME_TIMEOUT):
        raise DeviceError("截图失败")

    monkeypatch.setattr(nav, "goto_home", boom)
    raw = SimpleNamespace(name="fake", screenshot=lambda: None, tap=lambda x, y: None)
    assert nav.back_home(raw) is False


def test_support_scan_goes_home_when_picker_fails(monkeypatch, tmp_path):
    """#108 review：打不开支援卡「選択」列表（open_picker 抛错）时也要回首页。"""
    import importlib.util

    import hiro.tool_device
    from hiro.produce import formation_menu

    spec = importlib.util.spec_from_file_location(
        "support_inventory_tool", Path(__file__).resolve().parents[1] / "tools" / "support_inventory.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []
    monkeypatch.setattr(hiro.tool_device, "open_tool_device", lambda: SimpleNamespace())
    monkeypatch.setattr(formation_menu, "tool_device", lambda d: d)

    def fail(*a, **k):
        raise RuntimeError("点支援卡格没打开选择列表")

    monkeypatch.setattr(formation_menu, "open_picker", fail)
    monkeypatch.setattr(nav, "back_home", lambda d, timeout=nav.HOME_TIMEOUT: calls.append("home") or True)
    monkeypatch.setattr(sys, "argv", ["support_inventory", "--list", "--from-home", "--out", str(tmp_path / "o.json")])
    with pytest.raises(RuntimeError):
        mod.main()
    assert calls == ["home"]


def test_memory_scan_keeps_scene_even_if_saving_fails(monkeypatch, tmp_path):
    """#110 review：选择页关了、接着存盘出错（OSError）时，收尾也要保留现场。"""
    import hiro.memory_scan as ms
    from hiro.produce import formation_menu

    calls = []
    picker = SimpleNamespace(ocr=lambda img: [])
    dev = SimpleNamespace(screenshot=lambda: None, tap=lambda x, y: None, swipe=lambda *a, **k: None)
    monkeypatch.setattr(ms, "open_tool_device", lambda: dev)
    monkeypatch.setattr(ms, "Picker", lambda *a, **k: picker)
    monkeypatch.setattr(formation_menu, "open_picker", lambda *a, **k: None)
    monkeypatch.setattr(formation_menu, "tool_device", lambda d: d)

    def closed_then_disk_full(p, out, max_pages):
        p.formation_may_have_changed = True
        raise OSError("磁盘满了")

    monkeypatch.setattr(ms, "scan", closed_then_disk_full)
    monkeypatch.setattr(nav, "back_home", lambda d, timeout=nav.HOME_TIMEOUT: calls.append("home") or True)
    monkeypatch.setattr(
        sys, "argv", ["memory_scan", "scan", "--from-home", "--no-calib", "--out", str(tmp_path / "o.json")]
    )
    # #116 第二轮审查：标记打上后又出错，按「编成可能被改」以 FORMATION_CHANGED_EXIT 退出（开局前补扫才会停下不开局），照样保留现场
    assert ms.main() == ms.FORMATION_CHANGED_EXIT
    assert calls == []
