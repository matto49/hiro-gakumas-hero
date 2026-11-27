# AGENTS.md：给接手这个仓库的 AI

你拿到的是「数字小广」（hiro-gakumas-hero）的开源部分：一套让大模型自动玩《学园偶像大师》H.I.F 模式的框架。
读完这份文件，你应该能装好环境、在使用者自己的 Mac 上打一局，并在出问题时知道去哪里查。

先读这三份：

1. 本文件（规则、架构、怎么跑、怎么查）。
2. `docs/assets.md`：识别要用的素材从哪来、缺了会怎样、怎么补。
3. `docs/hif-playbook.md`：H.I.F 的社区打法（策略层的先验）。

## 一、绝对不能做的事（代码里有拦截，你也不要绕过）

这些操作不可撤销或会花真钱，任何时候都不做；遇到需要做的情况，停下来交给使用者。

| 规则 | 代码里的拦截 |
|---|---|
| 不用ジュエル（付费货币）。AP 不够只喝 APドリンク；确认框 / 再挑戦按钮带ジュエル就点キャンセル | `loop/ap_refill.py`、`produce/hif.py`（再挑戦）、考试危险词 |
| 不按「破棄」「リタイア」，不放弃进行中的培育 | `runtime/guard.py`（危险弹窗 / 危险字样拦点击）、`loop/rescue.py` |
| 在别的设备开着的培育：开局确认页绝不按「開始する」（会破弃另一台设备的进度），停下来 | `runtime/opening.py`（点キャンセル并停）、`runtime/guard.py` |
| 通信错误弹窗不点「タイトルへ」 | `loop/comm_error.py`、`loop/rescue.py` |
| LLM 兜底只能点 OCR 识别出的文字框或固定空白处；选择页、考试画面不点；点之前重新截图核对；次数有上限 | `loop/rescue.py` |
| 再挑戦只用免费次数 / 再挑戦チケット，最多 3 次 | `produce/hif.py` |
| 不默认选第一个：换卡 / 删卡认不出或全是保护卡就キャンセル；租借格挑不到就停；考试决策越界时按规则重选，不退回第一个合法动作 | `produce/`、`defaults/advisor.py` |

另外：

- 不要把截图、trace、账号、密钥、主数据原文件提交进仓库。`.gitignore` 已挡住 `resources/`、`tests/fixtures/`、`cache/`、`traces/`。
  `assets/pack/` 是仓库有意带的识别素材包（权利属于游戏的权利人，见 `assets/pack/README.md`），除此之外不要再往仓库里加游戏素材或截图。
- 代码不依赖 KAA（kotones-auto-assistant）：2026-11-24 删了 KAA 主循环和运行时补丁，2026-11-25 分 4 批去掉了剩下的
  策略类壳、日常任务、OCR 模型、主数据和 `vendor/kaa` 子模块。移植自 KAA 的文件保留 GPL-3.0 文件头。
  `git grep -nE "(from|import) (kaa|kotonebot)" -- src tools scripts app` 应该为空，别再加回去。

## 二、架构

```
                       ┌───────────── 策略层 hiro.brain / hiro.defaults.advisor ─────────────┐
                       │ 社区规则（docs/hif-playbook.md）→ 规则链 → LLM 兜底（hiro.brain.llm） │
                       └──────────────▲────────────────────────────────┬──────────────────┘
                                      │ GameState / BattleState        │ Decision（契约）
截图 ─► 识别层 hiro.vision / perception ─► Observation ─► reduce（纯函数）─► 状态 hiro.state
           ▲ 素材：插件 AssetSource                              │
           │                                                      ▼
设备 ◄── 操作层 hiro.devices / tap / exec / loop / produce ◄── 执行器（点击、等稳、补点、弹窗、恢复）
                                      │
                       trace（每一步：截图 + 观测 + 状态 + 决策 + 执行结果）
                                      ▼
                 复盘页 tools/viewer · 审计 tools/audit · 回放 hiro.loop.replay
```

- **契约**（`hiro/contracts`）是层与层之间唯一的接口。读不到的字段是 `None`，不要猜。
- **插件**（`hiro/plugins.py`）：五处可以换实现，主循环不用改。写法见 [docs/plugins.md](docs/plugins.md)。

