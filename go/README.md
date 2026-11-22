# Go 迁移

把 hiro-gakumas-hero 从 Python 迁到 Go：先是工具层（`hiro` 命令），最后是整个运行时（设备、识别、状态、决策、各模式主循环）。玩家装的初星助手里不再需要 Python。

这份文件是迁移的设计和进度表。改了决定或做完一个阶段，就更新这里。

## 为什么

| 收益 | 说明 |
|---|---|
| 玩家装得上 | 现在第一次打开要现场装 Python、依赖、拉源码（约 5 分钟，要 git，可能卡在网络和 PyPI）。迁完后一个安装包就能用，程序和代码一起更新 |
| 编译期抓错 | 2026-11-20 用 pyright 查 `src/hiro`：1086 个类型错误，其中 385 个是「可能是 None 却直接用」、50 个是调用参数不对。Go 编译器和 nilaway 能挡住大部分 |
| 一个进程 | 现在是 Go 外壳 → Python 后台服务 → 对局子进程。迁完后在一个程序里，Windows DMM 要的管理员权限也好处理 |
| 一种语言 | 初星助手外壳、初星攻略站服务器已经是 Go |

不指望的：执行效率。一局 40 分钟里 Python 自己算的时间不到 1 分钟，其余在等游戏和 LLM；识别里最重的部分本来就是 OpenCV、onnxruntime 的 C++ 代码。

## 已定的决定

