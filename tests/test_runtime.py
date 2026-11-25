"""新主循环骨架 hiro.runtime：危险点击拦截、中断层、wait_until、主循环一拍、暂停检查点、不 import KAA。全用假设备和假帧。"""

import os
import subprocess
import sys
import threading

from hiro.loop.date_change import DateChanged
from hiro.loop.errors import ResumeBlocked
from hiro.runtime import DONE, STAY, UNKNOWN, DangerBlocked, Frame, PageHit, Runtime, guard, interrupts, stop
from hiro.vision.ocr import Box, Text

import numpy as np
import pytest


def T(text, x, y):
    return Text(text, Box(x - 20, y - 10, x + 20, y + 10))


def frame(texts=(), dialog=None, t=0.0, fill=0):
    """假帧：文字和弹窗读数直接给，不跑识别。fill：画面底色（不同底色 = 画面变了）。"""
    f = Frame(np.full((1280, 720, 3), fill, np.uint8), t)
    f.__dict__.update(texts=list(texts), has_dialog=dialog is not None, dialog=dialog)
    return f


REAL_WORD_AT = guard.word_at


@pytest.fixture(autouse=True)
def _word_from_frame(monkeypatch):
    """点击点读字：用假帧给的文字，不真跑局部 OCR。"""

    def word_at(f, x, y):
        return next(
            (t.text for t in f.texts if t.rect.x1 - 6 <= x <= t.rect.x2 + 6 and t.rect.y1 - 6 <= y <= t.rect.y2 + 6),
            None,
        )

    monkeypatch.setattr(guard, "word_at", word_at)


class FakeDevice:
    name = "fake"

    def __init__(self):
        self.taps = []

    def screenshot(self):
        return np.zeros((1280, 720, 3), np.uint8)

    def tap(self, x, y):
        self.taps.append((x, y))

    def real_tap(self, x, y):
        self.taps.append(("real", x, y))

    def secret_click(self, x, y):  # 白名单外的方法：GuardedDevice 不该转发
        self.taps.append(("secret", x, y))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def runtime(frames, *, classify=lambda img: UNKNOWN, **kw):
    """Runtime，截图依次给 frames 里的帧（用完重复最后一帧）。"""
    clock = Clock()
    rt = Runtime(FakeDevice(), classify, clock=clock, sleep=clock.sleep, **kw)
    seq = list(frames)

    def shot():
        f = seq.pop(0) if len(seq) > 1 else seq[0]
        f.t = clock()
        rt.screen.frame = f
        return f

    rt.screen.shot = shot
    return rt, clock


RETIRE = {"kind": "retire", "title": "プロデュース・リタイア確認", "danger": True, "y": 600}
DISCARD = {"kind": "data_discard", "title": "プロデュースデータの破棄", "danger": True, "y": 600}
PLAIN = {"kind": "other", "title": "確認", "danger": False, "y": 600}


# ---------------------------------------------------------------- 危险点击拦截
def test_guard_on_danger_dialog_only_allows_cancel():
    rt, _ = runtime([frame([T("キャンセル", 200, 1160), T("リタイア", 500, 1160)], RETIRE)])
    rt.screen.shot()
    with pytest.raises(DangerBlocked):
        rt.device.tap(500, 1160)
    with pytest.raises(DangerBlocked):
        rt.device.tap(360, 640)  # 空白处也不行
    rt.device.tap(200, 1160)
    assert rt.raw_device.taps == [(200, 1160)]


def test_guard_hard_words_blocked_without_dialog_and_menu_ok():
    rt, _ = runtime([frame([T("ジュエルで回復", 360, 900), T("閉じる", 360, 1160), T("リタイア", 600, 1000)])])
    rt.screen.shot()
    with pytest.raises(DangerBlocked):
        rt.device.tap(360, 900)
    with pytest.raises(DangerBlocked):
        rt.device.tap(600, 1000)
    rt.device.tap(360, 1160)  # ≡ 菜单上点「閉じる」：同一画面有「リタイア」字样也照点
    rt.device.tap(100, 100)
    assert rt.raw_device.taps == [(360, 1160), (100, 100)]


def test_guard_rechecks_stale_frame_and_covers_real_tap():
    """处理器截完图等了一会儿才点：期间弹出的危险框要看见（帧比 0.5 s 旧就重截）。real_tap 同样拦；白名单外的方法不给。"""
    rt, clock = runtime(
        [frame([T("決定", 500, 1160)]), frame([T("リタイア", 500, 1160), T("キャンセル", 200, 1160)], RETIRE)]
    )
    rt.screen.shot()
    clock.now = 2.0
    with pytest.raises(DangerBlocked):
        rt.device.tap(500, 1160)
    with pytest.raises(DangerBlocked):
        rt.device.real_tap(500, 1160)
    rt.device.real_tap(200, 1160)
    assert rt.raw_device.taps == [("real", 200, 1160)]
    with pytest.raises(AttributeError):
        rt.device.secret_click(1, 1)
    assert rt.device.screenshot().shape == (1280, 720, 3)