| 插件 | 回答什么 | 开源默认实现 |
|---|---|---|
| `asset_source` | 素材和主数据从哪来 | `defaults/assets.py`：公开游戏图 + 从模拟器主数据生成的主数据库 + 使用者自建素材 |
| `exam_state_source` | 考试局面从哪来 | `defaults/exam_state.py`：画面识别 + 状态机记账（不知道牌库顺序）。另有 `hiro.sim.screen_source:SimScreenExamStateSource`：同样从画面读，再用模拟器跟踪局面、给每个候选动作附预演（不读存档） |
| `exam_advisor` | 考试每一步怎么打 | `defaults/advisor.py`：社区规则链 + LLM 兜底 |
| `observation_source` | 不看画面的数据来源（hook、存档…），按页面给字段，和画面识别走同一个 reduce | 没有（只用画面） |
| `produce_advisor` | 育成某几页怎么选（按页面接管，返回 None 交回内置） | 没有（`brain/deciders.py` 内置决策器） |

  换实现：环境变量 `HIRO_ASSET_SOURCE` / `HIRO_EXAM_STATE_SOURCE` / `HIRO_EXAM_ADVISOR`（写成 `包.模块:类名`），
  或在 `pyproject.toml` 的入口点组 `hiro_gakumas.plugins` 里登记，或代码里 `hiro.plugins.register(...)`。
  后两种（以及按 id 选的出招插件）用 `HIRO_HOME/strategy.json` / `HIRO_STRATEGY_FILE` 按决策点选用（入口点名字写 `<种类>.<id>`），
  可以混用；每一步记录是哪个插件定的，每段 trace 有 `plugins.json`。插件给了画面上看不到的信息（`hidden`）时，这一段对局打 `uses_hidden`，战绩单独统计。

- **模拟器**（`hiro/sim`，考试引擎 `vendor/gakumas-player/gakumas_exam`（Rust 后端 `gakumas_exam_rs`：有 cargo 本地编，没有就下预编译包 `scripts/engine_rs.py install`，都不行用 Python 版）、
  培育层公式 `gakumas_produce`，主数据由 setup 第 4 步从社区公开的 gakumasu-diff 下载构建）：
  轮间买什么 / 刷不刷货架、饮料取舍、本战相談按模拟算第二轮期望分（`HIRO_SIM=0` 关掉，只按社区规则）；
  考试出牌的预演要选用 `SimScreenExamStateSource`（见上表）。主数据没构建时这些自动退回社区规则。
- **不在这个仓库里的东西**：考试存档读取（解密）。作者用私有插件接回去。

## 三、环境

平台见下面「设备」：**作者实机验证过的有 macOS（Apple Silicon）+ PlayCover，和 Windows + MuMu 12**
（MuMu 6.8，22 局 3 次本战夺冠）；Windows DMM 版实机开局跑通（见「设备」）；安卓设备 / 模拟器（ADB）、雷电走 `AdbDevice`，代码接好了、有离线测试，但没有实机打过一局。

1. 装环境（一条命令，可重复跑，做过的步骤会跳过；需要 uv 和网络；脚本本身不要 git，下面用 git clone 只是拿源码的一种办法，下载 GitHub 的源码压缩包解压也行）：
   ```bash
   git clone https://github.com/matto49/hiro-gakumas-hero && cd hiro-gakumas-hero
   scripts/setup.sh      # .venv（Python 3.10）→ 安装 → OCR 模型 → 识别用的游戏原图 → 模拟器主数据 + 主数据库 → 自检
   ```
   从零约 4 分钟。已用 `HIRO_KAA_GAME_DATA` / `HIRO_MASTER_DB` 指向现成数据时加 `--no-data`。
   游戏原图（技能卡 / 偶像卡 / 饮料，`resources/game_data/`）由 `tools/assets/game_images.py` 从本仓库 Release `game-images-b9577da`
   （kaa-game-data b9577da 版的副本，识别按这一版调）下载、校验 SHA256；主数据库（`cache/master/game.db`）由 `python -m hiro.state.master_db` 从模拟器主数据生成，
   和原来 KAA 的 game.db 同结构。见 `docs/assets.md`。
   往本仓库发这类数据 Release（游戏图、OCR 模型、引擎包、主数据包）一律加 `--latest=false`：「Latest」只给桌面包 `desktop-v*`，
   被顶掉时 `.github/workflows/release-latest.yml` 会改回并报错。
   自检最后会打印插件来源、有没有主数据和自建素材，以及开局命令。
