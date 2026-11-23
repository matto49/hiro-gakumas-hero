"""OCR 测试集的游戏词表与字表（tools.ocr_dataset.lexicon，规格 §6.2 第 2 条、§6.5、§9.3）。"""

import pytest

from tools.ocr_dataset import lexicon
from tools.ocr_dataset.lexicon import Lexicon


def test_norm_unifies_width_and_spaces():
    assert lexicon.norm("ＡＢＣ　１２ ３") == "ABC123"
    assert lexicon.norm("Ｐポイント＋５０") == "Pポイント+50"


def test_snap_exact_for_short_words():
    lex = Lexicon(words={"編成", "プロデュース編成", "レッスン"})
    assert lex.snap("編成") == "編成"
    assert lex.snap("綿成") is None  # 4 字以内必须完全一样


def test_snap_tolerates_by_length():
    lex = Lexicon(words={"プロデュース編成", "ビジュアルレッスン", "スキルカード獲得"})
    assert lex.snap("プロデュース綿成") == "プロデュース編成"  # 8 字：容错 1
    assert lex.snap("プロデユース綿成") is None  # 错 2 字：超出
    assert lex.snap("ビジュアルレツスン") == "ビジュアルレッスン"


def test_snap_ambiguous_returns_none():
    lex = Lexicon(words={"ボーカルレッスン", "ダンスのレッスン"})
    assert lex.snap("ボーカルレッスソ") == "ボーカルレッスン"
    lex2 = Lexicon(words={"AAAAAB", "AAAAAC"})
    assert lex2.snap("AAAAAD") is None  # 两个词一样近：不猜


@pytest.mark.master
def test_build_from_master_data():
    lex = lexicon.build()
    assert "編" in lex.charset and "綿" not in lex.charset  # 「綿」只在剧情标题里
    assert {"ぁ", "ゔ", "ア", "ヶ", "0", "9", "A", "z", "%", "+"} <= lex.charset
    assert "届いて！" in lex.words or "届いて!" in lex.words  # 卡名
    assert any("リトライ" == w for w in lex.words)  # 识别代码里的界面文字
    assert lex.version and len(lex.version) >= 8


@pytest.mark.master
def test_cli_lexicon_writes_and_loads(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-m", "tools.ocr_dataset", "lexicon", str(tmp_path / "lex")], capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stderr
    lex = lexicon.load(tmp_path / "lex")
    assert "編" in lex.charset and lex.snap("リトライ") == "リトライ" and lex.version