def test_guarded_device_mirrors_real_tap_and_hides_shell():
    """real_tap 跟着底层设备有没有走（adb / dmm 没有）；shell（adb 能 input tap）不转发。"""

    class Adb:
        name = "adb"

        def tap(self, x, y): ...

        def shell(self, cmd):
            return "ok"

    from hiro.runtime.frame import Screen

    g = guard.GuardedDevice(Adb(), Screen(Adb(), lambda: 0.0))
    assert getattr(g, "real_tap", None) is None
    with pytest.raises(AttributeError):
        g.shell("input tap 1 1")
    rt, _ = runtime([frame()])
    assert callable(getattr(rt.device, "real_tap", None))  # FakeDevice 有 real_tap


def test_word_at_reads_only_a_small_region(monkeypatch):
    """点击点读字只 OCR 周围 320×80（贴边时裁到画面内），不做整帧 OCR。"""
    seen = []

    def fake(img, rect=None):
        seen.append(rect)
        return [T("決定", 500, 1160), T("キャンセル", 200, 1160)]

    monkeypatch.setattr(guard, "screen_texts", fake)
    f = frame()
    assert REAL_WORD_AT(f, 500, 1160) == "決定" and seen[-1] == (340, 1120, 320, 80)
    assert REAL_WORD_AT(f, 700, 1270) is None and seen[-1] == (540, 1230, 180, 50)


# ---------------------------------------------------------------- 中断层
def test_red_line_taps_cancel_and_discard_stops():
    rt, _ = runtime([frame([T("キャンセル", 200, 1160)], RETIRE)])
    f = rt.screen.shot()
    assert "キャンセル" in interrupts.red_line(f, rt) and rt.raw_device.taps == [(200, 1160)]
    rt, _ = runtime([frame([T("キャンセル", 200, 1160), T("開始する", 500, 1160)], DISCARD)])
    with pytest.raises(ResumeBlocked, match="破弃由用户决定"):
        interrupts.red_line(rt.screen.shot(), rt)
    assert rt.raw_device.taps == [(200, 1160)]  # 只点了キャンセル，没点開始する
    rt, _ = runtime([frame([T("リタイア", 500, 1160)], RETIRE)])
    with pytest.raises(ResumeBlocked, match="没读到"):
        interrupts.red_line(rt.screen.shot(), rt)


def test_stop_notice_and_date_change():
    rt, _ = runtime([frame([T("ただいまメンテナンス中です", 360, 640)], PLAIN)])
    with pytest.raises(ResumeBlocked, match="维护"):
        interrupts.check(rt.screen.shot(), rt)
    notice = {"kind": "other", "title": "メンテナンスのお知らせ", "danger": False, "y": 400}
    rt, _ = runtime([frame([T("11/20 15:00〜 メンテナンスを予定しております", 360, 640)], notice)])
    assert interrupts.stop_notice(rt.screen.shot(), rt) is None  # 预告不停
    rt, _ = runtime([frame([T("日付変更", 360, 600), T("タイトルへ", 360, 1160)], PLAIN)])
    with pytest.raises(DateChanged):
        interrupts.check(rt.screen.shot(), rt)


COMM = {"kind": "comm_error", "title": "通信エラー", "danger": False, "y": 470}


def _comm_frame():
    return frame(
        [
            T("通信エラー", 360, 470),
            T("通信中にエラーが発生しました", 360, 600),
            T("タイトルへ", 200, 1160),
            T("リトライ", 500, 1160),
        ],
        COMM,
    )


def test_comm_error_only_retry():
    rt, _ = runtime([_comm_frame()])
    assert interrupts.check(rt.screen.shot(), rt) == "通信エラー：retry"
    assert rt.raw_device.taps == [(500, 1160)]


def test_comm_error_needs_comm_dialog_on_top_and_still_there():
    """11-17：リトライ 位置在首页上靠近「プロデュース中」。最上层弹窗标题不是通信エラー不点；重截时弹窗不在了也不点。"""
    other = frame([T("通信エラー", 360, 470), T("リトライ", 500, 1160)], PLAIN)  # 最上层是别的弹窗
    rt, _ = runtime([other])
    assert interrupts.comm(rt.screen.shot(), rt) is None and rt.raw_device.taps == []
    rt, _ = runtime([_comm_frame(), frame()])  # 重截那一帧弹窗没了
    assert "不点" in interrupts.comm(rt.screen.shot(), rt) and rt.raw_device.taps == []


