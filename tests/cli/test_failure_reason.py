"""任务出错时把原因带进 failed 事件（11-25 review：通知说「原因在最后几步里」，可原因只在日志事件里，22 个出错任务一个都看不到）。"""

import json

from hiro.cli.jobs import failure_reason

import pytest


def test_stop_line_wins_and_drops_screenshot_path():
    lines = [
        "[2026-11-24 16:15:17,792] [INFO] [hiro.produce.formation_writer] 编成修正：支援卡第 1 格",
        "停下：没打开支援卡选择列表（截图在 reports/live/calib/20261124-161509-编成菜单）",
        "[2026-11-24 16:15:40,000] [INFO] [x] 收尾",
    ]
    assert failure_reason(lines) == "没能打开支援卡选择列表，游戏可能不在编成页面"


def test_last_error_line_without_log_prefix():
    lines = [
        "[2026-11-23 12:42:18,555] [ERROR] [hiro.runtime.loop] 判页 / 处理器抛了异常（连续第 2 次，到 10 次停机）",
        "[2026-11-23 12:51:48,730] [WARNING] [hiro.loop.landscape] 横屏 LIVE 黑屏 20s",
        "[2026-11-23 12:51:48,735] [ERROR] [__main__] LIVE 页面和画面 181s 没变化，停机",
        "LIVE 页面和画面 181s 没变化，停机",
    ]
    assert failure_reason(lines) == "LIVE 画面 181 秒没变化，任务已停下"


def test_long_reason_is_cut_and_none_when_nothing():
    r = failure_reason(["[2026-11-23 12:00:00,000] [ERROR] [m] " + "很长" * 200])
    assert r is not None and len(r) <= 160 and r.endswith("…")
    assert failure_reason(["[2026-11-23 12:00:00,000] [INFO] [m] 一切正常"]) is None


# ---- 11-26：出错原因说白话（PR #79 CodeRabbit R6）：卡号换卡名、程序原话换玩家看得懂的 ----


def _catalog(home):
    d = home / "catalog"
    d.mkdir(parents=True, exist_ok=True)
    cat = {
        "cards": {"p_card-01-ido-3_105": {"name": "エウレカ！"}},
        "items": {},
        "drinks": {},
        "enchants": {},
        "memories": {},
        "idols": {"i_card-shro-3-007": {"name": "【Campus mode!!】篠澤広"}},
    }
    zh = json.loads(json.dumps(cat))
    zh["cards"]["p_card-01-ido-3_105"]["name"] = "尤里卡！"
    (d / "catalog.ja.json").write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    (d / "catalog.zh.json").write_text(json.dumps(zh, ensure_ascii=False), encoding="utf-8")
    sup = {
        "format": "gkbench.supports.v1",
        "cards": {
            "s_card-3-0027": {"name": {"ja": "あなたにも作ってあげる！", "zh": "我也会做给你的！"}},
            "s_card-3-0028": {"name": {"ja": "日文名", "zh": ""}},
        },
    }
    (d / "supports.json").write_text(json.dumps(sup, ensure_ascii=False), encoding="utf-8")


def _why(text):
    return failure_reason([f"停下：{text}"])


def test_card_ids_become_names(hiro_home):
    _catalog(hiro_home)
    assert _why("点了 s_card-3-0027 但列表里没显示为选中（截图在 reports/x）") == (
        "点了「我也会做给你的！」但游戏列表里没显示为选中，可能没点准，回编成页看看"
    )
    assert _why("编成改完是 p_card-01-ido-3_105，不是 i_card-skin-shro-3-007") == (
        "编成改完是「尤里卡！」，不是「【Campus mode!!】篠澤広」"
    )
    # 中文名为空退回日文名；资料库里查不到的编号原样留着（不瞎编）
    assert _why("支援卡第 2 格改完是 s_card-3-0028，不是 s_card-9-9999") == (
        "支援卡第 2 格改完是「日文名」，不是 s_card-9-9999"
    )