| 决定 | 谁定的 |
|---|---|
| 主循环只走 `src/hiro/runtime/`，`loop/`（基于 KAA）不再加功能、逐步退役；Go 只照着 runtime 搬 | 用户 11-20 |
| 整体迁到 Go：工具层 + 游戏核心 | 用户 11-20 |
| 插件机制改版：仓库自带的选择（画面识别 / 模拟器跟踪、GNRPA / UINet）改成配置项；外部插件走 `go/pkg/plugin` 公开接口 | 用户 11-20 |
| 私有存档插件跟着改成新格式，**继续私有**，不进开源仓库（11-09「开源仓库不放读存档、解密、种子代码」不变） | 用户 11-20 |
| 运行时不留 Python；`tools/` 里的离线工具（复盘、审计、素材采集、模型训练）继续用 Python，不随安装包发 | 建议，用户确认开工 |
| 顺序：Python 里先退 `loop/`；Go 从工具层开始，再按「设备 → 识别 → 状态 → 决策 → 各模式主循环」搬 | 建议，用户确认开工 |
| 设备层照 `src/hiro/devices` 的三个实现自己写（MaaTools、adb、Win32）；图像用 gocv，模型推理用 onnxruntime_go，Mac 文字识别用 cgo 调 Vision；MaaFramework 只当 DMM 备选 | 建议，用户确认开工 |
| 考试引擎（Rust）当子进程用，和初星攻略站同一种接法 | 建议，用户确认开工 |
| 验收：每层用 traces 对照（同一截图两边认出的一致、同一局面两边决策一致），每个模式实机打一局成绩不低于 Python 版才切 | 建议，用户确认开工 |
| 迁移期间 Python 不冻结，新功能只加在 runtime 这条线；某层迁完后 Python 那份只修 bug，以 Go 为准 | 建议，用户确认开工 |
| `hiro` 的命令名、参数、JSON 信封、退出码不变；`HIRO_*` 环境变量名不变；`HIRO_HOME` 里的文件格式不变（Python 和 Go 能读写同一份） | 我的决定 |
| Go 代码放 `go/` 子目录（模块 `github.com/matto49/hiro-gakumas-hero/go`）：仓库根有 Python 的 `vendor/`，Go 会把它当依赖目录 | 我的决定 |
| 通用的「任何语言接入」子进程插件协议先不做：现在没有外部用户 | 我的决定 |
| 截图缩放用纯 Go 照 OpenCV 5 的通用 INTER_LINEAR 算法写（每个平台结果一样，不要 cgo）。cv2 自己在 Mac 上走 Carotene / KleidiCV、x86 上走 IPP，各平台本来就差 1–2 个灰度级；和 Mac 的 cv2 比：放大逐字节相同，缩小最多差 2。识别对照用的 traces 截图已经是 720x1280，不经过这一步 | 我的决定（11-20） |
| DMM 用 maa-framework-go（purego 动态加载，不要 cgo），安装包要带 MaaFramework v5.14.3 的 Windows 动态库（和 Python 版的 maafw 5.14.3 一样）；截图 / 输入方式的数值按 maafw 5.14.3 自己的对照表（绑定库 v3.5.1 的常量名对应的是别的版本） | 我的决定（11-20） |
| 识别层的图像处理用 gocv（cgo + OpenCV 4），不手写纯 Go：模板匹配、形态学、轮廓、连通块要和 cv2 一致，手写的对照成本太高。gocv 只在 `internal/cv` 里用，Mat 在函数里建、用完就关，往外只给 `img.BGR` / `img.Gray`；OpenCV 内部报错在 `cv` 里 panic，识别入口用 `cv.Recover` 转成 error。Mac 开发机 `brew install opencv@4`（keg-only，Taskfile 自己设 `PKG_CONFIG_PATH`）；Windows 和 CI 要装 OpenCV 并在各自平台上编，不能再从 Mac 交叉编译；安装包要带 OpenCV 动态库 | 用户 11-20 |
| Mac 文字识别用 cgo 调 Apple Vision（同 Python 版的 pyobjc 调法：accurate、不做语言纠正、区域先放大 3 倍双三次）。非 Mac 用 RapidOCR（onnxruntime），阶段 3 后段迁 | 我的决定（11-20） |
| ddddocr 用 Go 重写（同一个 common_old.onnx + 旧字符集，预处理照 Pillow 的 C 实现逐位写：Lanczos 缩放、转灰度），推理走 onnxruntime_go + onnxruntime 1.32（Mac：brew install onnxruntime）。Python 版被 KAA 钉在 onnxruntime 1.14，同一输入 logits 最多差 0.016，边缘情况读出的字不同；换成 1.22 以上（和 Go 一样）就逐位相同。模型文件不进仓库：开发机放 cache/models/ddddocr/，安装包放 HIRO_HOME/models/ | 我的决定（11-20） |
| 识别层自己的小模板和数据（signatures.json、layout_*.json、templates/）复制一份到 go/internal/vision/data 编进程序；迁移期间两份要一致，阶段 6 删 Python 时只留 Go 这份 | 我的决定（11-20） |
| 卡面图库（card_db）：Python 用 SIFT + faiss IVF（训练抽样靠 numpy 随机数，Go 复刻不了），Go 用 gocv 的 SIFT + 自己写的 IVF 检索；对照比「认出哪张卡」，不比距离 | 我的决定（11-20） |

## 目录

```
go/
  cmd/hiro/            hiro 命令（和 Python 版同名同参数）
  internal/
    jsonx/             JSON 输出：不转义 HTML、保留字段顺序（和 Python json.dumps(ensure_ascii=False) 一致）
    home/              HIRO_HOME
    cli/               命令登记、参数解析、信封、退出码、待确认流程
    pending/           待确认（HIRO_HOME/pending）
    store/             信息库（HIRO_HOME/hiro.db，SQLite，表结构同 Python）
    info/              hiro info …
    catalog/           hiro catalog …
    plan/              hiro plan …
    secret/            hiro secret …（钥匙串、剪贴板）
    service/           后台服务、任务（jobs）、客户端
    img/               BGR 图像、缩放（OpenCV 通用算法）
    device/            设备：maatools（Mac）、adb（MuMu / 雷电 / 真机）、dmm（Windows，MaaFramework）、DMM 窗口尺寸
    report/            成绩摘要
    doctor/            hiro doctor
    pybridge/          还没迁的部分转给 Python（迁完一块删一块）
  pkg/plugin/          外部插件接口（私有存档插件从这里接）
  testdata/            黄金数据
```

