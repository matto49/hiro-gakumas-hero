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


def test_formation_progress_counts_unrecognized_cards(monkeypatch, capsys):
    """#145 review：编成页里认不出的卡（name / support_card_id 是 None，但读得到等级）也算有卡。"""
    import hiro.tool_device
    import hiro.vision.support_screens as ss

    import support_inventory as si

    slots = [{"support_card_id": "s_card-1", "name": "卡1", "level": 50, "breaks": 2}]
    slots += [{"support_card_id": None, "name": None, "level": 40, "breaks": None}]
    slots += [{"support_card_id": None, "name": None, "level": None, "breaks": None}] * 3
    pages = iter([{"number": 3, "name": "サポートカード編成3", "slots": slots, "arrows": {"next": (680, 635)}}])
    monkeypatch.setattr(ss, "read_support_formation", lambda img: next(pages, None))
    dev = NS(screenshot=lambda: object(), tap=lambda x, y: None)
    monkeypatch.setattr(hiro.tool_device, "open_tool_device", lambda: dev)
    monkeypatch.setattr(si.time, "sleep", lambda s: None)
    si.live(1)
    out = capsys.readouterr().out
    assert "支援卡编成 3 号（サポートカード編成3）：2 格有卡" in out, out