def test_comm_error_retry_cap_stops_and_saves_frame():
    """通信エラー 一直在（11-17：10 分钟点了 162 次リトライ）：连着 COMM_RETRY_MAX 次之后停机报警、存帧；中间隔得久才重新计数。"""
    from types import SimpleNamespace as NS

    saved = []
    rt, clock = runtime([_comm_frame()])
    rt.session = NS(
        trace=NS(next_seq=lambda: 9, save_frame=lambda seq, img: saved.append(seq) or "frames/00009.jpg"),
        note=lambda s: None,
    )
    for _ in range(interrupts.COMM_RETRY_MAX):
        assert interrupts.comm(rt.screen.shot(), rt) == "通信エラー：retry"
    with pytest.raises(ResumeBlocked, match="通信エラー.*停机.*00009"):  # noqa: RUF043
        interrupts.comm(rt.screen.shot(), rt)
    assert len(rt.raw_device.taps) == interrupts.COMM_RETRY_MAX and saved
    rt, clock = runtime([_comm_frame()])
    for _ in range(interrupts.COMM_RETRY_MAX):
        interrupts.comm(rt.screen.shot(), rt)
    clock.now += interrupts.COMM_RESET_S + 1  # 隔了一阵没见到：重新计数
    assert interrupts.comm(rt.screen.shot(), rt) == "通信エラー：retry"


def test_interrupt_first_tick_saves_frame():
    """每次中断第一拍存一帧进 trace（同一句 60 秒内不重复存）。"""
    from types import SimpleNamespace as NS

    saved, notes = [], []
    seq = ["教程框：点「閉じる」", None, "教程框：点「閉じる」"]
    rt, clock = runtime([frame()], interrupts=(lambda f, rt: seq.pop(0),))
    rt.session = NS(
        trace=NS(next_seq=lambda: 3, save_frame=lambda s, img: saved.append(s) or "frames/00003.jpg"), note=notes.append
    )
    for _ in range(3):
        rt.tick()
        clock.now += 1
    assert saved == [3] and "frames/00003.jpg" in notes[0]  # 第二次 60 秒内：不重复存


def test_pitem_customize_confirm_taps_decide():
    """P 道具定制确认框（DMM 实机 11-14：新主循环没处理，181 秒停机）：点「決定」；不是 P 道具的定制框不管；读不到「決定」停机。"""
    custom = {"kind": "customize_confirm", "title": "カスタマイズ確認", "danger": False, "y": 469}
    texts = [T("以下のPアイテムにカスタマイズを実行します", 330, 540), T("キャンセル", 240, 1160), T("決定", 524, 1160)]
    rt, _ = runtime([frame(texts, custom)])
    assert "決定" in interrupts.check(rt.screen.shot(), rt) and rt.raw_device.taps == [(524, 1160)]
    rt, _ = runtime([frame([T("スキルカードをカスタマイズします", 330, 540), T("実行する", 500, 1160)], custom)])
    assert (
        interrupts.customize_confirm(rt.screen.shot(), rt) is None and rt.raw_device.taps == []
    )  # 特別指導的定制框：不归这里
    rt, _ = runtime([frame(texts[:2], custom)])
    with pytest.raises(ResumeBlocked, match="没读到"):
        interrupts.customize_confirm(rt.screen.shot(), rt)


def test_pitem_customize_confirm_lowercase_p():
    """DMM qualifyz3（11-18 02:51，559x993 窗口）：RapidOCR 把正文读成「以下のpアイテムに…」（小写 p），没认成 P 道具定制框，
    DIALOG 183 秒停机。「Pアイテム」不分大小写。"""
    custom = {"kind": "customize_confirm", "title": "カスタマイズ確認", "danger": False, "y": 493}
    texts = [T("以下のpアイテムにカスタマイズを実行します", 326, 576), T("キャンセル", 240, 1160), T("決定", 524, 1160)]
    rt, _ = runtime([frame(texts, custom)])
    assert "決定" in interrupts.check(rt.screen.shot(), rt) and rt.raw_device.taps == [(524, 1160)]


def test_pitem_customize_confirm_waits_after_tap():
    """qualifyz3 续跑（11-18 02:59）：点了「決定」3 秒后框还在、按钮读不到（DMM 响应慢，框在淡出），原来直接停机；
    其实点上了，下一屏就是「カスタマイズ完了」。点过之后 CONFIRM_FADE_S 内读不到「決定」当成等框关，过了才停。"""
    custom = {"kind": "customize_confirm", "title": "カスタマイズ確認", "danger": False, "y": 493}
    texts = [T("以下のPアイテムにカスタマイズを実行します", 326, 576), T("キャンセル", 240, 1160), T("決定", 524, 1160)]
    rt, clock = runtime([frame(texts, custom), frame(texts[:2], custom), frame(texts[:2], custom)])
    assert "決定" in interrupts.check(rt.screen.shot(), rt)
    clock.now += 3.0
    assert "等框关" in interrupts.customize_confirm(rt.screen.shot(), rt)
    clock.now += interrupts.CONFIRM_FADE_S
    with pytest.raises(ResumeBlocked, match="没读到"):
        interrupts.customize_confirm(rt.screen.shot(), rt)


def test_no_dialog_skips_ocr():
    f = frame()
    f.__dict__.pop("texts")  # 碰 texts 就会真去 OCR：没弹窗时中断层不该碰
    rt, _ = runtime([f])
    assert interrupts.check(rt.screen.shot(), rt) is None and "texts" not in f.__dict__


