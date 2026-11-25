"""初考试「不合格」名次页：自己那行（6th）被底部按钮挡住、分数读不到，「6th」还读成「Oth」→ read_hif_result 给不出 rank，
exam_rank 返回 None，等 12 秒交回主循环，主循环点了「次へ」，免费再挑戦没用上、培育结束（11-25 账号 3 プロ期中）。
左上「不合格」就是没过线：exam_rank 给 failed=True，照样走再挑戦。"""

import os
from pathlib import Path

from hiro.vision import hajime as hv

import cv2
import numpy as np
import pytest


@pytest.mark.synthetic
def test_exam_rank_failed_without_rank(monkeypatch):
    from hiro.vision import hif

    monkeypatch.setattr(
        hif, "read_hif_result", lambda img: {"rank": None, "score": None, "retry": {"left": 1, "free": True}}
    )
    monkeypatch.setattr(hv, "_failed_header", lambda img: True)
    assert hv.exam_rank(np.zeros((1280, 720, 3), np.uint8)) == {"rank": None, "score": None, "retry": {"left": 1, "free": True},
                                                                "failed": True}  # fmt: skip
    monkeypatch.setattr(hv, "_failed_header", lambda img: False)
    assert hv.exam_rank(np.zeros((1280, 720, 3), np.uint8)) is None  # 不是名次页


@pytest.mark.synthetic
def test_result_passed():
    assert hv.result_passed({"rank": 3}, 3) and not hv.result_passed({"rank": 4}, 3)
    assert not hv.result_passed({"rank": None, "failed": True}, 3)
    assert not hv.result_passed({"rank": 2, "failed": True}, 3)  # 写着不合格就是没过


def test_exam_rank_real_fail_page_hidden_self_row():
    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "pages"
        / "hajime-exam-fail-6th-hidden.jpg"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/hajime-exam-fail-6th-hidden.jpg")
    r = hv.exam_rank(cv2.imread(str(path)))
    assert r is not None and r["failed"] is True and r["retry"] == {"left": 1, "free": True}


@pytest.mark.synthetic
def test_need_rank_final_only():
    """--final-rank 1（初星課題「最終試験で1位」/ 星南亲爱度 9「プロ最終試験 1位」）：只收紧期末，其它考试仍按过线名次。"""
    from hiro.runtime import hajime

    assert hajime.need_rank("hajime-final", 3, 1) == 1
    assert hajime.need_rank("hajime-mid1", 3, 1) == 3
    assert hajime.need_rank("hajime-final", 3, None) == 3
    assert hajime.need_rank("hajime-final", 3, 5) == 3  # 不会放宽过线名次
