"""「通信エラー」（第二十二局本战轮间 12:26 卡死 195s 停机）：有リトライ就点リトライ（绝不点タイトルへ）；
只有「閉じる」（時間が経っているのでタイトルに戻します）就点閉じる、抛 CommErrorToTitle 走回首页续跑。"""

from pathlib import Path

import cv2
import pytest

from hiro.loop import comm_error as ce
from hiro.loop.date_change import DateChanged

import importlib.util as _iu
from pathlib import Path as _P

import importlib.util as _kiu

NEED_KAA = pytest.mark.skipif(_kiu.find_spec("kotonebot") is None or _kiu.find_spec("kaa") is None,
                              reason="操作层：要装 KAA / kotonebot（pip install -e \".[play]\"）")



def _real(*rel):
    """用实机截图的用例：截图放在 tests/fixtures/…（开源仓库不带，用自己的截图），识别层（hiro.vision.ocr）也要装。"""
    have = all((_P(__file__).parent / "fixtures" / r).exists() for r in rel) and _iu.find_spec("hiro.vision.ocr") is not None
    return pytest.mark.skipif(not have, reason="需要实机截图 tests/fixtures/" + ", ".join(rel) + " 和识别层")


FIX = Path(__file__).parent / "fixtures/comm_error"


def _boxes(*items):
    return [(t, 1.0, (x - 30, y - 12, x + 30, y + 12)) for t, (x, y) in items]


@_real('comm_error/m22-retry.png')
def test_real_frame_retry():
    from hiro.produce.ops import ocr_boxes

    e = ce.comm_error(ocr_boxes(cv2.imread(str(FIX / "m22-retry.png"))))
    assert e["kind"] == "retry" and abs(e["retry"][0] - 486) <= 40 and abs(e["retry"][1] - 1160) <= 15


def test_retry_never_taps_to_title():
    clicks = []
    b = _boxes(("通信エラー", (140, 973)), ("通信中にエラーが発生しました", (228, 1055)), ("タイトルへ", (238, 1160)), ("リトライ", (486, 1160)))
    assert ce.handle(b, lambda x, y: clicks.append((x, y)), sleep=lambda s: None) == "retry"
    assert clicks == [(486, 1160)]


def test_forced_title_closes_and_raises():
    clicks = []
    b = _boxes(("通信工ラー", (140, 973)), ("時間が経っているのでタイトルに戻します", (300, 1055)), ("閉じる", (360, 1160)))
    with pytest.raises(ce.CommErrorToTitle) as ex:
        ce.handle(b, lambda x, y: clicks.append((x, y)), sleep=lambda s: None)
    assert clicks == [(360, 1160)] and isinstance(ex.value, DateChanged)  # run_hif 按日期切换同一条路回首页续跑


def test_not_comm_error():
    assert ce.comm_error(_boxes(("体力回復確認", (150, 640)), ("キャンセル", (220, 1160)))) is None


@NEED_KAA
def test_loop_callback_handles_comm_error(monkeypatch):
    """KAA 每个 Loop 的全局回调（日期切换看守）顺带处理通信エラー：考试 / 其他 KAA 循环里也不会卡住。"""
    from types import SimpleNamespace as NS

    import kotonebot

    from hiro.loop import date_change

    clicks = []
    monkeypatch.setattr(kotonebot, "device", NS(click=lambda x, y: clicks.append((x, y))))
    monkeypatch.setattr(ce, "RETRY_SETTLE", 0)
    cb = date_change.loop_callback(lambda img: [("通信エラー", 1.0, (100, 80, 180, 105)), ("リトライ", 1.0, (450, 270, 520, 290))])
    import numpy as np

    cb(NS(screenshot=np.zeros((1280, 720, 3), np.uint8)))
    assert clicks == [(485, 280 + date_change.DIALOG_STRIP[0])]


