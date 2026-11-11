# 一条命令装好实机游玩环境（Windows，PowerShell 版；步骤与 scripts/setup.sh 一一对应）：
# KAA 子模块 → .venv（Python 3.10）→ 安装 → 生成 KAA 的 R.py → 下载 KAA 公开游戏数据 + 卡图索引 → 模拟器主数据 → 自检。
# 可重复跑，做过的步骤会跳过。需要 uv、git 和网络。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Regen] [-NoData]
#
# -Regen：KAA 换版本后重新生成 R.py；-NoData：已用 HIRO_KAA_GAME_DATA 指向现成数据时跳过下载。
# Windows 上没有 PlayCover：开局前设 HIRO_DEVICE（adb / mumu / leidian / dmm），见 AGENTS.md「设备」。
param([switch]$Regen, [switch]$NoData)
$ErrorActionPreference = "Stop"
# 中文提示与内嵌 Python 都按 UTF-8 传（PowerShell 5.1 默认用 ANSI 往管道写，中文会变成问号）
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($t) { Write-Host ""; Write-Host "== $t" }
function Need($cmd, $hint) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "需要 $cmd（$hint）" } }

Need uv "https://docs.astral.sh/uv/getting-started/installation/"
Need git "https://git-scm.com/download/win"

Step "1/7 KAA 子模块 vendor/kaa"
if (Test-Path .git) {
    git submodule update --init vendor/kaa
    if ($LASTEXITCODE) { throw "拉取 vendor/kaa 失败" }
} elseif (-not (Test-Path vendor/kaa/kaa)) {
    throw "这不是 git 仓库，也没有 vendor/kaa：请用 git clone 获取本仓库（子模块由本脚本拉）"
} else {
    Write-Host "不是 git 仓库，沿用已有的 vendor/kaa"
}

Step "2/7 虚拟环境 .venv（KAA 要求 Python 3.10）"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    uv venv --python 3.10 .venv
    if ($LASTEXITCODE) { throw "建 .venv 失败" }
} else {
    Write-Host "已有 .venv（$(& $Py --version)）"
}

Step "3/7 安装本仓库与 KAA（.[dev,play]）"
uv pip install --python $Py -e ".[dev,play]"
if ($LASTEXITCODE) { throw "安装失败" }

Step "4/7 生成 KAA 的 kaa/tasks/R.py（KAA 的生成文件，子模块里没有）"
if ($Regen -or -not (Test-Path vendor/kaa/kaa/tasks/R.py)) {
    $log = Join-Path $Root ".setup-make-resources.log"
    Push-Location vendor/kaa
    try {
        & $Py -c "import hiro, runpy, sys; sys.argv = ['make_resources.py']; runpy.run_path('tools/make_resources.py', run_name='__main__')" *> $log
        if ($LASTEXITCODE) { throw "生成 R.py 失败，见 .setup-make-resources.log" }
    } finally { Pop-Location }
    Write-Host "已生成 vendor/kaa/kaa/tasks/R.py（日志 .setup-make-resources.log）"
} else {
    Write-Host "已有 vendor/kaa/kaa/tasks/R.py（KAA 换版本后用 -Regen 重新生成）"
}

Step "5/7 KAA 公开游戏数据（game.db + 卡图 → resources/game_data，首次约 2–3 分钟，含卡图索引）"
if ($NoData) {
    Write-Host "跳过（-NoData）"
} else {
    @'
import hiro  # noqa: F401
from kaa.game_data.updater import GameDataUpdater, apply_pending, has_usable_baseline

u = GameDataUpdater()
r = u.check_only()  # 和 KAA 图形界面同一套：kotonebot/kaa-game-data 最新 release
missing = r is not None and (r.needs_db or any(r.category_missing.values()))
if r is None or not (r.needs_update or missing):
    print("游戏数据已是最新" if has_usable_baseline() else "没查到可下载的游戏数据（网络？）")
else:
    shown = {}

    def progress(name, cur, total):
        step = total // 4 if total else 0
        if total and (cur == total or cur - shown.get(name, 0) >= step):
            shown[name] = cur
            print(f"  {name} {cur * 100 // total}%", flush=True)

    u.download_to_staging(r, file_progress_cb=progress)
    print("已下载并应用" if apply_pending() else "下载完成，但没能应用（见上面的日志）")
if not has_usable_baseline():
    raise SystemExit("游戏数据不可用：可稍后重跑本脚本，或用 HIRO_KAA_GAME_DATA 指向已有的 game_data 目录")
'@ | & $Py -
    if ($LASTEXITCODE) { throw "下载游戏数据失败" }
}

