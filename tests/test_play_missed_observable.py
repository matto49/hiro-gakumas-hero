"""D34「回合自动结束、牌没打出去」的判据只对会动元気 / 体力的牌成立：0 消耗、无效果的眠気打出后两者都不变，
用掉本回合最后一次出牌时回合自然结束，不能判没打出去（偶像之路 Mac 15 层 11-14，除外区眠気 2 → 8，3940a9 / 8a3a1e7）。"""

import pytest

import hiro.exec.exam as ex

pytestmark = [pytest.mark.master]


@pytest.mark.parametrize(("card_id", "want"), [
    ("p_card-00-acc-0_002", False),  # 眠気：0 消耗、没有效果
    ("p_card-02-men-0_039", True),   # 距離感の基本：消耗体力
    ("p_card-02-ido-3_105", True),
    (None, True),                    # 没有卡 ID：按原判法
    ("p_card-not-exist", True),      # 主数据里查不到：按原判法
])  # fmt: skip
def test_play_observable(card_id, want):
    assert ex._play_observable(card_id, 0) is want


def test_unobservable_play_is_never_missed(monkeypatch):
    called = []
    monkeypatch.setattr(ex, "_lite_sig", lambda img: called.append(1) or ((3, 10, 0), None))
    assert ex._play_missed(object(), confirmed=False, observable=False) is False and not called  # 不读 HUD、不判没打出去
