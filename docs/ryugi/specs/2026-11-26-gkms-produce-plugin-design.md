# GKMS 育成策略插件设计（2026-11-26）

讨论文档：Ryugi `document/gkms-produce-plugin`（项目目录 `digital-hiro-oss-worktrees/feat_matto_strategy_plugins`，服务端 id `825GtQTFV4zf7Lnk8aHy6`），两轮共 13 题，答案都收在下面第 3 节。用户 11-26 答完第 2 轮后说「开工吧」，按本稿开工。

## 1. 为什么做

初星助手的选行动和选卡，现在靠我们自己写的规则表和 LLM：H.I.F 只有一张按もりし给广排的日程模板；初 LEGEND 没有专属规则，按 PRO 那一档在打；选卡只有广有手写必拿表，其他偶像靠流派表。

用户要的是「初、N.I.A、H.I.F 各个偶像 / 流派都有算法支撑的高质量决策系统」，不靠我们一张张手写规则。GKMS（yuzeis/GKMSProject）已经有两样现成的算法：

- **路线规划**：`GakumasCLI/produce/hajime_route.py`（初四个难度）、`nia_route.py`（N.I.A）、`hif_route.py`（H.I.F）。每步用按主数据和回包测出的数值模型，beam 搜索整局日程，按评价值打分。
- **选卡包 pickv**：`GakumasCLI/produce/pickv.py`。每张偶像卡 H.I.F 排行榜前 100 条记录里每张技能卡的出现频次 V；三选一、换卡、删卡、强化、刷新都按 V 算。网站接口 `/api/pickv/<偶像卡>` 公开、不用账号。

## 2. 目标与边界

**目标**

1. 本机装了 GKMS 就默认启用一个育成插件 `gkms`：初 / N.I.A 的选行动用 GKMS 路线规划，所有选卡类页面用 pickv。
2. 路线规划吃我们画面识别的局面（不靠采集站），Mac 和 Windows 都能用。
3. 初 LEGEND 另写社区规则，进我们自己的规则表（GKMS 不可用时生效）。
4. 离线回放对比 GKMS 插件和现有决策器，报告交给用户。

**不做**

- H.I.F 的选行动（用户第 2 轮选「以后」）：这次 H.I.F 只接选卡。
- 授業 / 外出 / 活動支給的具体选项、特別指導、N.I.A 营业、饮料、P 道具这些页面：还用现有决策器。
- 实机对比：另外请示。
- 把 GKMS 代码复制进仓库：只写适配层（GKMS rev384 起保留所有权利）。

**不能变坏的**

- GKMS 没装、版本太旧、出错、超时、算不了：这一步交回现有决策器，不停机，记下原因。
- 插件只出主意（`Decision`），点击和安全拦截仍在 hiro 里。

## 3. 已经定的

| 事项 | 结论 | 谁定的 |
|---|---|---|
| 接法 | 外部插件，开源仓库只放适配层，GKMS 代码不进仓库 | 用户 11-25 |
| 默认 | 装了 GKMS 就默认启用，没装照旧（是「行为变化默认关」的例外） | 用户 11-25 |
| 接入点 | `produce_advisor`；不管 / 出错 / 超时都交回内置决策器，记下原因 | 我，沿用插件设计 4.5、4.7 |
| 选卡包格式 | 按 GKMS 网站给的格式读；摘要和 GKMS 自己的主数据对不上就当过期不用 | 我（pickv 自己的规矩） |
| 隐藏信息 | 用到画面上看不到的数据时打 `uses_hidden`、战绩单列（这一版用不到） | 我，沿用已定规则 |
| 选行动 | 做一层转换，让规划器吃画面识别的局面 | 用户，第 1 轮 |
| pickv 范围 | 三选一、换卡两步、删卡、强化，以及相談、轮间里的买卡 / 删卡 / 强化 | 用户，第 1 轮 |
| 广 | 也默认交给 GKMS | 用户，第 1 轮 |
| 什么算装了 | 能导入 GKMS 的 GakumasCLI；选卡包从网站下载并缓存 | 第 1 轮，接受推荐 |
| 初 LEGEND | 另写社区规则，进我们自己的规则表 | 第 1 轮，接受推荐 |
| 作者 | 用户已沟通过，不另写说明 | 用户，第 1 轮 |
| H.I.F 选行动 | 这次不接；以后先核对开放课程编号能不能拼全，单独报告 | 用户，第 2 轮 |
| 日程来源 | GKMS 接管选行动时自动开日程列表探查；读不到的周这一步交回规则 | 第 2 轮，接受推荐 |
| 初 / N.I.A 选卡 | 用同一张偶像卡的 H.I.F 包近似，trace 里标明 | 第 2 轮，接受推荐 |
| P 点 | 卡按 V 来：值得、买得起就做，价值高的先做；H.I.F 本戦给轮间留钱 | 第 2 轮，接受推荐 |
| 对比 | 这一阶段只做离线回放，实机另请示 | 第 2 轮，接受推荐 |
| LEGEND 攻略 | 我搜日文社区攻略，每条规则标出处，用户过目 | 第 2 轮，接受推荐 |
| 规划节奏 | 每周重新规划；以后几周读不到 SP 的课按普通课算 | 我 |
| 关掉 | `strategy.json` 某页写 `builtin` 关这一页；`HIRO_GKMS=0` 整个关 | 我 |
| 实机 / 推送 / 合并 | 先问用户 | 已定规则 |

