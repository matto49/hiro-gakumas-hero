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
