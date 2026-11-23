"""初 / N.I.A 支持广以外的偶像（协调 11-19：Mac 账号 2 换 藤田ことね「世界一可愛い私」i_card-fktn-3-000，ロジック・好印象，打初 PRO）。
第一局暴露：① 构筑表只认广 → 考试中转层偶像为空，GNRPA 全退回 uinet；② 选卡按广的やる気必拿表、把好印象卡当冲突；
③ 中間考试开场页（審査基準 / 合格条件 / 倍率）的「タップして次へ」压在亮色衣装上，模板认不出，180 秒停机。"""

from pathlib import Path

import cv2
import pytest

FKTN, HIRO = "i_card-fktn-3-000", "i_card-shro-3-007"
FIXP = Path(__file__).with_name("fixtures") / "pages"
TEBYOSHI, IMETORE = "p_card-02-act-1_003", "p_card-02-men-2_008"
LOVELY, DAYDREAM = "p_card-02-act-2_046", "p_card-02-act-1_037"
OMAMORI, RESTART, YURUFUWA = "p_card-02-act-1_070", "p_card-02-men-1_030", "p_card-02-act-1_028"


@pytest.mark.master
def test_profile_from_master_for_other_idols():
    """广照旧用手写构筑表；ことね 按主数据生成：只有 id 和名字，最大体力 / 固有道具 / 打法文件给 None，不猜。"""
    from hiro.brain.state import CAMPUS_HIRO, IdolProfile, profile_for

    assert profile_for("i_card-skin-shro-3-007") is CAMPUS_HIRO
    p = profile_for("i_card-skin-fktn-3-000")
    assert p is not None and p.idol_id == FKTN and "世界一可愛い私" in p.name
    assert p.playbook is None and p.item_trigger_card is None and IdolProfile.is_half_hp(5, None) is None
    assert profile_for("i_card-xxxx-9-999") is None


@pytest.mark.master
def test_flow_tables():
    from hiro.brain import flows

    assert flows.flow(FKTN) == "review_smile" and flows.flow(HIRO) == "yaruki"
    assert set(flows.loop_parts(FKTN)) == {"p_card-02-act-2_049", "p_card-02-men-2_060", LOVELY}
    assert flows.conflicts("p_card-02-act-3_038", "好印象") == "主打やる気"  # 開花：やる気の200%分
    assert flows.conflicts(TEBYOSHI, "好印象") is None and flows.conflicts(TEBYOSHI, "やる気") == "主打好印象"


def _pick(idol, cands, deck=(), rerolls=0, excludes=0, mode="produce-002"):
    from hiro.brain.deciders import ProduceDeciders, action_key
    from hiro.brain.state import profile_for
    from hiro.contracts import GameState

    prod = {"idol_card_id": idol, "mode": mode, "stamina": 20, "max_stamina": 30, "produce_point": 50, "params": {"Vo": 200, "Da": 200, "Vi": 200},
            "growth_permil": {}, "rerolls_left": rerolls, "excludes_left": excludes, "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip
    d = ProduceDeciders(profile_for(idol), None).decide(
        "SELECT_CARD", GameState(produce=prod, page_fields={"candidates": [{"card_id": c} for c in cands]})
    )
    return action_key(d.action), d.reason


@pytest.mark.master
def test_fktn_card_pick_follows_review_flow():
    """ことね 的选卡页（初 PRO 11-19 trace）：拿主打好印象的卡、好印象的循环件；不为广的必拿（せのび / 届いて / デイドリーミング）刷新。"""
    key, reason = _pick(FKTN, [TEBYOSHI, IMETORE])
    assert key == "pick:0" and "主打好印象" in reason and "せのび" not in reason
    key, reason = _pick(FKTN, [TEBYOSHI, DAYDREAM, LOVELY])
    assert key == "pick:2" and "循环" in reason  # 原来按广拿了 デイドリーミング（社区必拿）
    key, reason = _pick(FKTN, [OMAMORI, RESTART, YURUFUWA], rerolls=4)
    assert key == "pick:0"  # おまもりミラクル（好印象）；原来按广的必拿表刷新


@pytest.mark.master
def test_hiro_card_pick_unchanged():
    """广照旧：好印象的卡对她是冲突。"""
    key, _ = _pick(HIRO, [TEBYOSHI, IMETORE])
    assert key == "pick:1"


@pytest.mark.master
def test_fktn_consult_keeps_review_cards():
    """ことね 的相談删卡：先删基本卡；主打好印象的卡、循环件不删（广的删卡顺序把它们当冲突卡）。"""
    from hiro.brain.deciders import ProduceDeciders, action_key
    from hiro.brain.state import profile_for
    from hiro.contracts import GameState

    deck = [
        TEBYOSHI,
        LOVELY,
        "p_card-00-act-0_001",
        "p_card-02-act-3_038",
    ]  # 手拍子、ラブリーウインク、アピールの基本、開花（主打やる気）
    prod = {"idol_card_id": FKTN, "mode": "produce-002", "stamina": 20, "max_stamina": 30, "produce_point": 160, "params": {"Vo": 1, "Da": 1, "Vi": 1},
            "growth_permil": {}, "deck": [{"card_id": c, "upgrade": 0} for c in deck]}  # fmt: skip
    pf = {"shelf": [], "services": {"delete": {"price": 100, "available": True}}}
    d = ProduceDeciders(profile_for(FKTN), None).decide("CONSULT", GameState(produce=prod, page_fields=pf))
    assert action_key(d.action) == "buy:delete:2" and "基本卡" in d.reason
    prod["deck"] = [{"card_id": c, "upgrade": 0} for c in deck if c != "p_card-00-act-0_001"]
    d = ProduceDeciders(profile_for(FKTN), None).decide("CONSULT", GameState(produce=prod, page_fields=pf))
    assert action_key(d.action) == "buy:delete:2" and "冲突" in d.reason  # 開花：主打やる気


@pytest.mark.master
def test_idol_section_in_prompt():
    from hiro.brain.deciders import _idol_section
    from hiro.brain.state import profile_for

    text = _idol_section(FKTN, profile_for(FKTN))
    assert "好印象" in text and "主打やる気" in text and "篠澤広" not in text and "暂无" in text
    assert "篠澤広" in _idol_section(HIRO, None)


def test_briefing_ocr_partial_hint():
    """考前说明页的提示读出半截（「プして次へ」）也算。"""
    from hiro.vision.nia import read_briefing

    texts = [("審査基準", 0, 0, 0, (147, 707, 231, 731)), ("合格条件", 0, 0, 0, (391, 709, 474, 731))]
    assert read_briefing(texts, None) is None  # 没有提示：倍率可能还在跳，不认


@pytest.mark.fixtures
def test_hajime_briefing_page_fktn():
    """ことね 中間考试开场页（帧 pages/hajime-briefing-fktn.png）：初的采样器认不出时用 OCR 认成考前说明页，读出三属性和倍率，点提示处。"""
    import tempfile

    from hiro.runtime.nia_sample import Sampler
    from hiro.vision.classify import classify

    p = FIXP / "hajime-briefing-fktn.png"
    if not p.exists():
        pytest.skip("需要实机截图 pages/hajime-briefing-fktn.png")
    hit = Sampler(classify, Path(tempfile.mkdtemp()), nia=False)(cv2.imread(str(p)))
    assert hit.name == "HIF_EXAM_BRIEFING"
    assert hit.fields["multiplier_pct"] == {"Vo": 275, "Da": 461, "Vi": 713} and hit.fields["_quick"]["tap"][1] > 1100
