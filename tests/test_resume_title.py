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
    monkeypatch.setattr(finish, "_leave_title", lambda rt: calls.append("title"))
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
        finish._leave_title(_rt(TITLE))


def test_data_link_reason_is_plain():
    from hiro.cli.jobs import failure_reason

    lines = [
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "hiro.loop.date_change.DataLinkBlocked: 游戏要求做「データ連携」（换了设备或登录失效）：这一步要你本人在游戏里处理，处理完再点「接着打」",
    ]
    why = failure_reason(lines)
    assert why is not None and why.startswith("游戏要求做「データ連携」") and "DataLinkBlocked" not in why
