"""第二十一局 06:01 差し入れ：饮料满，决策按模拟偏离社区默认（> 2 SE，source=sim）要丢 パワフル漢方、收 厳選初星ブレンド；
_drink_full_skip 只认 source=rule，把有效决策当成「认不出」停机。规则 / 模拟决策都应执行先丢再领。"""

from types import SimpleNamespace as NS

import hiro.produce.drink_swap as ds
import hiro.produce.hif as hif
from hiro.contracts import Action, ActionKind, Decision, GameState
from hiro.produce.hif import HifCore, UnhandledPage
from hiro.vision.ocr import Box, Text

import pytest

CANDS = [{"slot": 0, "drink_id": "pdrink_kampo", "name": "パワフル漢方ドリンク"}, {"slot": 1, "drink_id": "pdrink_blend", "name": "厳選初星ブレンド"},
         {"slot": 2, "drink_id": "pdrink_aojiru", "name": "初星スペシャル青汁"}]  # fmt: skip
HELD = [{"index": i, "drink_id": d, "name": n} for i, (d, n) in enumerate([("pdrink_kampo", "パワフル漢方ドリンク"), ("pdrink_blend", "厳選初星ブレンド"),
                                                                            ("pdrink_senburi", "センブリソーダ"), ("pdrink_aojiru", "初星スペシャル青汁")])]  # fmt: skip


def _st(monkeypatch, source):
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: [Text("Pドリンク所持上限", Box(350, 1170, 370, 1190))])
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None, click=lambda *a: None))
    monkeypatch.setattr(
        hif.dk if hasattr(hif, "dk") else __import__("hiro.brain.drinks", fromlist=["x"]),
        "best_incoming",
        lambda cands, held, stage, cap: CANDS[1],
    )
    st = HifCore.__new__(HifCore)
    st.s = NS(state=GameState(page_fields={"candidates": CANDS, "held": HELD}), observe=lambda **k: NS(fields={}), record=lambda *a, **k: None,
              recover=lambda *a: None)  # fmt: skip
    d = Decision(
        Action(ActionKind.ACTIVATE_EFFECT, {"receive": [1], "discard": [0]}),
        "丢 パワフル漢方、收 厳選初星ブレンド +8,020",
        source,
        0.8,
    )
    st.deciders = NS(decide=lambda page, state, **kw: d)
    swapped = []
    monkeypatch.setattr(
        ds,
        "discard_then_receive",
        lambda discard, slot, held=None: (swapped.append((discard, slot)), NS(ok=True, error=None))[1],
    )
    return st, swapped


@pytest.mark.parametrize("source", ["rule", "sim"])
def test_rule_and_sim_decisions_swap(monkeypatch, source):
    st, swapped = _st(monkeypatch, source)
    assert st._drink_full_skip() is True
    assert swapped == [(0, 1)]


def test_fallback_still_stops_when_switch_off(monkeypatch):
    monkeypatch.setenv("HIRO_DRINK_FULL_AUTO", "0")
    st, swapped = _st(monkeypatch, "fallback")
    st._stop = lambda page, why, **kw: (_ for _ in ()).throw(UnhandledPage(why))
    with pytest.raises(UnhandledPage):
        st._drink_full_skip()
    assert swapped == []


# ---------------------------------------------------------------- 开关 HIRO_DRINK_FULL_AUTO（协调 11-17，初 REGULAR 实机：LLM 结论同规则仍停机）
def _stopping(st):
    st._stop = lambda page, why, **kw: (_ for _ in ()).throw(UnhandledPage(why))


def test_llm_stops_when_switch_off(monkeypatch):
    """G1-F8（11-26）：停机原因要说清楚是「规则拿不准、LLM 的意见没执行」，不能写成「认不出」（开关关着时）。"""
    monkeypatch.setenv("HIRO_DRINK_FULL_AUTO", "0")
    st, swapped = _st(monkeypatch, "llm")
    _stopping(st)
    with pytest.raises(UnhandledPage) as e:
        st._drink_full_skip()
    assert swapped == []
    assert "认不出" not in str(e.value) and "HIRO_DRINK_FULL_AUTO" in str(e.value) and "丢 パワフル漢方" in str(e.value)


def test_llm_swaps_when_switch_on(monkeypatch):
    monkeypatch.setenv("HIRO_DRINK_FULL_AUTO", "1")
    st, swapped = _st(monkeypatch, "llm")
    _stopping(st)
    assert st._drink_full_skip() is True and swapped == [(0, 1)]


