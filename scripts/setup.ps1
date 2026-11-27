# 一条命令装好实机游玩环境（Windows，PowerShell 版；步骤与 scripts/setup.sh 一一对应）：
# .venv（Python 3.10）→ 安装 → OCR 模型 → 识别用的游戏原图（resources/game_data）→ 模拟器主数据 + 主数据库 → 自检。
# 可重复跑，做过的步骤会跳过。需要 uv 和网络（不需要 git）。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1 [-NoData] [-OcrDevice auto|dml|cpu]
#
# -NoData：已用 HIRO_KAA_GAME_DATA / HIRO_MASTER_DB 指向现成数据时跳过下载。
# -OcrDevice：文字识别（RapidOCR）用的 onnxruntime。cpu（缺省）= 保持 CPU 版；auto / dml = 换成同版本的 onnxruntime-directml，有显卡就用 GPU
#   （DirectML，任意 DX12 显卡；整帧识别约 2 s → 0.4 s，CPU 占用和风扇声下降），没有时运行时自己退回 CPU。缺省不换：识别阈值、放大倍数都是按 CPU 版调的，GPU 版先显式打开、回归一致后再讨论默认开。
#   运行时再用 HIRO_OCR_DEVICE（auto / cuda / dml / cpu）选，见 hiro.vision.ocr.ocr_device_order。
#   CUDA 版（onnxruntime-gpu）要和 play 组钉的 1.14 及本机 CUDA 11.x / cuDNN 8 对得上，不自动装。
# 环境变量 HIRO_SETUP_EXTRAS：装哪些可选组，缺省 dev,play；桌面版给玩家装时传 play（不装 pytest / ruff / basedpyright 等开发工具）。
# -NoSimd：不装 SIMD 文字识别（下面第 2 步里的可选项）；桌面版安装程序把它当单独一步装，所以传这个开关。
# Windows 上没有 PlayCover：开局前设 HIRO_DEVICE（adb / mumu / leidian / dmm），见 AGENTS.md「设备」。
param([switch]$NoData, [switch]$NoSimd, [ValidateSet("auto", "dml", "cpu")][string]$OcrDevice = "cpu")
$ErrorActionPreference = "Stop"
# 中文提示与内嵌 Python 都按 UTF-8 传（PowerShell 5.1 默认用 ANSI 往管道写，中文会变成问号）
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($t) { Write-Host ""; Write-Host "== $t" }
function Need($cmd, $hint) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "需要 $cmd（$hint）" } }

Need uv "https://docs.astral.sh/uv/getting-started/installation/"

Step "1/6 虚拟环境 .venv（Python 3.10）"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    uv venv --quiet --python 3.10 .venv
    if ($LASTEXITCODE) { throw "建 .venv 失败" }
} else {
    Write-Host "已有 .venv（$(& $Py --version)）"
}

$Extras = if ($env:HIRO_SETUP_EXTRAS) { $env:HIRO_SETUP_EXTRAS } else { "dev,play" }
Step "2/6 安装本仓库（.[$Extras]）"
uv pip install --python $Py -e ".[$Extras]"
if ($LASTEXITCODE) { throw "安装失败" }
# RapidOCR 后端的识别模型：从本仓库 GitHub Release 下载、校验 SHA256（没装 SIMD 时 Windows 缺省用它）
& $Py tools\assets\rapid_models.py
if ($LASTEXITCODE) { throw "RapidOCR 识别模型下载失败（网络？）：可稍后重跑本脚本，或手动 python tools\assets\rapid_models.py" }
# SIMD 文字识别（Windows 缺省的识别后端）：辅助程序 + 模型 + .NET 10 Runtime，装在 .deps，不要管理员权限。可选：装不上不让安装失败，识别退回 RapidOCR
if ($NoSimd) {
    Write-Host "  跳过 SIMD 文字识别（-NoSimd）"
} else {
    Write-Host "  SIMD 文字识别（Windows 缺省，可选；约 100 MB，另需 .NET 10 Runtime）…"
    & $Py tools\assets\simd_ocr.py
    if ($LASTEXITCODE) { Write-Host "  ⚠ SIMD 文字识别没装上（原因见上面几行）：识别用 RapidOCR，不影响使用；网络好了重跑本脚本即可" }
}

Step "3/6 识别用的游戏原图（本仓库 Release game-images-b9577da：技能卡 / 偶像卡 / 饮料 → resources/game_data，约 125 MB）"
if ($NoData) {
    Write-Host "跳过（-NoData）"
} else {
    & $Py tools\assets\game_images.py
    if ($LASTEXITCODE) { Write-Host "  ⚠ 游戏原图没下好（网络？）：卡面 / 偶像 / 饮料识别会退回 OCR；可稍后重跑本脚本" }
}

Step "4/6 模拟器主数据 + 主数据库（社区公开 master，vertesan/gakumasu-diff 固定提交 → vendor/gakumas-player/data/master，首次约 216 MB / 2 分钟；再生成 cache\master\game.db）"
if ($NoData) {
    Write-Host "跳过（-NoData）：模拟相关决策（轮间、饮料、考试预演）会退回社区规则"
} else {
    & $Py vendor/gakumas-player/scripts/fetch_master.py
    if ($LASTEXITCODE) {
        Write-Host "  ⚠ 模拟器主数据没装好（网络？）：可稍后重跑本脚本；没有时主数据查不到、模拟相关决策退回社区规则"
    } else {
        & $Py -m hiro.state.master_db
        if ($LASTEXITCODE) { Write-Host "  ⚠ 主数据库没生成成功：卡名 / 效果查不到，可稍后重跑本脚本" }
    }
}