层规则写在 `.go-arch-lint.yml`，`task lint` 会查：`jsonx`、`home` 谁都能用；`store`、`pending` 只依赖它们；命令包（`info`、`catalog`…）不互相依赖；`cli` 只做登记和分发。

## 分阶段

| 阶段 | 内容 | 验收 | 状态 |
|---|---|---|---|
| 0 工程底座 | `go/` 模块、Taskfile、golangci-lint、go-arch-lint、lefthook、CI | `task check` 全过 | 完成 |
| 1 工具层 | `hiro` 全部命令用 Go 实现；对局子进程仍是 Python；没迁的命令经 `pybridge` 转给 Python | 同一个 HIRO_HOME 下，每条命令 Go 和 Python 输出的 JSON 相同（对照测试）；桌面程序改用 Go 版 `hiro` | Mac 完成（11-20）；Windows 实机待验 |
| 2 设备层 | MaaTools、adb、Win32 截图 / 点击 / 滑动 | 实机：截图尺寸、坐标换算和 Python 版一致 | 代码完成（11-20），实机验收放到最后 |
| 3 识别层 | 判页、读数、OCR、卡面检索 | traces 里的截图逐张对照，页面和字段读数一致 | 进行中 |
| 4 状态 + 决策 | 状态账本、社区规则、编成方案、LLM、引擎子进程 | 黄金用例和 traces 局面上决策一致 | 未开始 |
| 5 各模式主循环 | runtime：初、N.I.A、H.I.F、偶像之路、日常 | 每个模式实机一局，成绩不低于 Python 版 | 未开始 |
| 6 去掉 Python | 安装包不再带 Python；删掉 `pybridge` | 新机器装上就能打完一局 | 未开始 |

## 迁移期间怎么共存

- **命令**：Go 版 `hiro` 是入口。还交给 Python 的（经 `pybridge` 调 `python -m hiro.cli.main`，信封原样返回）：`plan formation`（社区规则，阶段 4）、`run review` 和成绩摘要里的考试分数（复盘工具 `tools/viewer`，阶段 3）。`doctor --probe`、调 DMM 窗口（`fit-window` 任务）阶段 2 起用 Go。
- **桌面程序**：安装包带 Go 版 `hiro`（`app/scripts/build-hiro.mjs` 编进 `resources/<平台>/bin/`，`bun run build` 会先编它）。程序找 `hiro` 的顺序：`HIRO_CLI` → 自带的 Go 版 → `HIRO_HOME/src/.venv` 的 Python 版 → PATH。阶段 1 的 Go 代码不用 cgo，Mac 上能直接编出 Windows 版。
- **服务**：Go 服务和 Python 服务用同一把锁（`HIRO_HOME/service.lock`）、同一个 `service.json`（`{port, token, pid}`）、同一套接口（`/ping /status /start /events /pause /takeover /resume /stop /shutdown`），同一个 HIRO_HOME 只会有一个服务在跑，哪边的客户端都能连。
- **对局**：阶段 1–4 对局还是 Python 子进程（`hiro.run_hif`、`hiro.runtime.hajime` …）。Go 服务按这个顺序找 Python：`HIRO_PYTHON` → 仓库 `.venv` → `HIRO_HOME/src/.venv`；仓库位置：`HIRO_REPO` → 从程序位置和当前目录往上找 `src/hiro/__init__.py` → `HIRO_HOME/src`。
- **数据**：`HIRO_HOME` 下的 `hiro.db`、`pending/`、`jobs/<编号>/events.jsonl`、`job.json`、`secrets.json`、`formation-plan.json`、`settings.json` 格式不变，两边都能读写。钥匙串条目（服务名 `hiro-console`、账户 = 名字）不变。

## 验收方法

