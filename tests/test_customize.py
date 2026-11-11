# 来源：digital-hiro tests/test_customize.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""特別指導（SPECIAL_GUIDANCE）：q10 第 19 天实机帧（reports/live/special-guidance/，选卡屏原图 540x960 已放大到 720x1280）。"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision.customize import master_offers

FIX = Path(__file__).with_name("fixtures") / "guidance"
CONFETTI = "p_card-02-act-2_062"


def _obs(name: str):
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load
    from hiro.vision.scene import classify_current

    img = load(cv2.imread(str(FIX / f"{name}.jpg")))
    scene = classify_current().scene
    raw, _ = extract(scene, img)
    return observation(1, 0.0, scene, raw)


def test_master_offers_confetti():
    """きらきら紙吹雪+：主数据 3 项，顺序 = 画面从左到右（やる気コスト値- / 元気分パラメータ+ / 好印象追加），元気分パラメータ+ 两级 40 → 70。"""
    offers = master_offers(CONFETTI, 1)
    assert [o["item_index"] for o in offers] == [0, 1, 2]
    assert "cost_aggressive_reduce" in offers[0]["id"] and "lesson_depend_block_add" in offers[1]["id"] and "exam_review" in offers[2]["id"]
    assert [o["levels"] for o in offers] == [[40], [40, 70], [70]]
    assert master_offers(CONFETTI, 0) == [] and master_offers(None, 1) == []


@pytest.mark.parametrize(
    ("name", "at_top", "names"),
    [("q10-enter", True, ["えいえいおー", "ワクワクが止まらない", "デイドリーミング", "本番前夜", "ティーパーティ", "止められない想い", "ひなたぼっこ", "私がスター"]),
     ("q10-grid-scrolled", False, ["届いて！", "イメトレ", "ゆるふわおしゃべり", "紅葉ランニング", "きらきら紙吹雪", "ほかほか焼き芋", "せのびの魔法", "エウレカ！"])],
)  # fmt: skip
def test_select_grid(name, at_top, names):
    """卡格 4 列 x≈120/280/440/600、行距 160（原来按相談删卡对话框的 y=457 读，一张也读不到）；不能定制的卡压暗 → available=False。"""
    obs = _obs(name)
    assert obs.page.name == "SPECIAL_GUIDANCE" and obs.problems(strict=True) == []
    f = obs.fields
    assert f["view"] == "select" and f["cards_left"] == 2 and f["scrollable"] is True and f["at_top"] is at_top
    assert [c["name"] for c in f["cards"]] == names
    assert [round(c["center"][0], -1) for c in f["cards"][:4]] == [120, 280, 440, 600]
    dim = [c["name"] for c in f["cards"] if not c["available"]]
    assert dim == (["エウレカ！"] if name == "q10-grid-scrolled" else [])
    assert all(c["customizations"] for c in f["cards"] if c["available"])


def test_select_card_menu_preview():
    """选中 紙吹雪+：格子角框 + 上方详情卡名一致；菜单预览的实价并入这张卡的 customizations（source=screen，id 按位置对应主数据）。"""
    f = _obs("q10-card-selected").fields
    sel = next(c for c in f["cards"] if c["index"] == f["selected"])
    assert sel["card_id"] == f["selected_card"]["card_id"] == CONFETTI
    assert [(o["item_index"], o["price"], o["source"]) for o in sel["customizations"]] == [(0, 40, "screen"), (1, 40, "screen"), (2, 70, "screen")]
    assert sel["customizations"][1]["id"] == master_offers(CONFETTI, 1)[1]["id"]


@pytest.mark.parametrize(
    ("name", "items", "total"),
    [("q10-item-selected", [("やる気コスト値-", 40, 1, False, False), ("元気分パラメータ+", 40, 2, False, True), ("好印象追加", 70, 1, False, False)], 2),
     ("q10-done", [("やる気コスト値-", 40, 1, False, False), ("元気分パラメータ+", 70, 1, False, True), ("好印象追加", 70, 1, False, False)], 1),
     ("q10-done2", [("やる気コスト値-", 40, None, True, False), ("元気分パラメータ+", 70, None, True, False), ("好印象追加", 70, None, True, False)], None)],
)  # fmt: skip
def test_menu_items(name, items, total):
    """定制项页：每块 名称 / 价格（当前这一级）/ あと N回 / カスタマイズ上限 / 选中；「合計あと N回」。做第 2 级后价格 40 → 70。"""
    f = _obs(name).fields
    assert f["view"] == "menu" and f["selected_card"]["card_id"] == CONFETTI
    got = [(i["name"], i["price"], i["left"], i["maxed"], i["selected"]) for i in f["menu"]["items"]]
    from hiro.vision.ocr import engine

    if engine() != "vision":
        # RapidOCR 读不出定制项名末尾的「-」「+」：名字只用于日志，执行按 item_index（位置）点、id 按位置对主数据
        got = [(n.rstrip("+-") if n else n, *rest) for n, *rest in got]
        items = [(n.rstrip("+-"), *rest) for n, *rest in items]
    assert got == items and f["menu"]["total_left"] == total
    assert [i["id"] for i in f["menu"]["items"]] == [o["id"] for o in master_offers(CONFETTI, 1)]
    if name == "q10-done2":
        assert f["cards_left"] == 1 and all(i["available"] is False for i in f["menu"]["items"])


def test_customize_result_and_confirm_dialog():
    from hiro.vision.events import observed_events

    obs = _obs("q10-after-exec")
    assert obs.page.name == "CUSTOMIZE_RESULT" and obs.fields["card_id"] == CONFETTI and obs.fields["upgrade"] == 1
    (ev,) = observed_events(obs)
    assert (ev.kind, ev.args, ev.confirmed) == ("CARD_CUSTOMIZE", {"card_id": CONFETTI, "upgrade": 1}, True)
    d = _obs("q10-exec-confirm")
    assert (d.page.name, d.fields["kind"]) == ("DIALOG", "customize_confirm")  # 「カスタマイズ実行確認」（OCR 读成「美行」）


@pytest.mark.parametrize(("name", "p", "hp"), [("q11-r2-00001", 430, 20), ("q10-enter", 430, 16), ("q10-done", 390, 16), ("q10-done2", 320, 16)])
def test_produce_point(name, p, hp):
    """q11 第 19 天：特別指導页没有 P，决策「P None」一项都没定制。左上 P 栏与选行动页同一位置（OCR / 字体 / ddddocr 两票一致）。"""
    f = _obs(name).fields
    assert (f["produce_point"], f["stamina"], f["max_stamina"]) == (p, hp, 33)