# ---------------------------------------------------------------- wait_until / 主循环一拍
def test_wait_until_hits_and_times_out():
    rt, clock = runtime([frame(), frame(), frame([T("OK", 1, 1)])])
    ok, _f = rt.wait_until(lambda f: any(t.text == "OK" for t in f.texts), timeout=5)
    assert ok and clock.now == pytest.approx(0.6)
    rt, clock = runtime([frame()])
    ok, _ = rt.wait_until(lambda f: False, timeout=2)
    assert not ok and clock.now == pytest.approx(2.1)


def test_drive_does_not_count_interrupt_time():
    """DMM own 本战开局 11-14：三个设置弹窗各约 6 秒，把「本战第 1 步点次へ」的 20 秒用完，最后一个弹窗处理完当拍就超时。
    处理中断的那拍不算这一段的时间；中断一直不停也有上限（timeout × INTERRUPT_EXTRA）。"""
    from hiro.loop.errors import ResumeBlocked
    from hiro.runtime import loop

    dialogs = {"n": 3}

    def settings_dialog(f, rt):
        if dialogs["n"] > 0:
            dialogs["n"] -= 1
            rt.sleep(6.9)  # 弹窗处理（找选项、点決定、等关闭）
            return "设置弹窗"
        return None

    rt, clock = runtime([frame()], interrupts=(settings_dialog,))
    clock.now = 0.0
    calls = []
    assert (
        rt.drive(lambda f: calls.append(clock()) or (True if len(calls) >= 2 else None), 20, "本战第 1 步点次へ")
        is True
    )
    assert clock.now > 20  # 弹窗花了 20 多秒，还是走到了下一步
    rt, clock = runtime([frame()], interrupts=(lambda f, rt: rt.sleep(1.0) or "一直有弹窗",))
    with pytest.raises(ResumeBlocked, match="没完成"):
        rt.drive(lambda f: None, 5, "x")
    assert clock.now <= 5 * (1 + loop.INTERRUPT_EXTRA) + 3


def test_step_dispatches_handler_and_stop_raises():
    seen = []

    def handler(rt, f, hit):
        seen.append(hit.name)
        return DONE if len(seen) == 1 else stop("读不出候选")

    rt, _ = runtime([frame()], classify=lambda img: PageHit("SELECT_CARD", "quick"), handlers={"SELECT_CARD": handler})
    assert rt.step() is DONE and seen == ["SELECT_CARD"]
    with pytest.raises(ResumeBlocked, match="SELECT_CARD：读不出候选"):
        rt.step()


def test_step_unknown_advances_then_watchdog_stops():
    advanced = []
    rt, clock = runtime([frame()], advance=lambda rt, f, hit: advanced.append(hit.name) or False)
    assert rt.step() is STAY and advanced == ["unknown"]
    clock.now = 179.0
    rt.step()
    clock.now = 181.0
    with pytest.raises(ResumeBlocked, match="没变化|认不出"):  # noqa: RUF043
        rt.step()


def test_unknown_stops_even_if_screen_keeps_changing():
    rt, clock = runtime([frame(fill=v % 2 * 200) for v in range(20)])
    for now in (0.0, 90.0, 179.0):
        clock.now = now
        rt.step()
    clock.now = 181.0
    with pytest.raises(ResumeBlocked, match="认不出"):
        rt.step()


def test_download_page_with_moving_percent_is_progress():
    """11-20 账号 2：网络不稳，资源下载页（データダウンロード中 xx%）认不出，E209 リトライ 后接着下，180 秒按认不出停机。
    百分比在涨就不停；百分比不动照常按认不出停机。"""

    def dl(p):
        return frame([T("データダウンロード中", 123, 1208), T(f"{p}%", 371, 1208)])

    rt, clock = runtime([dl("10.00"), dl("27.27"), dl("55.10"), dl("80.00")])
    for now in (0.0, 120.0, 240.0, 360.0):
        clock.now = now
        rt._comm_retry = (0.0, now, 9)  # 中间一直有 E209 リトライ
        rt.step()  # 每拍百分比都变了：不停，リトライ 计数清零
        assert rt._comm_retry is None
    rt2, clock2 = runtime([dl("27.27")])
    for now in (0.0, 90.0, 179.0):
        clock2.now = now
        rt2.step()
    clock2.now = 181.0
    with pytest.raises(ResumeBlocked, match="认不出"):
        rt2.step()


def test_known_page_stuck_stops_but_progress_does_not():
    rt, clock = runtime(
        [frame()],
        classify=lambda img: PageHit("ACTION_SELECT", "pixel"),
        handlers={"ACTION_SELECT": lambda rt, f, hit: STAY},
    )
    rt.step()
    clock.now = 181.0
    with pytest.raises(ResumeBlocked, match="ACTION_SELECT 页面和画面"):
        rt.step()
    rt, clock = runtime([frame(fill=v % 2 * 200) for v in range(10)], classify=lambda img: PageHit("ACTION_SELECT", "pixel"),
                        handlers={"ACTION_SELECT": lambda rt, f, hit: STAY})  # fmt: skip
    for now in (0.0, 100.0, 200.0, 300.0):
        clock.now = now
        rt.step()  # 画面一直在变：有进展，不停