## 4. 设计

### 4.1 组成

```
src/hiro/gkms/
  locate.py      找 GKMS：目录、Python、版本、缺哪些模块
  worker.py      在 GKMS 的 Python 里跑的子进程脚本（JSON 行协议），只调用 GKMS 的函数
  client.py      启动 / 重启 worker、带超时的调用
  state_map.py   GameState → GKMS 的培育记录（属性、体力、成长率、P 道具、日程）
  advisor.py     GkmsProduceAdvisor：页面 → Decision
```

GKMS 在子进程里跑，不进 hiro 的进程：GKMS 的全局状态和线程碰不到主循环；Windows 安装版自带 Python，用它自己的；子进程挂了重启一次，再挂这一局就不用 GKMS 了。

### 4.2 找 GKMS

顺序（第一个找到的为准）：

1. `HIRO_GKMS_HOME`：GKMS 仓库根目录（下面有 `GakumasCLI/`）。
2. Windows：`%LOCALAPPDATA%\Programs\GKMS` 及其下一层目录里有 `GakumasCLI/` 的那个。
3. `~/github/_ext/GKMSProject`。

要求 `GakumasCLI/produce/` 下有 `hajime_route.py`、`nia_route.py`、`pickv.py`，主数据目录 `GakumasCLI/ro/master/tables/` 存在；缺了就算「没装」，原因写进 `hiro plugins list`（例如「GKMS 版本太旧：缺 pickv.py」）。

Python：`HIRO_GKMS_PYTHON` → `<home>/python/python(.exe)`（Windows 安装版）→ `<home>/.venv/bin/python` → hiro 自己的解释器。

`HIRO_GKMS=0` 时一律当没装。

### 4.3 worker 协议

`<python> -I -u src/hiro/gkms/worker.py <gkms_home>`，标准输入输出各一行 JSON。

| op | 输入 | 输出 |
|---|---|---|
| `hello` | — | GKMS 版本（git 提交或安装版版本号）、pickv `FORMAT` / `ALGO`、`TAU`、`REROLL_GAIN` |
| `route` | `record`（GKMS 培育记录）、`from_step`、`beam` | `hajime_route.plan` / `nia_route.plan` 的 `plan` 和 `decisions` |
| `pack` | `idol`、`wait_s` | 选卡包状态 ok / stale / loading / none（loading 时在 worker 里等最多 `wait_s` 秒） |
| `values` | `idol`、`deck`（卡 id 列表）、`offer`、`upgrade` | 每张候选的 V_{n+1}、牌组每张的 V_n、可强化卡的 V_1 × u、候选的 `reroll_gain` |
| `removal` | `idol`、`deck` | `pickv.removal_by_v` 的结果（先删トラブル，再删 V 最低） |

单次调用超时沿用插件闸门（`strategy.json` 的 `timeouts`，`gkms` 默认 20 秒）。

### 4.4 默认启用

- 插件以内置 id `gkms` 注册（`hiro.plugins` 里登记的随包插件，不用另装入口点），`hiro plugins list` 能看到能不能用、为什么。
- `strategy.produce_for(page)`：配了插件 id → 用它；显式写 `builtin` → 内置；都没写 → 这一页在 `gkms` 的默认页面里、且 `gkms` 能用时用 `gkms`。
- 默认页面：`ACTION_SELECT`、`SELECT_CARD`、`SKILL_CARD_CHANGE_1`、`SKILL_CARD_CHANGE_2`、`SKILL_CARD_REMOVAL`、`SKILL_CARD_ENHANCE`、`CONSULT`、`HIF_ROUND_INTERVAL`。
- `plugins.json` 的对应表里标出哪些页是默认启用的。

### 4.5 各页面怎么定

**选行动（初 produce-001/002/003/006、N.I.A produce-004/005；H.I.F 返回不管）**

1. `state_map` 把局面拼成 GKMS 培育记录（4.6）；日程缺周、本周选项对不上、属性没读到 → 不管，记原因。
2. `route` 从本周开始规划，取计划里本周那一项，对回画面上的选项（属性、SP、授業、相談、活動支給、外出、休む）。
3. 理由写本周每个选项的评分差（`vs_best`）和计划的终局属性。

**三选一（SELECT_CARD）**：候选里 V_{n+1} 最高的那张；可以刷新、且 `reroll_gain` 大于 pickv 的 `REROLL_GAIN` 时刷新。这一版不用禁用（我的决定）。

**换卡**：阶段 1 换入 V_{n+1} 最高的；阶段 2 换出 V_n 最低的（跳过トラブル、定制过的、没认出的格）。

**删卡**：按 `removal_by_v`（先トラブル，再 V 最低；跳过定制过的和没认出的格）；没有可删的就取消。

