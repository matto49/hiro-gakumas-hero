"""续跑进培育、结算收尾回首页、活動支給（hiro.runtime.finish）：假设备 + 假帧（复用 test_opening 的 rig / frame）。"""

from hiro.runtime import UNKNOWN, finish
from hiro.vision.classify import PageHit
from hiro.vision.ocr import Box, Text

import pytest
from test_opening import HOME, frame, rig  # 同目录（pytest 把测试目录放进 sys.path）


def T(text, x, y):
    return Text(text, Box(x - 20, y - 10, x + 20, y + 10))


def test_resume_taps_resume_then_enters(monkeypatch):
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    dlg = frame({}, [T("プロデュース再開", 360, 500), T("キャンセル", 200, 1160), T("再開する", 500, 1160)])
    rt, _ = rig(monkeypatch, [frame(HOME)], on_tap=lambda taps: [dlg] if len(taps) == 1 else [frame({})])
    assert finish.resume_produce(rt) == "resumed"
    assert rt.raw_device.taps[0] == tuple(finish.box("Produce.BoxProduceOngoing").center) and rt.raw_device.taps[1] == (
        500,
        1160,
    )


def test_resume_already_in_produce(monkeypatch):
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    monkeypatch.setattr("hiro.vision.classify.classify_scene", lambda img: PageHit("ACTION_SELECT", "pixel"))
    rt, _ = rig(monkeypatch, [frame({})])
    assert finish.resume_produce(rt) == "already" and rt.raw_device.taps == []


def test_finish_stops_at_start_confirm_and_never_presses_start(monkeypatch):
    confirm = frame({}, [T("2.開始確認", 360, 100), T("本戦へ", 500, 1160)])
    rt, _ = rig(monkeypatch, [confirm])
    assert finish.finish_produce_end(rt) is True and rt.raw_device.taps == []
    rt, _ = rig(monkeypatch, [confirm], on_tap=lambda taps: [frame(HOME)])
    assert finish.finish_produce_end(rt, go_home=True) is True
    assert rt.raw_device.taps == [(85, 1185)]  # go_home：点左下首页图标，不点「本戦へ」


def test_finish_go_home_from_menu_page_uses_toolbar_home(monkeypatch):
    """メモリー編成 等菜单页：左下工具栏 ⌂（Common.ButtonToolbarHome）回首页，不再一直点 (12,12) 到 180 秒超时（Mac 账号 2，11-14）。"""
    menu = frame({"Common.ButtonToolbarHome": (85, 1211)}, [T("メモリー編成1", 360, 125), T("サポート編成", 130, 1092)])
    rt, _ = rig(monkeypatch, [menu], on_tap=lambda taps: [frame(HOME)])
    assert finish.finish_produce_end(rt, go_home=True) is True and rt.raw_device.taps == [(85, 1211)]


def test_toolbar_home_template_on_real_menu_page():
    import os
    from pathlib import Path

    from hiro.vision.ui_templates import tpl

    import cv2

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures"))
        / "pages"
        / "menu-memory-formation-acct2.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/menu-memory-formation-acct2.png")
    m = tpl("Common.ButtonToolbarHome").find(cv2.imread(str(path)))
    assert m is not None and abs(m.rect.center.x - 85) < 15 and abs(m.rect.center.y - 1211) < 15


def test_finish_clicks_through_result_screens(monkeypatch):
    pages = [frame({}, [T("プロデュース完了", 360, 300), T("次へ", 360, 1160)]),
             frame({"InProduce.End.ButtonGenerate": (360, 1100)}),
             frame({}, [T("ログインボーナス", 360, 400)]), frame(HOME)]  # fmt: skip
    rt, _ = rig(monkeypatch, [pages[0]], on_tap=lambda taps: [pages[len(taps)]])
    assert finish.finish_produce_end(rt)
    assert rt.raw_device.taps == [(360, 1160), (360, 1100), (360, 1180)]  # 次へ、生成、领奖页点底部空白


def test_allowance_and_produce_end_handlers(monkeypatch):
    rt, _ = rig(monkeypatch, [frame({"InProduce.LootboxSliverLock": (360, 700)})])
    assert finish.on_allowance(rt, rt.screen.shot(), UNKNOWN).kind == "done"
    assert rt.raw_device.taps == [(360, 700), finish.BLANK_TAP]
    monkeypatch.setattr(finish, "finish_produce_end", lambda rt: True)
    assert finish.on_produce_end(rt, rt.screen.shot(), UNKNOWN) is finish.END


