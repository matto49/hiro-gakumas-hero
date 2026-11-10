"""平台差异只在设备层 / 配置：决策层（brain）、策略层（produce / sim / state / defaults）、执行层（exec）不判断平台。

允许判断平台的地方：hiro.device / hiro.compat / hiro.kaa_env / hiro.loop（主循环与 KAA 适配）/ hiro.vision.ocr（OCR 后端选择）/
hiro.run_hif（入口）/ hiro.real_click / hiro.tap，以及下面 ALLOW 里写明原因的几处。新加的平台差异放进设备层或配置
（scripts/play.ps1、环境变量），决策 / 策略层只读配置。"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "hiro"
LAYERS = ("brain", "produce", "sim", "state", "defaults", "exec", "contracts")
PATTERN = re.compile(r"sys\.platform|platform\.system\(|os\.name\b|is_playcover\(|is_dmm\(|is_mumu\(|HIRO_DEVICE")
ALLOW = {
    # LLM 适配层（不是决策）：找 codex 可执行文件，Windows 上在 Codex 应用包里
    ("brain/llm.py", "os.name"),
}


def _code(line: str) -> str:
    """去掉行尾注释和字符串里的说明，只看代码。"""
    return re.sub(r"#.*$", "", line)


def test_decision_and_strategy_layers_have_no_platform_branch():
    hits = []
    for layer in LAYERS:
        for f in sorted((SRC / layer).rglob("*.py")):
            rel = f.relative_to(SRC).as_posix()
            in_doc = False
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if line.count('"""') % 2 == 1:
                    in_doc = not in_doc
                    continue
                if in_doc:
                    continue
                m = PATTERN.search(_code(line))
                if m and (rel, m.group(0)) not in ALLOW:
                    hits.append(f"{rel}:{n}: {line.strip()}")
    assert not hits, "决策 / 策略 / 执行层出现平台判断（应放设备层或配置）：\n" + "\n".join(hits)
