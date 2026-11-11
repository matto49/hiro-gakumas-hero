"""scripts/play.ps1 的考试插件环境变量：外面设了 HIRO_EXAM_STATE_SOURCE / HIRO_EXAM_POLICY 要透传（私有存档插件在 Windows DMM 上用）。
Mac 上没有 PowerShell，按脚本文本检查。"""

import re
from pathlib import Path

PLAY = (Path(__file__).resolve().parents[1] / "scripts" / "play.ps1").read_text(encoding="utf-8-sig")


def _assignments(var: str) -> list[str]:
    return [line.strip() for line in PLAY.splitlines() if re.search(rf"\$env:{var}\s*=", line)]


def test_state_source_kept_when_preset():
    lines = _assignments("HIRO_EXAM_STATE_SOURCE")
    assert lines, "play.ps1 应在没设时给默认 SimScreen 来源"
    for line in lines:
        assert re.match(r"if \(-not \$env:HIRO_EXAM_STATE_SOURCE\)", line), f"无条件覆盖了 HIRO_EXAM_STATE_SOURCE：{line}"
    assert "SimScreenExamStateSource" in lines[0]


def test_nosim_still_clears_state_source():
    nosim = PLAY.split("if ($NoSim) {", 1)[1].split("} else {", 1)[0]
    assert "Remove-Item Env:HIRO_EXAM_STATE_SOURCE" in nosim


def test_exam_policy_passes_through():
    assert not _assignments("HIRO_EXAM_POLICY"), "play.ps1 不应改 HIRO_EXAM_POLICY（外面设了原样透传）"
    assert "Remove-Item Env:HIRO_EXAM_POLICY" not in PLAY


def test_logs_effective_source():
    assert re.search(r"Write-Host .*HIRO_EXAM_STATE_SOURCE=", PLAY)