**强化**：没强化过的卡里 V_1 × u 最高的。

**相談、轮间（混合页）**

1. 先让内置决策器看一份去掉了卡（货架上的卡、删卡 / 强化服务）的页面。它要买饮料、回体力等就照它做。
2. 它要结束时，问插件这一页的卡怎么办，预算 = P 点 − 预留：
   - H.I.F 選抜：内置的特別指導预留（`_guidance_reserve`）；
   - H.I.F 本戦的相談：给轮间留 200P（我的决定，同 `INTERVAL_DRINK_RESERVE`）；
   - 轮间、初、N.I.A：不留。
3. 插件按价值从高到低做一件：删 V_n 低于 `TAU` 的卡（删卡服务）、买 V_{n+1} ≥ `TAU` 的卡、强化 V_1 × u ≥ `TAU` 的卡；每种服务一次相談只用一次（插件自己记）。都不值得 → 结束。

这一步在 `ProduceDeciders.decide` 里实现（只对声明了「混合页」的插件生效），插件本身仍只回答卡。

### 4.6 局面转换（state_map）

- 属性 / 体力 / P 点 / 参数上限：`produce.params`、`stamina`、`max_stamina`、`produce_point`、`param_cap`。
- 成长率：我们的 `growth_permil` 是 1000 + x（1122 = +12.2%），GKMS 要 x。
- 周数：`produce.step`，没有时用 `produce.schedule.current_week`。
- 日程：`produce.schedule.weeks` 逐周换成 GKMS 的 `step_types`：训练 → `lesson_<属性>`（LEGEND 为 `legend_lesson_<属性>`，本周读到 SP 加 `_sp`）、追い込み → 三个 `lesson_<属性>_hard`、授業 → `event_school`、相談 → `shop`、活動支給 → `event_activity`、外出 → `event`、差し入れ → `present`、试镜 → `audition_mid1` / `audition_final`；每个非试镜周补上 `refresh`（休む随时可选）。本周以画面选项为准。
- P 道具：`produce.items`；支援卡：开局编成里读到的支援卡（等级读不到时按满级，我的决定）；LEGEND 的课程上限提升：`exam_session.lesson.lesson_limit_up_score`，没有时用 GKMS 的默认（90）。

### 4.7 日程列表探查自动打开

`schedule_probe.enabled()`：`HIRO_SCHEDULE_PROBE=1` 开、`=0` 关；没设时，`gkms` 接管这一局的选行动（初 / N.I.A）就开。

### 4.8 初 LEGEND 社区规则

在 `hiro.brain.hajime_nia_rules` 里给 produce-006 加专属规则（休息线、SP 优先、相談、外出、考前体力等），每条带出处（日文 wiki / note / 攻略视频文字总结），GKMS 实测的 LEGEND 课程目标和排行榜统计作补充。写好后交用户过目。

### 4.9 记录

- 每步：`Decision.plugin = gkms@<版本>`，理由写清用的是哪一部分（「GKMS 路线」「pickv（H.I.F 包）」），初 / N.I.A 用 H.I.F 包时标明。
- trace 目录写 `gkms.jsonl`：每步的输入摘要、GKMS 输出、选了什么、为什么不管。

### 4.10 离线对比

`tools/gkms_compare.py <trace 目录…>`：读 `steps.jsonl`，在选行动和选卡类页面上，用记录里的局面分别跑现有决策器和 GKMS 插件，统计：

- 两边选得不一样的比例（按模式、按页面）；
- 选行动：现有决策器的选择在 GKMS 模型里比最优差多少（`vs_best`）；
- 选卡：两边所选卡的 V；
- 插件「不管」的次数和原因。

结果写 `reports/gkms-compare/<日期>/report.md`。报告开头写明：只换一步、看不到后面的连锁，打分用的是 GKMS 自己的模型和 V，天然偏向 GKMS，只能说明「接得上、在哪里意见不同」。

## 5. 测试

- `state_map`：用 `tests/data` 里的真实局面（初 PRO 第 11 周等）转换，核对字段和日程。
- `worker`：有 GKMS 时跑真 worker（没装就跳过）；没有时用假 worker 测协议、超时、重启。
- `advisor`：每个页面的 Decision 格式能过插件闸门的白名单和越界检查；GKMS 不可用时不管。
- 默认启用：装了 / 没装 / `builtin` / `HIRO_GKMS=0` 四种情况。
- 混合页：内置先买饮料、再轮到插件买卡；预算不够不买；每种服务只用一次。
- 全量测试和基线比没有新增失败（基线 854e1b7：2 个失败）。

## 6. 留给实现者

- worker 的 GKMS 版本号怎么取（git 提交、安装版的版本文件）。
- 日程里画面识别出的选项名到 GKMS 类型的完整对照（以实机记录为准，对不上的周不管）。
- N.I.A 自主课的课程 id（`self_lesson-produce_004-0N`）按阶段推出来。

## 7. 用到的 artifact

- Ryugi 文档 `document/gkms-produce-plugin`：两轮问题与答案，见第 3 节。
