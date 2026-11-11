# hiro-gakumas-hero · 数字小广

让大模型自己玩《学园偶像大师》（学マス）的 H.I.F 模式：从截图识别、状态推算、决策，到点击执行和逐回合复盘，
全部自动化。角色是 Campus 模式的篠澤広。

> 非官方的个人研究项目，与游戏的开发商和运营方无关。仓库的 `assets/pack/` 带了一套识别用的游戏素材（卡面、图标、字体、
> 界面图、布局），**权利属于游戏的权利人，不属于本仓库的 GPL**，仅供识别游戏画面的研究用途，详见 [assets/pack/README.md](assets/pack/README.md)；
> 主数据不在仓库里，由 `setup.sh` 从公开来源下载。

## 成绩

截至 2026-11-04，H.I.F 本战（两轮合计第 1 名）共夺冠 6 次，其中两次是从选拔打起的整局。考试和培育的决策、点击都是程序自动完成的；遇到程序还不会处理的画面时会停下等人，人工处理过的地方都已补进代码（例如第 33 局的首次设置弹窗、P 道具触发的强化页）：

| 局 | 形式 | 两轮合计 | R1 | R2 |
|---|---|---|---|---|
| 20 | 只打本战 | 1,108,753 | 678,478 | 430,275 |
| 21 | **整局**（选拔三场全满星 → 本战） | 1,200,550 | 541,016 | 659,534 |
| 22 | 只打本战 | 1,228,979 | 620,481 | 608,498 |
| 31 | 只打本战 | **1,303,847** | 538,425 | 765,422 |
| 32 | 只打本战 | 1,135,657 | 549,384 | 586,273 |
| 33 | **整局** | 1,094,909 | 450,164 | 644,745 |

对手第一名（十王星南）两轮合计稳定在约 108 万。上面的成绩用的是作者的完整系统（考试局面来自解密的考试存档，见下文）；
本仓库现在带模拟器（轮间 / 饮料 / 相談按模拟，考试预演从画面局面推），不读存档也能打完一局。

## 它是怎么工作的

```
                       ┌───────────── 策略层 hiro.brain / hiro.defaults.advisor ─────────────┐
                       │ 社区攻略先验（docs/hif-playbook.md）→ 规则链 → LLM 兜底             │
                       └──────────────▲────────────────────────────────┬──────────────────┘
                                      │ 状态（契约）                    │ 决策（契约）
截图 ─► 识别层 hiro.vision ─► Observation ─► reduce（纯函数，可重放）─► 状态 hiro.state
           ▲ 素材：插件 AssetSource                              │
设备 ◄── 操作层 hiro.device / exec / loop / produce ◄── 执行器（点击、等稳、补点、弹窗、恢复、安全拦截）
                                      │
                 trace（每一步：截图 + 观测 + 状态 + 决策 + 执行结果）
                                      ▼
                 复盘页 tools/viewer · 低级失误审计 tools/audit · 离线回放
```

- **三层**：识别层（看）、策略层（想）、操作层（动）。层与层之间只交换数据契约（`hiro.contracts`），读不到的字段是 `None`，不猜。
- **状态只由纯函数修改**：同样的观测序列永远得到同样的状态，每一局都能离线重放、逐步比对。
- **记账 + 互验**：每个动作先推算下一步应该读到什么，再和识别读数对照；明显不合理的读数（掉位、越界、倒退）丢弃重读。
- **三个插件**（`hiro/plugins.py`），换实现不用改主循环：

  | 插件 | 回答什么 | 本仓库的默认实现 |
  |---|---|---|
  | `asset_source` | 素材和主数据从哪来 | KAA 公开数据 + 随仓库素材包 `assets/pack/` + 使用者自建素材 |
  | `exam_state_source` | 考试局面从哪来 | 画面识别 + 状态机记账 |
  | `exam_advisor` | 考试每一步怎么打 | 社区规则链 + LLM 兜底 |

- **安全拦截写在代码里**：不用ジュエル、不破棄、不按跨端培育的「開始する」、通信错误不点タイトルへ、LLM 兜底只能点识别出的文字框
  （完整列表见 [AGENTS.md](AGENTS.md)）。