Step "5/6 考试引擎 Rust 后端与考试出牌算法（都可选）"
# 考试引擎的 Rust 后端（可选，快约 8 倍）：有 Rust 工具链就编译装进 .venv；没有就下预编译包（GitHub Release，按源码哈希对版本、校验 sha256）；
# 都不行就用 Python 版（同一套规则，结果相同）
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    Write-Host "  编译考试引擎 Rust 后端（vendor/gakumas-player/gakumas_exam_rs，首次约 1 分钟）…"
    uv pip install --python $Py -q maturin
    $env:VIRTUAL_ENV = Join-Path $Root ".venv"
    & $Py -m maturin develop --release -q -m vendor/gakumas-player/gakumas_exam_rs/Cargo.toml
    if ($LASTEXITCODE) {
        # 本地编译不成（N45，G3 第 3 轮 11-26：gnu 工具链没 gcc，mimalloc 编不了）就改下预编译包，都不行才用 Python 版
        Write-Host "  Rust 后端没编译成，改下载预编译包…"
        & $Py scripts/engine_rs.py install --python $Py
        if ($LASTEXITCODE) { Write-Host "  ⚠ Rust 后端没装上：考试引擎用 Python 版（结果相同，慢一些）" }
    }
} else {
    Write-Host "  没有 Rust 工具链（cargo）：下载预编译的 Rust 后端…"
    & $Py scripts/engine_rs.py install --python $Py
    if ($LASTEXITCODE) { Write-Host "  考试引擎用 Python 版（结果相同，慢一些）；装了 Rust（https://rustup.rs）后重跑本脚本可本地编译" }
}

# 考试出牌：gakumas-bench 的开源算法（缺省 UINet，默认开；HIRO_BENCH_ADVISOR=0 关）。装不上不影响开局：考试出牌退回社区规则
Write-Host "  考试出牌算法（gakumas-bench + UINet 模型 + 主数据导出）…"
uv pip install --python $Py -q -e ".[bench]"
if ($LASTEXITCODE) {
    Write-Host "  ⚠ gakumas-bench / UINet 没装上：考试出牌会退回社区规则"
} else {
    & $Py -m hiro.bench_bridge setup
    if ($LASTEXITCODE) { Write-Host "  ⚠ UINet 模型 / 主数据没准备好（原因见上面几行）：考试出牌会退回社区规则；修好后重跑本脚本" }
}

# 文字识别用 GPU（DirectML）：play 组依赖的是 onnxruntime（CPU 版），第 2 步每次都会把它装回来；两个包装在同一个 onnxruntime 目录里，
# 所以先卸 CPU 版、再按同一个版本重装 DirectML 版（--reinstall-package 只重装它自己、把被覆盖的文件补回来；整体 --reinstall 会把 numpy 升到 2.x，1.14 读不了）
if ($OcrDevice -ne "cpu") {
    $OrtVer = & $Py -c "import importlib.metadata as m; print(m.version('onnxruntime'))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $OrtVer) {
        Write-Host "  文字识别改用 onnxruntime-directml==$OrtVer（GPU；没有可用显卡时运行时退回 CPU）…"
        uv pip uninstall --python $Py -q onnxruntime
        uv pip install --python $Py -q --reinstall-package onnxruntime-directml "onnxruntime-directml==$OrtVer"
        if ($LASTEXITCODE) {
            Write-Host "  ⚠ onnxruntime-directml 没装上，装回 CPU 版"
            uv pip install --python $Py -q "onnxruntime==$OrtVer"
        }
    } else {
        Write-Host "  文字识别：onnxruntime-directml 已装（或没有 onnxruntime），跳过"
    }
}

Step "6/6 自检"
@'
import os
import sys

import hiro.run_hif  # noqa: F401  入口能导入（策略、控制器、执行器）
from hiro.loop import manifest

m = manifest.collect()
print(f"插件：资产 {m.asset_source}；局面 {m.exam_state_source}；出招 {m.exam_advisor}")
print(f"主数据库：{'有' if m.master_db else '没有'}；随仓库素材包：{m.bundled_assets or '没有'}；自建素材：{m.user_assets or '没有'}")
for p in m.problems:
    print(f"  ⚠ {p}")
if not os.environ.get("HIRO_DEVICE"):
    print("  设备：没设 HIRO_DEVICE 时按 DMM 版连游戏；用 MuMu 等模拟器时开局用 scripts\\play.ps1 -Device mumu（会设好 HIRO_DEVICE），见 AGENTS.md「设备」。"
          "MuMu 与 DMM 版作者在 Windows 上实机打过；adb / 雷电没有实机验证过")
from hiro.vision import ocr
print(f"文字识别后端：{ocr.engine()}")
import hiro.sim
print(f"模拟器：引擎 {hiro.sim.ROOT}；主数据 {'已构建' if hiro.sim.master_ready() else '没有（模拟相关决策退回社区规则）'}")
from pathlib import Path
if not (Path("data") / "value_tables" / "drink_scale.json").exists():
    print("  ⚠ 没有 data/value_tables/（价值表随仓库发布）：轮间饮料增益不校准，请用完整源码（git clone 整个仓库，或下载 GitHub 的源码压缩包）")
print("自检通过：入口能导入")
'@ | & $Py -
if ($LASTEXITCODE) { throw "自检失败" }

Write-Host ""
Write-Host "装好了。开一局（先在游戏里编好支援卡 / 回忆编成，缺省用编成 4 / 5；参数见 scripts\play.ps1 开头）："
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\play.ps1 -Device mumu -Idol <偶像卡 skin id> -Tag 1'
Write-Host '  （DMM 版用 -Device dmm，会弹一次 UAC；只打本战加 -MainOnly）'
if ($Extras -match "dev") { Write-Host "离线测试：.venv\Scripts\python.exe -m pytest -q" }