def test_interrupt_handled_tick_skips_classify():
    calls = []
    rt, _ = runtime([frame([T("キャンセル", 200, 1160)], RETIRE)], classify=lambda img: calls.append(1) or UNKNOWN)
    assert rt.step() is STAY and calls == [] and rt.raw_device.taps == [(200, 1160)]


def test_session_observes_with_page_source():
    obs = []
    session = type("S", (), {"observe": lambda self, **kw: obs.append(kw)})()
    rt, _ = runtime([frame()], classify=lambda img: PageHit("ACTION_SELECT", "pixel"), session=session)
    rt.step()
    assert obs[0]["scene"] == "ACTION_SELECT" and obs[0]["page_source"] == "pixel" and obs[0]["fields"] is False


# ---------------------------------------------------------------- 暂停检查点
def test_checkpoint_waits_for_resume():
    rt, _ = runtime([frame()])
    rt.flow.request_pause()
    passed = threading.Event()
    th = threading.Thread(target=lambda: (rt.checkpoint(), passed.set()))
    th.start()
    assert not passed.wait(0.2)  # 暂停中：卡在检查点
    rt.flow.request_resume()
    assert passed.wait(2)
    th.join()


# ---------------------------------------------------------------- 不依赖 KAA
def test_runtime_imports_no_kaa():
    code = "import sys, hiro.runtime, hiro.runtime.interrupts; print([m for m in sys.modules if m.split('.')[0] in ('kaa', 'kotonebot')])"
    env = {
        k: v for k, v in os.environ.items() if k != "HIRO_KAA_GAME_DATA"
    }  # 设了它 hiro.vision 会给 KAA 指主数据目录（KAA 还在时要）
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()
    assert out == "[]"


@pytest.mark.parametrize("name, word", [("tutorial-90_h2_unknown.png", "次へ"), ("tutorial-91_tut_1.png", "閉じる"),
                                        ("tutorial-71_produce_menu.png", "次へ"), ("not-tutorial-sound-settings.png", None)])  # fmt: skip
def test_tutorial_card_taps_its_only_button(name, word):
    """新账号第一次进 H.I.F 的教程框（白底卡片 + 页码点 + 底部只有 次へ / 閉じる）：直接点那个按钮（Mac 账号 2，11-14，原来判成 unknown
    182 秒停机）。声音设置页（同样白底、有橙色滑条）不是。"""
    import os
    from pathlib import Path
    from types import SimpleNamespace as NS

    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    import cv2

    path = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / name
    if not path.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    taps = []
    r = interrupts.tutorial(Frame(cv2.imread(str(path)), 0.0), NS(device=NS(tap=lambda x, y: taps.append((x, y)))))
    if word is None:
        assert r is None and taps == []
    else:
        assert (
            r == f"教程框：点「{word}」" and len(taps) == 1 and abs(taps[0][1] - 1160) < 10 and 300 < taps[0][0] < 430
        )


def test_reroll_confirm_interrupt_on_resume():
    """续跑时「スキルカード再抽選確認」已经开着（上一个进程点了再抽選后停了）：中断层点「再抽選する」（协调 11-14，原来 181 秒停机）。
    标题条被大号白字切成两段、只认出下半截时标题读空：往上多裁一截重读。"""
    import os
    from pathlib import Path
    from types import SimpleNamespace as NS

    from hiro.runtime import interrupts
    from hiro.runtime.frame import Frame

    import cv2

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "settings"
        / "reroll-confirm-acct2.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 settings/reroll-confirm-acct2.png")
    f = Frame(cv2.imread(str(path)), 0.0)
    assert f.dialog["title"] == "スキルカード再抽選確認"
    taps = []
    assert "再抽選する" in interrupts.reroll_confirm(f, NS(device=NS(tap=lambda x, y: taps.append((x, y)))))
    assert len(taps) == 1 and taps[0][0] > 380 and abs(taps[0][1] - 1160) < 15


def test_reroll_confirm_interrupt_cost_cancels():
    from types import SimpleNamespace as NS

    from hiro.runtime import interrupts

    def fr(texts):
        return NS(has_dialog=True, dialog={"title": "スキルカード再抽選確認"}, texts=texts)

    taps = []

    def new_rt():  # 每种情况一个新的运行环境（同一个框点过后 TAP_GAP_S 内不再点）
        return NS(device=NS(tap=lambda x, y: taps.append((x, y))))

    cost = [T("ジュエルを30個消費します", 300, 760), T("キャンセル", 234, 1161), T("→ 再抽選する", 493, 1160)]
    assert "キャンセル" in interrupts.reroll_confirm(fr(cost), new_rt()) and taps == [(234, 1161)]
    with pytest.raises(ResumeBlocked, match="キャンセル"):
        interrupts.reroll_confirm(fr([T("ジュエルを30個消費します", 300, 760), T("→ 再抽選する", 493, 1160)]), new_rt())
    assert (
        interrupts.reroll_confirm(NS(has_dialog=True, dialog={"title": "再挑戦確認"}, texts=[]), new_rt()) is None
    )  # 别的确认框不管