- **每一步可审计**：每局自动生成逐回合复盘页（开局牌库、手牌、候选动作、状态曲线），人可以在任意一步标注「应该怎么做」，
  标注变成回归用例。

### 模拟器（`hiro/sim` + `vendor/gakumas-player`）

考试引擎 `gakumas_exam` 按客户端规则逐条实现考试（效果、附魔、成长、指针、费用、随机数），主数据用社区公开的 master
（vertesan/gakumasu-diff 固定提交，setup 时下载构建），并用实机记录整体对账；`gakumas_exam_rs` 是同一套规则的 Rust 实现，
setup 检测到 Rust 工具链时本地编译（快约 8 倍）；没有时下载 CI 编好的预编译包（GitHub Release「engine-rs-<源码哈希>」，Windows / macOS / Linux，校验 sha256），下载不到再用 Python 版，结果相同（Intel Mac 的预编译包是在 Apple Silicon 上交叉编译的，未经实机验证）。培育层的公式（日程、考试倍率、星星系数、
支援卡技能）在 `gakumas_produce`。
每一步把合法动作放进若干「确定化世界」（只打乱看不到的信息：牌堆顺序、未公开的回合属性），回合内搜索后续打到考试结束，
按配对差比较期望终局分；轮间买卖、饮料取舍等资源决策用同一批种子配对模拟。社区攻略是先验，模拟显著更好（约 2 个标准误）才偏离。

考试局面：作者自己的系统读解密的考试存档（精确局面，不开源）；本仓库的 `hiro.sim.screen_source` 改从画面推——
用培育记账的牌组合成开局，按执行的动作跟踪、抽到的牌按画面对齐，对不上就按画面重建（Windows 实机记录离线重放：97% 以上的步能给出预演）。

## 快速开始