1. **单元测试**：每个包自己的 `_test.go`。Python `tests/cli/` 的 86 个用例逐条搬过来。
2. **对照测试**（`go/internal/parity`）：准备一个 HIRO_HOME 夹具，同一组命令分别用 Python 和 Go 跑，比较 JSON（比较语义，不比空格）。本机有 Python 环境时跑，没有就跳过；设 `HIRO_PARITY_PYTHON=<python 路径>` 指定。
3. **黄金数据**（阶段 3 起）：`tools/golden/export_vision.py` 用 Python 版把截图（`digital-hiro` 仓库的 tests/fixtures 742 张 + traces 848 张）逐张跑一遍判页、读数和整帧 OCR，结果写到 `~/.cache/hiro/golden/vision`（截图和读数都不进仓库）；Go 的识别测试读这个目录逐张比对，目录不存在就跳过。要用装了 pyobjc Vision 的 Python（`~/github/digital-hiro/.venv`）、`HIRO_OCR=vision`，否则 OCR 后端不一样，结果没法比。
   逐函数对照：`tools/golden/probe.py` 在每张截图上跑 Python 版的某个函数（组合探针在 `tools/golden/probes.py`），`internal/golden` 缓存结果并和 Go 逐张比；`task golden` 跑全部。对照用的 Python 是专用环境 `~/.cache/hiro/golden-venv`（包版本同上，只把 onnxruntime 换成 1.22，和 Go 用的版本逐位一致），没有就设 `HIRO_PARITY_PYTHON`。
4. **实机**（阶段 2、5）：每个模式在 Mac（账号 2：初 / N.I.A / 偶像之路）或 Windows（账号 1：H.I.F）打一局。

## 第三方库

| 用途 | 库 | 何时引入 |
|---|---|---|
| SQLite（信息库） | `modernc.org/sqlite`（纯 Go，不要 cgo；初星攻略站也在用） | 阶段 1 |
| 钥匙串 / 凭据管理器 | `github.com/zalando/go-keyring`（条目格式和 Python 版一致） | 阶段 1 |
| Windows 系统调用（剪贴板、文件锁、窗口） | `golang.org/x/sys` | 阶段 1 |
| 测试 | 标准库 `testing` + `github.com/stretchr/testify`、`github.com/google/go-cmp` | 阶段 1 |
| LLM | `github.com/openai/openai-go/v3`（DeepSeek 等兼容接口）、`github.com/anthropics/anthropic-sdk-go` | 阶段 4 |
| 图像处理 | `gocv.io/x/gocv`（要 cgo + OpenCV） | 阶段 3 |
| 模型推理（OCR、ddddocr、UINet） | `github.com/yalue/onnxruntime_go`（运行时加载 onnxruntime 动态库） | 阶段 3 |
| 数值 | `gonum.org/v1/gonum` | 阶段 3 |
| 图像（纯 Go 部分） | `golang.org/x/image` | 阶段 3 |
| 黄金数据 | `github.com/sebdah/goldie/v2` | 阶段 3 |
| YAML（主数据构建） | `gopkg.in/yaml.v3` | 阶段 4 |
| DMM 备选 | `github.com/MaaXYZ/maa-framework-go/v3` | 需要时 |

原则：标准库能做的不引库（HTTP 服务、日志 `log/slog`、JSON、子进程、命令行参数）。

## 工程工具

本机已装（2026-11-20，`GOTOOLCHAIN=go1.27.4 go install <包>@latest`，装在 `~/go/bin`）。Go 版本跟着 `go.mod` 走（1.27.4：1.27.3 的 net/http 有 GO-2026-6859，govulncheck 查出来后升级）：

