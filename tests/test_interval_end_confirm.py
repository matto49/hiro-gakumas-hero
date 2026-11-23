"""mainz4（11-18 14:04）：本战轮间买完、强化完还剩 10P，点「終了」后弹「インターバル終了確認」（使用できるPポイントが残っています
インターバルを終了して本戦へ進みますか？），执行器在等轮间主画面、主循环判成 DIALOG 没有处理器，247 秒后停机。
这个框只在我们自己点了「終了」后出现（决策已定结束轮间），中断层点「本戦へ」。"""

from hiro.runtime import interrupts
from hiro.runtime.frame import Frame

import pytest
from test_runtime import frame as fake_frame
from test_runtime import runtime


def _real(name):
    import cv2

    p = __import__("pathlib").Path(__file__).with_name("fixtures") / "pages" / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    return cv2.imread(str(p))


def test_interval_end_confirm_dialog_kind():
    from hiro.vision.pages import read_dialog

    d = read_dialog(_real("mainz4-interval-end-confirm.jpg"))
    assert d is not None and d.get("kind") == "interval_end"


def test_interval_end_confirm_taps_honsen():
    rt, _ = runtime([fake_frame()])
    assert "インターバル終了" in interrupts.check(Frame(_real("mainz4-interval-end-confirm.jpg"), 0.0), rt)
    ((x, y),) = rt.raw_device.taps
    assert x > 400 and abs(y - 1160) < 20  # 右下「本戦へ」，不是左下キャンセル


def test_interval_end_confirm_waits_after_tap():
    rt, _ = runtime([fake_frame()])
    f = fake_frame(texts=[], dialog={"kind": "interval_end"})
    assert "本戦へ" in interrupts.interval_end_confirm(f, rt)
    assert "等框关" in interrupts.interval_end_confirm(f, rt)  # 刚点过：不连点
    assert len(rt.raw_device.taps) == 1
