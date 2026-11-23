"""N.I.A 特有页面（hiro.vision.nia 识别 + hiro.produce.nia 执行）：営業场所 / 営業话题 / 试镜选档，实机帧在私有 fixtures pages/nia-*。"""

from pathlib import Path
from types import SimpleNamespace as NS

from hiro.contracts import Action, ActionKind, Decision
from hiro.runtime.loop import DONE, STAY

import cv2
import numpy as np
import pytest

FIX = Path(__file__).with_name("fixtures") / "pages"


def _img(name):
    p = FIX / name
    if not p.exists():
        pytest.skip(f"需要实机截图 pages/{name}")
    return cv2.imread(str(p))


def _read(name):
    from hiro.vision.nia import read_nia_page
    from hiro.vision.ocr import screen_texts

    img = _img(name)
    return read_nia_page(screen_texts(img), img)


def test_sales_place_reading():
    name, f = _read("nia-sales-place.png")
    assert name == "NIA_SALES_PLACE" and not f["expanded"]
    assert [(p["kind"], p["gain"], p["hp_cost"]) for p in f["places"]] == [
        ("local", 2400, 5),
        ("corporate", 2400, 0),
        ("commercial", 2400, 5),
    ]
    name, f = _read("nia-sales-place-expanded.png")
    assert name == "NIA_SALES_PLACE" and f["expanded"] and f["selected"] == 2 and f["start"][1] > 980


def test_sales_event_reading():
    name, f = _read("nia-sales-event.png")
    assert name == "NIA_SALES_EVENT"
    assert [(c["text"], c["attr"]) for c in f["choices"]] == [
        ("学生時代の話", "Vo"),
        ("現役アイドルの仕事の話", "Da"),
        ("アドバイスをもらう", "Vi"),
    ]


def test_audition_select_reading():
    name, f = _read("nia-audition-select-1.png")
    assert name == "NIA_AUDITION_SELECT" and f["votes"] == 4680 and not f["expanded"] and f["stage"] == "mid1"
    assert [(t["vote_need"], t["number"]) for t in f["tiers"]] == [(9000, 3), (4000, 2), (0, 1)]
    name, f = _read("nia-audition-select-1-chosen.png")
    assert f["expanded"] and f["chosen_need"] == 4000 and abs(f["start"][1] - 889) < 15


def test_other_pages_not_nia():
    from hiro.vision.nia import read_nia_page
    from hiro.vision.ocr import screen_texts

    for n in ("nia-action-sales.png", "nia-consult-banner.jpg", "nia-live-setting.png"):
        img = _img(n)
        assert read_nia_page(screen_texts(img), img) is None, n


def _core(select, number=None):
    rec = []
    args = {"select": select} | ({"audition_number": number} if number else {})
    d = Decision(Action(ActionKind.EVENT_SELECT, args), "测试", "rule", 0.7)
    state = NS(page=NS(name=None))

    def observe(**kw):
        rec.append(kw["scene"])
        state.page.name = kw["scene"]  # 假 Session：一观测就切到这一页（真的 Session 有防抖，见下面的端到端用例）
        return kw

    s = NS(state=state, observe=observe, record=lambda *a, **k: None)
    return NS(s=s, deciders=NS(decide=lambda page, st: d), nia_audition_number=None), rec


def _rt():
    now, taps = [0.0], []
    return NS(device=NS(tap=lambda x, y: taps.append((x, y))), clock=lambda: now[0]), now, taps


PLACES = [{"index": i, "kind": k, "name": k, "gain": 2400, "hp_cost": c, "center": [360, 740 + 138 * i]}
          for i, (k, c) in enumerate((("local", 5), ("corporate", 0), ("commercial", 5)))]  # fmt: skip


def test_sales_place_two_steps():
    """决策选企業（index 1）：先点卡展开；展开的是它就点「営業開始」；展开的不是它就重点。"""
    from hiro.produce.nia import ACT_WAIT_S, NiaPages

    core, rec = _core(1)
    p = NiaPages(core)
    rt, now, taps = _rt()
    assert p.on_sales_place(rt, None, NS(fields={"places": PLACES, "expanded": False})) is DONE and taps == [(360, 878)]
    assert p.on_sales_place(rt, None, NS(fields={"places": PLACES, "expanded": False})) is STAY  # 动画中不连点
    now[0] += ACT_WAIT_S
    wrong = {"places": PLACES, "expanded": True, "selected": 2, "start": [507, 1013]}
    assert p.on_sales_place(rt, None, NS(fields=wrong)) is DONE and taps[-1] == (360, 878)  # 展开的不是它：重点
    now[0] += ACT_WAIT_S
    ok = {"places": PLACES, "expanded": True, "selected": 1, "start": [507, 1013]}
    assert p.on_sales_place(rt, None, NS(fields=ok)) is DONE and taps[-1] == (507, 1013)
    assert rec == ["NIA_SALES_PLACE"]  # 只决策一次