| 工具 | 管什么 | 包 |
|---|---|---|
| golangci-lint v2 | 主力 lint，一个配置管几十个检查器（`go/.golangci.yml`） | `github.com/golangci/golangci-lint/v2/cmd/golangci-lint` |
| staticcheck | 静态检查（golangci 里也带） | `honnef.co/go/tools/cmd/staticcheck` |
| gofumpt、goimports | 格式化、整理 import | `mvdan.cc/gofumpt`、`golang.org/x/tools/cmd/goimports` |
| nilaway | 空指针流分析（先当参考，不拦提交） | `go.uber.org/nilaway/cmd/nilaway` |
| go-arch-lint | 层与层的依赖规则（`go/.go-arch-lint.yml`） | `github.com/fe3dback/go-arch-lint` |
| govulncheck | 依赖漏洞 | `golang.org/x/vuln/cmd/govulncheck` |
| go-licenses | 依赖许可证（仓库是 AGPL） | `github.com/google/go-licenses/v2` |
| gotestsum | 测试输出 | `gotest.tools/gotestsum` |
| task | 任务入口（`go/Taskfile.yml`，Windows 也能用） | `github.com/go-task/task/v3/cmd/task` |
| lefthook | 提交前检查（仓库根 `lefthook.yml`） | `github.com/evilmartians/lefthook/v2`（v1 依赖的 go-json-experiment 和 Go 1.27 不兼容，编不过） |
| mockery | 按接口生成测试替身 | `github.com/vektra/mockery/v3` |
| goreleaser | 打包发布（阶段 6） | `brew install goreleaser`（`go install` 编不过，用官方编译好的） |
| deadcode | 找没人调用的代码（迁移后期） | `golang.org/x/tools/cmd/deadcode` |
| apidiff | 插件接口有没有破坏兼容 | `golang.org/x/exp/cmd/apidiff` |
| benchstat | 性能前后对比 | `golang.org/x/perf/cmd/benchstat` |

新机器一次装齐：`cd go && task tools`。

提交前钩子（`lefthook install`）**等这条分支合进主线再装**：钩子装在共享的 `.git/hooks` 里，会作用到同一仓库的所有 worktree，没有 `lefthook.yml` 的分支提交时会出错。在那之前手动跑 `task check`。

## 常用命令

```bash
cd go
task check      # 提交前：格式、lint、层规则、测试（-race）
task fmt        # gofumpt + goimports
task lint       # golangci-lint + go-arch-lint
task test       # gotestsum，带 -race
task parity     # Go / Python 对照测试（要本机 Python 环境）
task vuln       # govulncheck
task licenses   # 依赖许可证
task build      # 编出 bin/hiro
task nilaway    # 空指针分析（参考）
```

## 编码约定

- **输出和 Python 版逐字段一致**：信封 `{"ok": true, "data": …}` / `{"ok": false, "error": {"code", "message", "hint"}}`；退出码 0 成功、1 失败、2 用法错误、3 待确认。JSON 不转义 `<>&`、保留中文原文、字段顺序和 Python 一致（顺序对模型有用，例如 `counts` 在 `items` 前面），统一用 `internal/jsonx`。
- **禁止事项写在代码里**：命令名碰到 jewel / discard / retire / ジュエル / 破棄 / リタイア 不允许登记（和 Python 的 `FORBIDDEN_WORDS` 一样）；会动游戏的命令一律先出待确认；密钥值不进日志、不进事件、不进报错。
- **错误用白话**：`code` 给程序判断，`message` 说出了什么事，`hint` 说下一步做什么。
- **不猜**：读不到的字段是 `null`（Go 里用指针或 `omitempty` 以外的显式 null），不填默认值。
- **测试先行**：先写会失败的测试，再写实现；行为和 Python 不一样的地方先写对照测试。

## 进度

- [x] 2026-11-20 定方向、装工具、写这份 README
- [x] 阶段 0：工程底座（go.mod、Taskfile、golangci-lint、go-arch-lint、lefthook 配置、CI、jsonx）
- [x] 阶段 1：工具层（Mac）
  - [x] `tools` `confirm` `cancel` `pending list`
  - [x] `info summary / supports / memories / idols / affection / game-settings / import`
  - [x] `catalog sync / search / get / sprite`
  - [x] `run start / status / events / pause / takeover / resume / stop / report / review`（`review` 和考试分数经 pybridge）
  - [x] `secret set / list / copy / delete`（Mac 钥匙串、Windows 凭据管理器，和 Python 版条目互通）
  - [x] `doctor`（`--probe` 经 pybridge）
  - [x] `plan show`、`plan formation`（经 pybridge）
  - [x] 后台服务：同一把锁、同一个 `service.json`、同一套接口，Go / Python 互连
  - [x] 对照测试 5 组全过：`tools` 31 条命令逐字段相同；info / pending / plan / run / secret / doctor 输出一致
  - [x] 实测（11-20，Mac）：Go 版读开发用 HIRO_HOME 的信息库 / 资料库 / 方案正常；连上正在跑的 Python 服务；钥匙串写入 / 复制 / 删除正常；
        桌面程序用自带的 Go 版 `hiro`：信息页、编成方案页（出方案经 Python 2 秒）、开局页（搜偶像卡、准备开局、取消）都通
  - [ ] Windows 实机：安装包、凭据管理器、剪贴板、DMM 管理员权限
  - [ ] 实机对局：经 Go 服务起 Python 对局子进程打一局（要游戏）
