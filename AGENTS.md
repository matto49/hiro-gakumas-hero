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
| 不按「破棄」「リタイア」，不放弃进行中的培育 | `loop/kaa_patches.py`（NeverConfirmDiscard）、`loop/rescue.py` |
| 在别的设备开着的培育：开局确认页绝不按「開始する」（会破弃另一台设备的进度），停下来 | `loop/resume.py`、`loop/controller.py` |
| 通信错误弹窗不点「タイトルへ」 | `loop/comm_error.py`、`loop/rescue.py` |
| LLM 兜底只能点 OCR 识别出的文字框或固定空白处；选择页、考试画面不点；点之前重新截图核对；次数有上限 | `loop/rescue.py` |
| 再挑戦只用免费次数 / 再挑戦チケット，最多 3 次 | `produce/hif.py` |
| 不默认选第一个：换卡 / 删卡认不出或全是保护卡就キャンセル；租借格挑不到就停；考试决策越界时按规则重选，不退回第一个合法动作 | `produce/`、`defaults/advisor.py` |

另外：

- 不要把截图、trace、账号、密钥、主数据原文件提交进仓库。`.gitignore` 已挡住 `resources/`、`tests/fixtures/`、`cache/`、`traces/`。
  `assets/pack/` 是仓库有意带的识别素材包（权利属于游戏的权利人，见 `assets/pack/README.md`），除此之外不要再往仓库里加游戏素材或截图。
- 不要改 `vendor/kaa`（KAA 子模块，固定版本）。需要改 KAA 行为时，在 `hiro/loop/kaa_patches.py` 里打补丁。

## 二、架构

```
                       ┌───────────── 策略层 hiro.brain / hiro.defaults.advisor ─────────────┐
                       │ 社区规则（docs/hif-playbook.md）→ 规则链 → LLM 兜底（hiro.brain.llm） │
                       └──────────────▲────────────────────────────────┬──────────────────┘
                                      │ GameState / BattleState        │ Decision（契约）
截图 ─► 识别层 hiro.vision / perception ─► Observation ─► reduce（纯函数）─► 状态 hiro.state
           ▲ 素材：插件 AssetSource                              │
           │                                                      ▼
设备 ◄── 操作层 hiro.device / tap / exec / loop / produce ◄── 执行器（点击、等稳、补点、弹窗、恢复）
                                      │
                       trace（每一步：截图 + 观测 + 状态 + 决策 + 执行结果）
                                      ▼
                 复盘页 tools/viewer · 审计 tools/audit · 回放 hiro.loop.replay
```

- **契约**（`hiro/contracts`）是层与层之间唯一的接口。读不到的字段是 `None`，不要猜。
- **插件**（`hiro/plugins.py`）：三处可以换实现，主循环不用改。

| 插件 | 回答什么 | 开源默认实现 |
|---|---|---|
| `asset_source` | 素材和主数据从哪来 | `defaults/assets.py`：KAA 公开数据 + 使用者自建素材 |
| `exam_state_source` | 考试局面从哪来 | `defaults/exam_state.py`：画面识别 + 状态机记账（不知道牌库顺序）。另有 `hiro.sim.screen_source:SimScreenExamStateSource`：同样从画面读，再用模拟器跟踪局面、给每个候选动作附预演（不读存档） |
| `exam_advisor` | 考试每一步怎么打 | `defaults/advisor.py`：社区规则链 + LLM 兜底 |

  换实现：环境变量 `HIRO_ASSET_SOURCE` / `HIRO_EXAM_STATE_SOURCE` / `HIRO_EXAM_ADVISOR`（写成 `包.模块:类名`），
  或在 `pyproject.toml` 的入口点组 `hiro_gakumas.plugins` 里登记，或代码里 `hiro.plugins.register(...)`。

- **模拟器**（`hiro/sim`，引擎 `vendor/gakumas-player/gakumas_sim`，主数据由 setup 第 6 步从社区公开的 gakumasu-diff 下载构建）：
  轮间买什么 / 刷不刷货架、饮料取舍、本战相談按模拟算第二轮期望分（`HIRO_SIM=0` 关掉，只按社区规则）；
  考试出牌的预演要选用 `SimScreenExamStateSource`（见上表）。主数据没构建时这些自动退回社区规则。
- **不在这个仓库里的东西**：考试存档读取（解密）、高保真考试引擎。作者用私有插件接回去。

## 三、环境

平台与 KAA 支持的对齐（见下面「设备」）：**作者实机验证过的有 macOS（Apple Silicon）+ PlayCover，和 Windows + MuMu 12**
（MuMu 6.8，22 局 3 次本战夺冠）；Windows DMM 版实机开局跑通（见「设备」）；安卓设备 / 模拟器（ADB）、雷电走 KAA 的设备工厂，代码接好了、有离线测试，但没有实机打过一局。