def test_unrecognized_skips_when_switch_on(monkeypatch):
    """开关开：决策认不出（fallback）就勾「受け取らない」→ 点按钮，不停机；随后的「報酬スキップ」由 on_pdrink_max_confirm 放行。"""
    monkeypatch.setenv("HIRO_DRINK_FULL_AUTO", "1")
    st, swapped = _st(monkeypatch, "fallback")
    _stopping(st)
    clicks, recorded, notes = [], [], []
    texts = [Text("Pドリンク所持上限", Box(350, 1170, 370, 1190)), Text("受け取らない", Box(290, 990, 430, 1022)),
             Text("受け取らない", Box(300, 1080, 420, 1114))]  # fmt: skip
    confirm = [Text("報酬スキップ", Box(300, 500, 420, 530)), Text("確認", Box(300, 900, 420, 930))]
    seq = iter([texts, texts, confirm])  # 进来时、找按钮时都是选饮料页；点完按钮出「報酬スキップ」确认
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: next(seq))
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None, click=lambda x, y: clicks.append((x, y))))
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    st.s.record = lambda obs, **k: recorded.append((k.get("decision"), k.get("execution")))
    st.s.note = notes.append
    assert st._drink_full_skip() is True
    assert swapped == [] and clicks == [(360, 1006), (360, 1097)] and st._skip_incoming_drink
    d, res = recorded[0]
    assert d.action.args == {"discard": [], "receive": False} and "默认跳过" in d.reason and notes and res.ok


@pytest.mark.parametrize(
    "shown",
    [
        [Text("Pドリンク所持上限", Box(350, 1170, 370, 1190))],  # 两个控件都没认出来
        [
            Text("Pドリンク所持上限", Box(350, 1170, 370, 1190)),
            Text("受け取らない", Box(290, 990, 430, 1022)),
            Text("受け取らない", Box(300, 1080, 420, 1114)),
        ],  # 点了画面也没变
    ],
)
def test_skip_not_confirmed_stops_instead_of_recording_ok(monkeypatch, shown):
    """#183 review：勾「受け取らない」后画面没变（或控件找不到），重试一次还不行就停下，不记成功、不放行后面的「報酬スキップ」。"""
    monkeypatch.setenv("HIRO_DRINK_FULL_AUTO", "1")
    st, _swapped = _st(monkeypatch, "fallback")
    _stopping(st)
    recorded = []
    monkeypatch.setattr(hif, "screen_texts", lambda *a, **k: shown)
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    st.s.record = lambda obs, **k: recorded.append(k.get("execution"))
    st.s.note = lambda *a: None
    clicks = []
    monkeypatch.setattr(hif, "device", NS(screenshot=lambda: None, click=lambda x, y: clicks.append((x, y))))
    with pytest.raises(UnhandledPage) as e:
        st._drink_full_skip()
    assert "受け取らない" in str(e.value) and "手动" in str(e.value)
    if len(shown) > 1:  # 勾选框只点一次（再点会取消勾选），重试只补点底部按钮
        assert clicks == [(360, 1006), (360, 1097), (360, 1097)]
        assert "画面没变" in str(e.value)
    else:
        assert clicks == [] and "勾选框" in str(e.value) and "底部按钮" in str(e.value)
    assert recorded and recorded[-1].ok is False and recorded[-1].retries == 1
    assert not getattr(st, "_skip_incoming_drink", False)


def test_skip_done_needs_evidence():
    """#183 review：空读数不算点成了；出了「報酬スキップ」或读到别的页面（没有「所持上限」）才算。"""
    t = lambda s: Text(s, Box(0, 0, 10, 10))  # noqa: E731
    assert hif._drink_skip_done([]) is False
    assert hif._drink_skip_done([t(" ")]) is False
    assert hif._drink_skip_done([t("Pドリンク所持上限")]) is False
    assert hif._drink_skip_done([t("Pドリンク所持上限"), t("報酬スキップ")]) is True
    assert hif._drink_skip_done([t("レッスン")]) is True


def test_switch_on_by_default(monkeypatch):
    """用户 11-26 21:4x 定：HIRO_DRINK_FULL_AUTO 缺省开，不设也照 LLM 换饮料；=0 才关。"""
    monkeypatch.delenv("HIRO_DRINK_FULL_AUTO", raising=False)
    assert hif.drink_full_auto() is True
    monkeypatch.setenv("HIRO_DRINK_FULL_AUTO", "0")
    assert hif.drink_full_auto() is False
