---
name: hiro-cli
description: 用 hiro 命令行查学マス账号信息和游戏资料库、开局（H.I.F / 偶像之路 / 日常）、扫描库存、看对局进度、暂停或接手。使用者说「查我的支援卡」「这张卡是什么效果」「帮我打一局」「领日常」「扫描回忆」「现在打到哪了」时用。
---

# hiro 命令行

数字小广控制台的全部能力都从 `hiro` 命令出来；桌面程序和你用的是同一套命令。
在仓库里没装成命令时用 `PYTHONPATH=src python -m hiro.cli.main <命令>`。

## 输出

每条命令打印一行 JSON：

```json
{"ok": true, "data": {...}}
{"ok": false, "error": {"code": "busy", "message": "已经有任务在跑：…", "hint": "等它结束，或 hiro run stop"}}
```

退出码：0 成功；1 失败；2 用法错误；3 待确认（见下）。出错时把 `message` 和 `hint` 用白话转告使用者，照 `hint` 做下一步。
`hiro tools` 列出全部命令、风险等级和参数（参数以它为准）。

## 待确认：会动游戏的命令要先问用户

风险等级 `act` 的命令（`hiro run start`）第一次执行不会真的做，返回待确认，退出码 3：

```json
{"ok": true, "data": {"pending": {"id": "3f9a1c2b7d10", "command": "run start", "summary": "开一局 H.I.F 选拔", "expires_at": 1763380000}, "message": "…"}}
```

1. 把 `summary` 原样告诉使用者，**先问用户**同不同意。
2. 使用者同意后才运行 `hiro confirm <id>`；不同意就 `hiro cancel <id>`。不要替使用者确认，也不要为了省事连着确认。
3. 编号只能用一次，10 分钟过期；过期了重新发起。`hiro pending list` 看还有哪些没确认。

## 绝不做

- 花ジュエル、放弃培育（破棄 / リタイア）：没有这样的命令，也不要在游戏里手动点。
- 读种子或存档里的隐藏信息。
- 让账号密码明文出现：密码只由使用者在设置页填写；你只能 `hiro secret copy`，它不显示值。

## 查信息（只读，直接执行）

| 命令 | 用途 |
|---|---|
| `hiro info summary` | 账号概况：PLv / AP / ジュエル，每类信息的数量和上次扫描时间 |
| `hiro info idols [--idol 名字] [--rarity SSR]` | 偶像卡（突破、潜力） |
| `hiro info supports [--rarity SSR] [--attr Vo\|Da\|Vi\|Assist] [--name 字]` | 支援卡（等级、突破） |
| `hiro info memories [--timing start\|mid\|final] [--card 字]` | 回忆（技能卡、获得时机、参数、能力） |
| `hiro info affection` | 每位偶像的好感度 |
| `hiro info import --kind supports\|memories <json>` | 把扫描工具的输出导入信息库（扫描任务结束时会自动做，一般不用手动） |

返回里 `scanned_at` 是上次扫描时间；是 `null` 时说明还没扫描过，`hint` 写了怎么扫。回答使用者时说清楚是哪次扫描的数据。

## 游戏资料库（只读）

| 命令 | 用途 |
|---|---|
| `hiro catalog sync` | 下载 / 更新资料库（第一次用前运行；报 `catalog_missing` 时也是它） |
| `hiro catalog search <词> [--kind cards\|items\|drinks\|idols\|memories] [--lang ja\|zh]` | 按名字或效果文字搜，最多 50 条 |
| `hiro catalog get <编号> [--lang zh]` | 一条完整记录（强化后的效果在 `up` 里） |
| `hiro catalog sprite <asset>` | 小图的本机路径（界面用） |

## 开局和扫描（会动游戏：待确认）

`hiro run start <种类> [参数]`，同一时间只能跑一个任务，已经有任务在跑会返回 `busy`。

| 种类 | 参数 | 做什么 |
|---|---|---|
| `hif` | `--command new\|resume`、`--mode qualify\|main`、`--idol <偶像卡编号>`、`--no-llm` | H.I.F 培育。`--mode main` 会用掉一份选拔回忆；`resume` 接着打进行中的培育 |
| `tower` | `--idol <路线的偶像名>`、`--layers N`、`--no-llm` | 偶像之路 |
| `daily` | — | 日常：礼物、活動費、任务奖励、社团 |
| `scan-supports` | — | 扫描支援卡（只读，不改编成） |
| `scan-memories` | — | 扫描回忆（只读，不改编成） |
| `selftest` | `--seconds N` | 自检，只打印几行，不碰游戏 |

开局前先确认：游戏停在首页；没有别的设备上进行中的培育（那种情况开局确认页绝不按「開始する」，程序会停下来）。

## 看进度、暂停、接手（直接执行）

| 命令 | 用途 |
|---|---|
| `hiro run status` | 当前任务（`running` / `paused` / `takeover` / `stopping`）和最近 10 个任务 |
| `hiro run events [--job 编号] [--since N] [--follow]` | 任务事件：每一步的日志和结果。轮询时把上次最后一条的 `seq + 1` 作为 `--since`；`--follow` 一直跟到结束（每条事件一行） |
| `hiro run pause` | 暂停：主循环停在下一个检查点，不再点击 |
| `hiro run takeover` | 使用者要自己点几下：自动操作停下 |
| `hiro run resume` | 继续：从当前画面重新判断在哪一页，接着打 |
| `hiro run stop` | 停止任务（不放弃培育，游戏停在原地，之后可以 `hif --command resume` 续跑） |

任务结束的事件是 `done`、`failed`（看前面几条 `log` 判断原因，转告使用者）或 `stopped`。

## 账号密码

| 命令 | 用途 |
|---|---|
| `hiro secret list` | 保存了哪些（只有名字，不显示值） |
| `hiro secret copy <名字>` | 复制到剪贴板（带敏感标记，45 秒后清空），用来粘贴进游戏登录框 |
| `hiro secret set <名字>` | 只给使用者用：值从标准输入读。不要替使用者填写 |
| `hiro secret delete <名字>` | 只给使用者用 |

游戏掉登录时：先 `hiro secret copy game-account` 粘贴进账号框，再 `hiro secret copy game-password` 粘贴进密码框。
报 `secret_missing` 时请使用者到设置 → 账号密码里填写。

## 其他

| 命令 | 用途 |
|---|---|
| `hiro tools` | 全部命令和参数 |
| `hiro confirm <id>` / `hiro cancel <id>` / `hiro pending list` | 待确认（见上） |
