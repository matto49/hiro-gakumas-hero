# hiro-gakumas-hero · 数字小广

让大模型自己玩《学园偶像大师》（学マス）：从截图识别、状态推算、决策，到点击执行和逐回合复盘，全部自动化。
主线是 H.I.F 模式（角色是 Campus 模式的篠澤広）；2026-11 起也能打「初」、N.I.A 和偶像之路（アイドルへの道），
这三个模式刚打通，途中还常要人接手。能在 macOS（带 MaaTools 的 PlayCover 分支）、Windows MuMu 模拟器和 Windows DMM 版上跑。

**不读种子**：实战只用画面上看得到的信息，不读考试存档里的牌堆顺序和随机数。这是整个项目的规矩（2026-11-12 起）；
离线评测里「提前知道牌序」的搜索只当参照上限。

> 非官方的个人研究项目，与游戏的开发商和运营方无关。仓库的 `assets/pack/` 带了一套识别用的游戏素材（卡面、图标、字体、
> 界面图、布局），**权利属于游戏的权利人，不属于本仓库的开源许可**，仅供识别游戏画面的研究用途，详见 [assets/pack/README.md](assets/pack/README.md)；
> 主数据不在仓库里，由 `setup.sh` 从公开来源下载。

## 成绩

### H.I.F

截至 2026-11-04，H.I.F 本战（两轮合计第 1 名）共夺冠 6 次，其中两次是从选拔打起的整局。考试和培育的决策、点击都是程序自动完成的；遇到程序还不会处理的画面时会停下等人，人工处理过的地方都已补进代码（例如第 33 局的首次设置弹窗、P 道具触发的强化页）：

| 局 | 形式 | 两轮合计 | R1 | R2 |
|---|---|---|---|---|
| 20 | 只打本战 | 1,108,753 | 678,478 | 430,275 |
| 21 | **整局**（选拔三场全满星 → 本战） | 1,200,550 | 541,016 | 659,534 |
| 22 | 只打本战 | 1,228,979 | 620,481 | 608,498 |
| 31 | 只打本战 | **1,303,847** | 538,425 | 765,422 |
| 32 | 只打本战 | 1,135,657 | 549,384 | 586,273 |
| 33 | **整局** | 1,094,909 | 450,164 | 644,745 |

对手第一名（十王星南）两轮合计稳定在约 108 万。上面的成绩是在 macOS 上打的，当时作者的系统还读解密的考试存档拿局面；
现在已经不读存档，考试局面一律从画面推。

用本仓库（不读存档）在 Windows 上打的结果：

| 平台 | 结果 |
|---|---|
| Windows + MuMu 12 | 22 局里 3 次本战夺冠 |
| Windows DMM 版 | 2026-11-17、18 的验证局：本战两次第 2（两轮合计 1,006,309、1,011,498，后一局是从选拔打起的整局，选拔三场都过），一次第 3（782,246）。还没在 DMM 版上夺冠；目标是连续 3 局零人工介入 |

### 初、N.I.A、偶像之路

2026-11-17、18 在 macOS 上用另一个账号打的（这个账号的支援卡练度低，分数不能和 H.I.F 那个账号比）。初和 N.I.A 列的是
11-18 的第二局（第一局途中人工接手的页面，之后陆续补了处理器）；评价分都用 `docs/hajime-nia-mechanics.md` 的公式逐分核对过。

| 模式 | 结果 |
|---|---|
| 初 REGULAR（篠澤広） | 最終 第 1（21,519），评价 A（10,428）。中間 第 3（227）：那场用 GNRPA 出牌时考试倍率读成了 0，已修，修后还没实机复验。第一局（11-17，考试全用 UINet）：中間、最終都第 1，评价 B+（9,973） |
| N.I.A PRO（篠澤広） | 三场试镜都选到最高档（1 次、2 次是第 3 档，最終是第 4 档），ファン投票数 86,919，评价 S+（15,428）。第一局（11-17）评价 A（11,475），前半段人工接手较多 |
| 偶像之路（月村手毬） | 16–19 层连续 4 层一次拿三星，全程零介入；20、21 层一星，22 层三次没过 |

## 它是怎么工作的

