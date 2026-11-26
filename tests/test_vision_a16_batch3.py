"""A16 第三批（11-26，macOS 27.2.1 的 Vision）：私有截图测试里还失败的几类读法，这里用假 OCR 框测文字规则，CI 可跑。"""

import numpy as np
import pytest


def test_tower_rank_label_uses_suffix():
    """名次标签「3rd」读成「Srd」：后缀 rd 定名次 3，不按 S 当 5；「2ndl」结尾多一个字也认。"""
    from hiro.vision.tower import _ranking_fields

    # 标签顺序故意打乱：按行序兜底会得到 [1, 2, 3, 4, 5]，只有真认出标签才是 [3, 1, 5, 2, 4]（#133 review）
    rows = [("Srd", 377), ("Ist", 520), ("Sth", 662), ("2ndl", 808), ("4th", 951)]  # 每行名次都和行号不同
    boxes = []
    for i, (label, y) in enumerate(rows):
        boxes.append((label, (45, y - 25, 125, y + 25)))
        boxes.append((f"{90000 - i * 10000:,}Pt", (480, y - 5, 640, y + 25)))
    got = [r["rank"] for r in _ranking_fields(boxes)["rows"]]
    assert got == [3, 1, 5, 2, 4]


def test_tower_conditions_strip_star_icon():
    from hiro.vision.tower import tower_dialog

    boxes = [
        ("★（・3位以上でクリア", (100, 500, 500, 530)),
        ("・2位以上でクリア", (100, 560, 500, 590)),
        ("1位でクリア", (100, 620, 500, 650)),
    ]
    name, f = tower_dialog({"title": "条件", "body": ""}, boxes)
    assert name == "TOWER_LAYER_INFO"
    assert [(c["star"], c["text"]) for c in f["conditions"]] == [
        (1, "3位以上でクリア"),
        (2, "2位以上でクリア"),
        (3, "1位でクリア"),
    ]


@pytest.mark.parametrize(
    ("text", "want"), [("選抜試験1/", "exam_title"), ("選抜試験2", "exam_title"), ("選抜試験1まで", None)]
)
def test_exam_title_tolerates_trailing_slash(text, want):
    from hiro.vision.pages import _transition_kind

    assert _transition_kind([(text, (283, 616, 566, 668))]) == want


def test_sales_option_text_reread_without_icon(monkeypatch):
    """话题按钮：整屏读成「日学生時代の話」时，裁掉图标再读；再读读不到就保留原来的。"""
    import hiro.vision.ocr
    from hiro.vision import nia

    img = np.zeros((1280, 720, 3), np.uint8)
    monkeypatch.setattr(hiro.vision.ocr, "ocr", lambda crop, scale=2: [("学生時代の話", 0.9)])
    assert nia._option_text(img, 700, "日学生時代の話") == "学生時代の話"
    monkeypatch.setattr(hiro.vision.ocr, "ocr", lambda crop, scale=2: [])
    assert nia._option_text(img, 700, "日学生時代の話") == "日学生時代の話"
    assert nia._option_text(None, 700, "学生時代の話") == "学生時代の話"


@pytest.mark.parametrize("start", ["オーディション開始", "オテデイション開始", "オーディシヨン開始"])
def test_audition_start_button_misreads(start, monkeypatch):
    from hiro.vision import nia

    monkeypatch.setattr(nia, "read_votes", lambda img: 4680)
    texts = [
        (start, 0.9, (240, 870, 480, 910)),
        ("4,000以上", 0.9, (300, 930, 450, 960)),
        ("条件なし", 0.9, (320, 1045, 440, 1070)),
    ]
    f = nia.read_audition_select(texts, np.zeros((1280, 720, 3), np.uint8))
    assert f is not None and f["expanded"] and f["chosen_need"] == 4000
