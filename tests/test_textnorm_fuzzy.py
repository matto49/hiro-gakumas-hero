"""OCR 容错匹配（hiro.vision.textnorm）：用实机读错的真实例子（2026-11-06 / 11-21）。"""

from hiro.vision.textnorm import contains, max_errors, same


def test_error_budget_by_length():
    assert [
        max_errors(w) for w in ("決定", "休む", "ジュエル", "キャンセル", "サポート編成", "サポートカードを選択")
    ] == [0, 0, 0, 1, 1, 2]


def test_real_misreads_are_matched():
    assert contains("メモリー綿成", "メモリー編成") and contains(
        "サポート縞成", "サポート編成"
    )  # DMM 小窗口 編 → 綿 / 縞
    assert not contains("縞成詳細", "編成詳細")  # 4 字词不容错（代价：短词读错一个字认不出，要靠更长的词或别的判据）
    assert contains("Vi公開レツスン", "公開レッスン") and contains("YO公開レッズン", "公開レッスン")  # ッ → ツ、ス → ズ
    assert contains("NskIp", "SKIP") and contains("AuTo", "AUTO")  # 大小写
    assert contains("タイトルヘ", "タイトルへ") and contains("通信工ラー", "エラー")  # 原来就有的字形归一
    assert contains("2サポート選択", "サポート選択")
    assert contains("ジュエルを消費して再挑戦しますか", "ジュエル")


def test_short_words_stay_exact():
    assert not contains("決走", "決定") and not contains("次", "次へ") and contains("→次へ", "次へ")


def test_different_words_do_not_match():
    assert not contains("サポート編成", "メモリー編成")
    assert not contains("リトライ", "リタイア")
    assert not contains("アノマリーノート（ビジュアル）×2", "ジュエル")  # 11-21 测出来的撞车：4 字词不容错
    assert not contains("プロデュース", "プロデュース編成")  # 少了两个字：超过 1 个的容错


def test_same_is_whole_string():
    assert same("プロデュース綿成", "プロデュース編成")
    assert not same("プロデュース編成・スキルカード", "プロデュース編成")
    assert same("決定", "決定") and not same("決走", "決定")
    assert not same("プロデュース", "プロデュース編成")  # 首页按钮不能当成子菜单项


def test_fold_chinese_glyph_ba():
    """SIMD OCR 把「選抜試験」读成中文字形「選拔試験」（11-24）：4 个字的词不容错，靠字形归一。"""
    from hiro.vision.textnorm import contains, same

    assert same("選拔試験", "選抜試験") and contains("16位以上で選拔試験3突破", "選抜試験")
