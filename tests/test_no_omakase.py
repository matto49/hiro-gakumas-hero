# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）
"""支援卡和回忆默认不用「おまかせ」（用户 2026-11-02）：开源版要求写编成号，确实想用おまかせ写 auto。"""

from types import SimpleNamespace as NS

from hiro import formation_env

import pytest


def _reload_env(monkeypatch, **env):
    import importlib

    for k in ("HIRO_SUPPORT_SET", "HIRO_MEMORY_SET", "HIRO_MEMORY_CARDS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HIRO_FORMATION_PLAN", "off")  # 不读本机 conf/formation-plan.json（实机生成的方案会补上编成号）
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(formation_env)


def test_formation_env_reads_sets_from_env(monkeypatch):
    env = _reload_env(monkeypatch, HIRO_SUPPORT_SET="4", HIRO_MEMORY_SET="5", HIRO_MEMORY_CARDS="a, b,")
    assert (env.SUPPORT_SET, env.MEMORY_SET, env.MEMORY_CARDS) == (4, 5, ("a", "b"))
    env = _reload_env(monkeypatch, HIRO_SUPPORT_SET="auto")
    assert env.SUPPORT_SET == env.AUTO and env.MEMORY_SET is None and env.MEMORY_CARDS == ()
    with pytest.raises(ValueError):
        _reload_env(monkeypatch, HIRO_SUPPORT_SET="四")
    _reload_env(monkeypatch)


def _fake_templates(monkeypatch, next_enabled: bool):
    """第 2 步页面：步骤条在；「次へ」亮不亮由 next_enabled 定。"""
    import hiro.vision.ui_templates as ui

    monkeypatch.setattr(ui, "tpl", lambda name: NS(exists=lambda img, **kw: True))
    monkeypatch.setattr(ui, "find_enabled", lambda name, img: object() if next_enabled else None)


def test_best_row_picks_highest_level_da():
    from hiro.produce.rental import best_row

    rows = [{"card": {"attr": "Vo", "level": 60, "breaks": 4}}, {"card": {"attr": "Da", "level": 50, "breaks": 4}},
            {"card": {"attr": "Da", "level": 60, "breaks": 3}}, {"card": None, "covered": True}]  # fmt: skip
    assert best_row(rows)["card"]["level"] == 60 and best_row(rows)["card"]["attr"] == "Da"
    assert best_row([{"card": {"attr": "Vo", "level": 60}}]) is None


def test_best_row_excludes_plan_mismatch():
    from hiro.produce.rental import best_row

    rows = [{"card": {"attr": "Da", "level": 60, "breaks": 4, "name": "なぜこんなところにッ！？"}},
            {"card": {"attr": "Da", "level": 60, "breaks": 4, "name": "いつまでも続けばいいのに"}}]  # fmt: skip
    assert best_row(rows, exclude={"なぜこんなところにッ！？"})["card"]["name"] == "いつまでも続けばいいのに"


@pytest.mark.lowres_sample(
    "租借页样例 step2-rental-mismatch 是 540x960 截图；DMM 租借页待编成库存扫描（任务单第 6 步）取实机帧补进回归集"
)
def test_rental_mismatch_reads_real_frame(monkeypatch):
    """第十七局实机帧：租借格（右下）标「プラン不一致」。"""
    from pathlib import Path

    from hiro.produce import rental

    import cv2

    img = cv2.resize(cv2.imread(str(Path(__file__).parent / "fixtures/support/step2-rental-mismatch.png")), (720, 1280))
    monkeypatch.setattr(rental, "device", NS(screenshot=lambda: img))
    assert rental.rental_mismatch()
