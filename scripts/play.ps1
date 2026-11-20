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
#   -Device      HIRO_DEVICE（缺省 mumu；也可 adb / leidian / dmm）。dmm = DMM 版：游戏以管理员身份运行，
#                本脚本会自己申请管理员权限（弹一次 UAC）后在新窗口里开局；先关掉 localify 一类改界面文字的插件
#   -Runtime     kaa（缺省，KAA 主循环）/ own（新主循环 hiro.runtime，不用 KAA；-Device dmm 时设备走 MaaFramework，要先装 maafw）
#   -Llm         HIRO_LLM（缺省 codex；用 Claude Code 写 claude:sonnet）
#   -Proxy       给 LLM 调用设 HTTPS_PROXY / HTTP_PROXY（国内网络通常要）
#   -Relaunch    游戏被杀 / 闪退时自己拉起、回首页续跑（HIRO_RELAUNCH_GAME=1；缺省关，关着时讲清原因停下）
#   -NoSim       关掉模拟器（轮间 / 饮料 / 相談按模拟、考试预演），只按社区规则；内存紧时用
#   -Wait        前台等跑完再返回（缺省后台起进程，立刻返回 PID）
#   -NoSettingsCheck  跳过开局前的设置检查（tools/check_settings.py；它只在首页做，不在首页会自己跳过）
#   -AllowDiscard  开局时其他端末有进行中的培育就破弃它（run_hif --allow-discard；不可撤回，只在确认不要那局时加）
#   暂停：缺省设 HIRO_PAUSE_FLAG=%LOCALAPPDATA%\hiro\pause.flag（已设就用已设的）；建这个文件就停在下一个检查点，删掉继续
#   -ScheduleProbe  每场考试第 1 回合出牌前点开审查日程一次、存帧、关掉（HIRO_EXAM_SCHEDULE_PROBE=1，测试局用；关不掉会停机）
#   -RunHifArgs  原样交给 run_hif 的其他参数，一个字符串，如 -RunHifArgs "--no-llm"
#   -CloseGame   跑完（不管成败）关什么：off（缺省）/ emulator（MuMu 关模拟器、DMM 关游戏 + DMM Game Player）/ game（只关游戏）
#                （HIRO_CLOSE_GAME，见 hiro.loop.close_game；外面已设且没给这个参数时用外面的）
#   -UnreadStory 未读剧情：skip（缺省，照旧跳过）/ stop（点キャンセル、暂停等人、发通知；HIRO_UNREAD_STORY，见 hiro.loop.unread_story）
#   -UnreadTimeout  stop 时剧情框文字多少秒没变（没人推进剧情）就关游戏（缺省 60；HIRO_UNREAD_TIMEOUT_S）。等人文件 %LOCALAPPDATA%\hiro\waiting-for-user.txt，删掉就跳过这段继续
#
# 为什么是这些缺省（Windows 实测，2026-11-07–09，22 局 3 次夺冠）：
#   HIRO_SCREENSHOT=adb：Windows + MuMu 6 用 nemu_ipc 时截图正常但点击送不到游戏（kaa_env 缺省已是 adb，这里写明）。
#   HIRO_SIM_WORKERS=1：模拟器每个 worker 约 600 MB；16 GB 内存的机器多开时 MuMu 会被系统杀掉。
#   HIRO_EXAM_SIM_ROUNDS=r2：R1 开考试预演会早兑现（win20 R1 只得 8.2 万），只在 R2 开。
#   HIRO_EXAM_PLAYBOOK / HIRO_R1_TURN_PROXY：考试按攻略打法、R1 回合属性替身，三局夺冠都开着。
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
    [ValidateSet("kaa", "own")][string]$Runtime = "kaa",
    [string]$Llm = "codex",
    [string]$Proxy = "",
    [switch]$Relaunch,
    [switch]$NoSim,
    [switch]$Wait,
    [switch]$NoSettingsCheck,
    [switch]$ScheduleProbe,
    [switch]$AllowDiscard,
    [string]$RunHifArgs = "",
    [ValidateSet("off", "emulator", "game")][string]$CloseGame = "off",
    [ValidateSet("skip", "stop")][string]$UnreadStory = "skip",
    [int]$UnreadTimeout = 60
)
$ErrorActionPreference = "Stop"
# DMM 版游戏以管理员身份运行，普通权限的进程截图可以、点击会被系统挡掉（UIPI）：不是管理员就带着同样的参数提权重开
if ($Device -eq "dmm" -and -not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $pass = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    foreach ($kv in $PSBoundParameters.GetEnumerator()) {
        if ($kv.Value -is [switch]) { if ($kv.Value) { $pass += "-$($kv.Key)" } }
        else { $pass += @("-$($kv.Key)", "`"$($kv.Value)`"") }
    }
    Write-Host "DMM 版要管理员权限：在弹出的 UAC 里点「是」，开局在新窗口里进行"
    Start-Process powershell -Verb RunAs -ArgumentList $pass
    exit 0
}
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "没有 .venv：先跑 scripts\setup.ps1" }

$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$env:HIRO_DEVICE = $Device
$env:HIRO_RUNTIME = $Runtime  # own = 新主循环（hiro.runtime）；以参数传而不是外面设环境变量：DMM 提权重开（RunAs）带不过环境变量
if ($Device -eq "mumu" -and -not $env:HIRO_SCREENSHOT) { $env:HIRO_SCREENSHOT = "adb" }
# DMM 版截图很快（PrintWindow 约 0.19s），考试稳帧 3 次读数不到 1 秒，会在两段动画之间判稳、回合没切完就决策：要求一致读数持续 1.5s
if ($Device -eq "dmm" -and -not $env:HIRO_SETTLE_MIN_S) { $env:HIRO_SETTLE_MIN_S = "1.5" }
# DMM 窗口（用户 11-14）：游戏客户区按显示器自适应，9:16，高 = 工作区高 - 外框高，上限 1080x1920，下限 540x960。
# -Runtime own 开局时由 hiro.runtime.play.fit_dmm_window 核对、不对就调（要管理员，本脚本已提权）；HIRO_DMM_FIT=0 关掉
if ($Device -eq "dmm") {
    & "$Py" -m hiro.devices.win_window 2>&1 | Write-Host
}
# 外部暂停旗标（hiro.loop.pause_flag）：文件在就停在下一个检查点，删掉就继续。缺省放在普通用户能写的目录，
# DMM 版 hiro 以管理员身份跑，普通权限停不了它的进程，要让它停在安全点就靠这个文件（dmmt1 想挡住花券的再挑戦时没有它）
if (-not $env:HIRO_PAUSE_FLAG) { $env:HIRO_PAUSE_FLAG = Join-Path $env:LOCALAPPDATA "hiro\pause.flag" }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $env:HIRO_PAUSE_FLAG) | Out-Null
if (Test-Path $env:HIRO_PAUSE_FLAG) { Write-Host "⚠ 暂停旗标已存在（$env:HIRO_PAUSE_FLAG）：开局前会一直等它被删掉" }
Write-Host "暂停：New-Item `"$env:HIRO_PAUSE_FLAG`"；继续：Remove-Item `"$env:HIRO_PAUSE_FLAG`""
# 审查日程探测（默认关）：每场考试第 1 回合出牌前点开左上「残りターン / 属性」一次，存帧、关掉、记耗时（hiro.exec.schedule_probe）
if ($ScheduleProbe) { $env:HIRO_EXAM_SCHEDULE_PROBE = "1" }
$env:HIRO_SUPPORT_SET = $SupportSet; $env:HIRO_MEMORY_SET = $MemorySet
$env:HIRO_LLM = $Llm
if ($Proxy) { $env:HTTPS_PROXY = $Proxy; $env:HTTP_PROXY = $Proxy; $env:NO_PROXY = "127.0.0.1,localhost" }
$env:HIRO_EXAM_PLAYBOOK = "1"; $env:HIRO_R1_TURN_PROXY = "1"
if ($Relaunch) { $env:HIRO_RELAUNCH_GAME = "1" }
# 跑完关游戏 / 未读剧情停下等人：给了参数就用参数；没给且外面没设时用缺省
if ($PSBoundParameters.ContainsKey("CloseGame") -or -not $env:HIRO_CLOSE_GAME) { $env:HIRO_CLOSE_GAME = $CloseGame }
if ($PSBoundParameters.ContainsKey("UnreadStory") -or -not $env:HIRO_UNREAD_STORY) { $env:HIRO_UNREAD_STORY = $UnreadStory }
if ($PSBoundParameters.ContainsKey("UnreadTimeout") -or -not $env:HIRO_UNREAD_TIMEOUT_S) { $env:HIRO_UNREAD_TIMEOUT_S = "$UnreadTimeout" }
if ($NoSim) {
    $env:HIRO_SIM = "0"
    Remove-Item Env:HIRO_EXAM_STATE_SOURCE -ErrorAction SilentlyContinue
} else {
    Remove-Item Env:HIRO_SIM -ErrorAction SilentlyContinue
    $env:HIRO_SIM_WORKERS = "1"; $env:HIRO_EXAM_SIM_ROUNDS = "r2"
    # 调用前已设（如私有存档插件）就保留；没设才用带模拟器的画面来源
    if (-not $env:HIRO_EXAM_STATE_SOURCE) { $env:HIRO_EXAM_STATE_SOURCE = "hiro.sim.screen_source:SimScreenExamStateSource" }
}
# HIRO_EXAM_POLICY 不在这里设：外面设了原样透传（默认 llm，见 hiro.produce.exam_policy.policy_from_env）
Write-Host "考试局面来源 HIRO_EXAM_STATE_SOURCE=$(if ($env:HIRO_EXAM_STATE_SOURCE) { $env:HIRO_EXAM_STATE_SOURCE } else { '（默认画面识别）' })；出招 HIRO_EXAM_POLICY=$(if ($env:HIRO_EXAM_POLICY) { $env:HIRO_EXAM_POLICY } else { 'llm（默认）' })"

$argv = @("tools\play_game.py", "--idol", $Idol, "--main-attr", $MainAttr)
if ($MainOnly) { $argv += "--main-only" }
if ($Memory) { $argv += @("--memory", $Memory) }
if ($Tag) { $argv += @("--tag", $Tag) }
if ($NoSettingsCheck) { $argv += "--no-settings-check" }
$extra = @()
if ($AllowDiscard) { $extra += "--allow-discard" }
if ($RunHifArgs) { $extra += ($RunHifArgs -split "\s+" | Where-Object { $_ }) }
if ($extra) { $argv += @("--") + $extra }

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
