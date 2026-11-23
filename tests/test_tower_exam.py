"""偶像之路考试页（hiro.vision.tower）：顶部 6 人名次条上自己的名次和分数、各人分数，Mac 15 层实机帧。
缺口（8a3a1e7 11-14）：自己的分数读成屏幕中间的加分动画（5,442，实际 34,368）；H.I.F 判页也认不出这页。"""

from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "tower"


def _img(name):
    import cv2

    path = FIX / name
    if not path.exists():
        pytest.skip(f"需要实机截图 tests/fixtures/tower/{name}")
    return cv2.imread(str(path))


@pytest.mark.parametrize(("name", "rank", "scores"), [
    # 1st 的 98,543 最后一位被特效线挡住，读成 9854，比右边小 → 给 None，不猜
    ("exam-rank4-mac15.jpg", 4, [None, 67840, 42137, 34368, 23709, 23545]),
    ("exam-rank5-mac15.jpg", 5, [34849, 26793, 11491, 9557, 5015, 4815]),
    ("exam-rank1-start-mac15.jpg", 1, [0, 0, 0, 0, 0, 0]),
])  # fmt: skip
def test_read_tower_ranking(name, rank, scores):
    from hiro.vision import hif, tower

    img = _img(name)
    assert tower.is_tower_exam(img) and not hif.is_hif_exam(img)
    t = tower.read_tower_ranking(img)
    assert t["my_rank"] == rank and t["scores"] == scores and t["my_score"] == scores[rank - 1]
    r = hif.read_ranking(img)  # read_hif_exam 走的入口：分数取名次条，不取中间的加分动画
    assert r["my_rank_index"] == rank and r["my_score"] == scores[rank - 1]


def test_layout_is_right_aligned_with_divider():
    from hiro.vision.tower import layout

    cols = layout(4)
    assert [c[0] for c in cols] == [1, 2, 3, 4, 5, 6] and cols[-1][2] == 710
    assert cols[3][2] - cols[3][1] == 127 and cols[2][2] - cols[2][1] == 69
    assert cols[3][1] - cols[2][2] == 2 + 13  # 3rd / 4th 之间的分隔


def test_status_turn_count_not_misread():
    """好調「12ターン」：ddddocr 把单位的「タ」读成 5（125）。先用整行 OCR 认「数字 + 单位」。"""
    from hiro.vision.status import read_status_column

    st = {
        e["key"]: e["value"]
        for e in read_status_column(_img("exam-status-12turn-mac15.jpg"), cache=None)
        if e.get("key")
    }
    assert st["examparameterbuff"] == 12 and st["examlessonbuff"] == 15


def test_hand_close_art_scores_checked_by_name():
    """灰着的トークタイム+ 卡面和準備運動 只差 0.005（0.710 对 0.705）：差不到 ART_MARGIN 就读卡名核对，不再认成準備運動（8a3a1e7 11-14）。"""
    from hiro.vision.hif import read_hand

    hand, _ = read_hand(_img("exam-status-12turn-mac15.jpg"))
    assert hand[2]["name"] == "トークタイム+" and hand[2]["source"] == "name" and hand[2]["art_name"] == "準備運動"
