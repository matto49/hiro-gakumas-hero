# 初星助手界面改造：对话为主、页面插件化、mygo 原生 ui

状态：定稿（2026-11-24）。前一版界面设计见 `2026-11-17-hiro-console-design.md` 和 `2026-11-20-console-ui-brief.md`，本稿替换其中的界面部分；工具层（`hiro` 命令行）、Go 后端的调用方式不变。

标注说明：**用户定** = 用户在设计文档或画板上选过、或明确说过；**我的决定** = 设计时替用户定的默认，实现时可以按理由调整，改了在 PR 里说。

## 1. 为什么改

用户原话的三个痛点（用户定）：

1. 界面丑（驾驶舱、对话里的卡片）；对话里偶像卡的信息卡还渲染错了（套了支援卡模板，显示「?」「SR Lv?・突破 ?」）。
2. 交互差：开一局要点很多下。
3. agent 知道的上下文不够：说「再来一把」「刚才那局怎么样」时它不知道刚才是什么。

改完以后算失败的样子（用户定）：开一局仍要点很多下；看不懂打到哪了、为什么这么打；看起来像随便哪家的 AI 聊天软件。

## 2. 目标、非目标、反目标

目标：

- 对话是主界面。一句话「帮我用月村手毬打一把初」就能开局，中间只点一次「开始」（用户定）。
- 现有的页面（开局、编成方案、资料库、驾驶舱（改名游戏运行现状）、设置）都改成插件，程序自带；内置插件和外部插件用同一套接口（用户定）。
- 界面从 React 网页换成 mygo 原生 ui（`github.com/egoist/mygo/ui`，GPU 自己画，不起 WebView），代码只写一遍（用户定，11-24）。
- agent 每一轮都拿到够用的上下文（§7）。

非目标：

- 不改工具层命令和它的 JSON 信封；不改 `Hiro.Call`、`Bootstrap`、`Assistant` / `Agent`、fastpath 的对外行为（只加 §7 的上下文和 §6.4 的进度回调）。
- 不做暗色主题（沿用现有 DESIGN.md 的「不做暗色」）。
- 不做插件市场、在线下载、自动更新插件。

反目标（做了就是错）：

- 程序或 agent 替用户确认会动游戏的操作；任何「始终允许」。
- 花ジュエル、破棄 / リタイア、读种子或存档隐藏信息——这些能力不开放给任何插件，包括内置插件。
- 外部插件在初星助手进程里跑代码（DSH 那种进程内无沙箱的做法，用户定不要）。

## 3. 约束

- mygo `v0.10.0`，ui 包是 0.x，小版本之间 API 会变。升级 mygo 单独开 PR。Go ≥ 1.27.3（`app/go.mod` 已是 1.27.4）。
- 文档在模块缓存 `github.com/egoist/mygo@v0.10.0/docs/ui/`，示例在 `examples/`（`markdown-chat`、`gallery`、`embed-webview`、`launcher` 最有用）。
- 平台：macOS（Metal）和 Windows（Direct3D 11）都要能跑；Windows 安装包在 Ubuntu 上交叉编译（`.github/workflows/desktop.yml`）。
- 公开仓库（AGPL）：不提交截图、trace、游戏数据、私有路径、密钥。界面预览图只在本机生成（§10）。
- 数据目录 `HIRO_HOME`、应用标识 `top.matto.hiro-console`、对话文件 `threads/<id>.json` 的格式不变，老用户升级后对话和设置都在。

## 4. 视觉方向（用户定：画板第 3 轮按 B「初星舞台」推进，11-24）

一句话：暖奶油底 + 学マス橙的主按钮，偶像卡面和支援卡小图是界面上的主角；确认卡出在对话里，头部放这局的偶像卡面。看起来是「学マス的助手」，不是随便哪家的 AI 聊天软件。

（先前转达过选 A「安静工具」，用户 11-24 原话改成 B，以 B 为准。）

颜色（我的决定，按画板 B 的实际取值）：