def test_audition_two_steps_sets_audition_number():
    from hiro.produce.nia import ACT_WAIT_S, NiaPages

    tiers = [{"index": i, "number": n, "vote_need": v, "locked": False, "center": [360, 774 + 117 * i]}
             for i, (n, v) in enumerate(((3, 9000), (2, 4000), (1, 0)))]  # fmt: skip
    core, _ = _core(1, number=2)
    p = NiaPages(core)
    rt, now, taps = _rt()
    assert p.on_audition_select(
        rt, None, NS(fields={"tiers": tiers, "expanded": False, "stage": "mid2"})
    ) is DONE and taps == [(360, 891)]
    assert core.nia_audition_number == 2 and core.nia_stage == "mid2"
    now[0] += ACT_WAIT_S
    chosen = {"tiers": tiers[1:], "expanded": True, "chosen_need": 4000, "start": [360, 889]}
    assert p.on_audition_select(rt, None, NS(fields=chosen)) is DONE and taps[-1] == (360, 889)


def test_sales_event_taps_choice():
    from hiro.produce.nia import NiaPages

    core, _ = _core(2)
    rt, _, taps = _rt()
    choices = [
        {"index": i, "text": t, "attr": a, "center": [360, y]}
        for i, (t, a, y) in enumerate((("a", "Vo", 699), ("b", "Da", 800), ("c", "Vi", 901)))
    ]
    assert NiaPages(core).on_sales_event(rt, None, NS(fields={"choices": choices})) is DONE and taps == [(360, 901)]


def test_sales_option_and_removal_bubble():
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.quick import bottom_bubble

    raw, _ = extract("ACTION_SELECT", _img("nia-action-sales.png"))
    assert [o["option"] for o in observation(1, 0.0, "ACTION_SELECT", raw).fields["options"]] == ["sales"]
    page, f = bottom_bubble(_img("nia-sales-remove-result.png"))
    assert page == "DELETE_RESULT" and f["cards"][0]["name"] == "ふれあい"


def test_connecting_waits_instead_of_stopping(tmp_path):
    from hiro.runtime import nia_sample as ns
    from hiro.runtime.frame import Frame
    from hiro.vision.classify import UNKNOWN

    now = [0.0]
    rt = NS(clock=lambda: now[0], device=NS(tap=lambda x, y: pytest.fail("通信中不点")), session=None)
    adv = ns.make_advance(ns.Sampler(lambda img: UNKNOWN, tmp_path / "frames"), stop_s=15)
    f = Frame(_img("nia-connecting.jpg"), 0.0)
    for t in (0, 10, 20, 40):
        now[0] = t
        assert adv(rt, f, UNKNOWN) is False  # 一直不停


def test_exam_label_carries_audition_number():
    from hiro.runtime import nia_sample as ns

    Core = ns.nia_core("produce-004")
    c = Core.__new__(Core)
    c.exam_count, c.nia_audition_number = 0, 2
    assert c._battle_label(None) == "nia-mid1#audition-2" and c.nia_audition_number is None
    assert c._battle_label(None) == "nia-mid2"


def test_exam_label_uses_stage_from_select_page():
    """8a3a1e7 / 协调 11-17：第 2 场标签还是 nia-mid1（按进了几场数，中间夹着営業、自主课）。选档页读到的场次优先。"""
    from hiro.runtime import nia_sample as ns

    Core = ns.nia_core("produce-004")
    c = Core.__new__(Core)
    c.exam_count, c.nia_stage, c.nia_audition_number = 5, "mid2", 3  # 前面已经「数」过好几场（课程等）
    assert c._battle_label(None) == "nia-mid2#audition-3" and c.nia_stage is None


def test_sampler_rechecks_dialog_as_sales_place(tmp_path):
    """営业场所的黄色卡片被当成弹窗标题条（DIALOG）：采样的判页包装整帧 OCR 改认 NIA_SALES_PLACE。"""
    from hiro.runtime import nia_sample as ns
    from hiro.vision.classify import PageHit

    img = _img("nia-sales-place.png")
    s = ns.Sampler(lambda im: PageHit("DIALOG", "page_ocr"), tmp_path / "frames")
    hit = s(img)
    assert hit.name == "NIA_SALES_PLACE" and len(hit.fields["places"]) == 3
    assert s(np.zeros((1280, 720, 3), np.uint8)).name == "DIALOG"  # 认不出 N.I.A 页：照旧


