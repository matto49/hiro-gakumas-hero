# Design

初星助手的界面规范。产品定位和原则见 [PRODUCT.md](PRODUCT.md)；这里只记「长什么样、用哪个件」。改界面前先看这份，新页面照着搭，不另起一套。

## 方向

和初星攻略站同一个牌子，更像桌面程序：白底、中性灰的字和线，学マス橙只给主按钮、链接、选中和「现在」。紧凑，一屏能看完。不做暗色、不做卡片墙、不做聊天气泡。

## 颜色（`src/style.css`，OKLCH）

| 用途 | token | 说明 |
|---|---|---|
| 底 | `surface` / `surface-2` / `surface-3` | 白 / 侧栏和代码块 / 分段控件和占位 |
| 字 | `ink` / `ink-2` / `ink-3` | 正文 / 说明 / 次要（时间、计数） |
| 线 | `line` / `line-strong` | 分隔线、卡片边 / 输入框边 |
| 品牌 | `brand`、`brand-deep`、`brand-ink`、`brand-tint`、`brand-soft` | 主按钮、链接（`brand-deep`）、文字（`brand-ink`）、选中底（`brand-tint`）、焦点环（`brand-soft`） |
| 属性 | `vo` `da` `vi`（填充）、`*-t`（浅底）、`vo-strong` `da-strong` `vi-ink`（文字） | Vi 的黄在白底上当字不够对比度，文字一律用 `vi-ink` |
| 状态 | `good` / `warn` / `critical`，浅底 `ok-t` / `warn-t` / `err-t` | 颜色永远配文字（「已保存」「读不到」「出错停下」） |

旧名 `bg` `panel` `chip` `acc` `ok` `err` 还在（老代码兼容），新代码用上表的名字。

## 字

- 正文 `font-sans`（系统字 + 苹方 / 雅黑），13 px 起；说明 12–12.5 px；最小 10.5 px（标签）。
- 日文卡名、技能名、效果文字加 `.jp`（Hiragino Sans / Noto Sans JP），不然汉字会按中文字形显示。
- 分数、PLv / AP / ジュエル 等游戏数字用 `.disp`（Barlow Condensed 斜体），千分位 `toLocaleString("en-US")`。
- 页面标题 21 px 粗体，前面一道斜的品牌色短杠（`PageTitle`）。

## 布局

三栏：左侧栏 280 px（开局、编成方案、信息、对话、账号数值、模型）｜中间主区｜右栏（有任务时是当前任务，否则是账号信息）。

- 主区页面：`px-8 py-6`，内容最宽 640–820 px，`PageTitle` 在顶上，副标题写「数据是哪次扫描的」或这页做什么。
- 分组用 `section`：`rounded-xl border border-line bg-surface p-4`。不要套第二层卡片。
- 列表一行一条（`divide-y` 的 `ul`），行高 ≥ 44 px，悬停 `bg-surface-2`。支援卡、回忆、资料库都这样，不做网格卡片。

## 组件（`src/components/`）

| 件 | 用在哪 |
|---|---|
| `ui.tsx`：`Button`（`primary` = 橙色实心，其余描边）、`Chip`（筛选）、`Dot`、`SourceTag`、`Panel`、`Empty`、`Logo`、`PageTitle` | 到处 |
| `Field.tsx`：`Field`、`inputCls`、`Choice`（下拉框，UI 库的 Select）、`Segmented`（真 radio）、`Check`（真 checkbox） | 所有表单。下拉框一律用 `Choice`，不写原生 `select`（macOS 上是系统灰色菜单；`src/test/no-native-select.test.ts` 会拦） |
| `CatalogBits.tsx`：`Thumb`（卡面小图）、`NameStack`（中文名 + 日文原名）、`RowTip`（悬浮说明，UI 库的 Tooltip）、`TipSection` | 信息页、编成方案、偶像卡选择 |
| `IdolPicker` | 要填偶像卡的地方。按名字搜资料库，交出 skin 编号；也能直接填编号 |
| `StartTask` | 会动游戏的按钮：点了先出确认条，再点「开始」才发起 |
| `Job.tsx`：`StatePill`、`JobFrame`（9:16 游戏画面）、`StepRow`、`ScoreStrip`、`ReviewButton`、`JobControls` | 驾驶舱、右栏当前任务、对话里的对局卡 |
| `Cards.tsx`：确认条、错误、对局卡、信息卡、编成卡 | 对话里 |

## 签名细节

- **谁定的**：每一步后面一个小标签——规则（灰）、算法（橙）、LLM（蓝）、兜底（红）。标签不换行、不压缩。
- **现在这一步**：步骤列表第一行 `brand-tint` 底，时间位置写「现在」。
- **突破**：4 个斜的小格，点亮几个就是突破几次，后面跟数字。
- **页面名、动作名**：一律经 `pageLabel` / `actionLabel`（`src/lib/labels.ts`）转成中文，界面上不出现 `HIF_EXAM`、`PlayCard` 这种内部名。偶像卡也写卡名，不写 `i_card-…`。
- **卡名中文优先**：支援卡、回忆、偶像卡、技能卡先写中文名（初星攻略站的资料，`hiro catalog sync` 同步到本机），日文原名小字跟在下面或后面，游戏原名始终看得到（对照 OCR 用）；资料库没同步时只写日文，页面上给「同步资料库」提示条。属性写声乐 / 舞蹈 / 视觉 / 辅助；稀有度照游戏写 SSR / SR / R。
- **卡面小图**：列表每行左边 36 px（`size-9`，编成方案里 28 px）圆角小图，`Thumb` 取 `hiro://…/sprites/<图名>.png`（本机缓存，没缓存时让工具层下载一次）；取不到显示 `surface-3` 底的占位，不留空白。
- **悬浮说明**：行上的详细说明用 `RowTip`（UI 库的 Tooltip，不用 `title` 属性）：标题写中文名 + 日文原名，下面按小节写（支援卡：当前等级的能力、SP 发生率、事件；回忆：技能卡效果、属性加成、能力；技能卡 / P 道具：效果）。

## 动作和确认

会动游戏的操作（开局、扫描、写编成、检查设置）一律走确认：页面上是内联确认条（品牌色描边 + 「开始」「先不」），不弹窗。确认后变成一行结果（已开始 / 已取消 / 没能开始）。

## 动效

只有颜色和阴影的短过渡（≤ 150 ms）、运行中状态点的脉冲。尊重 `prefers-reduced-motion`。

## 验收

- `bun run preview:build && python3 scripts/preview-snap.py [页面 …]`：用假数据离屏截图（不弹窗、不抢焦点），输出 `.preview/shots/`。页面：chat、start、cockpit、plan、supports、memories、settings、wizard。
- 真程序：`bun run build` 后按仓库说明带 `HIRO_HOME` / `HIRO_CLI` 启动，用 `screencapture -R` 截窗口。学マス在跑时别切前台。