- [x] 阶段 2：设备层（代码，11-20）
  - [x] `img`：BGR 图像、裁剪、缩放（和本机 cv2 对照：放大逐字节相同，缩小最多差 2）
  - [x] `device`：逻辑坐标换算（横屏 16:10 居中裁 16:9、Python 的五取偶舍入）、横竖屏切换时重截、按 HIRO_DEVICE 选设备
  - [x] MaaTools（Mac）：协议客户端、Catalyst 0.77、断线重连补发抬起、发送前探测对端关闭、空帧重试 / 黑帧 / 30 秒报断开；假服务端走真协议测
  - [x] adb：screencap 解析（12 / 16 字节头）、点击、滑动（hold 用 motionevent）、掉线 adb connect 重试一次、超时算断开
  - [x] DMM（Windows）：MaaFramework Win32 控制器（截图 FramePool、输入 SendMessageWithWindowPos）、失败重连一次、全黑截图报错；调窗口尺寸（`hiro __win-window`）
  - [x] `doctor --probe` 和 `fit-window` 任务改用 Go，不再要 Python
  - [ ] 实机：Mac PlayCover、Windows DMM（MaaFramework 动态库）、MuMu（放到全部迁完后统一验）
- [ ] 阶段 3：识别层
  - [x] `cv`：gocv 封装（颜色、缩放、读写、阈值、inRange、边框、模板匹配、形态学、Canny、轮廓、连通块、直方图）；和 cv2 4.11 对照，逐字节一样，模板匹配分数差 1e-4 以内
  - [x] `ocr`：Apple Vision（cgo），文字和框跟 Python 版一样；NFKC 规整、整帧文字排序、读数字
  - [x] 黄金数据导出（`tools/golden/export_vision.py`）
  - [x] 黄金数据比对框架（`internal/golden`、`tools/golden/probe.py`、`task golden`）
  - [x] `assets`（素材位置）、`master`（主数据：卡 / 饮料 / P 道具）
  - [x] `vision/ui` 界面模板：134 个模板 × 120 张，框和分数和 Python 一样；按钮亮灭、复选框 300 张一样
  - [x] 判页的模板 / 像素部分：template_scene、option_regions、at_commu、页面签名（带遮罩 NCC）、quick_page、fallback_page，1590 张全一样
  - [x] ddddocr（`ocr/dddd.go`、`pil`）：Pillow 缩放 / 转灰度逐字节一样；onnxruntime 同版本时输出逐位一样
  - [ ] 判页链剩下的：整帧 OCR 页面地图（pages.classify_page、tower 页面、特別指導）、classify_scene / classify
  - [x] 卡面图库 card_db（gocv SIFT + OpenCV kmeans 训练的 IVF，Go 多协程检索；从零建索引 14 秒）：400 张截图认卡和 Python（faiss）全一样；三选一 card_select
  - [x] 数字字体 digits：随仓库字体直接用 Pillow 渲染好的模板（data/glyph_templates.json，按字体 SHA-256 认），400 张 × 8 个 HUD 框只差 4 处（都在 ddddocr 兜底，onnxruntime 版本不同）
  - [x] layout（客户端布局 + 校准）、P 饮料图标 drinks、获得演出卡面核对
  - [ ] 卡图 card_art、手牌、状态栏、P 道具 / 饮料栏
  - [ ] 各页读数（hif 考试、deck、action、shop、event、support、nia、hajime、customize）→ extract / observe
  - [ ] RapidOCR（onnxruntime，Windows）
