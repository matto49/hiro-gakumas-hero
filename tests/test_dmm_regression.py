"""DMM（Windows 版客户端）实机帧识别回归集（目标 windows-and-sync §1 第 3 条 / W3）。

样例清单 tests/dmm_regression.json：每条一帧（来自 DMM 实机 trace，平台模块传回），期望值是 Mac 识别读数逐条人工核过的结果，
note 写出处和核对要点。帧本身是游戏画面，不随仓库发布：放在 tests/fixtures/dmm/，没有就跳过。
同一套断言在 Mac（Vision OCR）和 Windows（RapidOCR）都跑；只在一边通不过的是平台差异，修好前在清单里标 xfail_on（附原因）；
标了 xfail_on 的条目照样读帧比对，读对了反而报错（strict），提醒去掉标记。
新出的 DMM 识别问题：先用 tools/audit/dmm_samples.py 把那一帧加进清单、核对期望，再修。"""

import json
import sys
from pathlib import Path

import pytest

from tools.audit.dmm_samples import diff, read

HERE = Path(__file__).parent
SAMPLES = json.loads((HERE / "dmm_regression.json").read_text(encoding="utf-8"))
FIX = HERE / "fixtures" / "dmm"
PLATFORM = "windows" if sys.platform == "win32" else "mac"


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["file"] for s in SAMPLES])
def test_dmm_frame(sample):
    path = FIX / sample["file"]
    if not path.is_file():
        pytest.skip(f"需要样例 tests/fixtures/dmm/{sample['file']}（DMM 实机帧，不随仓库发布）")
    why = (sample.get("xfail_on") or {}).get(PLATFORM)
    got = read(path, sample.get("page"))
    problems = []
    for key in ("quick", "tap", "scene"):
        if key in sample and got[key] != sample[key]:
            problems.append(f"{key}: 期望 {sample[key]!r}，读到 {got[key]!r}")
    for key in ("quick_fields", "fields"):
        if key in sample:
            problems += diff(sample[key], got[key], key)
    if why:
        # 先读帧再判 xfail（strict）：标了 xfail 的条目修好后会报出来，提醒去掉标记
        assert problems, f"{sample['file']} 已经能读对，去掉 dmm_regression.json 里的 xfail_on.{PLATFORM}（原因：{why}）"
        pytest.xfail(why)
    assert not problems, f"{sample['file']}（{sample.get('note', '')}）：\n" + "\n".join(problems)