Step "6/7 模拟器主数据（社区公开 master，vertesan/gakumasu-diff 固定提交 → vendor/gakumas-player/data/master，首次约 216 MB / 2 分钟）"
if ($NoData) {
    Write-Host "跳过（-NoData）：模拟相关决策（轮间、饮料、考试预演）会退回社区规则"
} else {
    & $Py vendor/gakumas-player/scripts/fetch_master.py
    if ($LASTEXITCODE) { Write-Host "  ⚠ 模拟器主数据没装好（网络？）：可稍后重跑本脚本；没有时模拟相关决策退回社区规则" }
}
# 考试引擎的 Rust 后端（可选，快约 8 倍）：有 Rust 工具链就编译装进 .venv；没有就下预编译包（GitHub Release，按源码哈希对版本、校验 sha256）；
# 都不行就用 Python 版（同一套规则，结果相同）
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    Write-Host "  编译考试引擎 Rust 后端（vendor/gakumas-player/gakumas_exam_rs，首次约 1 分钟）…"
    uv pip install --python $Py -q maturin
    $env:VIRTUAL_ENV = Join-Path $Root ".venv"
    & $Py -m maturin develop --release -q -m vendor/gakumas-player/gakumas_exam_rs/Cargo.toml
    if ($LASTEXITCODE) { Write-Host "  ⚠ Rust 后端没编译成：考试引擎用 Python 版（结果相同，慢一些）" }
} else {
    Write-Host "  没有 Rust 工具链（cargo）：下载预编译的 Rust 后端…"
    & $Py scripts/engine_rs.py install --python $Py
    if ($LASTEXITCODE) { Write-Host "  考试引擎用 Python 版（结果相同，慢一些）；装了 Rust（https://rustup.rs）后重跑本脚本可本地编译" }
}

Step "7/7 自检"
@'
import os
import sys

import hiro  # noqa: F401
from kaa.tasks import R  # noqa: F401  生成文件可用

import hiro.run_hif  # noqa: F401  入口能导入（策略、控制器、KAA 补丁、执行器）
from hiro.loop import manifest

m = manifest.collect()
print(f"插件：资产 {m.asset_source}；局面 {m.exam_state_source}；出招 {m.exam_advisor}")
print(f"主数据 game.db：{'有' if m.master_db else '没有'}；随仓库素材包：{m.bundled_assets or '没有'}；自建素材：{m.user_assets or '没有'}")
for p in m.problems:
    print(f"  ⚠ {p}")
if not os.environ.get("HIRO_DEVICE"):
    print("  ⚠ 开局用 scripts\\play.ps1 -Device mumu / dmm（会设好 HIRO_DEVICE 等环境变量），见 AGENTS.md「设备」。"
          "MuMu 与 DMM 版作者在 Windows 上实机打过；adb / 雷电走 KAA 的设备工厂，没有实机验证过")
from hiro.vision import ocr
print(f"文字识别后端：{ocr.engine()}")
import hiro.sim
print(f"模拟器：引擎 {hiro.sim.ROOT}；主数据 {'已构建' if hiro.sim.master_ready() else '没有（模拟相关决策退回社区规则）'}")
from pathlib import Path
if not (Path("data") / "value_tables" / "drink_scale.json").exists():
    print("  ⚠ 没有 data/value_tables/（价值表随仓库发布）：轮间饮料增益不校准，请用 git 拿完整仓库")
print("自检通过：入口和 KAA 都能导入")
'@ | & $Py -
if ($LASTEXITCODE) { throw "自检失败" }

Write-Host ""
Write-Host "装好了。开一局（先在游戏里编好支援卡 / 回忆编成，缺省用编成 4 / 5；参数见 scripts\play.ps1 开头）："
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\play.ps1 -Device mumu -Idol <偶像卡 skin id> -Tag 1'
Write-Host '  （DMM 版用 -Device dmm，会弹一次 UAC；只打本战加 -MainOnly）'
Write-Host "离线测试：.venv\Scripts\python.exe -m pytest -q"
