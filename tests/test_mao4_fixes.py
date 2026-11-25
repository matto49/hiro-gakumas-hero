"""11-24 mao4 实机问题：强化页取消后的「スキップ確認」、审查日程面板校正选拔场次。"""

from hiro.exec import schedule_probe as sp
from hiro.produce import card_ops


def test_exam_from_panel():
    p = {"pass_text": "16位以上で選抜試験3突破"}
    assert sp.exam_from_panel("qualify-2", p) == "qualify-3"  # mao4：第 3 场标成 qualify-2
    assert sp.exam_from_panel("lesson-1", {"pass_text": "32位以上で選抜試験2突破"}) == "qualify-2"
    assert sp.exam_from_panel("qualify-3", p) is None  # 对的不改
    assert sp.exam_from_panel("qualify-1", {"pass_text": "64位以上で選拔試験１突破"}) is None  # 「拔」/ 全角数字也认
    assert sp.exam_from_panel("r2", p) is None  # 本战不动
    assert sp.exam_from_panel("hajime-final", {"pass_text": "3位以上で最終試験突破"}) is None
    assert sp.exam_from_panel("qualify-2", None) is None


def _box(text, x, y):
    return (text, 1.0, (x - 40, y - 15, x + 40, y + 15))


def test_confirm_skip_after_cancel_taps_skip():
    taps, t = [], [0.0]
    frames = [
        [_box("強化", 100, 50)],
        [_box("スキップ確認", 200, 970), _box("対象選択画面に戻る", 220, 1160), _box("選択せず次へ", 520, 1160)],
    ]
    seq = iter(frames)
    ok = card_ops.confirm_skip_after_cancel(shoot=lambda: next(seq, frames[-1]), click=lambda x, y: taps.append((x, y)),
                                            sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0], ocr=lambda im: im)  # fmt: skip
    assert ok and taps == [(520, 1160)]  # 点「選択せず次へ」，不点「対象選択画面に戻る」


def test_confirm_skip_after_cancel_no_dialog():
    taps, t = [], [0.0]
    ok = card_ops.confirm_skip_after_cancel(shoot=lambda: [_box("相談", 360, 100)], click=lambda x, y: taps.append((x, y)),
                                            sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0], ocr=lambda im: im)  # fmt: skip
    assert not ok and taps == [] and t[0] >= card_ops.SKIP_CONFIRM_WAIT