def test_empty_crop_does_not_crash_main_loop(monkeypatch):
    """Mac h8 11-15：弹窗标题条贴着画面底边，read_dialog 裁正文裁出空图，ocr_boxes 里 cv2.resize 抛 !ssize.empty()，主循环崩溃。
    空图返回 []；正文裁不出来就当空正文。"""
    import os
    from pathlib import Path

    from hiro.vision import pages
    from hiro.vision.ocr import ocr_boxes

    import cv2
    import numpy as np

    assert ocr_boxes(np.zeros((0, 680, 3), np.uint8)) == [] and ocr_boxes(None) == []
    img = np.full((1280, 720, 3), 255, np.uint8)
    monkeypatch.setattr(pages, "_header_bands", lambda im: [(1240, 1280)])
    monkeypatch.setattr(pages, "_title_white", lambda im, y1, y2: True)
    monkeypatch.setattr(
        "hiro.vision.ocr.ocr",
        lambda crop, **k: [("確認", 1.0)] if crop.size else (_ for _ in ()).throw(AssertionError("空图")),
    )
    d = pages.read_dialog(img)
    assert d["title"] == "確認" and d["body"] == ""
    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "header-at-bottom-h8.jpg"
    )
    if path.exists():
        monkeypatch.undo()
        pages.read_dialog(cv2.resize(cv2.imread(str(path)), (720, 1280)))  # 实机那一帧：不抛异常


def _fake_session():
    from types import SimpleNamespace as NS

    rec = []
    saved = []
    sess = NS(recover=lambda kind, detail: rec.append((kind, detail)), observe=lambda **k: None,
              trace=NS(next_seq=lambda: 7, save_frame=lambda seq, img: saved.append(seq) or f"frames/{seq:05d}.jpg"))  # fmt: skip
    return sess, rec, saved


def test_handler_exception_is_absorbed_then_stops_after_limit():
    """总兜底（协调 11-15）：处理器 / 识别抛普通异常，这一拍按认不出处理、记 trace（带栈尾和帧）、继续；
    中间成功一拍就重新计数；同一异常连续 ERROR_LIMIT 次才停机，写明原因。"""
    from hiro.runtime import loop

    sess, rec, _saved = _fake_session()
    boom = {"on": True}

    def handler(rt, f, hit):
        if boom["on"]:
            raise ValueError("ssize.empty")
        return DONE

    rt, _clock = runtime(
        [frame()],
        classify=lambda img: PageHit("ACTION_SELECT", "pixel"),
        handlers={"ACTION_SELECT": handler},
        session=sess,
    )
    for _ in range(loop.ERROR_LIMIT - 1):
        assert rt.step() is STAY
    assert (
        len(rec) == loop.ERROR_LIMIT - 1
        and rec[0][0] == "unhandled"
        and "ValueError" in rec[0][1]
        and "frames/00007.jpg" in rec[0][1]
    )
    boom["on"] = False
    assert rt.step() is DONE  # 成功一拍：计数清零
    boom["on"] = True
    for _ in range(loop.ERROR_LIMIT - 1):
        rt.step()
    with pytest.raises(ResumeBlocked, match="连续 10 次"):
        rt.step()


def test_stop_class_exceptions_still_propagate():
    from hiro.loop.date_change import DateChanged

    for exc in (ResumeBlocked("停"), DateChanged("日付変更")):

        def handler(rt, f, hit, exc=exc):
            raise exc

        rt, _ = runtime(
            [frame()], classify=lambda img: PageHit("ACTION_SELECT", "pixel"), handlers={"ACTION_SELECT": handler}
        )
        with pytest.raises(type(exc)):
            rt.step()


def test_interrupt_exception_absorbed_in_tick():
    sess, rec, _ = _fake_session()

    def bad(f, rt):
        raise IndexError("crop")

    rt, _ = runtime([frame()], interrupts=(bad,), session=sess)
    _f, done = rt.tick()
    assert done and "IndexError" in done and rec and "中断层" in rec[0][1]


def test_drive_step_exception_absorbed_then_stops_after_limit():
    """开局 / 收尾流程 drive(step) 里 step 自己抛的普通异常也接住、记 trace、下一拍重来；连续 ERROR_LIMIT 次停机（协调 11-15）。"""
    from hiro.runtime import loop

    sess, rec, _ = _fake_session()
    rt, _ = runtime([frame()], session=sess)
    calls = {"n": 0}

    def flaky(f):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise KeyError("box")
        return "ok"

    assert rt.drive(flaky, 60, "开局第 1 步") == "ok" and len(rec) == 3 and "开局第 1 步" in rec[0][1]
    ok, _ = rt.wait_until(
        lambda f: (
            (_ for _ in ()).throw(KeyError("x")) if calls["n"] < 5 and not calls.update(n=calls["n"] + 1) else True
        ),
        60,
    )
    assert ok
    rt, _ = runtime([frame()], session=_fake_session()[0])
    with pytest.raises(ResumeBlocked, match=f"连续 {loop.ERROR_LIMIT} 次"):
        rt.drive(lambda f: (_ for _ in ()).throw(ValueError("坏")), 600, "收尾")


