"""开源入口：策略类注入（run_hif --strategy / HIRO_STRATEGY）、保护卡名单注入（kaa_patches）、整局脚本拼出的命令。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def test_protected_provider_overrides_knowledge(monkeypatch):
    from hiro.produce import card_ops

    monkeypatch.setattr(card_ops, "PROTECTED_PROVIDER", None)
    card_ops.set_protected_provider(lambda: {"p_card-x"})
    try:
        assert card_ops.protected_card_ids() == {"p_card-x"}
        cards = [{"card_id": "p_card-x", "name": "x"}, {"card_id": "p_card-y", "name": "y"}]
        assert card_ops.pick_change_target(cards)[0]["card_id"] == "p_card-y"
    finally:
        card_ops.set_protected_provider(None)
    assert card_ops.PROTECTED_PROVIDER is None


def test_play_game_command(monkeypatch):
    import play_game

    monkeypatch.setattr(play_game.sys, "platform", "linux")
    cmd = play_game.run_cmd(
        "main", "main1-x", ["--idol", "i_card-skin-x", "--main-attr", "Da"], ["--expect-memory", "1,2,3"]
    )
    assert cmd[1:] == ["-m", "hiro.run_hif", "new", "--mode", "main", "--idol", "i_card-skin-x", "--main-attr", "Da",
                       "--expect-memory", "1,2,3", "--trace", "main1-x"]  # fmt: skip