def test_run_stops_on_end(monkeypatch):
    rt, _ = rig(monkeypatch, [frame({})])
    rt.classify = lambda img: PageHit("PRODUCE_END", "template")
    rt.handlers = {"PRODUCE_END": lambda rt, f, hit: finish.END}
    assert rt.run(until=lambda: False) == 1


def test_resume_unknown_is_not_in_produce(monkeypatch):
    """认不出的画面不当成「已在培育内」（Mac 账号 2 11-17：主页通知框判 unknown → 直接开主循环，182 秒停机）：
    认不出时不点，UNKNOWN_HOME_S 秒后按回首页的按钮点；到首页再点「プロデュース中」→「再開する」。"""
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    monkeypatch.setattr("hiro.vision.classify.classify_scene", lambda img: UNKNOWN)
    popup = frame({"Common.ButtonIconClose": (360, 1200)})
    dlg = frame({}, [T("プロデュース再開", 360, 500), T("再開する", 500, 1160)])
    seq = {1: [frame(HOME)], 2: [dlg], 3: [frame({})]}
    rt, _ = rig(monkeypatch, [popup], on_tap=lambda taps: seq.get(len(taps)))
    assert finish.resume_produce(rt) == "resumed"
    assert rt.raw_device.taps[0] == (360, 1200) and rt.raw_device.taps[1] == tuple(
        finish.box("Produce.BoxProduceOngoing").center
    )


def test_resume_stops_when_never_recognized(monkeypatch):
    """一直认不出、也没有回首页的按钮：到超时停机，不进主循环。"""
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    monkeypatch.setattr("hiro.vision.classify.classify_scene", lambda img: UNKNOWN)
    rt, _ = rig(monkeypatch, [frame({})])
    from hiro.loop.errors import ResumeBlocked

    with pytest.raises(ResumeBlocked, match="续跑进培育"):
        finish.resume_produce(rt, timeout=20)
    assert rt.raw_device.taps == []


def test_finish_go_home_from_tower_result(monkeypatch):
    """11-17 Mac：run_hif new 起跑时停在偶像之路第 19 层 PERFECT 结果页（再挑戦 / 一覧に戻る，没有首页按钮），
    结算收尾一直点空白，180 秒超时。现在点「一覧に戻る」回层选择页，再点那里左下的首页；绝不点「再挑戦」。"""
    result = frame({}, [T("PERFECT", 360, 240), T("再挑戦", 130, 1160), T("→ 一覧に戻る", 350, 1160)])
    layers = frame({"Common.ButtonToolbarHome": (85, 1211)}, [T("挑戦する", 446, 987)])
    rt, _ = rig(monkeypatch, [result], on_tap=lambda taps: [layers] if len(taps) == 1 else [frame(HOME)])
    assert finish.finish_produce_end(rt, go_home=True) is True
    assert rt.raw_device.taps == [(350, 1160), (85, 1211)]


def test_goto_home_from_tower_result(monkeypatch):
    from hiro.runtime import nav

    result = frame({}, [T("再挑戦", 130, 1160), T("→一覧に戻る", 351, 1160)])
    rt, _ = rig(
        monkeypatch,
        [result],
        on_tap=lambda taps: [frame({"Common.ButtonToolbarHome": (85, 1211)})] if len(taps) == 1 else [frame(HOME)],
    )
    nav.goto_home(rt)
    assert rt.raw_device.taps == [(351, 1160), (85, 1211)]


@pytest.mark.parametrize("name", ["09_result.png", "100_perfect_result.png", "60_failed_ranking.png"])
def test_tower_back_on_real_frames(name):
    import os
    from pathlib import Path

    from hiro.runtime import nav
    from hiro.runtime.frame import Frame

    import cv2

    path = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "tower" / "pages" / name
    if not path.exists():
        pytest.skip(f"需要实机截图 tower/pages/{name}")
    b = nav.tower_back(Frame(cv2.resize(cv2.imread(str(path)), (720, 1280)), 0.0))
    assert b is not None and abs(b.rect.center.x - 350) < 20 and abs(b.rect.center.y - 1160) < 20


def test_resume_already_on_nia_page(monkeypatch):
    """N.I.A 营業话题页上 resume（协调 11-17：40 秒「续跑进培育」没完成）：Runtime 的判页认出 NIA_* 页就算已在培育内。"""
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    monkeypatch.setattr("hiro.vision.classify.classify_scene", lambda img: UNKNOWN)
    rt, _ = rig(monkeypatch, [frame({})])
    rt.classify = lambda img: PageHit("NIA_SALES_EVENT", "nia_ocr")
    assert finish.resume_produce(rt) == "already" and rt.raw_device.taps == []


