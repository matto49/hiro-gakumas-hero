"""随仓库素材包 assets/pack 的加载：自建目录为空时用素材包，HIRO_BUNDLED_ASSETS=0 关掉后退回 None。"""

from pathlib import Path

from hiro.defaults.assets import PublicAssetSource

import pytest

PACK = Path(__file__).resolve().parents[1] / "assets" / "pack"


@pytest.mark.skipif(not PACK.is_dir(), reason="没有 assets/pack")
def test_bundled_pack_used_when_user_dir_empty(tmp_path):
    a = PublicAssetSource(kaa_game_data=tmp_path / "kaa", user_assets=tmp_path / "user")
    assert a.bundled == PACK
    assert a.icon_dir("status") == PACK / "icons" / "status"
    assert a.card_art_dir() == PACK / "cards"
    assert a.font_path() == PACK / "font" / PublicAssetSource.DIGIT_FONT
    assert a.layout() and a.sprites_dir() == PACK / "sprites" and a.support_dir() == PACK / "support"


@pytest.mark.skipif(not PACK.is_dir(), reason="没有 assets/pack")
def test_bundled_pack_can_be_turned_off(tmp_path):
    a = PublicAssetSource(kaa_game_data=tmp_path / "kaa", user_assets=tmp_path / "user", bundled_assets="0")
    assert a.bundled is None
    assert a.icon_dir("status") is None and a.font_path() is None and a.layout() is None


@pytest.mark.skipif(not PACK.is_dir(), reason="没有 assets/pack")
def test_fuller_library_wins_over_small_user_dir(tmp_path):
    user = tmp_path / "user"
    (user / "icons" / "status").mkdir(parents=True)
    (user / "icons" / "status" / "only_one.bin").write_bytes(b"x")
    a = PublicAssetSource(kaa_game_data=tmp_path / "kaa", user_assets=user)
    assert a.icon_dir("status") == PACK / "icons" / "status"


@pytest.mark.synthetic  # 写的是空占位文件，不读截图
@pytest.mark.skipif(not PACK.is_dir(), reason="没有 assets/pack")
def test_pack_card_art_kept_when_public_images_have_more_files(tmp_path):
    """公开卡图（kaa-game-data）换新版后文件数可能超过素材包，但少了素材包里的卡面变体：
    素材包开着时卡面一律用素材包，公开卡图只在素材包关掉时参与比较（11-25 新版 2312 张 > 素材包 2290 张，认错过变体）。"""
    kaa = tmp_path / "kaa"
    (kaa / "skill_cards").mkdir(parents=True)
    n = sum(1 for _ in (PACK / "cards").iterdir())
    for i in range(n + 10):
        (kaa / "skill_cards" / f"c{i}.png").write_bytes(b"x")
    assert PublicAssetSource(kaa_game_data=kaa, user_assets=tmp_path / "u").card_art_dir() == PACK / "cards"
    off = PublicAssetSource(kaa_game_data=kaa, user_assets=tmp_path / "u", bundled_assets="0")
    assert off.card_art_dir() == kaa / "skill_cards"
