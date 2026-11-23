"""D43 第三十三局本战：run_hif resume --trace <同名> 续跑。
- TraceWriter 以前 seq 从 1 重编，覆盖前几段的 frames/000NN.jpg、run.json；现在接着已有的编号往下写，run.json 记 segments；
- 续跑停在相談（不读头部 P / 体力），state.produce 全空，决策只能不买；现在用之前段最后一个有 produce 的状态打底（来源 infer，画面读到就覆盖）。
回放：前一段最后 3 步（seq 66–71，P 380、体力 13/33）+ 续跑这一段相談之前的几步（seq 1–7，produce 为空）。"""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace as NS

from hiro.contracts import TraceWriter

import pytest

FIX = Path(__file__).parent / "data" / "g33-main-resume-steps.jsonl"  # 实机状态（trace 步骤文本），随仓库发布


@pytest.mark.synthetic  # 只用 tests/data 的实机状态文本，不读截图
def test_trace_writer_continues_existing_trace(tmp_path):
    tw = TraceWriter(root=tmp_path, name="g", meta={"command": "new"})
    for _ in range(3):
        tw.next_seq()
    (tw.dir / "steps.jsonl").write_text("".join(json.dumps({"seq": i}) + "\n" for i in (1, 2, 3)), encoding="utf-8")
    (tw.dir / "frames" / "00005.jpg").write_bytes(b"x")
    tw.close()
    tw2 = TraceWriter(root=tmp_path, name="g", meta={"command": "resume"})
    assert tw2.next_seq() == 6  # 帧文件 00005 也算，不覆盖
    run = json.loads((tw2.dir / "run.json").read_text(encoding="utf-8"))
    assert (
        run["command"] == "new" and run["segments"][0]["command"] == "resume" and run["segments"][0]["first_seq"] == 6
    )
    tw2.close()


def _strategy(tmp_path):
    pytest.importorskip("kaa")
    from hiro.loop.session import Session
    from hiro.produce.hif import HiroHifStrategy

    d = tmp_path / "lead-main33"
    d.mkdir()
    shutil.copy(FIX, d / "steps.jsonl")
    tw = TraceWriter(root=tmp_path, name="lead-main33")
    s = Session(tw, shoot=lambda: None, classify=lambda: ("CONSULT", "produce"), extract=lambda scene, img: ({}, 0.0))
    h = HiroHifStrategy.__new__(HiroHifStrategy)
    h.s = s
    return h, tw


@pytest.mark.synthetic  # 只用 tests/data 的实机状态文本，不读截图
def test_g33_resume_seeds_produce_from_previous_segment(tmp_path):
    h, tw = _strategy(tmp_path)
    assert tw.seq == 71  # 接着前面的最大序号
    assert h._prior_trace_dirs() == [tw.dir]
    assert not (h.s.state.produce or {}).get("produce_point")
    h._seed_produce_from_trace()
    p = h.s.state.produce
    assert (p["produce_point"], p["stamina"], p["max_stamina"]) == (380, 13, 33)
    assert h.s.state.sources["produce.produce_point"].origin == "infer"


@pytest.mark.synthetic  # 只用 tests/data 的实机状态文本，不读截图
def test_screen_reading_overrides_seed(tmp_path):
    from hiro.contracts import Observation
    from hiro.loop.reduce import reduce

    h, _ = _strategy(tmp_path)
    h._seed_produce_from_trace()
    obs = Observation.from_dict({"seq": 72, "t": 0.0, "page": {"name": "ACTION_SELECT", "area": "produce", "confidence": 1.0, "stable_frames": 3},
                                 "fields": {"produce_point": 255, "stamina": 20, "max_stamina": 33}, "source": "screen"})  # fmt: skip
    st = reduce(h.s.state, obs)
    assert st.produce["produce_point"] == 255 and st.produce["stamina"] == 20


@pytest.mark.synthetic  # 只用 tests/data 的实机状态文本，不读截图
def test_consult_rereads_when_p_missing(monkeypatch):
    pytest.importorskip("kaa")
    import hiro.produce.hif as hif
    from hiro.contracts import Action, ActionKind, Decision, GameState

    reads = []

    def observe(**k):
        reads.append(k.get("scene"))
        if len(reads) >= 2:
            sess.state = GameState(page_fields={"shelf": []}, produce={"produce_point": 380})
        return NS(fields={"shelf": [{"index": 0}]})

    sess = NS(state=GameState(page_fields={"shelf": []}, produce={}), observe=observe, record=lambda *a, **k: None,
              observe_complete=lambda *a, **k: observe(scene="CONSULT"), left_page=lambda *a: None)  # fmt: skip
    seen = []
    h = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    h.s = sess
    h.deciders = NS(
        decide=lambda page, st: (
            seen.append((st.produce or {}).get("produce_point"))
            or Decision(Action(ActionKind.SHOP_END), "结束", "rule", 0.9)
        )
    )
    h._consult_open_dialog = lambda d, done: d
    h._choose_dialog_card = None
    h._deck_size = lambda: None
    monkeypatch.setattr(hif, "sleep", lambda s: None)
    monkeypatch.setattr(
        "hiro.produce.shop.execute_shop", lambda *a, **k: NS(ok=True, error=None, page_after="ACTION_SELECT")
    )
    try:  # noqa: SIM105
        h.on_consult(None)
    except Exception:  # noqa: BLE001  ShopEnd 之后的收尾依赖实机，这里只看决策前有没有重读
        pass
    assert seen and seen[0] == 380 and len(reads) >= 2


def test_g33_consult_hud_reaches_produce_and_overrides_seed(tmp_path):
    """识屏 140621f：相談页也读头部体力 / P 点（只进 page_fields）；reduce 把它们当 ANY_PAGE_PRODUCE 写进 state.produce，盖过打底值。
    用第三十三局本战续跑后 seq 14 那帧（P 380、体力 13/33）。打底值先改成 999 / 1，确认是画面读数生效。"""
    from hiro.loop.reduce import reduce
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    import cv2

    frame = Path(__file__).parent / "fixtures" / "shop" / "consult-main33-00014.jpg"
    if not frame.exists():
        pytest.skip("缺样例帧 tests/fixtures/shop/consult-main33-00014.jpg")
    h, _ = _strategy(tmp_path)
    h._seed_produce_from_trace()
    h.s.state.produce.update(produce_point=999, stamina=1)
    raw, _ = extract("CONSULT", cv2.imread(str(frame)))
    obs = observation(72, 0.0, "CONSULT", raw)
    st = reduce(reduce(h.s.state, obs), obs)
    assert st.page.name == "CONSULT"
    assert (st.produce["produce_point"], st.produce["stamina"], st.produce["max_stamina"]) == (380, 13, 33)
    assert st.sources["produce.produce_point"].origin == "screen"
