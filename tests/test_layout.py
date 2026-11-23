"""客户端布局 + 截图校准：游戏更新改了界面结构时应在这里报错，而不是悄悄用错坐标。"""

import hiro  # noqa: F401
from hiro.vision import layout

import pytest


def test_calibrated_nodes_resolve():
    for view, entries in layout.calibration().items():
        for suffix in entries:
            path, node = layout.node(view, suffix)
            assert node["active"], path


def test_anchor_only_element_matches_screen():
    """纯锚点元素不需要校准：回合圆盘中心与截图实测 (62, 90) 吻合。"""
    from hiro.vision import assets

    if assets.layout() is None:
        pytest.skip("需要完整 UI 布局（AssetSource.layout，自建素材包）；仓库只带识别层用到的几个实测框")
    cx, cy = layout.center(layout.EXAM, "HeaderRoot/TurnViewRoot")
    assert abs(cx - 62) <= 2 and abs(cy - 90) <= 2