| 用途 | 值 |
|---|---|
| 主区底 | `#FFFBF6`（奶油白） |
| 侧栏、侧边面板底 | `#FBF0E3` |
| 卡片底 | `#FFFFFF` |
| 次级底（代码块、选中行） | `#FFF4E8` |
| 线 / 强线 | `#EFDFCB` / `#E2C9AA`，宽 0.5 px；确认卡描边 1 px 橙 |
| 字：正文 / 说明 / 次要 | `#2A1C10` / `#6E5846` / `#A08B78` |
| 主按钮、品牌橙（运行中、「现在」、算法标签） | `#EE6A1F` 底白字；浅底 `#FFE8D6`（也是用户气泡底） |
| 确认（等你确认） | 描边 `#EE6A1F`，浅底 `#FFF1E6`，字 `#9A3F0B`；卡头渐变 `#FFE3CC → #FFF6EE` |
| 成功点 | `#2E9E5B` |
| 属性 | 声乐 `#F0508C`、舞蹈 `#3C82F0`、视觉 `#E8B400`（视觉的黄只做色点，不当字色） |
| 谁定的 | 规则：灰字灰底；算法：橙字橙浅底；LLM：蓝字 `#3C82F0` 配 12% 蓝底；兜底：红 |

形状：圆角 8（行、小按钮）/ 12（卡片）/ 16（大卡）/ 20（输入框、确认卡）；阴影只给输入框、确认卡、对局卡，暖色淡阴影。

字：系统字（mygo 默认回落，中日文在画板渲染里都正常）。命令行一律等宽：`Font("monospace")`（系统等宽字体；我的决定）。正文 14，说明 12–13，标签 10.5–11.5；确认卡标题 17 粗。

整屏（1280×800 基准，最小 960×640）：

