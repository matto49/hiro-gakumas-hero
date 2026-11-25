"""tools/assets/capture_icons.py：合成图（不用游戏截图）上走一遍 收集 → 聚类 → 标名 → 写入自建素材 → 识别层读得出。"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

TOOL = Path(__file__).parents[1] / "tools" / "assets" / "capture_icons.py"
pytestmark = pytest.mark.synthetic


@pytest.fixture(scope="module")
def ci():
    spec = importlib.util.spec_from_file_location("capture_icons", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capture_icons"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 合成画面
def _status_slot(img, ci, k: int, shape: str, value: str) -> None:
    """蓝色菱形 + 白描边 + 白色图案 + 右下角白色数值，位置与游戏一致。"""
    cx, cy, r = ci.SLOT_X, round(ci.SLOT_Y0 + ci.SLOT_PITCH * k), ci.SLOT_HALF - 1
    diamond = np.array([[cx, cy - r], [cx + r, cy], [cx, cy + r], [cx - r, cy]], np.int32)
    blue = cv2.cvtColor(np.uint8([[[102, 170, 235]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    cv2.fillConvexPoly(img, diamond, blue)
    cv2.polylines(img, [diamond], True, (255, 255, 255), 1)
    white = (255, 255, 255)
    if shape == "circle":  # 圆环
        cv2.circle(img, (cx, cy), 10, white, 3)
    elif shape == "plus":  # 横条
        cv2.rectangle(img, (cx - 13, cy - 3), (cx + 13, cy + 3), white, -1)
    elif shape == "arrow":  # L 形
        cv2.rectangle(img, (cx - 10, cy - 12), (cx - 4, cy + 10), white, -1)
        cv2.rectangle(img, (cx - 10, cy + 4), (cx + 10, cy + 10), white, -1)
    cv2.putText(img, value, (cx + 14, cy + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, white, 2)


def _pitem_art(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    art = np.kron(rng.integers(0, 255, (6, 6, 3)), np.ones((11, 11, 1))).astype(np.uint8)
    cv2.putText(art, "PQ"[seed % 2], (14, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 4)
    return art


def _frame(ci, i: int, statuses: list[tuple[str, str]], pitems: list[int]) -> np.ndarray:
    """每帧背景不同（P 道具半透明，透出背景；聚类要能把同一道具在不同背景前认成一类）。"""
    rng = np.random.default_rng(1000 + i)
    img = np.kron(rng.integers(40, 200, (32, 18, 3)), np.ones((40, 40, 1))).astype(np.uint8)
    for k, (shape, value) in enumerate(statuses):
        _status_slot(img, ci, k, shape, value)
    x0, y0 = ci.PITEM_TOP_LEFT
    for k, seed in enumerate(pitems):
        y = y0 + ci.PITEM_PITCH * k
        roi = img[y : y + ci.PITEM_SIZE, x0 : x0 + ci.PITEM_SIZE]
        img[y : y + ci.PITEM_SIZE, x0 : x0 + ci.PITEM_SIZE] = cv2.addWeighted(_pitem_art(seed), 0.8, roi, 0.2, 0)
    return img


@pytest.fixture
def shots(ci, tmp_path):
    d = tmp_path / "shots"
    d.mkdir()
    plans = [([("circle", "12"), ("plus", "3")], [1]), ([("circle", "7")], [1, 2]), ([("plus", "20"), ("arrow", "4")], [1, 2]),
             ([("arrow", "15"), ("circle", "9")], [1]), ([("circle", "5")], [])]  # fmt: skip
    for i, (st, pi) in enumerate(plans):
        cv2.imwrite(str(d / f"exam-{i}.png"), _frame(ci, i, st, pi))
    cv2.imwrite(str(d / "landscape.png"), np.zeros((720, 1280, 3), np.uint8))  # 不是竖屏：跳过
    return d


@pytest.fixture
def master(tmp_path, monkeypatch):
    """最小主数据：效果名与 P 道具名候选（PublicAssetSource 要求有 ProduceCard 表）。"""
    d = tmp_path / "game_data"
    d.mkdir()
    db = sqlite3.connect(d / "game.db")
    db.executescript("""
        create table ProduceCard (id text, upgradeCount int);
        create table ProduceDescriptionExamEffect (type text, name text, noIcon int);
        insert into ProduceDescriptionExamEffect values ('ProduceExamEffectType_ExamCardPlayAggressive', 'やる気', 0),
            ('ProduceExamEffectType_ExamReview', '好印象', 0), ('ProduceExamEffectType_ExamLesson', 'パラメータ', 1);
        create table ProduceItem (id text, name text, assetId text);
        insert into ProduceItem values ('pi1', '合成オブジェ', 'img_general_pitem_9-001');
    """)
    db.commit()
    db.close()
    monkeypatch.setenv("HIRO_KAA_GAME_DATA", str(d))
    monkeypatch.setenv(
        "HIRO_MASTER_DB", str(d / "game.db")
    )  # 主数据库按 HIRO_MASTER_DB 读（不再从游戏图目录找 game.db）
    return d


# ---------------------------------------------------------------- 用例
def test_status_glyph_drops_border_and_value(ci):
    """菱形描边与右下角数值不进图案：同一图案配不同数值，裁出来几乎一样。"""
    a = ci.status_glyph(_frame(ci, 0, [("circle", "12")], []), 0)
    b = ci.status_glyph(_frame(ci, 1, [("circle", "888")], []), 0)
    assert a is not None and b is not None
    assert ci._glyph_sim(a, b) > 0.95
    assert ci.status_glyph(_frame(ci, 0, [], []), 0) is None  # 不是蓝底槽


def test_collect_clusters(ci, shots, tmp_path):
    cs = ci.collect([str(shots)], tmp_path / "work", tmp_path / "user", exam_only=False)
    assert sorted(len(c.members) for c in cs["status"]) == [2, 2, 4]  # circle ×4、plus ×2、arrow ×2
    assert [len(c.members) for c in cs["pitem"]] == [
        4,
        2,
    ]  # 1 号道具 4 帧、2 号道具 2 帧（第二格，第一格也是道具）；空栏背景不成类
    labels = json.loads((tmp_path / "work" / "labels.json").read_text(encoding="utf-8"))
    assert set(labels) == {"status", "pitem"} and len(labels["status"]) == 3
    assert (tmp_path / "work" / "sheet_status.png").exists() and (tmp_path / "work" / "sheet_pitem.png").exists()


def test_apply_writes_user_assets_and_reads_back(ci, shots, tmp_path, master):
    from hiro.vision.items import IconLibrary

    work, user = tmp_path / "work", tmp_path / "user"
    cs = ci.collect([str(shots)], work, user, exam_only=False)
    labels = json.loads((work / "labels.json").read_text(encoding="utf-8"))
    circle = next(c.id for c in cs["status"] if len(c.members) == 4)
    labels["status"][str(circle)]["name"] = "やる気"
    labels["status"][str(next(c.id for c in cs["status"] if c.id != circle))]["name"] = "好印象"
    labels["pitem"]["0"]["name"] = "合成オブジェ"
    labels["pitem"]["1"]["name"] = "主数据里没有的道具"
    (work / "labels.json").write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    written = ci.apply(work, user)
    assert sorted(written["status"]) == ["examcardplayaggressive", "examreview"]
    sindex = json.loads((user / "icons" / "status" / "index.json").read_text(encoding="utf-8"))
    assert sindex["examcardplayaggressive"] == {
        "type": "ProduceExamEffectType_ExamCardPlayAggressive",
        "name": "やる気",
        "source": "capture",
    }
    assert sindex["examreview"]["name"] == "好印象"
    png = cv2.imread(
        str(user / "icons" / "status" / "img_general_icon_exam-effect_examcardplayaggressive.png"), cv2.IMREAD_UNCHANGED
    )
    assert png.shape == (ci.STATUS_SIZE, ci.STATUS_SIZE, 4)
    pindex = json.loads((user / "icons" / "pitem" / "index.json").read_text(encoding="utf-8"))
    assert pindex == {"img_general_pitem_9-001": "合成オブジェ", "user_pitem_1": "主数据里没有的道具"}

    # 识别层的图库（只有两个图标也能用）：新的一帧里两个道具都认得出，名称来自 index.json
    lib = IconLibrary.load(user / "icons" / "pitem", ci.PITEM_SIZE)
    img = _frame(ci, 50, [], [1, 2])
    assert lib.match(ci.pitem_crop(img, 0))[0][1] == "img_general_pitem_9-001"
    assert lib.match(ci.pitem_crop(img, 1))[0][1] == "user_pitem_1"
    assert lib.names["img_general_pitem_9-001"] == "合成オブジェ"
    # 再收集一次：已写入的图标不再出现
    again = ci.collect([str(shots)], work, user, exam_only=False)
    assert len(again["status"]) == 1 and again["pitem"] == []


def test_icon_library_with_one_icon(tmp_path):
    from hiro.vision.items import IconLibrary

    cv2.imwrite(str(tmp_path / "a.png"), np.dstack([_pitem_art(1), np.full((66, 66), 255, np.uint8)]))
    top = IconLibrary.load(tmp_path, 66).match(_pitem_art(1))
    assert top[0][1] == "a" and top[1] == (0.0, "")


def test_label_with_llm(ci, shots, tmp_path, master):
    from hiro.brain.llm import MockLLM

    work = tmp_path / "work"
    cs = ci.collect([str(shots)], work, tmp_path / "user", exam_only=False)
    n = len(cs["status"]) + len(cs["pitem"])
    replies = [{"name": "やる気", "sure": True}, {"name": "好印象", "sure": False}, {"name": "存在しない効果", "sure": True},
               {"name": "合成オブジェ", "sure": True}, {"name": None, "sure": False}]  # fmt: skip
    llm = MockLLM(replies=replies[:n])
    labels = ci.label_with_llm(work, llm)
    assert labels["status"]["0"]["name"] == "やる気"
    assert labels["status"]["1"]["name"] is None and labels["status"]["1"]["llm"] == "好印象"  # 拿不准：只记下来
    assert (
        labels["status"]["2"]["name"] is None and labels["status"]["2"]["llm"] == "存在しない効果"
    )  # 对不上候选：不写
    assert labels["pitem"]["0"]["name"] == "合成オブジェ"
    assert "やる気" in llm.calls[0][1] and "好印象" in llm.calls[0][1]  # 候选来自主数据