def test_confirm_interrupt_taps_once_while_dialog_fades():
    """カスタマイズ確認 0.5 秒里点了两次「決定」（h10 11-15）：同一个中断点过同一个框后 TAP_GAP_S 内框还在就不再点，过了再点。"""
    custom = {"kind": "customize_confirm", "title": "カスタマイズ確認", "danger": False, "y": 469}
    texts = [T("以下のPアイテムにカスタマイズを実行します", 330, 540), T("キャンセル", 240, 1160), T("決定", 524, 1160)]
    rt, clock = runtime([frame(texts, custom)])
    assert "点「決定」" in interrupts.customize_confirm(rt.screen.shot(), rt)
    clock.now += 0.5
    assert "刚点过" in interrupts.customize_confirm(rt.screen.shot(), rt) and rt.raw_device.taps == [(524, 1160)]
    clock.now += interrupts.TAP_GAP_S
    interrupts.customize_confirm(rt.screen.shot(), rt)
    assert rt.raw_device.taps == [(524, 1160), (524, 1160)]  # 框一直不关：过了间隔再点


def test_pitem_customize_done_page_is_tapped_through():
    """P 道具「カスタマイズ完了」展示页（没有按钮，说明里有「レッスン終了時」，旧的文字过滤不点）：点一下空白（h10 11-15，原来 180 秒停机）。"""
    import os
    from pathlib import Path
    from types import SimpleNamespace as NS

    from hiro.runtime import play
    from hiro.runtime.frame import Frame
    from hiro.vision.classify import classify

    import cv2

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "pitem-customize-done.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/pitem-customize-done.png")
    img = cv2.imread(str(path))
    taps = []
    rt = NS(
        device=NS(tap=lambda x, y: taps.append((x, y))), sleep=lambda s: None, screen=NS(shot=lambda: Frame(img, 0.0))
    )
    hit = classify(img)
    assert hit.name == "PITEM_CUSTOMIZE_DONE" and play.advance(rt, Frame(img, 0.0), hit) is True and taps == [(12, 12)]


@pytest.mark.parametrize("name", ["achievement-toast-home.png", "achievement-toast-step1.png"])
def test_achievement_toast_waits_without_tapping(name):
    """成就达成提示条盖住开局第 1 步的页头（Mac 账号 2 11-16）：中断层不点、等它消失；一直在超过 TOAST_MAX_S 就不再拦。"""
    import os
    from pathlib import Path

    from hiro.runtime.frame import Frame

    import cv2

    path = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / name
    if not path.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    rt, clock = runtime([frame()])
    f = Frame(cv2.resize(cv2.imread(str(path)), (720, 1280)), 0.0)
    assert "成就提示" in interrupts.achievement_toast(f, rt) and rt.raw_device.taps == []
    clock.now += interrupts.TOAST_MAX_S
    assert interrupts.achievement_toast(f, rt) is None  # 一直不走：交回判页 / 看门狗
    assert interrupts.achievement_toast(frame(), rt) is None  # 没有提示条：不管


def test_drive_waits_out_achievement_toast():
    """drive 等第 1 步页头时提示条盖着：这几拍当中断处理（不调 step、不算超时），消失后照常认出。"""
    seq = [True, True, True, False]
    rt, _clock = runtime([frame()], interrupts=(lambda f, rt: "成就提示条盖住页头：等它消失" if seq.pop(0) else None,))
    calls = []
    assert rt.drive(lambda f: calls.append(1) or True, 1.0, "等第 1 步") is True and calls == [1]


def test_repeated_interrupt_logged_once_then_end(caplog):
    """同一句中断连续出现只记第一拍和结束（成就提示条 11-17：每拍一行 INFO）。"""
    caplog.set_level("INFO", logger="hiro.runtime.loop")
    seq = ["成就提示条盖住页头：等它消失"] * 5 + [None]
    rt, _ = runtime([frame()], interrupts=(lambda f, rt: seq.pop(0),))
    for _ in range(6):
        rt.tick()
    lines = [r.getMessage() for r in caplog.records if "中断" in r.getMessage()]
    assert (
        lines == ["中断：成就提示条盖住页头：等它消失", lines[1]]
        and lines[1].startswith("中断结束：成就提示条")
        and "连续 5 拍" in lines[1]
    )