```
                       ┌──────────── 策略层 hiro.brain / hiro.search / hiro.sim ────────────┐
                       │ 考试出牌：Rust GNRPA 搜索 → UINet（bench 中转层）→ 社区规则         │
                       │ 培育决策：规则 / 模拟 → 拿不准时 LLM → 社区攻略兜底                 │
                       │ 考试引擎 vendor/gakumas-player（Python / Rust）                     │
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
  | `exam_state_source` | 考试局面从哪来 | 画面识别 + 状态机记账；要预演时换成 `hiro.sim.screen_source:SimScreenExamStateSource`（`play.ps1` 缺省就用它） |
  | `exam_advisor` | 考试每一步怎么打 | `hiro.search.advisor:SearchAdvisor`：Rust GNRPA 搜索（见下文「考试出牌」）；搜不了的步退回 [gakumas-bench](https://github.com/matto49/gakumas-bench) 中转层（缺省 [UINet](https://github.com/yuzeis/GKMSUINet)，`hiro.bench_bridge`），再出错退回社区规则 |

  换实现有两种办法：设环境变量 `HIRO_ASSET_SOURCE` / `HIRO_EXAM_STATE_SOURCE` / `HIRO_EXAM_ADVISOR`（`包.模块:类名`），
  或者在另一个包的 `pyproject.toml` 里声明入口点 `[project.entry-points."hiro_gakumas.plugins"]`，装进同一个环境就自动登记。
  作者读存档的局面来源就是这样做成私有插件的，主仓库里没有任何读存档的代码。每局的 `run.json` 记下这一局实际用了哪些实现。

- **两套主循环**：H.I.F 缺省沿用 KAA 的任务流程（`hiro/loop/kaa_patches.py` 打运行时补丁）；`HIRO_RUNTIME=own` 换成自建主循环
  `hiro.runtime`（自有判页、页面处理器、中断层，不 import KAA）。初、N.I.A、偶像之路只在自建主循环上跑（KAA 不支持 N.I.A）。
- **安全拦截写在代码里**：不用ジュエル、不破棄、不按跨端培育的「開始する」、通信错误不点タイトルへ、LLM 兜底只能点识别出的文字框
  （完整列表见 [AGENTS.md](AGENTS.md)）。
- **每一步可审计**：每局自动生成逐回合复盘页（开局牌库、手牌、候选动作、状态曲线），人可以在任意一步标注「应该怎么做」，
  标注变成回归用例。

### 考试出牌（`hiro/search`）

看不到牌堆顺序，所以每一步在 Rust 引擎里抽若干个「确定化世界」（只打乱看不到的信息：牌堆顺序、未公开的回合属性），
每个世界上并行跑一次 GNRPA 搜索，按各世界的第一手投票，再把动作映射回画面上的位置。局面来自模拟器跟踪（开局是这一局的编成），
每步先和画面核对手牌、能打的牌、饮料数，核不上就不搜。

| 场次 | 缺省 | 退路 |
|---|---|---|
| H.I.F 本战 R1 / R2 | GNRPA，每步 15 秒、8 个世界 | 局面来源不是模拟器跟踪、或 Rust 包不带搜索时，整局退回 UINet；搜不了的步、一场里连续 3 步核不上也退回 |
| H.I.F 选拔 | UINet（选拔没有模拟器跟踪） | 社区规则 |
| 初 中間 / 最終、练习课 | GNRPA（`HIRO_EXAM_SEARCH_HAJIME=0` 关）。11-18 修了「考试倍率以画面读数为准」和对账护栏，修后待实机复验 | UINet |
| N.I.A 试镜 | UINet（`HIRO_EXAM_SEARCH_NIA=1` 开 GNRPA，按场次目标和对手分缩放） | 社区规则 |
| 偶像之路 | GNRPA，每步 5 秒，以三星条件为目标（`HIRO_TOWER_ALGO`） | UINet |

偶像之路用同一编成重试时，上一次在画面上看到的抽牌顺序会交给搜索当山札开头。这是玩家自己也能记住的信息，不算读种子。
搜索算法先在 [gakumas-bench](https://github.com/matto49/gakumas-bench)（题目来自实战开局，和别的算法同题同牌序配对比较）上证明有效，再上实战。

### 培育决策（`hiro/brain`）

规则和模拟能明确决定的直接给；拿不准的（规则没覆盖、各选项的模拟结果差不到噪声以上、识别缺字段）交给 LLM 在候选里选，
同时把模拟算出的数值给它参考；LLM 不可用或输出不合法时按社区攻略兜底。目前接了 LLM 的决策点：选行动、抓卡三选一、
换卡第 2 段（换出哪张）、删卡、饮料三选一、相談买饮料、饮料满时丢哪瓶、轮间、P 道具三选一、特別指導、事件选项、
考试里的技能卡移动。`HIRO_PRODUCE_LLM`（缺省全开）可以按名字只开其中几条或全关。每局报告统计各决策来自规则、模拟、LLM
还是兜底，以及兜底次数。

初和 N.I.A 有自己的规则和打法文档（`hiro/brain/hajime_nia.py`、`docs/hajime-playbook.md`、`docs/nia-playbook.md`、
`docs/hajime-nia-mechanics.md`）；偶像之路的编成（主回忆 + 副回忆）按每种组合的估计三星率在 Rust 引擎里批量评估后选。

### 模拟器（`hiro/sim` + `vendor/gakumas-player`）

考试引擎 `gakumas_exam` 按客户端规则逐条实现考试（效果、附魔、成长、指针、费用、随机数），主数据用社区公开的 master
（vertesan/gakumasu-diff 固定提交，setup 时下载构建），并用实机记录整体对账；`gakumas_exam_rs` 是同一套规则的 Rust 实现，
setup 检测到 Rust 工具链时本地编译（快约 8 倍）；没有时下载 CI 编好的预编译包（GitHub Release「engine-rs-<源码哈希>」，Windows / macOS / Linux，校验 sha256），下载不到再用 Python 版，结果相同（Intel Mac 的预编译包是在 Apple Silicon 上交叉编译的，未经实机验证）。培育层的公式（日程、考试倍率、星星系数、
支援卡技能）在 `gakumas_produce`。
轮间买卖、饮料取舍、相談等资源决策用同一批种子配对模拟各选项；社区攻略是先验，模拟显著更好（约 2 个标准误）才偏离。

考试局面从画面推（`hiro.sim.screen_source`）：用培育记账的牌组合成开局（带上这一局的 P 道具、支援卡、回忆），按执行的动作跟踪，
抽到的牌、强化次数、饮料按画面对齐，对不上就按画面重建（Windows 实机记录离线重放：97% 以上的步能给出预演）。本战开局会主动打开牌组
一览把整副牌读一遍，牌组记不全就建不起开局。考试出牌插件用的就是这份跟踪出来的局面；另有「预演」（每个合法动作的期望终局分，
给社区规则和 LLM 参考），`play.ps1` 缺省只在 R2 预演（`HIRO_EXAM_SIM_ROUNDS=r2`），代码缺省是 R1、R2 都预演。

## 快速开始

作者实机验证过的平台：macOS（Apple Silicon）+ PlayCover，Windows + MuMu 12（22 局 3 次本战夺冠），和 Windows DMM 版
（`-Device dmm`，要管理员权限、游戏用日文原版）。安卓设备 / 模拟器（ADB）、雷电与 [KAA](https://github.com/XcantloadX/kotones-auto-assistant) 对齐，用 `HIRO_DEVICE` 选择、走 KAA 的设备工厂，
代码和离线测试都有，但还没有实机打过一局（见 [AGENTS.md](AGENTS.md)「设备」）。

> **macOS 只支持带 MaaTools 的 PlayCover。** 请安装 [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover)，
> 并在游戏的 PlayCover 设置里打开 `maaTools`（端口 `maaToolsPort`）。截图和点击都走游戏进程内的 MaaTools 服务，
> 不要求游戏在前台，也不动鼠标。**原版 PlayCover 不支持**：以前「连不上 MaaTools 就退回 Quartz 截图」的兼容路径已经去掉，
> 连不上时程序会报错停下，不会换一种方式继续。换装分支版后，第一次要从 PlayCover 界面启动游戏（让它装好 MaaTools 用的插件）。

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
   考试要用预演，再加 `HIRO_EXAM_STATE_SOURCE=hiro.sim.screen_source:SimScreenExamStateSource`（Windows 的 `play.ps1` 已经替你设好）。

   **Windows（MuMu / DMM 版）**：装环境用 `scripts\setup.ps1`，开局用 `scripts\play.ps1`（缺省就是作者夺冠时的配置）：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\play.ps1 -Idol <偶像卡 skin id> -MainAttr Da -Tag 1
   ```

   DMM 版加 `-Device dmm`（脚本会申请管理员权限）。参数和注意事项见 [AGENTS.md](AGENTS.md)「设备」。

   Windows 上要知道的几件事：

   - **DMM 版开局后别动那台机器的鼠标。** DMM 版游戏不看点击消息里的坐标，只认真实光标的位置，所以后台点击的做法是：
     先等光标停稳，把游戏窗口挪到「要点的位置正好在光标下」，再发点击消息。光标本身不会被移动，但游戏窗口会跟着光标跳；
     人一动鼠标，程序就会等着，或者点偏。最好把这台机器当专用机。MuMu 走 adb，没有这个问题。
   - **DMM 版要用日文原版客户端。** 装了汉化补丁（比如 gakuen-imas-localify）的中文界面认不出来。
   - **内存**：hiro 的进程要 3 GB 多（图库、卡图索引、OCR 模型、主数据、模拟器），DMM 版游戏本身约 4 GB。
     内存吃紧时加 `-NoSim` 关掉模拟器；MuMu 被系统杀掉时加 `-Relaunch`，让程序自己拉起游戏。
   - **文字识别**：Windows 缺省用 RapidOCR，macOS 缺省用系统自带的 Vision，用 `HIRO_OCR` 切换。
   - **Rust**：`setup.ps1` 找不到 cargo 时，会下载 CI 编好的考试引擎预编译包，不用自己装 Rust。
   - **DMM 版的截图 / 点击**（实验，缺省关）：`HIRO_DMM_DRIVER=maafw` 换成 MaaFramework 的 Win32 控制器，见 [AGENTS.md](AGENTS.md)「设备」。

   **其他模式**（自建主循环，游戏停在首页，编成号按开局编成方案）：

   ```bash
   PYTHONPATH=src python -m hiro.runtime.hajime     new --idol <偶像卡 skin id> [--difficulty regular|pro|master]   # 初
   PYTHONPATH=src python -m hiro.runtime.nia_sample new --idol <偶像卡 skin id> [--difficulty pro|master]           # N.I.A
   PYTHONPATH=src python -m hiro.runtime.tower_play --idol-name <偶像名> [--layers N]                              # 偶像之路
   ```

   初和 N.I.A 停下后用 `resume` 续跑。N.I.A 还是「采样模式」：认不出、没有处理器的页面存帧后停下交人，人处理完再 `resume`。
   偶像之路按层打，没拿到三星且重试不花钱时再挑战（每层最多 3 次），确认框出现消耗字样就停。

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