2. 游戏窗口：用 [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover)，在游戏的 PlayCover 设置里打开 `maaTools`（端口 `maaToolsPort`）。
   截图和点击走游戏进程内的 MaaTools 服务（见 `hiro/devices/maatools.py`），**不要求窗口在前台，也不动鼠标**。
   **原版 PlayCover 不支持**（2026-11-13 起）：MaaTools 连不上时直接报错停下，不再退回 Quartz 截图 + `CGEventPostToPid`。
   macOS 要给运行它的终端 / 程序「屏幕录制」权限；截图报 `could not create image from display` 时先查这个权限和显示器状态。

   **设备**（`HIRO_DEVICE`）：

   | `HIRO_DEVICE` | 平台 | 截图 / 点击 | 验证情况 |
   |---|---|---|---|
   | `auto`（缺省） | macOS 上就是 `maatools`；其他系统要求显式设置 | — | — |
   | `maatools` | macOS + hguandl 分支 PlayCover（原版不支持） | 游戏进程内 MaaTools | 作者实机验证（6 次夺冠） |
   | `adb` | 任意安卓设备 / 模拟器 | `HIRO_SCREENSHOT=adb`（缺省）或 `uiautomator2` | 未实机验证 |
   | `mumu` | MuMu 12 | `adb`（缺省；Windows 上 `nemu_ipc` 截图可用但点击送不到游戏，别用） | Windows + MuMu 6.8 实机打过（见下） |
   | `leidian` | 雷电模拟器 | `adb` / `uiautomator2` | 未实机验证 |
   | `dmm` | Windows DMM 版 | `windows_background`（缺省：PrintWindow 截图 + SendMessage 点击，窗口不用在前台）/ `windows_native` | 作者实机开局验证（2026-11-10，见下） |

   - `HIRO_ADB=ip:port` 指 adb 地址（`adb` / `leidian` 用，缺省 `127.0.0.1:5555`；`mumu` 不设时由 MuMu SDK 自动取）；
     `HIRO_EMULATOR_INSTANCE` 指 MuMu / 雷电的实例 ID（MuMu 缺省 `0`，多开时指定）。
     MuMu 缺省开后台模式（按游戏包名截它所在的显示；MuMu 6 把应用放在单独的显示里，不开时截到的是模拟器桌面），
     `HIRO_MUMU_BACKGROUND=0` 关掉。
   - 非 PlayCover 后端不做 PlayCover 专用的退路（横屏 LIVE 黑屏时切全屏、MaaTools 点了没反应时用真实鼠标再点）。
   - 文字识别（`HIRO_OCR`）：macOS 缺省用 Apple Vision；Windows 装好了本机 SIMD OCR（`docs/assets.md`）就用它，没装或启动失败时退回 RapidOCR，其他系统用 RapidOCR（`rapidocr_onnxruntime` + PaddleOCR PP-OCRv3 日文 / 英文识别模型，
     模型在 `cache/rapidocr/`，由安装脚本调 `tools/assets/rapid_models.py` 从本仓库 GitHub Release `ocr-models-v1` 下载、校验 SHA256）。页面判定、找按钮的整帧文字统一走 `hiro.vision.ocr.screen_texts`。
     RapidOCR 在作者的回归截图上的准确率与速度见 `docs/assets.md`；换平台后先用自己的截图验一遍。
   - Windows 用 PowerShell 版安装脚本：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1`（步骤与 `setup.sh` 相同）。
     作者的 Windows 台式机上实测过安装、自检和全套离线测试，以及 `HIRO_DEVICE=mumu`（MuMu 6.8，截图 / 点击 `adb`）
     实机打 H.I.F 本战（2026-11-07–09，22 局 3 次夺冠，RapidOCR + Codex）。`HIRO_SCREENSHOT=nemu_ipc` 时截图正常、
     点击不生效（页面一直不变，开局 guard_new 15 秒超时），缺省已改成 `adb`。
     游戏原图 / 模拟器主数据下载不了时，从别处拷一份 `resources/game_data` 和 `cache/master/game.db`，用 `-NoData` 加 `HIRO_KAA_GAME_DATA` / `HIRO_MASTER_DB` 指过去。
   - Windows 开局用 `scripts\play.ps1`：把作者夺冠时的环境变量设好再调 `tools\play_game.py`，日志写到 `reports\live\play-<tag>.out`：
     ```powershell
     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\play.ps1 -Idol i_card-skin-shro-3-007 -MainOnly -Memory <Vo,Da,Vi> -Tag 1
     ```
     缺省 `-Device mumu`（截图 / 点击 `adb`）、`-Llm codex`、支援卡 / 回忆编成 4 / 5、模拟器开但考试预演只在 R2
     （`HIRO_SIM_WORKERS=1`、`HIRO_EXAM_SIM_ROUNDS=r2`，原因见脚本开头注释）。`-Proxy http://…` 给 LLM 设代理，`-NoSim` 关模拟器，
     `-Relaunch`（游戏被杀时自己拉起，缺省关）只在旧主循环里接过，2026-11-24 删旧主循环后暂时不起作用，`-Wait` 前台等跑完。参数全表见脚本开头。
   - **DMM 版**（`-Device dmm`）：
     - 游戏以管理员身份运行，普通权限的进程能截图、点击会被系统挡掉（UIPI），所以 hiro 也要以管理员身份跑。
       `scripts\play.ps1 -Device dmm` 不是管理员时会自己申请（弹一次 UAC），在新窗口里开局。
     - 游戏界面必须是日文原版：localify 之类的汉化 / 改文字插件（放在游戏目录的 `version.dll` 代理等）要先拿掉，否则 OCR 认不出页面。
     - 游戏闪退 / 窗口被关时程序会认出来并停下（按窗口标题「gakumas」判断），不会自己拉起：
       DMM 版要从 DMM Game Player 启动、每次弹管理员确认，程序点不到。重新启动游戏后 `python -m hiro.run_hif resume` 续跑。
     - 同一个账号不要同时开 MuMu 和 DMM：另一端进入培育时会弹「プロデュースデータの破棄」。
     - 后台点击要读真实光标（游戏认的是光标位置，SendMessage 前先把窗口挪到目标点落在光标下）：程序在跑时别在这台 Windows 上
       弹 UAC / 锁屏。真碰上了，`DmmWin32Device` 会等桌面恢复（最多 10 分钟，`hiro.devices.desktop`）再重做，超时讲清原因停下。
     - 滑动到终点会原地停 0.3 秒再松手：直接松手时选卡对话框按松手速度惯性多滑，
       相邻两屏没有重叠的行，牌组拼接不可信。
     - 设备层 `hiro/devices/`（2026-11-13 起；主循环 hiro.runtime 用它，旧的 kotonebot 设备 `hiro.device` 2026-11-24 删了）：统一接口 6 个方法
       （`connect / close / screenshot / tap / swipe(…, hold=) / alive`，逻辑坐标 720×1280、横屏 1280×720，失败抛 `DeviceGone` /
       `DeviceUnavailable`），三个实现 `MaaToolsDevice`（Mac）、`DmmWin32Device`（DMM）、`AdbDevice`（MuMu / 雷电 / 真机，
       `HIRO_ADB`、`HIRO_ADB_PATH`），`hiro.devices.open_device()` 按 `HIRO_DEVICE` 选。
       另有两个不在统一接口里的可选方法，各自只给一个调用方：`MaaToolsDevice.real_tap`（drink_swap 的 PlayCover 真实鼠标兜底）、
       `AdbDevice.shell`（`hiro.loop.game_app` 的 `foreground(shell)` / `relaunch(shell)`）。横屏判断看 `dev.orientation`。
     - `DmmWin32Device`（`hiro/devices/dmm_win32.py`）自己调 Win32（ctypes），不依赖 MaaFramework / pywin32（2026-11-25 去掉 maafw）。
       截图 `HIRO_DMM_CAPTURE`：`printwindow`（缺省，做法同 KAA 的 DMM 截图）/ `gdi`（BitBlt 窗口 DC，普通权限实测约 11 ms 一张，窗口被挡住的部分截不到，实验）；
       点击 / 滑动和上面的后台点击同一种做法（挪窗口让目标点落在光标下 + PostMessage），读光标「拒绝访问」时同样等桌面恢复。
       2026-11-25 hiro-w4 实机（管理员）：PrintWindow 约 34 ms 一张，窗口被挡着也截得对；点击、底部导航 3/3、横幅滑动 2/2 通过。
     - 游戏窗口尺寸（用户 2026-11-14 定）：客户区按显示器自适应，9:16，高 = 主显示器工作区高（去掉任务栏）− 窗口外框高，
       上限 1080x1920、下限 540x960；窗口挪到工作区左上角，整个窗口在屏幕内。开局时 `hiro.runtime.play.fit_dmm_window`
       核对、不对就调（`hiro.devices.win_window`，要管理员），显示器放不下 540x960 或调不过去就停下报告，实际尺寸记进 trace 备注；
       `HIRO_DMM_FIT=0` 关掉。窗口小了截图要放大，小字 OCR 和模板分数会掉（541x961 时「残りターン」0.50、牌组按钮 0.78）。
       看 / 调：`python -m hiro.devices.win_window [fit | 宽 高]`。不改系统缩放和分辨率。
     - 考试里判「画面稳了」另要求一致读数持续 1.5 秒（`play.ps1 -Device dmm` 缺省设 `HIRO_SETTLE_MIN_S=1.5`，自己设了就用自己的）：
       DMM 截图约 0.19 秒一张，三次读数不到 1 秒，会落在两段动画之间，回合没切完就决策（dmm1m：190 次决策 23 次作废重来）。
   - Windows 上的坑（实测）：16 GB 内存同时开 MuMu、模拟器和其他常驻程序时 MuMu 会被系统杀掉，开局前先关掉不用的程序；
     MuMu 拉起游戏时的「加速服务」提示程序会点「不加速」；结算后 LIVE 横屏偶尔整屏黑掉不动，只能
     `MuMuManager.exe control -v 0 restart` 重启模拟器后 `python -m hiro.run_hif resume` 续跑。
