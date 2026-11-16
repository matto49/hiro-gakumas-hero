from pathlib import Path

SKILL = Path(__file__).resolve().parents[2] / "skills" / "hiro-cli" / "SKILL.md"


def test_every_command_is_documented():
    import hiro.cli.main  # noqa: F401
    from hiro.cli import registry

    text = SKILL.read_text(encoding="utf-8")
    missing = [n for n in registry.COMMANDS if f"hiro {n}" not in text]
    assert missing == []


def test_pending_protocol_and_red_lines_are_stated():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: hiro-cli\n")
    assert "待确认" in text and "先问用户" in text and "hiro confirm" in text
    assert "ジュエル" in text and "放弃培育" in text
    assert "secret copy" in text and "不显示" in text