（Windows 上 MuMu 的 22 局和 DMM 版的整局都是带素材包打的；每次改识别都拿 DMM 实机帧的回归集（`tests/dmm_regression.json`，样例帧不随仓库）在 Mac 两种 OCR 和 Windows 上各跑一遍。）

不想用随仓库的素材包时，设 `HIRO_BUNDLED_ASSETS=0`，识别层退回只用 KAA 公开数据，考试效果名等要跑 `capture_icons` 补。

完整表和退路说明见 [docs/assets.md](docs/assets.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `src/hiro/contracts/` | 数据契约（dataclass + JSON schema） |
| `src/hiro/vision/`、`perception/` | 识别层 |
| `src/hiro/state/`、`loop/reduce.py` | 状态、记账、不变量 |
| `src/hiro/brain/`、`defaults/advisor.py` | 策略层：培育决策（规则、社区先验、LLM 补位；`hajime_nia.py` 是初 / N.I.A），LLM 适配（`claude -p`、Codex CLI、OpenAI 兼容） |
| `src/hiro/search/` | 考试出牌：Rust GNRPA 搜索插件（`advisor.py`）、初 / N.I.A / 偶像之路的考试开局和算法（`exam_env.py`、`exam_algo.py`、`tower_algo.py`） |
| `src/hiro/exec/`、`loop/`、`produce/` | 操作层：执行器、KAA 主循环、页面处理、恢复、安全拦截；`loop/win_desktop.py` 是 DMM 后台点击的桌面保护 |
| `src/hiro/runtime/` | 自建主循环（不用 KAA）：判页、处理器、中断层、开局 / 续跑；初（`hajime.py`）、N.I.A（`nia_sample.py`）、偶像之路（`tower_play.py`）的入口 |
| `src/hiro/devices/`、`device.py` | 设备层：MaaTools（macOS）、ADB、DMM 窗口、MaaFramework Win32 |
| `src/hiro/sim/` | 模拟器接入：轮间 / 饮料 / 相談的模拟、考试预演、从画面跟踪局面（`screen_source.py`） |
| `src/hiro/bench_bridge/` | 和 gakumas-bench 的中转层：把画面局面写成 bench 的观测（UIObs），调 UINet 等算法 |
| `src/hiro/plugins.py`、`defaults/` | 插件接口与默认实现 |
| `tools/play_game.py`、`src/hiro/run_hif.py` | 入口 |
| `scripts/` | `setup.sh` / `setup.ps1` 装环境，`play.ps1` 是 Windows 一键开局 |
| `tools/viewer/`、`tools/audit/`、`tools/preview_stats.py` | 复盘页、低级失误审计、过场耗时、预演耗时统计 |
| `tools/assets/capture_icons.py` | 从自己的截图补齐图标素材 |
| `docs/` | 契约、状态机、素材与退路、社区打法（H.I.F `hif-playbook.md`，初 / N.I.A `hajime-playbook.md`、`nia-playbook.md`、`hajime-nia-mechanics.md`） |
| `vendor/gakumas-player` | 考试引擎 `gakumas_exam`、Rust 版 `gakumas_exam_rs`（含世界采样和 GNRPA 搜索）、培育公式 `gakumas_produce`（含初 / N.I.A 的日程和评价公式）；主数据由 `vendor/gakumas-player/scripts/fetch_master.py` 下载（setup 会跑），不进仓库 |
| `vendor/kaa` | KAA（kotones-auto-assistant），子模块，固定版本 |
| `.github/workflows/` | 测试；考试引擎 Rust 预编译包（Windows / macOS / Linux，发到 Release） |

## 致谢与许可

- 识别层的部分逻辑基于 [KAA（kotones-auto-assistant）](https://github.com/XcantloadX/kotones-auto-assistant) 的识别接口、主数据与界面模板；
  培育的页面流程沿用 KAA 的任务流程，在 `hiro/loop/kaa_patches.py` 里打运行时补丁。KAA 以 GPL-3.0 发布。
- **许可**：本仓库自己的代码以 **AGPL-3.0-or-later** 发布（[LICENSE](LICENSE)；2026-11-20 起，之前是 GPL-3.0-or-later）。
  从 KAA 派生的文件保留 GPL-3.0（文件头注明，全文见 [LICENSE.GPL-3.0](LICENSE.GPL-3.0)），按 GPL-3.0 / AGPL-3.0 第 13 条和其余代码合在一起发布。
  改过的版本分发出去，或者放到网上给别人用，要同样公开源码。
- 截图和点击默认通过 [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover)（GPL-3.0，派生自 PlayCover）里
  [PlayTools](https://github.com/hguandl/PlayTools)（AGPL-3.0）内置的 MaaTools 服务完成：它在游戏进程内开 TCP 服务，进程内截图、注入触摸，
  不要求游戏在前台，也不动鼠标。本仓库只按它的协议做客户端（`hiro/device.py`），不包含它的代码；macOS 上只支持这一种方式。
  致谢 MaaTools 的作者们。
- 考试出牌用到 yuzeis 的 [GKMSUINet](https://github.com/yuzeis/GKMSUINet)（UINet）。用 GNRPA 搜索打学マス考试的思路来自
  yuzeis 的 GKMS 项目；本仓库的实现按 Cazenave（2020）的 GNRPA 论文在 Rust 引擎里自己写（`gakumas_exam_rs/src/search.rs`）。
- H.I.F 打法整理自社区攻略（seesaawiki、wikiwiki 等），出处写在 `docs/hif-playbook.md` 和 `hiro/brain/hif_rules.py`。
- 《学園アイドルマスター》及相关名称、图像的权利属于其权利人。`assets/pack/` 里随仓库带的识别素材同样属于权利人，仅供识别研究用途（见 `assets/pack/README.md`）；请遵守游戏的用户协议，
  不要把相关技术用于破坏公平性或损害他人利益的用途。
