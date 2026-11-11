# 来源：digital-hiro tests/test_hif_hud.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""HIF 考试 HUD 数字识别对照真值：.venv/bin/python tests/test_hif_hud.py"""

import json
from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision import assets
from hiro.vision.hif import is_hif_exam, read_hif_exam
from hiro.vision.offline import load

FIX = Path(__file__).with_name("fixtures") / "hif_hud"


USER_ASSET_KEYS = {
    "statuses": "status",
    "pitem_names": "pitem",
}  # 这些字段的名称来自自建图标；素材不在时不比（数值照比）


def evaluate() -> list[str]:
    errors = []
    skip = {k for k, kind in USER_ASSET_KEYS.items() if not assets.available(kind)}
    for name, truth in json.loads((FIX / "truth.json").read_text()).items():
        img = load(cv2.imread(str(FIX / f"{name}.jpg")))
        if not is_hif_exam(img):
            errors.append(f"{name} scene: not HIF_EXAM")
        got = read_hif_exam(img)
        got["hand_names"] = [c.get("name") for c in got["hand"]]
        got["statuses"] = [[e.get("name"), e.get("value")] for e in got["statuses"]]
        got["drink_names"] = [d and d["name"] for d in got["drinks"]]
        got["pitem_names"] = [p["name"] for p in got["pitems"]]
        if "statuses" in skip:  # 没有状态图标：状态名给 None，只比数值
            got["statuses"] = [v for _, v in got["statuses"]]
            truth = {**truth, "statuses": [v for _, v in truth.get("statuses", [])]} if "statuses" in truth else truth
        for k, want in truth.items():
            if k in skip - {"statuses"}:
                continue
            if got.get(k) != want:
                errors.append(f"{name} {k}: want {want} got {got.get(k)}")
    return errors


def test_hif_hud():
    assert evaluate() == []


if __name__ == "__main__":
    errs = evaluate()
    print("\n".join(errs) or "all ok")
    print(f"{len(errs)} errors")


def test_hif_result_scene():
    from hiro.vision.hif import is_hif_result

    result = load(cv2.imread(str(FIX / "result-02416.jpg")))
    assert is_hif_result(result) and not is_hif_exam(result)
    for name in json.loads((FIX / "truth.json").read_text()):
        assert not is_hif_result(load(cv2.imread(str(FIX / f"{name}.jpg"))))


DRINKS = FIX.parent / "drinks"
DRINK_TRUTH = {
    DRINKS / "r2-00002.jpg": ["特製ハツボシエキス", "パワフル漢方ドリンク", None, None],
    DRINKS / "r7-00002.jpg": ["特製ハツボシエキス", "パワフル漢方ドリンク", "烏龍茶", None],
    FIX / "01782.jpg": ["特製ハツボシエキス", None, None, None],
}


def test_drink_slots():
    from hiro.vision.items import read_drink_slots

    for path, want in DRINK_TRUTH.items():
        slots = read_drink_slots(cv2.imread(str(path)))
        assert [d["name"] if d["state"] == "drink" else None for d in slots] == want, path.name
        assert [d["state"] for d in slots] == ["drink" if w else "empty" for w in want], path.name
        assert all(d["drink_id"] and d["available"] for d in slots if d["state"] == "drink"), path.name


def test_read_hif_hud_never_misreads():
    from hiro.vision.hif import read_hif_hud

    for name, truth in json.loads((FIX / "truth.json").read_text()).items():
        hud = read_hif_hud(cv2.imread(str(FIX / f"{name}.jpg")))
        for k in ("remaining_turns", "hp", "genki"):
            assert hud[k] in (truth[k], None), (name, k)  # 没把握时可以是 None，但不能读错