3. 素材（见 `docs/assets.md`）：识别用的卡面、图标、字体、Sprite、布局随仓库在 `assets/pack/`，开箱即用。
   客户端更新后认不出新图标时，才打一两场考试，用 `tools/assets/capture_icons.py` 补；设了 `HIRO_BUNDLED_ASSETS=0` 时则必须补，
   否则考试效果名和 P 道具名一直是 `None`，策略层只能用保守规则，分数会明显低。
4. 游戏内设置：`PYTHONPATH=src .venv/bin/python tools/check_settings.py` 检查快进 / 跳过演出是否全开。
5. LLM：`HIRO_LLM` 选适配器（`claude-cli:sonnet`、OpenAI 兼容 API 等，见 `hiro/brain/llm.py`）。

## 四、跑一局

跑之前确认：游戏停在首页；没有别的设备上进行中的培育；AP 够或有 APドリンク；游戏里已经编好支援卡和回忆的编成。

支援卡和回忆可以让程序按社区规则选（[docs/formation-plan.md](docs/formation-plan.md)）：扫库存 → `python -m hiro.brain.formation_plan --idol … --support-set N --memory-set N`
→ 按报告存进这两个编成位。之后开局不用再设编成号和 `--main-attr`，第 2、3 步会按方案核对编成，对不上就停。