- 左侧栏 232 px：标志「初星助手」→「新对话 ⌘N」→「对话」列表 →「插件」列表（开局、编成方案、资料库、游戏运行现状、管理插件；有局在跑时「游戏运行现状」后面一个橙点「运行中」）→ 底部两行：游戏连接（绿点「学マス · 已连接」，点了试连）和模型（点了进设置）。
- 中间对话：顶栏 48 px 写对话标题；消息区；底部输入框，上面一排快捷按钮（`/开局` `/编成` `/扫描` `/资料库`）。
- 右侧边面板：默认关；打开时初始宽 45%，可拖到 70%，中间对话至少留 400 px（我的决定）。`⌘\` 开关。

签名细节（用户定，来自画板 B）：

- **确认卡在对话里**：最大宽 560、圆角 20、1 px 橙描边、暖色阴影。卡头渐变底，左边 72 px 偶像卡面，右边标题「开一局 初」、副标题「月村手毬【Luna say maybe】· 普通」、属性标签和「等你确认」小标签。卡身「编成」一行 + 支援卡小图（34 px）。卡脚左边写代价（「会操作游戏 · 用掉 1 次培育」），右边「先不 Esc」「开始 ↵」（橙色主按钮）。输入框照常可用，占位字「回复，或者直接点上面的『开始』」。
- **对局卡钉在对话顶上**：9:16 小缩略图 + 「初 · 月村手毬」+「第 7 周 / 16 · 48 步 · 12:40」+「刚才：…」+ 暂停 / 停止；点卡片打开右侧「游戏运行现状」。
- **每一步流式出现在对话里**（用户定，11-24）：对局开始后，对话里那张对局卡下面一条条长出每一步：时间（第一条写「现在」、橙浅底）、页面名、谁定的标签、做了什么和理由。像流式输出一样往下长，对话跟着滚到底（用户往上翻时不抢）。
- **游戏运行现状**（原「驾驶舱」，用户定改名）：右侧面板只放实时画面（9:16）、暂停 / 停止、几行数（第几周、步数、已打）、结束后成绩条和复盘按钮；**不放步骤列表**。

画板上 B 的两张图是验收的参照（§12）；步骤列表从右边挪进对话这一点以本节文字为准。

## 5. 插件模型

### 5.1 一个插件能给什么（用户定：全部都能给）

| 贡献 | 说明 |
|---|---|
| 工具 | 给 agent 用的工具（内置插件认领工具层 `hiro tools` 里的命令；外部插件自己声明） |
| `/` 命令 | 输入框里打 `/` 出的命令，例如 `/开局` |
| 快捷按钮 | 输入框上面那一排 |
| 对话卡片 | 某种卡片类型由哪个插件画，例如 `info`（偶像卡、支援卡列表）、`formation`、`run` |
| 侧边面板页面 | 插件的完整页面，在右侧边面板打开（用户定） |
| 设置项 | 设置页里的一节 |
| agent 上下文 | 每轮重算的一段「现在的情况」（§7） |

入口（用户定）：`/` 命令 + 快捷按钮 + 侧栏插件列表。主界面只有对话，没有常驻状态栏（用户定）。

### 5.2 Go 接口（我的决定，内置插件按它写，外部插件由宿主包一层成同样的形状）

```go
// app/plugin/plugin.go
type Plugin struct {
    ID, Name string
    Icon     *ui.SVG
    Tools    []string                  // 认领的工具层命令名（ToolSpec.Name，例如 "run start"、"info idols"）
    Commands []Command                 // "/" 命令
    Chips    []Chip                    // 快捷按钮
    Cards    map[string]CardView       // 卡片类型 → 怎么画
    Panel    func(c *ui.Context, h Host, route string) // 侧边面板页；route 是插件自己的子页（"" 为首页）
    Settings func(c *ui.Context, h Host)
    Context  func(ctx context.Context, h Host) string  // agent 上下文，返回一段白话；空串表示没东西
}
```

`Host` 是插件能碰的全部宿主能力：`Call(args)`（走 `Hiro.Call`）、`Ask(text)`（以用户身份发一句话）、`OpenPanel(pluginID, route)`、`Post(card)`（往当前对话加一张卡）、`Update(func())`（回到界面线程改状态）、`Now()`（测试可换）。插件拿不到 `Hiro` 本身，拿不到 `Bootstrap`、Key 存储。

没被任何插件认领的工具层命令：照旧给 agent（保持现有行为），归到「其它」。

### 5.3 内置插件清单（我的决定）

| 插件 | 认领的工具 | `/` 命令 / 快捷按钮 | 卡片 | 面板 | 上下文 |
|---|---|---|---|---|---|
| 开局 `start` | `run start`、`run status` | `/开局`（打开开局面板，等于现在的开局页）、快捷「接着打进行中的培育」「领日常」 | `confirm` 的模式 / 偶像 / 编成解读 | 开局表单（模式、难度、偶像、编成） | 最近开过的几局（模式、偶像、结果） |
| 游戏运行现状 `cockpit` | `run events`、`run review` | — | `run`（对局卡 + 流式步骤） | 游戏运行现状；复盘页用网页视图（§5.6） | 正在跑的任务 + 最近 5 步；最近 3 局成绩 |
| 编成方案 `plan` | `plan formation`、`plan show`、`write-formation`、`scan-formations` | `/编成` | `formation` | 编成方案页 | 当前编成方案摘要 |
| 资料库 `library` | `info *`、`catalog *` | `/资料库`、`/扫描`（扫描支援卡 / 回忆 / 首页数值） | `info`（按 kind 分模板，**偶像卡用偶像模板**，修掉现在的错） | 支援卡、回忆、偶像卡、资料库搜索 | 账号数值（PLv / AP）、各库存扫到哪天 |
| 设置 `settings` | `doctor`、`secret list`、`secret set` 不给 agent | — | `error` 的「去设置」按钮 | 模型、账号、环境、插件管理 | 游戏连没连上（用最近一次 `doctor --probe` 的缓存，不每轮试连） |

「常用的偶像和模式」由开局插件从 `run status` 的最近任务里统计（我的决定）。

### 5.4 外部插件（用户定：现在就支持，清单 + 子进程 + JSON 描述界面）

安装位置 `HIRO_HOME/plugins/<id>/`，里面一个 `plugin.json`：

```json
{
  "id": "my-plugin",
  "name": "示例插件",
  "version": "0.1.0",
  "icon": "icon.svg",
  "command": ["python3", "main.py"],
  "acts": ["run start daily"],
  "contributes": {
    "tools": [{ "name": "my_lookup", "help": "…", "args": [{ "name": "query", "type": "string", "required": true }] }],
    "commands": [{ "name": "示例", "hint": "…", "panel": "" }],
    "chips": [{ "label": "…", "say": "…" }],
    "cards": ["my-card"],
    "panels": [{ "route": "", "title": "示例", "kind": "native" }, { "route": "report", "title": "报告", "kind": "web", "file": "report.html" }],
    "settings": true,
    "context": true
  }
}
```

协议（我的决定）：宿主按需启动 `command`（工作目录是插件目录，环境只带 `HIRO_HOME` 和 `HIRO_PLUGIN_ID`，不带模型 Key），stdin / stdout 一行一条 JSON（JSON-RPC 2.0）。插件 60 秒没请求就关掉，下次再起。

| 方向 | 方法 | 用途 |
|---|---|---|
| 宿主 → 插件 | `tool.call {name, args}` | agent 调了插件的工具；返回和工具层一样的信封 `{ok, data, error}` |
| 宿主 → 插件 | `panel.render {route, state}` | 要画面板；返回组件树 |
| 宿主 → 插件 | `card.render {type, data}` | 要画对话里的卡片；返回组件树 |
| 宿主 → 插件 | `event {route, id, value, state}` | 用户点了按钮 / 改了输入；返回新的组件树和新 `state` |
| 宿主 → 插件 | `context.get {}` | 每轮的上下文，返回字符串 |
| 插件 → 宿主 | `hiro.call {args}` | 调工具层；只读命令直接放行，会动游戏的命令必须在 `acts` 里，且照样返回待确认、照样出确认卡 |

组件树第一版的件（我的决定，够内置插件那几种页面用）：`column` `row` `text` `heading` `tag` `button` `card` `list` `table` `image`（只认插件目录或 `HIRO_HOME` 下的图）`divider` `progress` `input` `select`。树里的字符串一律当纯文字画，不解析 Markdown 链接、不执行任何东西。

把关（用户定）：

- 安装时弹一张清单卡：插件要哪些 `acts`、要不要给 agent 工具、要不要读上下文；用户点同意才装上。`acts` 变了（升级）要重新同意。
- 每次真动游戏仍出确认卡，没有「始终允许」。确认卡标题写来自哪个插件。
- `acts` 里写不进去的命令：`confirm`、`secret *`（只能由界面发起），以及工具层本来就不提供的ジュエル、破棄之类。
- 安装来源只有「从本机文件夹安装」（我的决定）。
- `acts` 怎么匹配（我的决定）：每条是命令的前几个词（按空格切），`hiro.call` 的参数开头几个词和某条完全相同就算在内，后面的参数随意。例如 `run start daily` 允许 `run start daily --items x`，不允许 `run start hajime`。宿主记下「这个待确认 id 是哪个插件发起的」，确认卡标题写插件名。
- 用 claude / codex 命令行当 agent（CLIAgent）时，外部插件的工具不给它（我的决定）：CLIAgent 是自己调 `hiro` 命令行的，绕不过宿主的 `acts` 校验；它照旧只能通过 `HIRO_ACTION` 行发起 `run start`。

组件树的往返（我的决定）：

- 宿主异步调插件，界面一直画手上最后一棵树；请求在路上时，触发它的按钮显示忙碌、不能再点。第一次还没有树时显示加载占位。
- `input` 在回车或失焦时发 `event`，`select`、`button` 点了就发。不按键发。
- `state` 由宿主保存（插件每次返回新的 `state`，宿主下次原样带回），所以插件进程被回收后再起不丢状态。
- 对话里的插件卡片：发出时 `card.render` 一次，之后只在这张卡上的 `event` 后更新。

### 5.5 内置插件也走同一个接口

内置插件是 Go 写的 `Plugin`，直接画原生 ui；外部插件由 `extplugin` 包装成 `Plugin`（Panel / Cards 调 `panel.render` / `card.render` 后把组件树画出来）。宿主、`/` 命令、确认卡、上下文汇总都只认 `Plugin`，不区分内外（用户定）。

### 5.6 很重的页面用网页视图

复盘页（`run review` 生成的 HTML）和外部插件 `kind: "web"` 的面板，在侧边面板里嵌一块网页视图（`ui.WebView`，参考 `docs/ui/web-view.md`、`examples/embed-webview`）。我的决定：

- 只加载本机文件（`HIRO_HOME` 或插件目录），不给任何绑定。
- 外部插件的网页一律按不可信页面开（`PageOptions.Untrusted`），并加 CSP `default-src 'self'; connect-src 'none'`，不能联网。网页内容跑在系统网页视图自己的进程里，不算在初星助手进程里跑插件代码。复盘页是我们自己生成的，同样不给绑定，但不加 `connect-src 'none'`（它不需要，也不妨碍）。
- Windows 上照旧依赖 WebView2 运行时：旧界面整个就是 WebView2，现有安装包已经处理了这件事，这次不变；所以构建不加 `mygo_nowebview`。

## 6. 对话

### 6.1 一句话开局（用户定）

「帮我用月村手毬打一把初」→ agent 按系统提示词里的固定流程（PR #47 已加）：`info_idols` 找卡（错字容忍，多张 SSR 时先问）→ 定模式和默认难度（初 = hajime regular，N.I.A = nia pro，H.I.F = hif 选拔）→ `run_start` 只带 kind 和 `--idol` → 停。对话里出偶像卡（资料库插件画）和确认卡。

### 6.2 确认卡（用户定，画板 B）

- 待确认来自 `Reply.Cards` 里 `type: "confirm"`、`status: "waiting"` 的卡，就画在对话里那条回复下面（§4 的样子）。
- 「开始 ↵」= `hiro confirm <id>`；「先不 Esc」= `hiro cancel <id>`。按键（我的决定）：有卡在等、输入框是空的时候，Enter = 开始、Esc = 先不；输入框里有字时 Enter 照常发消息。同时有几张在等时，按键只作用于最新那张。
- 点过之后这张卡缩成一行结果（「已开始 · 你点了『开始』」/「已取消」/「没能开始：<原因>」），按钮不再出现，重复点不会再确认。
- 待确认过期（`expires_at`）：卡变成「已过期，再说一次就行」。
- 解读（模式 / 偶像 / 编成、卡面图）由开局插件从 `pending.command` 解析：kind → 模式中文名和难度，`--idol` → 资料库查中文名和卡面，编成写「沿用编成方案」并列出支援卡小图。解析不了就只显示 `summary` 和命令（我的决定）。
- 确认只能在界面上点；agent、外部插件都没有确认的路径（反目标）。

### 6.3 对局卡、流式步骤和游戏运行现状（用户定）

- 有活着的任务（`run status` 的 `current` 在 running / paused / takeover / stopping）时，对局卡钉在**每个**对话的顶上（任务全局只有一个，我的决定）。
- 数据：每秒 `run events --job <id> --since <seq>` + `run status`，任务结束停（照搬现在 `useJob` 的做法）；窗口隐藏时降到 5 秒（我的决定）。
- 每一步（`type: "step"` 的事件）流式显示在对话里发起这局的那张对局卡下面（§4）；那张卡不在当前对话里时（比如在别的对话里开的），钉在顶上的对局卡点开「展开步骤」也能看到（我的决定）。
- 点钉住的对局卡打开右侧「游戏运行现状」；对局开始时自动打开一次（我的决定；用户关了就不再自动开）。
- 有要用户管的事（考试结束、待接管、出错）在对话里冒一条消息（我的决定）。

### 6.4 消息怎么显示

- 用户的话：右对齐浅灰气泡。agent 的话：不加气泡，Markdown 显示（`ui/markdown`）。
- 工具调用：一行灰字「✓ info_idols 找到 1 张 SSR 月村手毬」。`Agent.Ask` 加一个进度回调 `OnTool(name, summary)`，模型还在想的时候就把这行显示出来（我的决定）；CLIAgent（claude / codex）没有进度，只在最后显示。
- 卡片：按 `type` 交给认领它的插件画；没人认领的卡显示成通用的「键：值」卡（我的决定）。

### 6.5 对话存储

格式不变：`threads/<id>.json` 是 `{id, title, messages: [{id, role, text?, cards, at}], updatedAt}`，卡片类型 `confirm` / `run` / `info` / `error` / `formation`。新程序直接读老对话。新卡片类型（外部插件的）存成 `{type: "plugin", plugin, card, data}`（我的决定）。

### 6.6 没配模型时

照现在的「快捷指令」（`lib/quick.ts` 的 QUICK 列表）：只能点快捷按钮和 `/` 命令；输入框提示「先到设置里配一个模型，才能直接说话」。

## 7. agent 每轮的上下文（用户定：全部）

每轮调模型前，宿主并行跑每个插件的 `Context`（每个最多 2 秒、最多 1500 字，超了截断），拼成一段放在系统提示词后面：

```
## 现在的情况（<时间>）
### 账号
PLv 52 · AP 120/150；支援卡 11-20 扫过、回忆 11-18 扫过、偶像卡 11-22 扫过
### 编成方案
…
### 游戏
学マス已连接（11-24 01:30 试连）
### 正在跑
初 · 月村手毬 · 第 7 周 · 最近 5 步：…
### 最近几局
…
### 常用
偶像：月村手毬（5 局）、篠澤広（3 局）；模式：初（6 局）
```

只读缓存或便宜的命令（`info summary`、`plan show`、`run status`、`run events --since`），不在这里扫描、不试连。外部插件的上下文只有用户安装时同意了才加。CLIAgent 走它现有的 `status()`，换成同一段文字（我的决定）。

## 8. 首次运行和设置

- 环境没装好（`Bootstrap.Status().Ready == false`）或 `WizardDone == false`：整窗显示设置插件的「第一次使用」流程（装环境 → 选模型、填 Key → 填游戏账号 → 试连游戏），和现在 Wizard 的步骤一样，只是改原生画（我的决定）。
- 账号密码只经 `Hiro.SetSecret`（stdin）；输入框是密码样式，不回显、不进对话、不进日志。
- 设置面板：模型、账号、环境（重装）、插件管理（已装插件、同意过的 `acts`、卸载、从文件夹安装）。

## 9. 代码结构（我的决定）

```
app/
  main.go            窗口：Content: ui.View(shell.View)；不再起 WebView 页面，homeFiles 协议只给网页视图面板用
  hiro.go … fastpath.go filekeys.go bootstrap.go   不动（agent.go 加 OnTool、上下文参数）
  ui/                设计 token（§4）和公用件：Button、Tag、Thumb（卡面小图，走 catalog sprite 缓存）、StatePill、StepRow、SourceTag、KV
  shell/             侧栏、对话、输入框、确认卡接管、侧边面板（可拖宽）、"/" 命令面板、对局卡置顶
  plugin/            Plugin / Host / Command / Chip / CardView 类型，注册表，上下文汇总
  plugins/start cockpit plan library settings   内置插件
  extplugin/         清单读取、子进程 JSON-RPC、组件树 → 原生 ui、安装审批
  labels/            页面名、动作名、任务种类、属性的中文（从 src/lib/labels.ts 搬过来）
  locales/           不变
  mygo.json          取代 mygo.config.ts（name、identifier、version、i18n）