1. 装环境（一条命令，可重复跑，做过的步骤会跳过；需要 uv、git 和网络）：
   ```bash
   git clone --recurse-submodules https://github.com/matto49/hiro-gakumas-hero && cd hiro-gakumas-hero
   scripts/setup.sh      # 子模块 → .venv（Python 3.10）→ 安装 → 生成 KAA 的 R.py → 下载 KAA 公开数据 + 卡图索引 → 自检
   ```
   从零约 4 分钟。KAA 换版本后加 `--regen` 重新生成；已用 `HIRO_KAA_GAME_DATA` 指向现成数据时加 `--no-data`。
   自检最后会打印插件来源、有没有主数据和自建素材，以及开局命令。
2. 游戏窗口：用 [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover)，在游戏的 PlayCover 设置里打开 `maaTools`（端口 `maaToolsPort`）。
   默认的截图和点击走游戏进程内的 MaaTools 服务（见 `hiro/device.py` 开头注释），**不要求窗口在前台，也不动鼠标**；
   只有 MaaTools 连不上、退回 Quartz 截图 + `CGEventPostToPid` 时，才需要保持竖屏窗口在前台。
   macOS 要给运行它的终端 / 程序「屏幕录制」权限；截图报 `could not create image from display` 时先查这个权限和显示器状态。

   **设备**（`HIRO_DEVICE`，与 KAA 支持的平台对齐）：

   | `HIRO_DEVICE` | 平台 | 截图 / 点击 | 验证情况 |
   |---|---|---|---|
   | `auto`（缺省） | macOS：MaaTools 能连就 `maatools`，否则 `quartz`；其他系统要求显式设置 | — | — |
   | `maatools` | macOS + hguandl 分支 PlayCover | 游戏进程内 MaaTools | 作者实机验证（6 次夺冠） |
   | `quartz` | macOS + 原版 PlayCover | Quartz 截图 + `CGEventPostToPid`，要游戏在前台 | 作者实机用过 |
   | `adb` | 任意安卓设备 / 模拟器（KAA 的 custom） | `HIRO_SCREENSHOT=adb`（缺省）或 `uiautomator2` | 未实机验证 |
   | `mumu` | MuMu 12 | `adb`（缺省；Windows 上 `nemu_ipc` 截图可用但点击送不到游戏，别用） | Windows + MuMu 6.8 实机打过（见下） |
   | `leidian` | 雷电模拟器 | `adb` / `uiautomator2` | 未实机验证 |
   | `dmm` | Windows DMM 版 | `windows_background`（缺省：PrintWindow 截图 + SendMessage 点击，窗口不用在前台）/ `windows_native` | 作者实机开局验证（2026-11-10，见下） |

   - `HIRO_ADB=ip:port` 指 adb 地址（`adb` / `leidian` 用，缺省 `127.0.0.1:5555`；`mumu` 不设时由 MuMu SDK 自动取）；
     `HIRO_EMULATOR_INSTANCE` 指 MuMu / 雷电的实例 ID（MuMu 缺省 `0`，多开时指定）。
     MuMu 缺省开后台模式（按游戏包名截它所在的显示；MuMu 6 把应用放在单独的显示里，不开时截到的是模拟器桌面），
     `HIRO_MUMU_BACKGROUND=0` 关掉。组合不合法时开局前就报错（用 KAA 自己的配置校验）。
   - 非 PlayCover 后端不做 PlayCover 专用的退路（横屏 LIVE 黑屏时切全屏、MaaTools 点了没反应时用真实鼠标再点）。
   - 文字识别（`HIRO_OCR`）：macOS 缺省用 Apple Vision，其他系统自动改用 KAA 自带的 RapidOCR（日文 / 英文模型）。
     RapidOCR 在作者的回归截图上的准确率与速度见 `docs/assets.md`；换平台后先用自己的截图验一遍。
   - Windows 用 PowerShell 版安装脚本：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1`（步骤与 `setup.sh` 相同）。
     作者的 Windows 台式机上实测过安装、自检和全套离线测试，以及 `HIRO_DEVICE=mumu`（MuMu 6.8，截图 / 点击 `adb`）
     实机打 H.I.F 本战（2026-11-07–09，22 局 3 次夺冠，RapidOCR + Codex）。`HIRO_SCREENSHOT=nemu_ipc` 时截图正常、
     点击不生效（页面一直不变，开局 guard_new 15 秒超时），缺省已改成 `adb`。
     KAA 公开数据的下载镜像连不上时，从别处拷一份 `game_data` 目录，用 `-NoData` 加 `HIRO_KAA_GAME_DATA` 指过去。
   - Windows 开局用 `scripts\play.ps1`：把作者夺冠时的环境变量设好再调 `tools\play_game.py`，日志写到 `reports\live\play-<tag>.out`：
     ```powershell
     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\play.ps1 -Idol i_card-skin-shro-3-007 -MainOnly -Memory <Vo,Da,Vi> -Tag 1
     ```
     缺省 `-Device mumu`（截图 / 点击 `adb`）、`-Llm codex`、支援卡 / 回忆编成 4 / 5、模拟器开但考试预演只在 R2
     （`HIRO_SIM_WORKERS=1`、`HIRO_EXAM_SIM_ROUNDS=r2`，原因见脚本开头注释）。`-Proxy http://…` 给 LLM 设代理，`-NoSim` 关模拟器，
     `-Relaunch` 让游戏被杀时自己拉起（缺省关），`-Wait` 前台等跑完。参数全表见脚本开头。
   - **DMM 版**（`-Device dmm`）：
     - 游戏以管理员身份运行，普通权限的进程能截图、点击会被系统挡掉（UIPI），所以 hiro 也要以管理员身份跑。
       `scripts\play.ps1 -Device dmm` 不是管理员时会自己申请（弹一次 UAC），在新窗口里开局。
     - 游戏界面必须是日文原版：localify 之类的汉化 / 改文字插件（放在游戏目录的 `version.dll` 代理等）要先拿掉，否则 OCR 认不出页面。
     - 游戏闪退 / 窗口被关时程序会认出来并停下（按窗口标题「gakumas」判断），不会自己拉起：
       DMM 版要从 DMM Game Player 启动、每次弹管理员确认，程序点不到。重新启动游戏后 `python -m hiro.run_hif resume` 续跑。
     - 同一个账号不要同时开 MuMu 和 DMM：另一端进入培育时会弹「プロデュースデータの破棄」。
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
| `HIRO_SUPPORT_SET` / `HIRO_MEMORY_SET` | 游戏里的编成号。没设、也没有开局编成方案时，在碰游戏前退出（码 2）。写 `auto` 等于 KAA「おまかせ」，支援卡会直接确认列表第一张租借卡，只在你明确想这样时用 |
| `HIRO_MEMORY_CARDS` | 回忆编成带进培育的技能卡 ID（逗号分隔）。决策器把它们算作已有，三选一里不再找；不设时用开局编成方案里的，都没有就当空。手动设编成号时记得一起设 |
| `HIRO_FORMATION_PLAN` | 开局编成方案文件，默认 `conf/formation-plan.json`；`off` = 不读。见 [docs/formation-plan.md](docs/formation-plan.md) |
| `HIRO_LLM` | LLM，如 `claude:sonnet`（默认，走 `claude -p`）、`codex[:<model>]`（走 OpenAI Codex CLI 的 `codex exec`，用 ChatGPT 账号登录，不要 API key；Windows 上能自动找到 Codex 应用自带的 `codex.exe`，或用 `HIRO_CODEX_BIN` 指定）、`openai:<model>`（OpenAI 兼容）。`--no-llm` 只用规则。需要代理的网络给进程设 `HTTPS_PROXY` |
| `HIRO_KAA_GAME_DATA` | KAA 公开数据目录（含 `game.db`），默认 `resources/game_data` |
| `HIRO_USER_ASSETS` | 自建素材目录（`capture_icons` 的产出），可选；与随仓库素材包并用，同类图库取文件更多的一个 |
| `HIRO_BUNDLED_ASSETS` | 随仓库素材包目录，默认 `assets/pack`；设 `0` 关掉 |
| `HIRO_ASSET_SOURCE` / `HIRO_EXAM_STATE_SOURCE` / `HIRO_EXAM_ADVISOR` | 换插件实现（`包.模块:类名`）。考试预演：`HIRO_EXAM_STATE_SOURCE=hiro.sim.screen_source:SimScreenExamStateSource` |
| `HIRO_EXAM_POLICY` | 有预演时怎么用：`llm`（缺省，预演写进提示词给 LLM 参考）/ `expert`（按预演期望 + 硬规则链直接选） |
| `HIRO_SIM=0` | 不用模拟器（轮间 / 饮料 / 相談只按社区规则） |
| `HIRO_INTERVAL_REFRESH=0` | 关掉规则版的轮间货架刷新（没有模拟器时：货架没有复制 / 爆发饮料先刷新） |
| `HIRO_STRATEGY` | 换培育策略类（KAA `HifGrindStrategy` 子类），默认 `hiro.produce.hif:HiroHifStrategy` |
| `HIRO_PAUSE_FLAG` | 外部暂停旗标文件路径：文件存在就暂停对局 |
| `HIRO_DEVICE` / `HIRO_ADB` / `HIRO_SCREENSHOT` / `HIRO_EMULATOR_INSTANCE` | 设备后端与连接，见「三、环境」的「设备」表 |
| `HIRO_OCR` | 文字识别后端：`auto`（缺省）/ `vision`（Apple Vision，只有 macOS）/ `rapidocr` |
| `HIRO_RESCUE_LLM=0` | 关掉认不出画面时的 LLM 看图兜底（默认开） |
| `HIRO_DEBUG_RECOG=1` | 把全部识别互验写进 trace，页面变化都存帧 |

固定编成时，租借格按 `--main-attr` 挑主属性、等级最高的一张；挑不到就停下（不开局）。

别的设备上有进行中的培育时，`run_hif new` 会停下（退出码 3）。只有使用者明确同意放弃那一份进度，才加 `--allow-discard`。

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
