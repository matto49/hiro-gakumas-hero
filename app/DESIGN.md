# Design

初星助手的界面规范。产品定位和原则见 [PRODUCT.md](PRODUCT.md)，整体设计稿见 `docs/ryugi/specs/2026-11-24-hiro-console-native-chat-first-design.md`。这里只记「长什么样、用哪个件」。改界面前先看这份，新页面照着搭，不另起一套。

界面是 mygo 原生 ui（`github.com/egoist/mygo/ui`，GPU 自己画，没有网页、没有前端构建）。版本钉在 `go.mod`，ui 包是 0.x，升级单独开 PR。文档在模块缓存的 `docs/ui/`。

## 方向：B「初星舞台」

暖奶油底 + 学マス橙的主按钮，偶像卡面和支援卡小图是界面上的主角。对话是主界面，页面都是插件，在右侧边面板打开。看起来要像「学マス的助手」，不像随便哪家的 AI 聊天软件。

## 颜色（`kit/tokens.go`）

| 用途 | 名字 | 值 |
|---|---|---|
| 主区底 / 卡片底 | `kit.Bg` / `kit.CardBg` | `#FFFBF6` / `#FFFFFF` |
| 侧栏、侧边面板 | `kit.Side` | `#FBF0E3` |
| 次级底（代码、选中行） | `kit.Surface2` | `#FFF4E8` |
| 线 / 强线 | `kit.Line` / `kit.LineStr` | `#EFDFCB` / `#E2C9AA`，宽 `kit.Hair`（0.5） |
| 字：正文 / 说明 / 次要 | `kit.Ink` / `kit.Ink2` / `kit.Ink3` | `#2A1C10` / `#6E5846` / `#A08B78` |
| 主按钮、运行中、「现在」、算法 | `kit.Brand`，浅底 `kit.BrandT`（也是用户气泡） | `#EE6A1F` / `#FFE8D6` |
| 等你确认 | `kit.Warn` 描边、`kit.WarnT` 底、`kit.WarnInk` 字 | `#EE6A1F` / `#FFF1E6` / `#9A3F0B` |
| 状态 | `kit.Good` / `kit.Bad`（浅底 `kit.BadT`） | 颜色永远配文字 |
| 属性 | `kit.Vo` `kit.Da` `kit.Vi`，`kit.AttrColor(attr)` | 视觉的黄只做色点，不当字色 |

`kit.Theme` 把 mygo 控件的默认色换成这套。不做暗色。

## 字和形状

- 系统字；中日文都靠系统回落，不用另配字体。命令行一律 `Font(kit.Mono)`（系统等宽）。
- 正文 14（`kit.FBody`），说明 12–13，标签 10.5–11.5；页面标题 15–21 粗。
- 圆角 8（行、小按钮）/ 12（卡片）/ 16（大卡）/ 20（输入框、确认卡）。阴影只给输入框、确认卡、对局卡，暖色淡阴影。

## 布局（1280×800 基准，最小 960×640）

- 左侧栏 232：标志 → 新对话 ⌘N → 对话列表 → 插件列表（各插件的 `Nav`）→ 底部各插件的 `Footer`（游戏连接、模型）。
- 中间对话：标题栏 48 → 钉顶对局卡（有活任务时）→ 消息 → 快捷按钮 → 输入框（Enter 发送，Shift+Enter 换行，「/」出命令列表）。
- 右侧边面板：`ui.SplitPercent`，初始 45%，最多 70%，对话至少 400 px；⌘\ 开关。面板里画插件的 `Panel(route)`。

## 件（`kit/`）

| 件 | 用在哪 |
|---|---|
| `Primary` / `Secondary` / `Ghost` | 主按钮（橙，每屏一个）/ 描边按钮 / 文字链 |
| `RowButton` | 侧栏、命令列表这种整行可点的项（选中 / 悬停浅底） |
| `IconButton` | 暂停、停止、关面板（一定要给 `Label`） |
| `Tag` / `SourceTag` / `AttrTag` / `StatePill` | 小标签 / 谁定的（规则灰、算法橙、LLM 蓝、兜底红）/ 属性 / 任务状态 |
| `KV` / `Code` / `Card` / `Section` / `Note` | 键值行 / 等宽命令 / 白底细线卡 / 面板小节标题 / 灰色说明 |
| `Thumb` / `Bitmap` | 卡面小图（取不到时浅色占位，不留空白）/ 读本机图 |

插件共用的资料库名字和小图缓存在 `plugins/common`（`common.Shared.Get` / `Sprite`，在后台查，查到后界面重画）。

## 签名细节

- **确认卡在对话里**：最大宽 560、圆角 20、1 px 橙描边；卡头渐变底 + 72 px 偶像卡面 + 「开一局 初」+ 副标题 + 属性和「等你确认」；卡身几行键值 + 支援卡小图；卡脚代价 +「先不 Esc」「开始 ↵」。输入框空时 Enter / Esc 作用于最新那张；点过缩成一行结果。解读由插件的 `Explain` 给，外部插件发起的写「来自插件『X』」。
- **每一步流式进对话**：对局卡下面一条条往下长，最新一条写「现在」、橙浅底，后面跟谁定的标签。超过 30 步折叠。
- **游戏运行现状**（原驾驶舱）：右侧只放 9:16 实时画面、暂停 / 接手 / 继续 / 停止、几行数、成绩条和复盘；不放步骤列表。
- **页面名、动作名**一律经 `labels`（`labels.Page` / `labels.Action` / `labels.Kind`）转中文；卡名中文优先，日文原名跟在后面。
- **偶像卡和支援卡各有模板**：偶像卡写偶像名 + 【称号】+ 稀有度 + 培育计划，不写「Lv / 突破」。

## 插件

内置插件在 `plugins/`（开局 `start`、编成方案 `plan`、资料库 `library`、游戏运行现状 `cockpit`、设置 `settings`），外部插件由 `extplugin` 包成同样的 `plugin.Plugin`。新页面就是给某个插件加 `Panel` 的一个 route；对话里的新卡片就是加 `Cards` 里的一种类型。会动游戏的操作一律 `h.RunArgs(…)` 发起，拿到待确认后由宿主出确认卡；插件自己不能确认。

## 动效

只有颜色的短过渡和运行中的状态点。尊重系统的「减少动态效果」。

## 验收

- `go test ./...`：每个插件、主界面的流程都用 `ui.NewTester` 测（假的工具层、同步的 Runner）。
- 预览图：`HIRO_PREVIEW_OUT=<目录> [HIRO_PREVIEW_ASSETS=<卡面图目录>] go test -run TestPreview .`，离屏画「等你确认」「对局进行中」和各插件面板，1280×800 @2x。图不进仓库。
- 真程序：`go run .`（或 `go tool mygo dev`），带 `HIRO_HOME` / `HIRO_CLI` 指向测试环境；用 `screencapture -l <窗口号>` 截图。学マス在跑时别切前台。