def test_hand_four_cards_covered_plus_is_unknown():
    """4 张手牌时后一张盖住前一张卡名栏右侧：看不到「+」的卡强化次数为 None，而不是当成基础版。"""
    from hiro.vision.hif import read_hand

    hand, _ = read_hand(load(cv2.imread(str(DRINKS / "r2-00005-hand4.jpg"))))
    assert [(c["name"].rstrip("+"), c["plus"]) for c in hand] == [
        ("幸せのおまじない", None), ("アイコンタクトの基本", None), ("えいえいおー", 1), ("距離感の基本", 0)]  # fmt: skip


def test_region_cache_reuses_identical_frame():
    import time

    from hiro.vision.hif import read_hif_exam
    from hiro.vision.region_cache import RegionCache

    img = load(cv2.imread(str(FIX / "01782.jpg")))
    cache = RegionCache()
    t = time.perf_counter()
    first = read_hif_exam(img, cache=cache)
    t_first = time.perf_counter() - t
    hits = cache.hits
    t = time.perf_counter()
    second = read_hif_exam(img, cache=cache)
    t_second = time.perf_counter() - t
    # 相对判据（总控 / 协调 2026-11-03：原来「< 0.05s」在负载 54–140 时多次误报）：第二次全部命中缓存、结果一致，且明显快于第一次
    assert second == first and cache.hits > hits
    assert t_second < 0.5 * t_first, (t_second, t_first)


def test_multiplier_occluded_first_digit():
    """qualify6 r3 #76：回合转盘的白色箭头盖住倍率首位「1」，OCR 读成 7481%（实际 1481%）。
    两种读法（OCR / ddddocr）不一致时：等于该属性上次一致的读数就用它，否则 None；#77 属性名读成「ベジュアル」也要认成 visual。"""
    from hiro.vision import hif
    from hiro.vision.hif import _attr_crop, _parse_attr, _vote_multiplier
    from hiro.vision.ocr import ocr

    hif._MULT_SEEN.clear()
    occluded = cv2.imread(str(Path(__file__).with_name("fixtures") / "hif_hud" / "mult-occluded-q6r3-00076.jpg"))
    clear = cv2.imread(str(Path(__file__).with_name("fixtures") / "hif_hud" / "mult-attr-q6r3-00077.jpg"))
    assert _parse_attr("ベジュアル1481%") == {"attr": "visual", "multiplier_pct": 1481}

    def read(img):
        a = _parse_attr("".join(t for t, _ in ocr(_attr_crop(img))))
        return _vote_multiplier(img, a["multiplier_pct"], None, a["attr"])

    v, votes = read(occluded)
    assert v in (None, 1481) and 7481 not in ([v] if v else [])  # 没有记忆时不猜（两种读法一致时也只能是 1481）
    assert read(clear)[0] == 1481
    assert read(occluded)[0] == 1481
    hif._MULT_SEEN.clear()


@pytest.mark.vision_only("模拟「没有数字字体」时 ddddocr 两读不一致、用 OCR 判的路径；Windows / DMM 用随仓库的素材包（assets/pack/font 带数字字体），实机走字形比对，不走这条")
def test_no_font_hud_numbers(monkeypatch):
    """没有数字字体（开源默认素材）：HUD 数字走 ddddocr（整框 / 收左边两次，不一致用 Apple Vision 判）。
    元気 0 曾被整框读成 10（体力条右端的圆头当成「1」）；白点花纹背景上收左边会丢首位（1283→283）。"""
    from hiro.vision import digits, hif

    monkeypatch.setattr(digits, "_templates", dict)
    monkeypatch.setattr(hif, "_templates", dict)
    root = FIX.parent
    for rel, genki in (("exam_none/status-unknown-slot-00385.jpg", 0), ("finals/exam-r1-t9.jpg", 0), ("genki/m25r1-202-1283.jpg", 1283),
                       ("hif_hud/01714.jpg", 23)):  # fmt: skip
        img = cv2.imread(str(root / rel))
        assert digits.read_number(img, hif.BOX_GENKI, align=True) == genki, rel
