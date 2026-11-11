# 来源：digital-hiro tests/test_memory.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""回忆选择页详情框读取（回忆编成选择系统 §2）。夹具是上次实机扫描存下的截图（360x640，放大到 720x1280 再读），真值按截图目视。"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision.memory import merge_detail, parse_stat, read_memory_detail

FIX = Path(__file__).with_name("fixtures") / "memory"


def _read(name: str) -> dict:
    img = cv2.resize(cv2.imread(str(FIX / f"{name}.jpg")), (720, 1280), interpolation=cv2.INTER_CUBIC)
    return read_memory_detail(img)


@pytest.mark.lowres_sample("回忆详情框样例是 360x640 截图放大，RapidOCR 把「獲得」读成「獅得 / 幾得」、能力框错字；DMM 回忆页待编成库存扫描取实机帧补进回归集")
@pytest.mark.parametrize(
    ("name", "timing", "card_id", "card_name", "upgrade", "plan", "customized"),
    [("memscan-0-140-647", "mid", "p_card-01-men-2_099", "一発勝負", 1, "sense", False),
     ("memscan-0-140-787", "mid", None, "翔び立て！", 1, "anomaly", True),  # 定制：卡图标中下方绿色「1」
     ("memscan-0-360-647", "start", None, "タフネス", 1, "anomaly", False)],
)  # fmt: skip
def test_card_page(name, timing, card_id, card_name, upgrade, plan, customized):
    d = _read(name)
    assert d["page"] == 1
    assert (d["timing"], d["card"]["name"], d["card"]["upgrade"], d["plan"], d["customized"]) == (timing, card_name, upgrade, plan, customized)
    if card_id:
        assert d["card"]["card_id"] == card_id
    assert d["card_text"]


@pytest.mark.lowres_sample("回忆详情框样例是 360x640 截图放大，RapidOCR 把「獲得」读成「獅得 / 幾得」、能力框错字；DMM 回忆页待编成库存扫描取实机帧补进回归集")
def test_ability_page_with_hif_ability():
    d = _read("memscan2-0-140-647")
    assert d["page"] == 2 and d["ability"].startswith("試験ごとに、以降1回まで、ペース配分使用後") and d["ability_truncated"] is False
    assert [(s["type"], s["attr"], s["value"], s["active"]) for s in d["stats"]] == [
        ("initial_param", "Vo", 20, True), ("passive", "Vi", 4, True), ("initial_param", "Da", 10, True)]
    passive = d["stats"][1]
    assert (passive["trigger"], passive["chance_pct"], passive["effect"]) == ("ビジュアルレッスン終了時", 45, "体力回復")


@pytest.mark.lowres_sample("回忆详情框样例是 360x640 截图放大，RapidOCR 把「獲得」读成「獅得 / 幾得」、能力框错字；DMM 回忆页待编成库存扫描取实机帧补进回归集")
def test_ability_page_old_memory_inactive_tiles():
    """旧回忆没有 H.I.F 能力框，词条从顶部开始；灰底「発動不可」（【センス専用】…）在 H.I.F 里不触发。"""
    d = _read("memscan2-4-360-920")
    assert d["ability"] is None
    got = [(s["type"], s["attr"], s["value"], s["active"], s.get("plan")) for s in d["stats"]]
    assert got == [("param_bonus_pct", "Vo", 3.5, True, None), ("passive", None, 2, False, "sense"), ("passive", None, 2, False, "sense"),
                   ("initial_param", "Da", 15, True, None), ("initial_param", "Vi", 15, True, None)]  # fmt: skip


@pytest.mark.lowres_sample("回忆详情框样例是 360x640 截图放大，RapidOCR 把「獲得」读成「獅得 / 幾得」、能力框错字；DMM 回忆页待编成库存扫描取实机帧补进回归集")
def test_ability_page_truncated_tiles():
    d = _read("memscan2-6-360-787")
    assert [(s["type"], s["active"], s["truncated"]) for s in d["stats"]] == [
        ("passive", False, True), ("produce_point", True, False), ("passive", False, True), ("param_bonus_pct", True, False), ("passive", True, False)]


@pytest.mark.lowres_sample("回忆详情框样例是 360x640 截图放大，RapidOCR 把「獲得」读成「獅得 / 幾得」、能力框错字；DMM 回忆页待编成库存扫描取实机帧补进回归集")
def test_merge_detail():
    rec = merge_detail(_read("memscan-0-140-647"), _read("memscan2-0-140-647"))
    assert rec["card"]["name"] == "一発勝負" and rec["timing"] == "mid" and len(rec["stats"]) == 3 and rec["ability"]


@pytest.mark.parametrize(
    ("text", "want"),
    [("初期ボーカル上昇+20", {"type": "initial_param", "attr": "Vo", "value": 20}),
     ("ダンスパラメータポーナス+2.8%", {"type": "param_bonus_pct", "attr": "Da", "value": 2.8}),
     ("ビジュアルバラメータボーナス+2.1%", {"type": "param_bonus_pct", "attr": "Vi", "value": 2.1}),
     ("初期Pポイント+40", {"type": "produce_point", "attr": None, "value": 40}),
     ("【ロジック専用】1次オーディション開始時、65%の確率で好印象+3", {"type": "passive", "plan": "logic", "chance_pct": 65, "value": 3})],
)  # fmt: skip
def test_parse_stat(text, want):
    got = parse_stat(text)
    assert {k: got.get(k) for k in want} == want