def test_idle_stop_logs_and_stops_when_nothing_happens(caplog):
    """偶像之路 g5 11-17：首页画面会动（「画面没变」看门狗管不到），没有处理器，静默空转 4 分钟。idle_stop_s 内没有任何操作就写日志停机；
    过一半先 WARNING。有操作（处理器 DONE）就重新计时。"""
    caplog.set_level("WARNING", logger="hiro.runtime.loop")
    acted = {"on": False}
    rt, clock = runtime([frame(fill=0), frame(fill=200)] * 50, classify=lambda img: PageHit("home", "template"),
                        handlers={"home": lambda rt, f, hit: DONE if acted["on"] else STAY}, idle_stop_s=60)  # fmt: skip
    for _ in range(6):
        rt.step()
        clock.now += 10
    assert any("没有任何操作" in r.getMessage() for r in caplog.records)  # 30 秒时先警告
    with pytest.raises(ResumeBlocked, match="没有任何操作"):
        for _ in range(4):
            rt.step()
            clock.now += 10
    acted["on"] = True
    rt2, clock2 = runtime([frame(fill=0), frame(fill=200)] * 50, classify=lambda img: PageHit("home", "template"),
                          handlers={"home": lambda rt, f, hit: DONE}, idle_stop_s=60)  # fmt: skip
    for _ in range(20):
        rt2.step()
        clock2.now += 10  # 一直有操作：不停


def test_affection_unlock_taps_close_on_real_frame(monkeypatch):
    """主页「親愛度STEP3解放」（Mac 账号 2 11-17，一连 5 个）：点底部 ×；没有这两个字的同款 ×（日程表浮层）不点。"""
    import os
    from pathlib import Path

    from hiro.runtime.frame import Frame

    import cv2

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "pages"
        / "home-affection-step-unlock.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/home-affection-step-unlock.png")
    img = cv2.resize(cv2.imread(str(path)), (720, 1280))
    rt, clock = runtime([frame()])
    f = Frame(img, 0.0)
    f.__dict__["texts"] = [T("親愛度STEP3解放", 360, 480), T("親愛度STEP3が解放されました", 360, 860)]
    assert "親愛度" in interrupts.affection_unlock(f, rt) and rt.raw_device.taps == [interrupts.AFFECTION_CLOSE]
    assert "刚点过" in interrupts.affection_unlock(f, rt) and len(rt.raw_device.taps) == 1  # 框淡出中：不连点
    clock.now += interrupts.TAP_GAP_S
    assert interrupts.affection_unlock(f, rt) and len(rt.raw_device.taps) == 2  # 下一个偶像的通知
    other = Frame(img, 0.0)
    other.__dict__["texts"] = [T("スケジュール", 360, 200)]
    assert interrupts.affection_unlock(other, rt) is None
    assert interrupts.affection_unlock(frame(), rt) is None  # 没有 ×：不 OCR、不点


def test_use_limit_dialog_taps_cancel():
    """主页「使用期限確認」：点「キャンセル」（读不到按钮时点左下固定位置）。"""
    dlg = {"kind": "other", "title": "使用期限確認", "danger": False, "y": 600}
    rt, _ = runtime([frame()])
    f = frame(
        [T("使用期限確認", 360, 400), T("使用期限の近い持ち物があります", 360, 600), T("キャンセル", 212, 1161)],
        dialog=dlg,
    )
    assert "キャンセル" in interrupts.use_limit(f, rt) and rt.raw_device.taps == [(212, 1161)]
    rt, _ = runtime([frame()])
    assert interrupts.use_limit(frame([T("使用期限確認", 360, 400)], dialog=dlg), rt) and rt.raw_device.taps == [
        interrupts.USE_LIMIT_CANCEL
    ]
    assert interrupts.use_limit(frame([T("確認", 360, 400)], dialog=PLAIN), rt) is None


def test_use_limit_on_real_frame():
    """实机「使用期限確認」（Mac 11-17，tower-mac/frames/expiry.png）：整条中断层走到 use_limit，点左下「✕キャンセル」，不点「イベントへ」。"""
    import os
    from pathlib import Path

    from hiro.runtime.frame import Frame

    import cv2

    path = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "home-use-limit.png"
    if not path.exists():
        pytest.skip("需要实机截图 pages/home-use-limit.png")
    rt, _ = runtime([frame()])
    assert "使用期限" in interrupts.check(Frame(cv2.resize(cv2.imread(str(path)), (720, 1280)), 0.0), rt)
    # 按钮中心按 OCR 框算，后端 / 系统版本之间差几个像素（macOS 27.2.1 的 Vision 是 (214, 1161)）
    assert (
        len(rt.raw_device.taps) == 1
        and abs(rt.raw_device.taps[0][0] - 212) <= 6
        and abs(rt.raw_device.taps[0][1] - 1161) <= 6
    )


def test_live_setting_dialog_on_real_frame():
    """N.I.A 第一次进第 2 步弹的「NEXT IDOL AUDITIONライブ設定」（实机帧）：长标题把标题条切断，原来认不出弹窗；
    现在中断层走到 live_setting，点右下「決定」，默认选项不改。"""
    import os
    from pathlib import Path

    from hiro.runtime.frame import Frame

    import cv2

    path = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "nia-live-setting.png"
    if not path.exists():
        pytest.skip("需要实机截图 pages/nia-live-setting.png")
    rt, _ = runtime([frame()])
    assert "ライブ設定" in interrupts.check(Frame(cv2.resize(cv2.imread(str(path)), (720, 1280)), 0.0), rt)
    ((x, y),) = rt.raw_device.taps
    assert abs(x - 518) < 30 and abs(y - 1160) < 15
