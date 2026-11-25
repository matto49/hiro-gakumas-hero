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