def test_card_ids_stay_when_catalog_not_downloaded(hiro_home):
    assert _why("支援卡第 1 格改完是 s_card-3-0027，不是 s_card-3-0028") == (
        "支援卡第 1 格改完是 s_card-3-0027，不是 s_card-3-0028"
    )


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        (
            "new 开局要编成号：--support-set / --memory-set，或开局编成方案",
            "开局没有定用游戏里的哪一套编成：先给方案选好支援卡和回忆的编成号，或开局时指定编成号",
        ),
        (
            "new 开局要编成号：设 HIRO_SUPPORT_SET / HIRO_MEMORY_SET，或先生成开局编成方案",
            "开局没有定用游戏里的哪一套编成：先给方案选好支援卡和回忆的编成号，或开局时指定编成号",
        ),
        ("没打开支援卡选择列表", "没能打开支援卡选择列表，游戏可能不在编成页面"),
        (
            "支援卡选择列表里没找到 s_card-3-0027（库存里没有，或认不出）",
            "支援卡列表里没找到「我也会做给你的！」：账号里可能没有这张卡，或没认出来，可以重扫支援卡再试",
        ),
        ("翻了 6 轮仍没到 3 号编成", "翻了 6 轮还是没翻到第 3 号编成"),
        ("编成号读不出来（8s）", "没读出当前是第几号编成"),
        ("支援卡编成和方案对不上，不开局：第 1 格是 A", "游戏里的支援卡编成和编成方案对不上，没有开局：第 1 格是 A"),
        ("回忆编成和方案对不上，不开局：第 2 格是 B", "游戏里的回忆编成和编成方案对不上，没有开局：第 2 格是 B"),
        ("设备出错，停机：OSError: adb died", "和游戏设备的连接出了问题，任务已停下，看看游戏或模拟器还开着没有"),
        ("LIVE 页面和画面 181s 没变化，停机", "LIVE 画面 181 秒没变化，任务已停下"),
        ("判页 / 处理器抛了异常（连续第 2 次，到 10 次停机）", "程序处理页面连续出错 2 次，任务已停下"),
        ("未接管的页面 / 钩子：X：Y。已存帧，停止运行", "遇到程序还不会处理的页面，已存下画面并停下"),
        ("编成格子不够放方案的卡：缺 [1, 2]，可换的格子 [0]", "游戏编成里能换的格子不够放下方案里的卡"),
    ],
)
def test_common_reasons_in_plain_words(hiro_home, raw, plain):
    _catalog(hiro_home)
    assert _why(raw) == plain


def test_error_line_is_also_plain_and_unknown_text_untouched(hiro_home):
    _catalog(hiro_home)
    assert (
        failure_reason(["[2026-11-24 01:31:04,918] [ERROR] [hiro.runtime.play] new 开局要编成号：--support-set"])
        == "开局没有定用游戏里的哪一套编成：先给方案选好支援卡和回忆的编成号，或开局时指定编成号"
    )
    assert _why("某个没见过的原因 abc") == "某个没见过的原因 abc"


def test_failed_event_carries_plain_reason(hiro_home):
    """jobs.Job 的 failed 事件和 reason 字段都走 failure_reason：这里直接确认出口是白话。"""
    _catalog(hiro_home)
    r = failure_reason(["停下：点了 s_card-3-0027 但列表里没显示为选中"])
    assert r is not None and "s_card" not in r


def test_crash_after_recovered_error_is_the_reason():
    """N40（G2 11-26）：先有一条已经恢复的 ERROR（Vision 坏了改用 RapidOCR），后面程序崩了：原因是崩溃，不是那条 ERROR。"""
    lines = [
        "[2026-11-26 18:59:53,100] [ERROR] [hiro.vision.ocr] 文字识别：Apple Vision 连续 3 次失败（e5rtError），这个进程改用 RapidOCR",
        "Traceback (most recent call last):",
        '  File "tools/support_inventory.py", line 133, in list_scan',
        '    d.tap(*r["controls"]["next"])',
        "TypeError: 'NoneType' object is not subscriptable",
    ]
    why = failure_reason(lines)
    assert why is not None and "Vision" not in why
    # ERROR 在崩溃之后：还是取后面那条
    assert "后来的错" in failure_reason(lines + ["[2026-11-26 19:00:00,000] [ERROR] [m] 后来的错"])


def test_center_dmm_window_only_for_dmm(monkeypatch):
    """任务结束把 DMM 窗口放回屏幕中间（11-25）：只在 HIRO_DEVICE=dmm 时调 win_window.center，出错不抛。"""
    import sys

    from hiro.cli import jobs
    from hiro.devices import win_window

    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(win_window, "center", lambda *a, **k: calls.append(1) or (0, 0))
    monkeypatch.setenv("HIRO_DEVICE", "mumu")
    jobs.center_dmm_window()
    assert calls == []
    monkeypatch.setenv("HIRO_DEVICE", "dmm")
    jobs.center_dmm_window()
    assert calls == [1]

    def boom(*a, **k):
        raise OSError("拒绝访问")

    monkeypatch.setattr(win_window, "center", boom)
    jobs.center_dmm_window()  # 不抛