```

删掉：`app/src/`、`index.html`、`package.json`、`bun.lockb`、`bunfig.toml`、`tsconfig.json`、`vite*.ts`、`preview/`、`scripts/mygo.mjs`、`scripts/preview-snap.py`。`scripts/fetch-uv.mjs` 改成 Go（`go run ./scripts/fetchuv`），CI 不再装 Bun（我的决定）。`desktop.yml` 改成 `go tool mygo build -platform …`。

`app/DESIGN.md` 按 §4 重写；`PRODUCT.md` 只改「界面是网页」之类的描述。

## 10. 测试和预览

- 每个插件、shell 的主要流程用 `ui.NewTester` 写测试，工具层用假的 `Call`（按参数返回预置信封）。必测流程：一句话开局（假 agent 回偶像卡 + 待确认）→ 对话里出确认卡（带卡面）→ 点「开始」或空输入框按 Enter 调 `confirm` → 对局卡置顶、步骤流式出现在对话里 → 点开游戏运行现状；「先不」或 Esc 调 `cancel`；输入框有字时 Enter 只发消息不确认；待确认过期；偶像卡信息卡用偶像模板（回归）；没配模型只出快捷按钮；外部插件 `acts` 外的命令被拒、`acts` 内的命令仍出确认卡。
- 预览图：`HIRO_PREVIEW_OUT=<目录> go test ./... -run Preview` 用假数据离屏渲染 1280×800 @2x 的 PNG（对话、确认、对局中、各插件面板、设置、第一次使用），不弹窗不抢焦点。预览图不进仓库。
- 现有 Go 测试（agent、assistant、bootstrap、fastpath、files、filekeys、hiro）全部保留并通过。

## 11. 顺序（我的决定）

1. 骨架：`ui/`、`shell/`、`plugin/`，对话能读老对话、能和 agent 说话、确认卡接管、侧边面板。
2. 内置插件：开局 → 游戏运行现状 → 资料库 → 编成方案 → 设置（含第一次使用）。
3. agent：上下文汇总、进度回调。
4. 外部插件：清单、子进程、组件树、安装审批、网页视图面板。
5. 删 React、改打包和 CI、重写 DESIGN.md。

每步都能编译运行；1–3 完成就能在本机替换掉旧界面试用，4–5 完成再合 main。按步提交，整个改造一个 PR（或 1–3、4–5 两个 PR）。

## 12. 验收

- §10 的测试全过；`go vet`、现有 CI 全绿。
- 预览图和画板 B 的两张图（等你确认、对局进行中）对得上（画板图在本机 `.ryugi/artifacts/artboard/hiro-console-chat-first/pages/directions/b-*.png`；步骤挪进对话、改名这两处以 §4 文字为准）：布局、颜色、确认卡样式、对局卡、驾驶舱。
- 本机真程序：一句话开一局初，从说话到对局开始只点一次「开始」；对局中对局卡一直在顶上，每一步（做了什么、谁定的）流式出现在对话里，右侧「游戏运行现状」看得到实时画面。
- Windows 安装包能装、能开、中日文显示正常（CI 只能验证能编出来；真机打开要在 Windows 上看一次）。

## 13. 留给实现者

- 各件的具体间距、动效（≤150 ms 的颜色过渡、运行中点的脉冲）、空状态文案。
- 组件树 JSON 的字段细节（在 §5.4 的件范围内）。
- 侧边面板拖宽的手柄样式、窗口变窄时的退化（对话 < 400 px 时面板改成盖在对话上）。
- 资料库各页的筛选和排序控件。

## 14. 用到的 artifact

本机 Ryugi artifact（`.ryugi/artifacts/` 只有文档进 git，画板不进）：

- 文档 `document/hiro-console-chat-first`（`.ryugi/artifacts/document/hiro-console-chat-first/doc.xml`）：第 1、2 轮问答。定下的事都已写进本稿：对话为主、页面插件化且自带；一句话开局 + 对话里确认；确认参考 DSH；外部插件现在就支持；插件页在右侧边面板打开；入口是 `/` 命令、快捷按钮、侧栏；主界面只有对话；原生 ui；外部插件 = 清单 + 子进程 + JSON 描述界面、重页面用网页视图；插件能贡献全部七类东西；`acts` 安装时同意、每次仍确认、没有始终允许；agent 上下文要全部；对局卡置顶、点开驾驶舱；失败标准见 §1。
- 画板 `artboard/hiro-console-chat-first`，页 `directions`：组 `dir`（三选一）最终按 `dir-b`「初星舞台」推进（用户 11-24 原话；之前转达的 A 作废）；组 `dir-run` 是同一方向「对局进行中」的参考图。用户另外定了：「驾驶舱」改名「游戏运行现状」，每一步放进主对话流式显示。图是 mygo ui 离屏渲染的（生成代码在本机 `.temp/console-ui-20261123/native-mock/`，不进仓库）。§4 记下了 B 的全部取值。
- DSH（DeepSeek Harness）调研笔记在本机 `.temp/console-ui-20261123/dsh-research.md`（不进仓库）；用到的结论：确认卡接管输入框、Enter / Esc、只能在界面上批准、插件能注册工具 / 命令 / 卡片 / 右侧面板 / 设置 / 上下文、右侧面板 45% 起 70% 封顶、对话至少 400 px、圆角 4/8/12/16/20、0.5 px 线、近黑主色。
