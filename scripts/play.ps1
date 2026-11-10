# Windows 上开一局（PowerShell 版启动脚本）：设好环境变量后调 tools\play_game.py，日志写到 reports\live\play-<tag>.out。
# 缺省就是作者 Windows 台式机上夺冠用的配置（2026-11-09 win21：MuMu 6.8 + RapidOCR + Codex，只打本战，R1 第 1、两轮合计第 1）。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\play.ps1 -Idol i_card-skin-shro-3-007 -MainOnly -Memory <Vo,Da,Vi> -Tag 1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\play.ps1 -Idol i_card-skin-shro-3-007 -Tag 2          # 整局：选拔 + 本战
#
# 参数：
#   -Idol        偶像卡 skin id（必填）
#   -MainAttr    主属性 Vo / Da / Vi（缺省 Da，按支援卡编成定）
#   -MainOnly    只打本战（用已有的选拔回忆）；不加就整局
#   -Memory      本战要核对的选拔回忆 Vo,Da,Vi；不给就在开始确认页读
#   -Tag         trace 名里的局号
#   -SupportSet / -MemorySet  支援卡 / 回忆编成号（缺省 4 / 5，见 AGENTS.md「开局编成」）
#   -Device      HIRO_DEVICE（缺省 mumu；也可 adb / leidian / dmm，dmm 还没实机验证）
#   -Llm         HIRO_LLM（缺省 codex；用 Claude Code 写 claude:sonnet）
#   -Proxy       给 LLM 调用设 HTTPS_PROXY / HTTP_PROXY（国内网络通常要）
#   -Relaunch    游戏被杀 / 闪退时自己拉起、回首页续跑（HIRO_RELAUNCH_GAME=1；缺省关，关着时讲清原因停下）
#   -NoSim       关掉模拟器（轮间 / 饮料 / 相談按模拟、考试预演），只按社区规则；内存紧时用
#   -Wait        前台等跑完再返回（缺省后台起进程，立刻返回 PID）
#   其余参数原样交给 run_hif（写在 -- 后面），如 -- --no-llm
#
# 为什么是这些缺省（Windows 实测，2026-11-07–09，22 局 3 次夺冠）：
#   HIRO_SCREENSHOT=adb：Windows + MuMu 6 用 nemu_ipc 时截图正常但点击送不到游戏（kaa_env 缺省已是 adb，这里写明）。
#   HIRO_SIM_WORKERS=1：模拟器每个 worker 约 600 MB；16 GB 内存的机器多开时 MuMu 会被系统杀掉。
#   HIRO_EXAM_SIM_ROUNDS=r2：R1 开考试预演会早兑现（win20 R1 只得 8.2 万），只在 R2 开。
#   HIRO_EXAM_PLAYBOOK / HIRO_R1_TURN_PROXY / HIRO_CHANGE_PICK_BY_CULL：考试按攻略打法、R1 回合属性替身、换牌按精简牌组挑，三局夺冠都开着。
#   内存吃紧时 MuMu 会杀游戏：加 -Relaunch 让程序自己拉起。
param(
    [Parameter(Mandatory = $true)][string]$Idol,
    [ValidateSet("Vo", "Da", "Vi")][string]$MainAttr = "Da",
    [switch]$MainOnly,
    [string]$Memory = "",
    [string]$Tag = "",
    [string]$SupportSet = "4",
    [string]$MemorySet = "5",
    [ValidateSet("mumu", "adb", "leidian", "dmm")][string]$Device = "mumu",
    [string]$Llm = "codex",
    [string]$Proxy = "",
    [switch]$Relaunch,
    [switch]$NoSim,
    [switch]$Wait,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "没有 .venv：先跑 scripts\setup.ps1" }

$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$env:HIRO_DEVICE = $Device
if ($Device -eq "mumu" -and -not $env:HIRO_SCREENSHOT) { $env:HIRO_SCREENSHOT = "adb" }
$env:HIRO_SUPPORT_SET = $SupportSet; $env:HIRO_MEMORY_SET = $MemorySet
$env:HIRO_LLM = $Llm
if ($Proxy) { $env:HTTPS_PROXY = $Proxy; $env:HTTP_PROXY = $Proxy; $env:NO_PROXY = "127.0.0.1,localhost" }
$env:HIRO_EXAM_PLAYBOOK = "1"; $env:HIRO_R1_TURN_PROXY = "1"; $env:HIRO_CHANGE_PICK_BY_CULL = "1"; $env:HIRO_INTERVAL_REFRESH = "1"
if ($Relaunch) { $env:HIRO_RELAUNCH_GAME = "1" }
if ($NoSim) {
    $env:HIRO_SIM = "0"
    Remove-Item Env:HIRO_EXAM_STATE_SOURCE -ErrorAction SilentlyContinue
} else {
    Remove-Item Env:HIRO_SIM -ErrorAction SilentlyContinue
    $env:HIRO_SIM_WORKERS = "1"; $env:HIRO_EXAM_SIM_ROUNDS = "r2"
    $env:HIRO_EXAM_STATE_SOURCE = "hiro.sim.screen_source:SimScreenExamStateSource"
}

$argv = @("tools\play_game.py", "--idol", $Idol, "--main-attr", $MainAttr)
if ($MainOnly) { $argv += "--main-only" }
if ($Memory) { $argv += @("--memory", $Memory) }
if ($Tag) { $argv += @("--tag", $Tag) }
if ($Rest) { $argv += @("--") + ($Rest | Where-Object { $_ -ne "--" }) }

$logs = Join-Path $Root "reports\live"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$name = if ($Tag) { "play-$Tag" } else { "play-$(Get-Date -Format yyyyMMdd-HHmmss)" }
$out = Join-Path $logs "$name.out"; $err = Join-Path $logs "$name.err"
Write-Host "HIRO_DEVICE=$Device LLM=$Llm 支援 $SupportSet / 回忆 $MemorySet 模拟器$(if ($NoSim) { '关' } else { '开（R2 预演）' })"
Write-Host "日志：$out"
$p = Start-Process -FilePath $Py -ArgumentList $argv -WorkingDirectory $Root -NoNewWindow -PassThru `
    -RedirectStandardOutput $out -RedirectStandardError $err
if ($Wait) {
    $p.WaitForExit()
    "EXIT $($p.ExitCode) $(Get-Date -Format HH:mm:ss)" | Out-File -Append -Encoding utf8 $out
    exit $p.ExitCode
}
Write-Host "已在后台开局，PID $($p.Id)（看进度：Get-Content -Wait $out）"
