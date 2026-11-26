"""G3（11-26 Windows 小白验收）：扫支援卡时桌面版进度栏显示最后一行日志，原来这里打印的是卡表原始数据
（「1 4 +6 [('すっかり秋色ですね！', 50, 2), …]」）。进度要是白话，原始读数只进调试日志。"""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def test_list_scan_progress_is_plain(monkeypatch, capsys):
    import hiro.tool_device
    import hiro.vision.support_screens as ss

    import support_inventory as si

    ctl = {"next": (600, 1000), "cancel": (200, 1200)}

    def card(i):
        return {"support_card_id": f"s_card-{i}", "name": f"卡{i}", "level": 50, "breaks": 2, "partial": False}

    screens = iter(
        [
            {"pages": 1, "controls": ctl, "cards": [card(1), card(2)]},  # 第一次判断在不在列表
            {"pages": 1, "controls": ctl, "cards": [card(1), card(2)]},
            {"pages": 1, "controls": ctl, "cards": [card(3)]},
            {"pages": 1, "controls": ctl, "cards": [card(3)]},
            {"pages": 1, "controls": ctl, "cards": [card(3)]},
        ]
    )
    monkeypatch.setattr(ss, "read_support_list", lambda img: next(screens, None))
    dev = NS(screenshot=lambda: object(), tap=lambda x, y: None, swipe=lambda *a, **k: None)
    monkeypatch.setattr(hiro.tool_device, "open_tool_device", lambda: dev)
    monkeypatch.setattr(si.time, "sleep", lambda s: None)
    si.list_scan()
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines and all("[(" not in ln and "s_card" not in ln for ln in lines), out
    assert any(ln.startswith("支援卡扫描：第 1 页") and "一共 3 张" in ln for ln in lines), out