```bash
# 整局（选拔 → 本战）
HIRO_SUPPORT_SET=<支援卡编成号> HIRO_MEMORY_SET=<回忆编成号> \
  python tools/play_game.py --idol <偶像卡 skin id> --main-attr <Vo|Da|Vi>

# 只打本战（已经有选拔得到的回忆）
HIRO_SUPPORT_SET=… HIRO_MEMORY_SET=… \
  python tools/play_game.py --main-only --idol <…> --main-attr <…> [--memory Vo,Da,Vi]

# 底层单段命令（play_game 就是把这两段串起来）
PYTHONPATH=src python -m hiro.run_hif new    --mode qualify|main --idol <…> --main-attr <…>
PYTHONPATH=src python -m hiro.run_hif resume --mode qualify|main   # 续跑进行中的培育
```

`play_game` 的 `--` 之后的参数原样交给 `run_hif`（例如 `-- --no-llm` 只用规则）。

| 环境变量 | 作用 |
|---|---|
| `HIRO_SUPPORT_SET` / `HIRO_MEMORY_SET` | 游戏里的编成号。没设、也没有开局编成方案时，在碰游戏前退出（码 2）。写 `auto` 等于游戏里的「おまかせ」，支援卡会直接确认列表第一张租借卡，只在你明确想这样时用 |
| `HIRO_MEMORY_CARDS` | 回忆编成带进培育的技能卡 ID（逗号分隔）。决策器把它们算作已有，三选一里不再找；不设时用开局编成方案里的，都没有就当空。手动设编成号时记得一起设 |
| `HIRO_FORMATION_PLAN` | 开局编成方案文件，默认 `conf/formation-plan.json`；`off` = 不读。见 [docs/formation-plan.md](docs/formation-plan.md) |
| `HIRO_LLM` | LLM，如 `claude:sonnet`（默认，走 `claude -p`）、`codex[:<model>]`（走 OpenAI Codex CLI 的 `codex exec`，用 ChatGPT 账号登录，不要 API key；Windows 上能自动找到 Codex 应用自带的 `codex.exe`，或用 `HIRO_CODEX_BIN` 指定）、`openai:<model>`（OpenAI 兼容）。`--no-llm` 只用规则。需要代理的网络给进程设 `HTTPS_PROXY` |
| `HIRO_KAA_GAME_DATA` | 识别用的游戏原图目录（`skill_cards/`、`idol_cards/`、`drinks/`，`tools/assets/game_images.py` 下载），默认 `resources/game_data`；名字沿用 KAA 时期的 |
| `HIRO_MASTER_DB` | 主数据库路径：缺省 `cache/master/game.db`（`python -m hiro.state.master_db` 从模拟器主数据生成，模拟器主数据换提交自动重建）；指向一份已有的库时原样用、不重建 |
| `HIRO_USER_ASSETS` | 自建素材目录（`capture_icons` 的产出），可选；与随仓库素材包并用，同类图库取文件更多的一个 |
| `HIRO_BUNDLED_ASSETS` | 随仓库素材包目录，默认 `assets/pack`；设 `0` 关掉 |
| `HIRO_ASSET_SOURCE` / `HIRO_EXAM_STATE_SOURCE` / `HIRO_EXAM_ADVISOR` | 换插件实现（`包.模块:类名`）。考试预演：`HIRO_EXAM_STATE_SOURCE=hiro.sim.screen_source:SimScreenExamStateSource` |
| `HIRO_BENCH_ADVISOR` / `HIRO_BENCH_ALGO` | 考试出牌（缺省开）：用 gakumas-bench 的开源算法，`HIRO_BENCH_ALGO` 写法同 bench（缺省 `uinet`，也可 `my.py:Cls`）；出错 / 不合法 / 缺依赖时这一步退回社区规则（不调 LLM）。`HIRO_BENCH_ADVISOR=0` 关掉，回到社区规则 + LLM。模型和主数据缓存在 `~/.cache/hiro/bench`（`HIRO_BENCH_CACHE`），`python -m hiro.bench_bridge check` 查能不能用 |
| `HIRO_EXAM_ADVISOR` | 考试出招插件（`模块:类`）。缺省 `hiro.search.advisor:SearchAdvisor`（用户 11-14 定）：本战 R1 / R2 每步用 Rust GNRPA 搜 15 秒、8 个世界、CPU 核数 − 1 个线程（`hiro.search.advisor.BUDGET`），局面来自模拟器跟踪、开局是这一局的编成，和画面核对过；要求局面来源是 `SimScreenExamStateSource`（`play.ps1` 缺省就是）和带 `search` 的 Rust 后端，没有就整局退回 bench 中转层（日志写明）；搜不了的步（选拔考试、核不上、出错）也退回。设 `hiro.bench_bridge.advisor:BenchAdvisor` 换回 bench 中转层。偶像之路场次另由 `HIRO_TOWER_ALGO` 选（缺省 `hiro.search.tower_algo:TowerGnrpa`，每步 5 秒；Rust 后端没有 search / plan_many 时退回 uinet，理由里写明）|
| `HIRO_EXAM_SEARCH_HIF` | H.I.F 选拔考试也走 GNRPA 搜索（缺省关）：`final` = 只有选拔第 3 场，`all` = 三场，也可写场次号 `1,3`。同初 / N.I.A：中转层按培育记账现拼编成（主数据的选拔试镜行 + 这一局的牌组 / P 道具 / 支援卡），引擎按 battle config 开局，出牌用 `hiro.search.exam_algo:ExamGnrpa`，每步约 5 秒（`HIRO_BENCH_BUDGET_S`）。三属性倍率要三项都读到才开搜（H.I.F 实读和粗算的比例三项差很多），没读全的步用 UINet；配合 `HIRO_EXAM_SCHEDULE_PROBE=1` 第 1 回合就能读全。本战 R1 / R2 不归它管 |
| `HIRO_EXAM_POLICY` | 有预演时怎么用：`llm`（缺省，预演写进提示词给 LLM 参考）/ `expert`（按预演期望 + 硬规则链直接选） |
| `HIRO_PRODUCE_LLM` | 培育决策点补全：规则 / 模拟拿不准时交 LLM 在候选里选（LLM 不可用或输出非法按规则兜底）。缺省（不设）或 `all` 全开（用户 11-14 定），`off` 全关，逗号分隔写名字只开这几条：`change_remove`（换卡第 2 段换出哪张）、`remove_card`（删卡）、`select_drink`（饮料三选一）、`consult`（相談买饮料）、`drink_overflow`（饮料满丢哪瓶）、`interval`（轮间：社区默认方案 / 模拟方案二选一）、`select_pitem`（P 道具三选一）、`special_guidance`（特別指導：优先表做完后表外定制做不做）、`event_choice`（事件选项页：没有全面更好的选项时）、`card_move`（考试里的技能卡移动对话框：前两名差不多或有卡没认出时）。每局报告里有各决策器的来源统计和兜底次数；离线回放黄金用例：`python -m hiro.brain.cases --llm claude:sonnet` |
| `HIRO_HIF_GUIDES` | H.I.F 用偶像卡攻略包（`hiro.brain.guides`，开局自动下载，24 小时更新一次，新版下好后删旧版）：`1` 时广以外的偶像在选卡 / 删卡 / 换卡 / 强化上按自己的流派 + 攻略条目（同初 / N.I.A），不再按广的必拿表刷新、禁用；缺省 `0` 照旧。广仍要 `HIRO_GUIDES_HIRO=1` 才从条目读。见 `hiro/brain/idol_know.py` |
| `HIRO_FARM` / `HIRO_FARM_TARGET` / `HIRO_FARM_BAD_MARGIN` | 刷回忆（メモリー転写，实验，缺省关）：`bad` = 「初」BAD 育成，目标评价 B（社区方法见 `docs/memory-transcription.md`；初星助手开局页「初」→「刷回忆」，或 `hiro run start hajime --farm bad --farm-target <卡名或 id>`）：13 周固定日程（上课 / 休息 / 追い込み / 第 7 周相談或外出），练习课离通关线还差「通关线 × `HIRO_FARM_BAD_MARGIN`（缺省 0.4）+ 好印象之后每回合自动加的分 × 1.2」以内就不再出牌（刚好没过、不掉卡），事件选项里拿技能卡的算坏处，相談把这局拿到的卡全删（初始卡 / 偶像固有卡 / 转写目标除外）。转写目标 `HIRO_FARM_TARGET`（技能卡 id 或卡名，逗号分隔；不设用回忆编成带的卡）。考试照常打。回忆结果页读出生成的回忆带的卡，不是目标、还有次数就「再生成」一次（确认框有ジュエル或认不出确认按钮一律キャンセル）。租借回忆程序不会选：玩家在游戏里开好局后 `--command resume` 接手。见 `hiro/brain/farm.py` |
| `HIRO_CONTEST_PICK` / `HIRO_SHOP` / `HIRO_SHOP_ITEMS` / `HIRO_SHOP_AP` / `HIRO_SHOP_REFRESH` / `HIRO_COIN_GACHA` | 日常（`tools/daily/run_daily.py`，初星助手开局页的「日常」选项会传）：竞赛挑哪个对手（`lowest` 缺省 / `1` `2` `3`）；商店每日兑换所（`off` 缺省 / `recommended` 买おすすめ，没有就买最便宜的一件 / `all`），`HIRO_SHOP_ITEMS` 另外按名字买（逗号分隔，中日文都行）；体力商店 `pt,note,retry,regen`（缺省不买）；`HIRO_SHOP_REFRESH=on` 买完用每日免费刷新再买一轮（缺省 off；只在右上角一字不差读到「リスト更新:1回無料」时点，点了出任何确认框都点空白关掉、绝不确认）；硬币扭蛋（`off` 缺省 / `auto` 抽 1 次 / `friend:1,sense:2`）。只用マニー / AP / 各池的币：确认框里没有「マニーを使用して」「APを使用して」「コインを消費」或出现「ジュエル」一律キャンセル。见 `hiro/daily/shop.py`、`coin_gacha.py` |
| `HIRO_SIM=0` | 不用模拟器（轮间 / 饮料 / 相談只按社区规则） |
| `HIRO_DECK_REVIEW` | 培育中主动打开牌组一览读全量（约 10 秒一次）。缺省：本战开局牌组为空时读一次；牌组标成待复核时（跳过剧情后支援事件可能给了卡、删卡没对上等）到选行动页读一次，同一天只试一次。`1` = 另外每个考试段第一次到选行动页都读（调试）；`0` = 只保留开局那次，待复核不读 |
| `HIRO_PAUSE_FLAG` | 外部暂停旗标文件路径：文件存在就暂停对局 |
| `HIRO_CLOSE_GAME` | 跑完（不管成败）关游戏：`off`（缺省）/ `on`·`emulator`（MuMu 关模拟器、雷电设了 `HIRO_LDCONSOLE` 时关模拟器、DMM 关游戏 + DMM Game Player）/ `game`（只关游戏）。只由整局的发起方关一次：`play_game` 整局结束关（选拔和本战之间不关）；单段的 `run_hif` 不关。`play.ps1 -CloseGame`。见 `hiro/runtime/close_game.py` |
| `HIRO_UNREAD_STORY` / `HIRO_UNREAD_TIMEOUT_S` | 未读剧情：`skip`（缺省，照旧跳过）/ `stop`：未读确认框点キャンセル、暂停、发系统通知、写等人文件 `%LOCALAPPDATA%\hiro\waiting-for-user.txt`（`HIRO_WAIT_FILE`）；看完剧情自动继续，删掉文件 = 跳过这段继续；剧情框文字（OCR）`HIRO_UNREAD_TIMEOUT_S` 秒（缺省 60）没变 = 没人在看，关游戏并退出（码 3，之后 `resume`）。按文字判断，因为剧情画面一直在动。stop 时开局设置不打开「未読コミュでも早送り」（打开着的会关掉）。`play.ps1 -UnreadStory stop`。见 `hiro/runtime/unread_story.py` |
| `HIRO_RETRY_MAX` | 同一场考试失败后最多再挑戦几次（0–3，缺省 3 = 游戏上限）。设成小于 3 时，这一场用了几次按本局 trace 记录数：画面「あと N回」是当天全局共用、每天重置的次数，只用来判断还有没有（あと0回 就不点）。`play.ps1 -RetryMax` |
| `HIRO_RETRY_HANDOFF` / `HIRO_RETRY_HANDOFF_TIMEOUT_S` | 考试没过线、要不要再挑戦之前停下交给人（缺省关）：发通知、DMM 窗口居中，看画面判断：`HIRO_RETRY_HANDOFF_TIMEOUT_S` 秒（缺省 60）内你点了「再挑戦」就等你亲手打完（不计时），打完离开结算页程序接着走、又没过再给 60 秒；没人动 = 交回程序按规则决定；你点了次へ 不打了 = 从当前画面接着走。不用删文件。用来分清是打牌还是养成的问题。`play.ps1 -RetryHandoff`。见 `hiro/runtime/retry_handoff.py` |
| `HIRO_PHOTO_PICK` / `HIRO_PHOTO_TIMEOUT_S` | LIVE 后的选回忆照片页（「メモリーにするフォト」）：`auto`（缺省，用默认的第一张）/ `stop`：不点，发通知、写等人文件，等你自己选照片、点「次へ」「決定」，两种画面都不在了自动继续；删掉等人文件或 `HIRO_PHOTO_TIMEOUT_S` 秒（缺省 600）没人管 = 用默认的那张继续。`play.ps1 -PhotoPick stop`。见 `hiro/runtime/photo_pick.py` |
| `HIRO_LIVE` / `HIRO_LIVE_TIMEOUT_S` | 培育结束后的 LIVE：`play`（缺省，照旧点开始、播完，照片按 `HIRO_PHOTO_PICK`）/ `watch`（我亲自看：曲目页开始前停下，像未读剧情一样发通知、写等人文件、DMM 窗口居中，你点开始看完程序接着走，照片自己选；`HIRO_LIVE_TIMEOUT_S` 秒（缺省 60）没人点开始就关游戏并退出（码 3）；删等人文件 = 这场跳过）/ `pick`（跳过 LIVE，照片自己选）/ `auto`（跳过 LIVE，照片用默认）。跳过 = 点右上角 SKIP（认不到时点画面中间调出菜单，有次数上限）。`play.ps1 -Live`。见 `hiro/runtime/live_mode.py` |
| `HIRO_RETRY_GAP_MAX` | 本战 R2 没拿第 1 时重不重打：离第 1 的差距超过我方本轮分的这个比例就不重打（缺省 `0.3`，用户 11-18 定；读不到第 1 名或本轮分时按原规则重打） |
| `HIRO_DEVICE` / `HIRO_ADB` / `HIRO_SCREENSHOT` / `HIRO_EMULATOR_INSTANCE` | 设备后端与连接，见「三、环境」的「设备」表 |
| `HIRO_OCR` | 文字识别后端：`auto`（缺省：macOS → `vision`；Windows 装好了 SIMD → `simd`；否则 `rapidocr`）/ `vision`（Apple Vision，只有 macOS）/ `rapidocr` / `simd`（本机 SIMD PaddleOCR PP-OCRv6，需要 `.deps` 和 .NET 10）/ `winocr` / `ppocr5` |
| `HIRO_OCR_DML_DEVICE` | RapidOCR 走 DirectML 时用第几块显卡（缺省 0 号；有核显的机器上独显常是 1 号，实测 0 号比 CPU 还慢） |
| `HIRO_TRACE_NATIVE=1` | trace 存帧时另存一张原生分辨率 PNG（`frames/<序号>.native.png`），采集 OCR 测试集用（`tools/ocr_dataset/`）；run.json 里的 `platform` 记设备 / 截图方式 / OCR 后端 / 原生尺寸 |
| `HIRO_OCR_DEVICE` / `HIRO_OCR_THREADS` | RapidOCR 推理设备：`auto`（缺省：CUDA → DirectML → CPU，按装的是哪种 onnxruntime）/ `cuda` / `dml` / `cpu`；加载不成功自动试下一个，日志写实际用的设备。Windows 上 `setup.ps1 -OcrDevice dml` 换成同版本的 `onnxruntime-directml`（缺省 `cpu` 不换：识别阈值按 CPU 版调的，GPU 版先显式打开），RTX 3060 实测整帧识别约 2 s → 0.4 s、识别文字不变。`HIRO_OCR_THREADS`：CPU 推理线程数（缺省占满所有核） |
| `HIRO_RESCUE_LLM=0` | 关掉认不出画面时的 LLM 看图兜底（默认开） |
| `HIRO_DEBUG_RECOG=1` | 把全部识别互验写进 trace，页面变化都存帧 |

