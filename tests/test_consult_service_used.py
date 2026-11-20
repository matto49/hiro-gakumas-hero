"""相談的强化 / 删除服务用过之后按钮上写「交換済」（协调 11-18：3940a9 的 8941550 一次相談删过一张就结束，续跑时决策会丢记账，
以画面为准）→ services.<服务>.used。实机帧（初 PRO 相談删卡前后）在私有 fixtures pages/consult-delete-*.jpg。"""

from pathlib import Path

import cv2
import pytest

FIXP = Path(__file__).with_name("fixtures") / "pages"


def _services(name):
    p = FIXP / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    from hiro.vision.shop import read_consult

    return read_consult(cv2.imread(str(p)))["services"]


def test_delete_used_after_delete():
    s = _services("consult-delete-used.jpg")
    assert s["delete"]["used"] is True and s["delete"]["price"] == 125  # 价格换成下一次的「次 125」
    assert s["upgrade"]["used"] is False


def test_nothing_used_before():
    s = _services("consult-delete-unused.jpg")
    assert s["delete"]["used"] is False and s["upgrade"]["used"] is False and s["delete"]["price"] == 100


def test_used_reaches_page_fields():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    p = FIXP / "consult-delete-used.jpg"
    if not p.exists():
        pytest.skip("需要实机截图 pages/consult-delete-used.jpg")
    raw, _ = extract("CONSULT", cv2.imread(str(p)))
    assert observation(1, 0.0, "CONSULT", raw).fields["services"]["delete"]["used"] is True