@pytest.mark.parametrize("name", ["nia-audition-bonus.jpg", "nia-audition-bonus-param.jpg"])
def test_audition_bonus_is_show_page(name, tmp_path):
    """试镜结束的「オーディションボーナス」两页（实机帧；第 2 页原来判 DIALOG、第 1 页 unknown）：认成展示页，点 TAP。"""
    from hiro.produce.nia import NiaPages
    from hiro.runtime import nia_sample as ns
    from hiro.vision.classify import UNKNOWN, PageHit

    img = _img(name)
    hit = ns.Sampler(lambda im: PageHit("DIALOG", "page_ocr") if "param" in name else UNKNOWN, tmp_path / "frames")(img)
    assert hit.name == "NIA_AUDITION_BONUS"
    core, _ = _core(0)
    rt, _, taps = _rt()
    assert NiaPages(core).on_audition_bonus(rt, None, hit) is DONE
    ((x, y),) = taps
    assert abs(x - 360) < 20 and abs(y - 1222) < 15


def test_audition_select_second_stage():
    """第 2 次试镜选档页（左上「2次 オーディション」，GALAXY / POPFair / MELODY Journey）：stage=mid2。"""
    name, f = _read("nia-audition-select-2.png")
    assert name == "NIA_AUDITION_SELECT" and f["stage"] == "mid2" and f["votes"] == 15943
    assert [(t["vote_need"], t["number"]) for t in f["tiers"]] == [(25000, 3), (14000, 2), (0, 1)]
    name, f = _read("nia-audition-select-2-chosen.png")
    assert name == "NIA_AUDITION_SELECT" and f["stage"] == "mid2" and f["expanded"]


def test_resort_layout_reading():
    """协调 11-17：リゾート施設（绿，+2,800，消費なし）/ 企業 +4,000 / 商業 +4,000 -5；展开态把リゾート施設读成「ノゾート施談」也认。"""
    name, f = _read("nia-sales-place-resort.png")  # noqa: RUF059
    assert [(p["kind"], p["gain"], p["hp_cost"]) for p in f["places"]] == [
        ("resort", 2800, 0),
        ("corporate", 4000, 0),
        ("commercial", 4000, 5),
    ]
    _name, f = _read("nia-sales-place-camp-expanded.png")
    assert f["places"][0]["kind"] == "resort" and f["expanded"] and f["selected"] == 0 and abs(f["start"][1] - 779) < 15


def test_fields_reach_real_decider_through_session(tmp_path):
    """端到端：真 Session + 真决策器。原来 observation() 把 N.I.A 页的字段丢掉，决策器一直「营业场所没读到」，180 秒停。"""
    from hiro.brain.deciders import ProduceDeciders
    from hiro.contracts import TraceWriter
    from hiro.loop.session import Session
    from hiro.produce.nia import NiaPages
    from hiro.vision.nia import read_sales_place
    from hiro.vision.ocr import screen_texts

    img = _img("nia-sales-place-resort.png")
    fields = read_sales_place(screen_texts(img))
    session = Session(TraceWriter(name="nia-e2e", root=tmp_path), shoot=lambda: img)
    session.state.produce = {"mode": "produce-004", "stamina": 22, "max_stamina": 30, "produce_point": 60, "step": 5,
                             "params": {"Vo": 193, "Da": 616, "Vi": 238}}  # fmt: skip
    core = NS(s=session, deciders=ProduceDeciders(None, None), nia_audition_number=None)
    rt, now, taps = _rt()
    p = NiaPages(core)
    for _ in range(4):  # 防抖几拍后决策、点卡
        now[0] += 3.0
        if p.on_sales_place(rt, None, NS(fields=fields)) is DONE:
            break
    assert taps and taps[0][1] in (740, 878, 1016)


@pytest.mark.parametrize(("name", "kinds", "costs"), [
    ("nia-sales-place.png", ["local", "corporate", "commercial"], [5, 0, 5]),
    ("nia-sales-place-resort.png", ["resort", "corporate", "commercial"], [0, 0, 5]),
    ("nia-sales-place-expanded.png", ["local", "corporate", "commercial"], [5, 0, 5]),
    ("nia-sales-place-camp-expanded.png", ["resort", "corporate", "commercial"], [0, 0, 5]),
])  # fmt: skip
def test_sales_place_by_icons_without_names(name, kinds, costs):
    """用户 11-17：名字、金额读不出也要能决策。只给「営業先」提示文字，其余全靠图形：卡片颜色定种类、药丸里的爱心定耗不耗体力。"""
    from hiro.vision.nia import read_sales_place

    f = read_sales_place([("営業先を選択してください", (200, 540, 520, 560))], _img(name))
    assert [p["kind"] for p in f["places"]] == kinds and [p["hp_cost"] for p in f["places"]] == costs