作者实机验证过的平台：macOS（Apple Silicon）+ PlayCover，Windows + MuMu 12（22 局 3 次本战夺冠），和 Windows DMM 版
（`-Device dmm`，要管理员权限、游戏用日文原版）。安卓设备 / 模拟器（ADB）、雷电与 [KAA](https://github.com/XcantloadX/kotones-auto-assistant) 对齐，用 `HIRO_DEVICE` 选择、走 KAA 的设备工厂，
代码和离线测试都有，但还没有实机打过一局（见 [AGENTS.md](AGENTS.md)「设备」）。macOS 上默认的截图和点击走 [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover)
里的 MaaTools 服务，需要用这个分支，并在游戏的 PlayCover 设置里打开 `maaTools`（端口 `maaToolsPort`）；用原版 PlayCover 时会退回 Quartz 截图，
这时游戏窗口要在前台，点击也更不稳。

```bash
git clone https://github.com/matto49/hiro-gakumas-hero
cd hiro-gakumas-hero
scripts/setup.sh                                        # 装依赖、生成 KAA 文件、下载公开游戏数据、自检（约 4 分钟）
PYTHONPATH=src .venv/bin/python -m pytest -q            # 没有截图样例时，读截图的用例会跳过
```

1. **素材**：公开游戏数据由 `setup.sh` 下载，识别用的图标、卡面、字体随仓库（`assets/pack/`），不用再准备。
   客户端更新出新界面、新图标时，用自己的截图跑 `tools/assets/capture_icons.py` 补。详见 [docs/assets.md](docs/assets.md)。
2. **打一局**（游戏停在首页，已编好支援卡 / 回忆编成）：

   ```bash
   HIRO_SUPPORT_SET=<支援卡编成号> HIRO_MEMORY_SET=<回忆编成号> \
     python tools/play_game.py --idol <偶像卡 skin id> --main-attr <Vo|Da|Vi>
   ```

   也可以让程序按社区攻略选支援卡和回忆、生成开局编成方案，开局时自动核对编成，见 [docs/formation-plan.md](docs/formation-plan.md)。

   **Windows（MuMu / DMM 版）**：装环境用 `scripts\setup.ps1`，开局用 `scripts\play.ps1`（缺省就是作者夺冠时的配置）：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\play.ps1 -Idol <偶像卡 skin id> -MainAttr Da -Tag 1
   ```

   DMM 版加 `-Device dmm`（脚本会申请管理员权限）。参数和注意事项见 [AGENTS.md](AGENTS.md)「设备」。

3. **看复盘**：`python tools/viewer/build_exam_view.py <trace>`，打开生成的 HTML。

更完整的说明（环境变量、续跑、出问题怎么查）在 [AGENTS.md](AGENTS.md)。这份文件也是给 AI 看的：把仓库交给一个 AI 会话，
让它读 `AGENTS.md` 和 `skills/`，它就能装环境、开局和排查问题。

没有游戏也可以先看复盘页长什么样：

```bash
python examples/make_demo_trace.py
python tools/viewer/build_exam_view.py demo-run --traces examples/traces --out /tmp/hiro-view
open /tmp/hiro-view/demo-run.html
```

## 默认素材下能读到什么

| 读数 | 默认（KAA 公开数据 + 随仓库素材包） |
|---|---|
| 判页、HUD（剩余回合 / 元気 / 体力）、考试分数、名次、手牌、P 饮料、主数据、日程 | 可用 |
| 考试效果名（やる気、好印象…）、P 道具名、开始确认页的支援卡 | 可用（素材包带图标和支援卡图） |
| 支援卡效果、考试要求 / 倍率 / 星级基准 | 不可用（策略层走规则退路） |

（带素材包的读数还没在开源仓库里打一整局验证过，A5 那局用的是不带素材包的配置。）

不想用随仓库的素材包时，设 `HIRO_BUNDLED_ASSETS=0`，识别层退回只用 KAA 公开数据，考试效果名等要跑 `capture_icons` 补。

完整表和退路说明见 [docs/assets.md](docs/assets.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `src/hiro/contracts/` | 数据契约（dataclass + JSON schema） |
| `src/hiro/vision/`、`perception/` | 识别层 |
| `src/hiro/state/`、`loop/reduce.py` | 状态、记账、不变量 |
| `src/hiro/brain/`、`defaults/advisor.py` | 策略层：规则、社区先验、LLM 适配（`claude -p`、OpenAI 兼容） |
| `src/hiro/exec/`、`loop/`、`produce/`、`device.py` | 操作层：执行器、主循环、页面处理、恢复、安全拦截 |
| `src/hiro/plugins.py`、`defaults/` | 插件接口与默认实现 |
| `tools/play_game.py`、`src/hiro/run_hif.py` | 入口 |
| `tools/viewer/`、`tools/audit/` | 复盘页、低级失误审计、过场耗时 |
| `tools/assets/capture_icons.py` | 从自己的截图补齐图标素材 |
| `docs/` | 契约、状态机、素材与退路、H.I.F 社区打法 |
| `vendor/kaa` | KAA（kotones-auto-assistant），子模块，固定版本 |

## 致谢与许可

- 识别层的部分逻辑基于 [KAA（kotones-auto-assistant）](https://github.com/XcantloadX/kotones-auto-assistant) 的识别接口、主数据与界面模板；
  培育的页面流程沿用 KAA 的任务流程，在 `hiro/loop/kaa_patches.py` 里打运行时补丁。KAA 以 GPL-3.0 发布；
  本仓库随之采用 **GPL-3.0-or-later**。从 KAA 派生的文件在文件头注明。
- 截图和点击默认通过 [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover)（GPL-3.0，派生自 PlayCover）里
  [PlayTools](https://github.com/hguandl/PlayTools)（AGPL-3.0）内置的 MaaTools 服务完成：它在游戏进程内开 TCP 服务，进程内截图、注入触摸，
  不要求游戏在前台，也不动鼠标。本仓库只按它的协议做客户端（`hiro/device.py`），不包含它的代码。连不上时退回 kotonebot 的 Quartz 截图
  + `CGEventPostToPid` 投递。致谢 MaaTools 的作者们。
- H.I.F 打法整理自社区攻略（seesaawiki、wikiwiki 等），出处写在 `docs/hif-playbook.md` 和 `hiro/brain/hif_rules.py`。
- 《学園アイドルマスター》及相关名称、图像的权利属于其权利人。`assets/pack/` 里随仓库带的识别素材同样属于权利人，仅供识别研究用途（见 `assets/pack/README.md`）；请遵守游戏的用户协议，
  不要把相关技术用于破坏公平性或损害他人利益的用途。