def test_resume_already_on_hajime_briefing(monkeypatch):
    """初的考前说明页上 resume（协调 11-20：40 秒超时）：采样器的 OCR 兜底认出 HIF_EXAM_BRIEFING 就算已在培育内。"""
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    monkeypatch.setattr("hiro.vision.classify.classify_scene", lambda img: UNKNOWN)
    rt, _ = rig(monkeypatch, [frame({})])
    rt.classify = lambda img: PageHit("HIF_EXAM_BRIEFING", "briefing_ocr", "produce", {})
    assert finish.resume_produce(rt) == "already" and rt.raw_device.taps == []


def test_resume_hajime_briefing_on_real_frame(monkeypatch, tmp_path):
    """实机帧（ことね 中間考试开场页，pages/hajime-briefing-fktn.png）：初的采样器（nia=False）认出考前说明页，resume 直接开始。"""
    import os
    from pathlib import Path

    from hiro.runtime.nia_sample import Sampler
    from hiro.vision.classify import classify

    import cv2

    path = (
        Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "hajime-briefing-fktn.png"
    )
    if not path.exists():
        pytest.skip("需要实机截图 pages/hajime-briefing-fktn.png")
    img = cv2.imread(str(path))
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    rt, _ = rig(monkeypatch, [frame({}, img=img) if "img" in frame.__code__.co_varnames else frame({})])
    sampler = Sampler(classify, tmp_path, nia=False)
    rt.classify = lambda _img: sampler(img)
    assert finish.resume_produce(rt) == "already"


def test_resume_nia_page_on_real_frame(monkeypatch, tmp_path):
    """实机帧（营業话题，左上「営業」+ 三个话题）：采样的判页包装认出 NIA_SALES_EVENT，resume 直接开始。"""
    import os
    from pathlib import Path

    from hiro.runtime import nia_sample as ns
    from hiro.runtime.frame import Frame

    import cv2

    p = Path(os.environ.get("HIRO_FIXTURES", Path(__file__).parent / "fixtures")) / "pages" / "nia-sales-event-3.png"
    if not p.exists():
        pytest.skip("需要实机截图 pages/nia-sales-event-3.png")
    img = cv2.imread(str(p))
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda im: None)
    rt, _ = rig(monkeypatch, [frame({})])
    real = Frame(img, 0.0)
    rt.screen.shot = lambda: real
    from hiro.vision.classify import classify

    rt.classify = ns.Sampler(classify, tmp_path / "frames")
    assert finish.resume_produce(rt) == "already"


def test_resume_comm_error_taps_retry_never_to_title(monkeypatch):
    """11-19 账号 2：续跑入口碰上 E209 通信エラー，判 unknown 9 秒后点了「X タイトルへ」上的 X，游戏回标题。
    现在先按通信错误处理：只点リトライ；认不出的画面上有「タイトルへ」时也不点关闭按钮。"""
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    monkeypatch.setattr("hiro.vision.classify.classify_scene", lambda img: UNKNOWN)
    err = frame({"Common.ButtonIconClose": (150, 1160)}, [T("通信エラー", 140, 902), T("通信環境の良い場所でリトライしてください", 299, 1019),
                                                          T("X タイトルへ", 212, 1160), T("くリトライ", 478, 1160)])  # fmt: skip
    rt, _ = rig(monkeypatch, [err], on_tap=lambda taps: [frame({})])
    from hiro.loop.errors import ResumeBlocked

    with pytest.raises(ResumeBlocked):
        finish.resume_produce(rt, timeout=20)
    assert rt.raw_device.taps and all(t == (478, 1160) for t in rt.raw_device.taps)


def test_resume_unknown_with_to_title_text_does_not_tap_close(monkeypatch):
    monkeypatch.setattr("hiro.vision.quick.quick_page", lambda img: None)
    monkeypatch.setattr("hiro.vision.classify.classify_scene", lambda img: UNKNOWN)
    odd = frame({"Common.ButtonIconClose": (150, 1160)}, [T("X タイトルへ", 212, 1160)])
    rt, _ = rig(monkeypatch, [odd])
    from hiro.loop.errors import ResumeBlocked

    with pytest.raises(ResumeBlocked):
        finish.resume_produce(rt, timeout=20)
    assert rt.raw_device.taps == []
