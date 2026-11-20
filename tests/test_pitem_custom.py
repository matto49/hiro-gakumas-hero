"""P 道具定制三选一（hiro.vision.pitem_custom）：DMM 三局的实机帧。

缺口来源（协调 11-14）：dmmt1 / dmmt2 / dmmw1 第二段（装饰）三个候选都读成「ロボ（黄）」——画面上没有毛绒名标题，
旧代码走了第一段的按颜色认毛绒，规则一直选第 1 个。第一段的もじゃ（淡紫）也认不出。
"""

from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "pitem"


def _img(name):
    import cv2

    path = FIX / name
    if not path.exists():
        pytest.skip(f"需要实机截图 tests/fixtures/pitem/{name}")
    return cv2.imread(str(path))


@pytest.mark.parametrize(("name", "want"), [
    ("custom-plush-dmmw1.jpg", ["ロボ（黄）", "うさぎ（黄）", "インコ（黄）"]),
    ("custom-plush-dmmt1.jpg", ["ロボ（黄）", "もじゃ（黄）", "インコ（黄）"]),
    ("custom-deco-dmmt2.jpg", ["花ロボ（黄）", "リボンロボ（黄）", "羽ロボ（黄）"]),
    ("custom-deco-dmmw1.jpg", ["リボンロボ（黄）", "羽ロボ（黄）", "花ロボ（黄）"]),
    # z6（11-19 12:54 DMM 1080x1920）：上面是淡紫的もじゃ（高饱和像素不够），没认出第二段，三格都读成「もじゃ（黄）」停机；第三格是メダル（首个实机样本）
    ("custom-deco-moja-z6.jpg", ["花もじゃ（黄）", "羽もじゃ（黄）", "メダルもじゃ（黄）"]),
])  # fmt: skip
def test_custom_names_from_real_frames(name, want):
    from hiro.vision.pitem_custom import custom_names, is_decoration_stage

    img = _img(name)
    assert is_decoration_stage(img) is name.startswith("custom-deco")
    assert custom_names(img, "黄") == want


def test_decoration_pick_follows_community_rank():
    """名字认对以后，社区排序（花 > リボン > 羽）选到花，不再是第 1 个。"""
    from hiro.brain import pitem
    from hiro.vision.pitem_custom import custom_names

    names = custom_names(_img("custom-deco-dmmw1.jpg"), "黄")
    scores = [pitem.score(pitem.parse({"name": n})) for n in names]
    assert names[scores.index(max(scores))] == "花ロボ（黄）" and scores.index(max(scores)) == 2


def test_cutin_is_not_decoration_stage():
    """「カスタムPアイテム」过场（选择页之前）：上面没有背包，不当成第二段；主循环只在「カスタム後を選択」页才认候选。"""
    from hiro.vision.pitem_custom import is_decoration_stage

    assert not is_decoration_stage(_img("custom-cutin-dmmt1.jpg"))
