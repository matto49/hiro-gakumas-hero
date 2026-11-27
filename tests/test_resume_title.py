"""11-26 C1 Mac 实机：游戏卡死后重开，续跑停在标题页 40 秒超时（不认识标题页）；标题页点进去又弹「機種変更…データ連携」框，
日切换恢复那套会把「閉じる」当公告关掉。现在续跑先从标题页回首页；遇到データ連携框什么都不点，停下交给玩家。"""

from types import SimpleNamespace as NS

from hiro.loop import date_change
from hiro.runtime import finish

import pytest

TITLE = ["Ver. 3.6.0", "プロデューサーID: タップで表示", "データ連携", "TAP TO START", "メニュー"]
DATA_LINK = [
    "機種変更をした場合は、データ連携をしてアプリの再起動をお願いします。",
    "アプリを再起動してもゲームを開始できない場合は以下よりお問い合わせください。",
    "閉じる",
    "お問い合わせ",
]


def test_at_title():
    assert finish.at_title(TITLE)
    assert not finish.at_title(["ホーム", "プロデュース", "メニュー"])
    assert not finish.at_title(["プロデューサーID 12345678"])  # 只有 ID（プロフィール页）不算


def _rt(texts):
    return NS(screen=NS(shot=lambda: NS(texts=[NS(text=t) for t in texts], img=None)), device=NS(tap=lambda x, y: None))


def test_resume_leaves_title_first(monkeypatch):
    calls = []
    monkeypatch.setattr(finish, "leave_title", lambda rt: calls.append("title"))
    rt = _rt(TITLE)
    rt.drive = lambda step, timeout, what: calls.append("drive") or "resumed"
    assert finish.resume_produce(rt) == "resumed"
    assert calls == ["title", "drive"]

    calls.clear()
    rt = _rt(["ホーム", "プロデュース"])
    rt.drive = lambda step, timeout, what: calls.append("drive") or "resumed"
    finish.resume_produce(rt)
    assert calls == ["drive"]  # 不在标题页不走这条


def _boxes(texts):
    return [(t, 0.9, (100, 200 + 60 * i, 600, 240 + 60 * i)) for i, t in enumerate(texts)]


def test_recover_stops_on_data_link_without_clicking():
    clicks = []
    with pytest.raises(date_change.DataLinkBlocked):
        date_change.recover_to_home(
            lambda: object(),
            lambda x, y: clicks.append((x, y)),
            lambda img: _boxes(DATA_LINK),
            lambda img: False,
            clock=lambda: 0.0,
            sleep=lambda s: None,
        )
    assert clicks == []  # 「閉じる」也不点


def test_leave_title_raises_when_not_home(monkeypatch):
    from hiro.runtime.loop import ResumeBlocked

    monkeypatch.setattr(date_change, "recover_to_home", lambda *a, **k: False)
    with pytest.raises(ResumeBlocked):
        finish.leave_title(_rt(TITLE))


def test_data_link_reason_is_plain():
    from hiro.cli.jobs import failure_reason

    lines = [
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "hiro.loop.date_change.DataLinkBlocked: 游戏要求做「データ連携」（换了设备或登录失效）：这一步要你本人在游戏里处理，处理完再点「接着打」",
    ]
    why = failure_reason(lines)
    assert why is not None and why.startswith("游戏要求做「データ連携」") and "DataLinkBlocked" not in why


# ---- 11-27：日常 / 扫描等任务回首页时也认标题页（原来只有续跑认） ----

DIM = ["Ver. 3.6.1", "プロデューサーID: タップで表示", "データ連携", "メニュー"]  # TAP TO START 闪到暗处，读不到


def _frame(texts):
    return NS(texts=[NS(text=t) for t in texts], img=None)


def test_title_screen_by_steady_texts():
    from hiro.runtime import nav

    assert nav.at_title_screen(_frame(TITLE))
    assert nav.at_title_screen(_frame(DIM))  # 靠不闪的字认，TAP TO START 暗的时候也算
    assert not nav.at_title_screen(
        _frame(["プロフィール", "プロデューサーID 12345678", "メニュー"])
    )  # プロフィール页不算


def test_goto_home_leaves_title_then_returns_home(monkeypatch):
    from hiro.runtime import nav

    calls, frames = [], [_frame(DIM), _frame(["ホーム"])]
    monkeypatch.setattr(nav, "at_home", lambda f: f.texts[0].text == "ホーム")
    monkeypatch.setattr(finish, "leave_title", lambda rt: calls.append("title") or frames.pop(0))

    def drive(step, timeout, what):
        calls.append("drive")
        return step(frames[0])

    rt = NS(drive=drive, device=NS(tap=lambda x, y: None))
    home = nav.goto_home(rt)
    assert calls == ["drive", "title", "drive"] and home.texts[0].text == "ホーム"


def test_goto_home_stops_when_stuck_on_title(monkeypatch):
    """离开标题页两次又回到标题页：停下讲原因，不把内部记号当首页帧交出去。"""
    from hiro.loop.errors import ResumeBlocked
    from hiro.runtime import nav

    import pytest

    monkeypatch.setattr(nav, "at_home", lambda f: False)
    monkeypatch.setattr(finish, "leave_title", lambda rt: None)
    rt = NS(drive=lambda step, timeout, what: step(_frame(DIM)), device=NS(tap=lambda x, y: None))
    with pytest.raises(ResumeBlocked, match="标题页"):
        nav.goto_home(rt)


def test_recover_taps_blank_when_tap_to_start_is_dim():
    """TAP TO START 闪到暗处读不到：点上方空白（标题页点哪里都行），不点データ連携 / メニュー。"""
    clicks = []
    seen = iter([False, True])
    date_change.recover_to_home(
        lambda: object(),
        lambda x, y: clicks.append((x, y)),
        lambda img: _boxes(DIM),
        lambda img: next(seen),
        clock=lambda: 0.0,
        sleep=lambda s: None,
    )
    assert clicks == [date_change.BLANK_TAP]
