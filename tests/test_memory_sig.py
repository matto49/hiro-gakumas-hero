"""回忆签名（设计 5.2）：增量补扫靠它认出资料库里已有的回忆。能力文字 OCR 噪声大（11-25 / 11-26 两次扫描只有 193 / 405 格一致），
只取所有能力里的数字（多重集），和词序、杂字、切行无关。下面的能力文字对都摘自那两次扫描同一格的读数。"""

from hiro.produce import memory_sig as ms

import pytest

SAME = [
    (
        "ポーカルSPレッスン開始時、55％の確率でぐ消費体力減少2ターン",
        "バーカルSPレッスン開始時、55％の確率で（消費体力減少2ターン",
    ),
    ("初期ビジュアル上昇+15照", "初期ビジュアル上昇+15"),
    ("ビジュアルSPレツスン開始時、55%の確率で元気+4", "発動不ビジュアルSPレッスン開始時、55％の確率で元気+4"),
    (
        "ビジュアルSPレッスン開始時、70％の確率で消費体力減少2ターン",
        "ごジュアルSPレッスン開始時、70％の確率で消費体力減少2ターン",
    ),
    (
        "【センス専用】ボーカルSPレッスン開始時、70%の確率で好調2ターン",
        "【センス専用】ボーカルSPレッスン開始時、70%&好調2ターンの確率で",
    ),
    ("ダンスパラメータボーナス+2.1%", "ダンスパラメータポーナス+2.1%"),
    ("初期ポイント+40", "初期Pポイント+40"),
]


SAME += [
    (
        "【センス専用】ボーカルレッスン開始時、50%の確率で好調1ターン",
        "【センス専用】ボーカルレッスン開始時、50％の、対調1ターン唯率でく",
    ),
    (
        "ダンスSPレッスン開始（消時、55％の確率で費体力減少2ターン",
        "ブンスSPレッスン開始（消時、55％の確率でく費体力減少2ターン",
    ),
]


@pytest.mark.parametrize(("a", "b"), SAME)
def test_ocr_noise_gives_same_numbers(a, b):
    assert ms.ability_numbers([a]) == ms.ability_numbers([b])


def test_numbers_ignore_how_lines_are_split():
    """两条能力有时被 OCR 读成一行（11-26 第 66 行第 3 格）。"""
    assert ms.ability_numbers(["初期ポイント+40", "ダンスSPレッスン開始時、35％の確率で元気+4"]) == ms.ability_numbers(
        ["初期ポイント+40ダンスSPレッスン開始時、35％の確率で元気+4"]
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ビジュアルSPレッスン開始時、55％の確率で元気+4", "ビジュアルSPレッスン開始時、70％の確率で元気+4"),
    ],
)
def test_different_abilities_differ(a, b):
    assert ms.ability_numbers([a]) != ms.ability_numbers([b])


def _entry(card="p_card-01-men-3_036", up=0, stats=(10, 52, 64, 0), abilities=("初期ビジュアル上昇+15",)):
    return {
        "card": {"card_id": card, "upgrade": up},
        "stats": dict(zip(("Vo", "Da", "Vi", "hp"), stats)),
        "abilities": [{"text": t, "disabled": False} for t in abilities],
    }


def test_signature_uses_card_upgrade_stats_and_abilities():
    base = ms.signature(_entry())
    assert ms.signature(_entry(abilities=("初期ビジュアル上昇+15照",))) == base
    assert ms.signature(_entry(up=1)) != base
    assert ms.signature(_entry(stats=(10, 52, 69, 0))) != base
    assert ms.signature(_entry(abilities=("初期ビジュアル上昇+20",))) != base
    # 旧格式：abilities 是字符串列表
    assert ms.signature({**_entry(), "abilities": ["初期ビジュアル上昇+15"]}) == base


def test_known_set_counts_duplicates():
    a, b = _entry(), _entry(card="p_card-x")
    known = ms.KnownSet([a, a, b])
    assert known.take(a) and known.take(a) and not known.take(a)
    assert known.take(b) and not known.take(b)


def test_known_set_tolerates_one_extra_number():
    """图标「②」偶尔读成 2：多一个数字也认成同一份；多两个就不是。"""
    known = ms.KnownSet([_entry(abilities=("スキルカード使用数追加+1",))])
    assert known.take(_entry(abilities=("②スキルカード使用数追加+1",)))
    known = ms.KnownSet([_entry(abilities=("スキルカード使用数追加+1",))])
    assert not known.take(_entry(abilities=("②スキルカード使用数追加+1 3",)))


def test_known_set_needs_same_card_upgrade_and_stats():
    known = ms.KnownSet([_entry()])
    assert not known.take(_entry(up=1)) and not known.take(_entry(stats=(10, 52, 69, 0)))
    assert known.take(_entry())
