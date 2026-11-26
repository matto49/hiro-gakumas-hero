"""读首页数值 + プロデューサーID（hiro.daily.account_scan）：首页读 PLv / AP / ジュエル，再从菜单进プロフィール读 P-ID 和名字。"""

from pathlib import Path
from types import SimpleNamespace as NS

from hiro.daily import account_scan

import pytest

PROFILE = Path(__file__).with_name("fixtures") / "pages" / "profile-account2.png"

# 11-25 账号 2 プロフィール页的 OCR 框（实机）
PROFILE_BOXES = [("%プロフィール", 0.9, (24, 36, 233, 79)), ("式部", 0.9, (54, 147, 113, 179)),
                 ("P-ID VRRL6GVW", 0.9, (43, 543, 236, 564)), ("誕生日", 0.9, (329, 543, 389, 564)),
                 ("式部", 0.9, (65, 629, 128, 661)), ("PLV 65", 0.9, (65, 684, 153, 710))]  # fmt: skip


def test_parse_profile():
    assert account_scan.parse_profile(PROFILE_BOXES) == {"producer_id": "VRRL6GVW", "name": "式部"}


def test_parse_profile_id_in_separate_box():
    boxes = [("P-ID", 0.9, (43, 543, 90, 564)), ("LHLB56ZP", 0.9, (100, 543, 236, 564)),
             ("プロデューサー", 0.9, (65, 629, 260, 661)), ("PLv 15", 0.9, (65, 684, 153, 710))]  # fmt: skip
    assert account_scan.parse_profile(boxes) == {"producer_id": "LHLB56ZP", "name": "プロデューサー"}


def test_parse_profile_not_profile_page():
    assert account_scan.parse_profile([("ホーム", 0.9, (329, 1185, 389, 1204))]) is None


def test_parse_profile_real_frame():
    if not PROFILE.exists():
        pytest.skip("需要实机截图 pages/profile-account2.png")
    from hiro.runtime.frame import Frame

    import cv2

    assert account_scan.parse_profile(Frame(cv2.imread(str(PROFILE)), 0).boxes)["producer_id"] == "VRRL6GVW"


def test_run_reads_home_then_profile(monkeypatch):
    from hiro.runtime import nav

    home = [("ホーム", 0.9, (329, 1185, 389, 1204))]
    menu = [("プロフィール", 0.9, (60, 430, 170, 455)), ("名刺", 0.9, (420, 430, 470, 455))]
    screens = {"now": home}
    taps, homes = [], []

    def tap(x, y):
        taps.append((x, y))
        if (x, y) == account_scan.MENU_TAP:
            screens["now"] = menu
        elif screens["now"] is menu:
            screens["now"] = PROFILE_BOXES

    def drive(step, timeout, what, every=0.5):
        for _ in range(10):
            if (r := step(NS(boxes=screens["now"], img=None))) is not None:
                return r
        raise AssertionError(what)

    monkeypatch.setattr(nav, "goto_home", lambda rt: homes.append(1) or screens.update(now=home))
    monkeypatch.setattr(nav, "at_home", lambda f: f.boxes is home)
    monkeypatch.setattr(
        account_scan, "read_home_values", lambda img: {"plv": 65, "ap": 100, "ap_max": 100, "jewel": 15720}
    )
    monkeypatch.setattr(account_scan.common, "frame", lambda rt: NS(boxes=screens["now"], img=None))
    rt = NS(device=NS(tap=tap), sleep=lambda s: None, drive=drive)
    out = account_scan.run(rt)
    assert out == {"plv": 65, "ap": 100, "ap_max": 100, "jewel": 15720, "producer_id": "VRRL6GVW", "name": "式部"}
    assert taps[0] == account_scan.MENU_TAP and taps[1] == (115, 442)  # 菜单里「プロフィール」的中心
    assert len(homes) == 2  # 开始先回首页、读完再回首页（后面的任务都从首页开始）


def test_scan_account_job_uses_account_scan():
    from hiro.cli.jobs import KINDS

    k = KINDS["scan-account"]
    argv = k.argv({}, Path("/tmp/job"))
    assert "hiro.daily.account_scan" in argv and k.output == "account"


def test_parse_profile_rapidocr_mixed_case():
    """G1 第 4 轮 11-26（Mac，Vision 坏了换 RapidOCR）：这一行读成「P-Id VrrL6gVW」，原来区分大小写认不出，30 秒超时。"""
    boxes = [(t, 1.0, b) if t != "P-ID VRRL6GVW" else ("P-Id VrrL6gVW", 1.0, b) for t, _, b in PROFILE_BOXES]
    assert account_scan.parse_profile(boxes)["producer_id"] == "VRRL6GVW"


def test_parse_profile_ignores_pid_inside_other_text():
    """#177 review：名字等其他文字里带「pid」（不分大小写后会撞上）不能当成 P-ID 标签。"""
    noise = [("Rapid 12345678", 1.0, (40, 300, 300, 330))]
    assert account_scan.parse_profile(noise + list(PROFILE_BOXES))["producer_id"] == "VRRL6GVW"
    assert account_scan.parse_profile(noise) is None
