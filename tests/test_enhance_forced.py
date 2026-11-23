"""D42（总控 2026-11-04，第三十三局本战 18:45 #81）：P 道具触发的免费强化页是必选的（画面只有「強化」，没有キャンセル）。
核心卡都已强化时原来给 cancel（执行不了）；page_fields.cancelable=False 时改为退选：非核心里价值最高的未强化卡 → 基础卡 → 冲突卡。
夹具 tests/data/q33_enhance_forced.json（page_fields 文本，不是截图，随仓库发布）：#81 的 20 张卡，未强化的只有 元気な挨拶 / ゆるふわおしゃべり / イメトレ / ひなたぼっこ。"""

import json
from pathlib import Path

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import ActionKind, GameState

import pytest

pytestmark = pytest.mark.master  # 退选要按主数据判断卡能不能强化

FIX = json.loads((Path(__file__).parent / "data" / "q33_enhance_forced.json").read_text(encoding="utf-8"))


def _state(**extra):
    return GameState(page_fields={**FIX["page_fields"], **extra}, produce=FIX["produce"])


def decide(**extra):
    return ProduceDeciders(CAMPUS_HIRO, None).decide("SKILL_CARD_ENHANCE", _state(**extra))


def test_unknown_cancelable_keeps_cancel():
    d = decide()  # 识别没给 cancelable：照旧（可取消的 相談 强化页）
    assert d.action.args.get("cancel") is True


def test_cancelable_true_cancels():
    assert decide(cancelable=True).action.args.get("cancel") is True


def test_forced_picks_non_core_never_cancel():
    for extra in ({"cancelable": False}, {"trigger": "pitem"}):
        d = decide(**extra)
        assert d.action.kind is ActionKind.ENHANCE_CARD and not d.action.args.get("cancel"), d.reason
        assert d.action.args["card_id"] in {
            "p_card-02-act-1_004",
            "p_card-02-act-1_028",
            "p_card-02-men-2_008",
            "p_card-02-men-2_052",
        }
        assert "必选强化页" in d.reason


def test_forced_without_cards_waits():
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(
        "SKILL_CARD_ENHANCE", GameState(page_fields={"cards": [], "cancelable": False}, produce=FIX["produce"])
    )
    assert d.action.kind is ActionKind.WAIT
