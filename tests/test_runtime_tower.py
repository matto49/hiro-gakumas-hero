"""偶像之路打法规则（hiro.runtime.tower）和弹窗消耗词拦截（GuardedDevice 第 3 条）。"""

from types import SimpleNamespace as NS

from hiro.runtime import guard, tower

import numpy as np
import pytest


def layers(*stars, locked_from=None):
    return [
        {"layer": i + 1, "stars": s, "unlocked": locked_from is None or i + 1 < locked_from}
        for i, s in enumerate(stars)
    ]


def test_pick_first_layer_without_three_stars():
    run = tower.TowerRun("tower_001-ttmr")
    assert run.pick_layer(layers(3, 3, 1, 0)) == 3
    assert run.pick_layer(layers(3, 3, 3, 0, locked_from=4)) is None  # 下一层没解锁：没得打
    assert tower.TowerRun("t", max_layer=2).pick_layer(layers(3, 3, 0)) is None  # 超过配置的最高层
    run.gave_up_three.append(3)
    assert run.pick_layer(layers(3, 3, 1, 0)) == 4  # 这一轮放弃三星的层跳过


def test_three_stars_next_else_retry_until_limit():
    run = tower.TowerRun("t")
    assert run.after_result(1, cleared=True, stars=3) == "next"
    assert [run.after_result(2, cleared=True, stars=2) for _ in range(tower.MAX_TRIES)] == ["retry", "retry", "next"]
    assert run.gave_up_three == [2]  # 过关但次数用完：记下来往下走，不停机
    assert [run.after_result(3, cleared=False, stars=0) for _ in range(tower.MAX_TRIES)] == [
        "retry",
        "retry",
        "stop",
    ]  # 一次都没过关：停
    assert (
        run.after_result(4, cleared=True, stars=1) == "retry" and run.after_result(4, cleared=False, stars=0) == "retry"
    )
    assert run.after_result(4, cleared=False, stars=0) == "next"  # 第一次过关过：最后一次失败也算过关层
    assert run.summary()["cleared"] == [1, 2, 4] and run.summary()["best_stars"][4] == 1


@pytest.mark.parametrize("body,cost", [("再挑戦しますか？", None), ("ジュエルを消費して再挑戦しますか？", "ジュエル"),
                                       ("アイテムを使用します", "アイテム"), ("残り挑戦回数 2/3", "回数"), ("チケット 1 枚", "チケット")])  # fmt: skip
def test_retry_cost_words(body, cost):
    assert tower.retry_cost({"title": "再挑戦確認", "body": body}) == cost
    assert tower.retry_cost(None) is not None  # 没读到确认框：当有消耗，不点


def _screen(dialog, word):
    f = NS(img=np.zeros((1280, 720, 3), np.uint8), t=0.0, has_dialog=dialog is not None, dialog=dialog)
    return NS(frame=f, clock=lambda: 0.0, shot=lambda: f), word


def test_guard_dialog_words_only_allow_cancel(monkeypatch):
    dialog = {
        "kind": "other",
        "title": "再挑戦確認",
        "body": "ジュエルを消費して再挑戦しますか？",
        "danger": False,
        "y": 600,
    }
    for word, words, blocked in [
        ("OK", tower.DIALOG_WORDS, True),
        ("キャンセル", tower.DIALOG_WORDS, False),
        ("OK", (), False),
    ]:
        screen, w = _screen(dialog, word)
        monkeypatch.setattr(guard, "word_at", lambda f, x, y, w=w: w)
        if blocked:
            with pytest.raises(guard.DangerBlocked):
                guard.check_tap(screen, 500, 1160, words)
        else:
            guard.check_tap(screen, 500, 1160, words)  # 不开弹窗危险词（H.I.F）/ 点キャンセル：放行