def test_sales_place_expanded_rewards_by_icon():
    from hiro.vision.nia import read_sales_place

    f = read_sales_place(
        [("営業先を選択してください", (200, 540, 520, 560))], _img("nia-sales-place-camp-expanded.png")
    )
    assert f["selected"] == 0 and f["rewards"] == ["card", "stamina"] and abs(f["start"][1] - 779) < 15
    f = read_sales_place([("営業先を選択してください", (200, 540, 520, 560))], _img("nia-sales-place-expanded.png"))
    assert f["selected"] == 2 and f["rewards"] == ["card_plus"]


@pytest.mark.parametrize("name", ["nia-sales-event.png", "nia-sales-event-3.png"])
def test_sales_event_attr_by_icon_shape(name):
    """营業话题：按左侧图标形状（话筒 Vo / 鞋 Da / 人像 Vi）定属性，文字没意义也一样。"""
    from hiro.vision.nia import read_sales_event

    texts = [
        ("営業", (91, 52, 134, 75)),
        ("xxxx", (100, 690, 300, 710)),
        ("yyyy", (100, 790, 300, 810)),
        ("zzzz", (100, 890, 300, 910)),
    ]
    assert [c["attr"] for c in read_sales_event(texts, _img(name))["choices"]] == ["Vo", "Da", "Vi"]


def test_briefing_tapped_through_quick_path(tmp_path):
    """试镜考前说明页（实机帧，原来 unknown 停）：采样的判页包装认成 HIF_EXAM_BRIEFING（读出三属性和倍率），advance 按快速判页的格式点「タップして次へ」。"""
    from hiro.runtime import nia_sample as ns
    from hiro.runtime import play
    from hiro.runtime.frame import Frame
    from hiro.vision.classify import UNKNOWN

    img = _img("nia-exam-briefing.png")
    hit = ns.Sampler(lambda im: UNKNOWN, tmp_path / "frames")(img)
    assert hit.name == "HIF_EXAM_BRIEFING" and hit.fields["multiplier_pct"] == {"Vo": 1116, "Da": 1054, "Vi": 958}
    rt, _, taps = _rt()
    rt.sleep = lambda s: None
    rt._quick_wait = None
    assert play.advance(rt, Frame(img, 0.0), hit) is True
    ((x, y),) = taps
    assert abs(x - 363) < 30 and abs(y - 1199) < 20


def test_final_result_is_show_page(tmp_path):
    from hiro.produce.nia import NiaPages
    from hiro.runtime import nia_sample as ns
    from hiro.vision.classify import UNKNOWN

    hit = ns.Sampler(lambda im: UNKNOWN, tmp_path / "frames")(_img("nia-final-result-1st.png"))
    assert hit.name == "NIA_AUDITION_RESULT" and hit.fields["stage"] == "final"
    core, _ = _core(0)
    rt, _, taps = _rt()
    assert NiaPages(core).on_audition_result(rt, None, hit) is DONE and taps == [(361, 1147)]


def test_exam_stage_survives_resume(tmp_path):
    """N.I.A PRO 实机 11-17：三场考试标签都是 nia-mid1——选完档停下、resume 是新进程，内存里的场次丢了。
    选档时写文件，新进程取标签时读它。"""
    from hiro.produce.nia import NiaPages
    from hiro.runtime import nia_sample as ns

    path = tmp_path / "next-exam.json"
    tiers = [{"index": 0, "number": 2, "vote_need": 28000, "locked": False, "center": [360, 891]}]
    core, _ = _core(0, number=2)
    rt, _, _ = _rt()
    NiaPages(core, persist=path).on_audition_select(
        rt, None, NS(fields={"tiers": tiers, "expanded": False, "stage": "final"})
    )
    assert path.exists()
    Core = ns.nia_core("produce-004")  # resume 后的新进程
    c = Core.__new__(Core)
    c.exam_count, c.nia_stage, c.nia_audition_number = 0, None, None
    Core.next_exam_file = path
    assert c._battle_label(None) == "nia-final#audition-2" and not path.exists()


def test_reward_icon_tie_does_not_raise(monkeypatch):
    """「獲得報酬」小图标：所有模板 IoU 都是 0（打平）时不能去比名字和 None（同 _icon_attr 的 TypeError，协调 11-18）。"""
    from hiro.vision import nia

    empty = np.zeros((44, 44), bool)
    monkeypatch.setattr(nia, "_reward_templates", lambda: {"card": empty, "stamina": empty})  # 每个模板 IoU 都是 0
    img = np.zeros((1280, 720, 3), np.uint8)
    # 一格白色图形，形状和任何模板都不重叠：IoU 全 0
    cx, cy = nia.REWARD_X0, 900 + nia.REWARD_DY
    img[cy - 22 : cy - 18, cx - 22 : cx + 22] = 255
    assert nia.read_rewards(img, (400, 900)) == ["unknown"]