固定编成时，租借格按 `--main-attr` 挑主属性、等级最高的一张；挑不到就停下（不开局）。
有开局编成方案时按「加上这张后编成的价值」挑（社区推荐）；两页 + 刷新都打不上分时，退回租对应类型里等级最高的一张
（自己已有 4 张主属性就租非主属性，不足就租主属性；流派由游戏的「プラン適正」挡住）。`HIRO_RENTAL_FALLBACK=0` 关掉，照旧停下。

别的设备上有进行中的培育时，`run_hif new` 会停下（退出码 3）。程序不会破弃那一份进度（原来的 `--allow-discard` 2026-11-24 随 KAA 主循环删了），
要放弃由使用者在游戏里自己操作。

运行中：

- 每一步写进 `traces/<run>/steps.jsonl`（截图在同目录）。
- 看门狗：画面和局面 3 分钟没有进展就停机报警（`loop/repeat_guard.py`），不会自动重启。
- 外部暂停：设了 `HIRO_PAUSE_FLAG=<文件路径>` 时，那个文件存在就暂停对局，删掉就继续（录屏中断时用）。
- 日志 `reports/live/<trace>.log`，每局报告 `reports/runs/<trace>.json`（含 LLM 调用次数、输入 / 缓存 / 输出 token、费用）。
- 选拔进程非 0 退出时 `play_game` 不会自动续跑本战：先看日志，确认没问题再 `run_hif resume`。

## 五、出问题怎么查

| 现象 | 先看 |
|---|---|
| 停机报警 | 日志最后几行 + `traces/<run>/` 里停机前的截图；`python -m tools.audit.blunders <run>` |
| 读数不对（分数、元気、体力…） | `docs/assets.md` 的退路表；对应识别模块的测试（`tests/test_*_read.py`） |
| 决策看起来不对 | 复盘页：`python tools/viewer/build_exam_view.py <run>`，逐步看候选、状态、理由 |
| 页面认不出 | `hiro/vision/quick.py`、`hiro/vision/pages.py`；未知页面会交给 LLM 兜底（`loop/rescue.py`） |

改代码后：`PYTHONPATH=src pytest -q`。没有截图样例时，读截图的用例会自动跳过；有自己的截图可以设 `HIRO_FIXTURES`。

## 六、改策略的规矩

- 社区攻略是最高依据（`docs/hif-playbook.md`，每条写来源）。自己的实机记录只当状态数据，不当打法依据。
- 改变实机行为的改动放在开关后面、默认关，用回放 / 复盘验证后再打开。
- 每局复盘里发现的错误，写成合成用例放进 `examples/cases/`，防止回归。