@NEED_KAA
def test_comm_retry_episode_pauses_unknown_stop(monkeypatch):
    """Windows 第二十二局：断网 8 分钟，每次リトライ之间是 CONNECTING（UNKNOWN），累计 220s 停机。
    点过リトライ后 90s 内的 UNKNOWN 不计时、不问 LLM；同一轮故障超过 15 分钟恢复原来的 180 秒停机。"""
    from types import SimpleNamespace as NS

    import kotonebot

    from hiro.loop import controller as ctl
    from kaa.tasks.produce.new.consts import Scene, SceneType

    monkeypatch.setattr(kotonebot, "device", NS(screenshot=lambda: None))
    c = ctl.HiroController.__new__(ctl.HiroController)
    c._landscape = None
    stops = []
    c.strategy = NS(_stop=lambda *a: stops.append(a))
    monkeypatch.setattr(c, "_llm_rescue", lambda scene, s: pytest.fail("通信故障期间不该问 LLM"), raising=False)
    monkeypatch.setattr(c, "_ocr_texts", lambda: ["CONNECTING"], raising=False)
    monkeypatch.setattr(ctl.HiroController, "session", None)
    t = [1000.0]
    monkeypatch.setattr(ctl.time, "monotonic", lambda: t[0])
    unknown = Scene(SceneType.UNKNOWN)
    # 每 60s 点一次リトライ，持续 10 分钟：不停机
    for _ in range(10):
        c._note_comm_retry(t[0])
        for _ in range(6):
            t[0] += 10
            assert c._watch_unknown(unknown) is False
    assert stops == []
    # 同一轮超过 15 分钟：恢复计时，180 秒后停机
    monkeypatch.setattr(c, "_llm_rescue", lambda scene, s: False, raising=False)
    for _ in range(6):
        c._note_comm_retry(t[0])
        t[0] += 60
        c._watch_unknown(unknown)
    for _ in range(25):
        t[0] += 10
        c._watch_unknown(unknown)
    assert stops and stops[0][0] == "unknown"


@NEED_KAA
def test_comm_episode_window():
    from hiro.loop import controller as ctl

    c = ctl.HiroController.__new__(ctl.HiroController)
    assert not c._in_comm_error_episode(0.0)
    c._note_comm_retry(0.0)
    assert c._in_comm_error_episode(80.0)
    assert not c._in_comm_error_episode(100.0)  # 90s 没再弹：这一轮结束
    c._note_comm_retry(200.0)  # 新一轮从 200 起算
    assert c._comm_first == 200.0


def test_e209_retry_hits_button_not_hint_sentence():
    """11-19 账号 2：E209「アセット取得に失敗」的说明句含「リトライ」、排在按钮上面；原来点的是说明句 (299,1019)，10 次都没重试。
    按钮 OCR 读成「くリトライ」也要认。"""
    from hiro.loop.comm_error import comm_error

    boxes = [("通信エラー", 1, (60, 885, 220, 920)), ("アセット取得に失敗", 1, (60, 965, 280, 1000)),
             ("通信環境の良い場所でリトライしてください", 1, (60, 1002, 540, 1036)), ("(E209)", 1, (60, 1040, 140, 1072)),
             ("X タイトルへ", 1, (130, 1145, 295, 1178)), ("くリトライ", 1, (410, 1145, 546, 1178))]  # fmt: skip
    got = comm_error(boxes)
    assert got["kind"] == "retry" and got["retry"] == (478, 1161)


@pytest.mark.fixtures
def test_e209_on_orange_loading_screen_is_a_dialog():
    """11-19 账号 2：资源下载的加载画面整片橙黄，E209 弹窗的标题条和背景连成一片，has_dialog=False，中断层没点リトライ，180 s 停机。"""
    import os
    from pathlib import Path

    import cv2

    from hiro.loop.comm_error import comm_error
    from hiro.runtime.frame import Frame

    path = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "comm-e209-on-loading-acct2.png"
    if not path.exists():
        pytest.skip("需要实机截图 pages/comm-e209-on-loading-acct2.png")
    f = Frame(cv2.imread(str(path)), 0)
    assert f.has_dialog and (f.dialog or {}).get("kind") == "comm_error"
    e = comm_error(f.boxes)
    assert e["kind"] == "retry" and abs(e["retry"][0] - 475) < 30 and abs(e["retry"][1] - 1161) < 20


@pytest.mark.fixtures
def test_e209_on_loading_with_orange_bottom_still_retries():
    """11-20 账号 2 LOADING 页：按钮下方的橙色底被当成最下面的「标题条」，弹窗 kind 读成别的，中断层不点リトライ、180 s 停机。
    现在核对「通信エラー」字样本身在标题条上（橙色行 + 下方白色正文框），照样只点リトライ。"""
    import os
    from pathlib import Path
    from types import SimpleNamespace

    import cv2

    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    path = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "comm-e209-on-loading-move-acct2.png"
    if not path.exists():
        pytest.skip("需要实机截图 pages/comm-e209-on-loading-move-acct2.png")
    img = cv2.imread(str(path))
    taps = []
    rt = SimpleNamespace(screen=SimpleNamespace(shot=lambda: Frame(img, 0)), device=SimpleNamespace(tap=lambda x, y: taps.append((x, y))),
                         sleep=lambda s: None, clock=lambda: 0.0)  # fmt: skip
    assert interrupts.comm(Frame(img, 0), rt) == "通信エラー：retry"
    assert len(taps) == 1 and abs(taps[0][0] - 478) < 40 and abs(taps[0][1] - 1161) < 20
