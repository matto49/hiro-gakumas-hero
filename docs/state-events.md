# 状态推算与识别互验：详细方案（草案 v0.1，待用户确认）

> 起草：总控会话，2026-10-31。依据：设计草案与三局复盘
> 三局实机复盘。
> 本文只是方案，用户同意后再派任务、改代码。字段名沿用客户端协议（`UserProduceProgress` / `Api.Produce*Request`），与 `docs/contracts.md` 一致。

## 0. 要解决的问题

- 培育状态 24 个字段，三局只读到 9 个；`produce.deck` 三局全空，选卡 / 换卡 / 删卡盲选，相談从未购买。
- 读错了没人发现：分数 37811→3771、Da≈900→73，错值直接进了 LLM。
- 现在的 `hiro.loop.reduce` 只做「页面转移校验 + 字段合并（None 不清旧值、save > screen）」，不按游戏规则推进状态，所以无从校验识别。

目标：**牌组、饮料、P 道具、属性、体力、P 点、星星由状态机按事件维护；每一步先预测，再和识别互验；主动查看（打开牌组页）只做兜底和开考前复核。**

## 1. 总体流程

```
决策 Decision ──► 执行 ExecutionResult ──► 事件 Event（由动作推出，或由观测认出）
                                               │
                              apply_event(state, event) ──► 预测 Prediction（下一个观测点各字段的期望值 / 区间）
                                               │
观测 Observation ──► check(prediction, obs) ──► 互验 Check：consistent / read_error / model_gap / unpredicted
                                               │
                              reduce(state, obs, checks) ──► GameState（字段带状态：verified / predicted / observed / pending）
                                               │
                         pending 或开考前 ──► 主动查看（牌组页全量读）──► 覆盖 + 记差异
```

- 事件来源有两种：**动作事件**（决策层发出的动作执行成功，例如选了第 2 张卡），**观测事件**（识别认出的页面 / 弹窗，例如「获得卡」弹窗、考试结算）。两者都进 trace。
- 预测只在事件之后做，期望值写进 `Prediction`；下一个带相关字段的观测到来时做 `Check`。

## 2. 契约新增（`hiro.contracts`，版本升到 1.2，只增不改名）

### 2.1 `Event`

| 字段 | 类型 | 说明 |
|---|---|---|
| `seq` | int | 与触发它的 Step / Observation 同序号空间 |
| `kind` | str | 事件类型，见 §3 事件表（如 `CARD_ACQUIRE`） |
| `origin` | str | `action`（由已执行的 Decision 推出）/ `observed`（识别认出）/ `inferred`（互验差异反推）/ `review`（主动查看覆盖） |
| `args` | dict | 事件参数：`card_id`、`upgrade`、`deck_index`、`drink_id`、`price`、`option`、`exam`、`score` 等 |
| `basis` | str | 推算依据：`master`（主数据）/ `community`（`gakumas_produce.community_rules`）/ `sim`（模拟器）/ `observe_only`（只能观测，无法推算） |
| `confirmed` | bool \| None | 观测是否确认事件确实发生（`None` 未确认） |
| `ref` | dict | 关联：`decision_seq`、`observation_seq`、`screenshot` |

### 2.2 `Prediction`

| 字段 | 类型 | 说明 |
|---|---|---|
| `seq` | int | 预测基于哪个事件之后的状态 |
| `event_seq` | int | 来源事件 |
| `fields` | dict[str, Expect] | 字段路径 → 期望（`produce.params.Da`、`produce.deck`、`exam.score` 等） |
| `due` | str | 在哪个页面 / 观测点核对（`ACTION_SELECT`、`HIF_EXAM`、`any`） |
| `expires_after` | int \| None | 超过多少帧没核对就作废 |

`Expect = {value?, min?, max?, exact: bool, tolerance?, note?}`。属性等有随机性的字段用区间（例如 SP 课程按社区 SP 概率给上下界），牌组用精确多重集。

### 2.3 `Check`

| 字段 | 类型 | 说明 |
|---|---|---|
| `seq` | int | 观测序号 |
| `field` | str | 字段路径 |
| `predicted` | Any | 期望（来自 Prediction，或不变量） |
| `observed` | Any | 识别读数 |
| `verdict` | str | `consistent` / `read_error` / `model_gap` / `unpredicted` |
| `rule` | str | 判据：`prediction`、不变量名（如 `score_monotonic`、`digit_count`、`param_le_cap`） |
| `action` | str | 处理：`accept` / `discard_reread` / `mark_pending` / `active_review` |
| `retries` | int | 重读次数 |

### 2.4 `GameState` 新增

| 字段 | 说明 |
|---|---|
| `field_status: dict[str, str]` | 字段路径 → `verified`（推算与读数一致）/ `predicted`（只有推算）/ `observed`（只有读数）/ `pending`（不一致待复核）/ `unknown` |
| `pending_review: list[str]` | 待复核字段，非空时触发主动查看（§5） |
| `ledger: list[Event]` | 本局已应用的事件（最近 N 条，完整的在 trace） |
| `sources` 来源新增 `event`、`review` | 优先级：`save` > `review` > `screen`（已互验）= `event`（已确认）> `screen`（未互验）> `infer` |

### 2.5 `Step` 新增

`events: list[Event]`、`predictions: list[Prediction]`、`checks: list[Check]`（调试模式下一致的 Check 也写，正常模式只写不一致的）。

## 3. 事件表初稿

「认出方式」里 **A** = 由已执行的动作推出，**O** = 识别认出页面 / 弹窗，**R** = 只能靠主动查看。

| 事件 | 认出方式 | 改哪些字段 | 改多少 | 依据 |
|---|---|---|---|---|
| `PRODUCE_START` | O：开局确认页 `PRODUCE_START_CONFIRM` | deck、items、drinks、supports、params、growth_permil、stamina、max_stamina、produce_point、param_cap | 偶像卡初始牌组 + 固有卡 + 固有 P 道具（主数据）；支援卡由识别读出 | master；supports 来自 observe |
| `DAY_ADVANCE` | O：选行动页 `step` 变化 | step、wappen 等日程字段 | step+1 | master（HIFSchedule） |
| `LESSON` | A：选了课程（含 SP）；O：课程结束页 | params、stamina、star、wappen_left | 属性 = 课程基础值 × growth_permil；SP 概率用 `community_rules.sp_rate`；体力按课程消耗 | master + community（SP 是随机→区间） |
| `STUDY`（授業） | A：选了第 i 项；O：效果说明 / 获得弹窗 | params、stamina、deck（获得 / 强化 / 换卡） | 选项效果（识别读出的 effect 文本 + 主数据） | master + observe |
| `OUTING`（外出） | A：选了第 i 项 | stamina、produce_point、drinks、deck | 回复量 / P 消耗按选项 | community（社区原表） |
| `GIFT`（差し入れ）/ `ALLOWANCE`（活動支給） | A + O：获得弹窗 | produce_point、drinks、deck | P +80 等；随机饮料 / 卡由弹窗读出 | community + observe |
| `REST` | A | stamina | + 回复量 | master |
| `CARD_ACQUIRE` | A：三选一拿第 i 张；O：获得弹窗 | deck | +1（card_id、upgrade 来自候选识别） | observe（候选）+ A |
| `CARD_REROLL` | A：再抽選 | rerolls_left、page_fields.candidates | -1；候选整体替换 | master（次数）+ observe |
| `CARD_EXCLUDE` | A：カード除去 | excludes_left、excluded | -1；+card_id | master + A |
| `CARD_UPGRADE` | A：相談强化 / 强化页选卡；O：「…を強化しました」 | deck[i].upgrade、produce_point、upgrades_done | +1；P −`service_price` | community + O |
| `CARD_DELETE` | A：相談删卡 / 删除页；O：删除确认 | deck、produce_point、deletes_done | −1；P −`service_price` | community + O |
| `CARD_CHANGE` | A：授業换卡 stage1/2 | deck | 换出一张、换入一张 | A + O |
| `CARD_AUTO_PROCESS` | O：考试后自动处理基础卡的演出 | deck | 选拔①② 各自动处理 2 张基础卡（playbook） | community；**具体是哪两张只能观测 → 认不出就 pending** |
| `CARD_CUSTOMIZE` | O：定制演出 | deck[i].customized | true | observe |
| `SHOP_BUY` | A：execute_shop 成功（「交換済」） | produce_point、deck 或 drinks | P −实付价（识别读出）；+物品 | A + observe |
| `DRINK_ACQUIRE` | O：获得弹窗 / 选饮料页 | drinks | +1（满了会出 `PDRINK_MAX`） | observe |
| `DRINK_USE` | A：考试内喝 | drinks | −1 | A（考试存档可复核） |
| `DRINK_DISCARD` | A：`PDRINK_MAX` 选了丢哪瓶 | drinks | −1 | A |
| `PITEM_ACQUIRE` / `PITEM_CUSTOMIZE` | A：背包 / 定制选择 | items | 替换为新 ID（颜色 + 毛绒 + 装饰） | master（ProduceCustomizeItem） |
| `WAPPEN_TRIGGER` | O：ワッペン演出 / 星星变化 | wappen_left、star | −1；星星 + 社区值 | community |
| `EXAM_START` | O：进入 `HIF_EXAM` | exam.*（存档为真值） | 重置考试状态 | save |
| `EXAM_RESULT` | O：`HIF_EXAM_RESULT` | star、params、produce_point、passed | 星星 `selection_star_base` / `finals_star_base` × star_permil；属性 `selection_param_split` | community（星星无精确公式→区间） |
| `ROUND_INTERVAL_BUY` | A：轮间购买 / 回体力 | produce_point、stamina、deck、drinks | 价格 `interval_*_price` | community |
| `UNMODELED_CHANGE` | 互验得出：读数稳定且合理但对不上 | 对应字段 | 以读数为准、字段标 pending | inferred（反过来补事件表） |

**考试内**（§6）的事件（出牌、喝饮料、回合结束）以本地存档为真值，不在本表展开。

### 3.1 识别现状（识别-01 2026-11-01 更新，0f58dc4）

识别认出的事件由 `hiro.vision.events.observed_events(obs, prev)` 给出（§2.1 字段名，另带 `confidence` `note`；同一弹窗连续几帧只发一次）。
离线帧（datasets / traces / reports/runs 共 2869 帧）跑出 54 个事件，逐个对照截图或已核对的真值：54 / 54 正确。

| 事件页面（page） | Event | args | confirmed | 离线帧 |
|---|---|---|---|---|
| 获得弹窗 ACQUIRE（メモリー効果、支援卡事件、活动支给、相談 / 授業拿卡后的演出） | `CARD_ACQUIRE` / `DRINK_ACQUIRE` / `PITEM_ACQUIRE` | card_id upgrade / drink_id / pitem_id | True | 48 帧 → 8 卡、3 饮料、2 P 道具（含 H.I.Fワッペン） |
| 相談 交換確認（DIALOG kind=exchange，`confirm`） | `SHOP_BUY` | card_id upgrade 或 drink_id、price（实付，SALE 为折后）、p_before、p_after、sale | None（点「交換する」后才算） | 2 |
| 删卡页 削除确认（SKILL_CARD_REMOVAL `confirm`） | `CARD_DELETE` | card_id upgrade deck_index price p_before p_after | None | 1 |
| 「〜を強化しました」UPGRADE_RESULT | `CARD_UPGRADE` | card_id、upgrade（强化后） | True | 夹具 1 |
| ワッペン演出 STAR_GAIN | `WAPPEN_TRIGGER` | star_gain | True | 7（连续两次触发分得开） |
| 饮料溢出 PDRINK_MAX | `DRINK_ACQUIRE` | drink_id、overflow=True、held（列表看全时） | None（丢哪瓶 / 不要由动作定） | 5 |
| 考试结算 HIF_EXAM_RESULT | `EXAM_RESULT` | exam score rank passed | True | 26（结算页停留很久、中间被打断时会再发一次，按 exam 去重） |
| 牌组页看全 DECK_LIST | `DECK_REVIEW`（origin=review） | deck、deck_size | True | 只有 KAA 参考图；比较用 `deck.deck_diff(expected, observed)` |
| 三选一 / 删卡 / 强化 / 换卡页的选中项 | 不成事件 | 动作事件用 `selection_args(obs)` 取 card_id upgrade deck_index | — | — |
| 选饮料页饮料满（SELECT_DRINK `held` `full`） | 不成事件 | 执行器先丢后领用 | — | — |
| 随机强化（支援卡事件） | 认不出（画面不说是哪张） | — | — | — |
| 考试后自动处理基础卡 | 认不出（还没有样本） | — | — | — |
| 授業 / 外出眠気 | 选项 `choices[].trouble`；拿到时出获得弹窗 → CARD_ACQUIRE | — | — | — |

据此，`CARD_AUTO_PROCESS`、随机强化在识别补齐前一律走 `pending` → 开考前主动查看。

## 4. 不变量清单（不依赖事件也能抓读错）

| 名称 | 字段 | 规则 | 违反时 |
|---|---|---|---|
| `range` | params / stamina / produce_point / star | 0 ≤ x ≤ 上限（属性 ≤ param_cap，体力 ≤ max_stamina） | read_error |
| `digit_count` | exam.score、params、star、produce_point | 与上一已采信值相比位数减少且减少量 > 合理变化 | read_error（例：37811→3771） |
| `score_monotonic` | exam.score | 同一场考试内不减 | read_error |
| `turn_step` | exam.remaining_turns | 每回合减 1；加回合卡可 +1 | read_error / model_gap |
| `param_monotonic` | params | 培育中只增不减（无减属性事件时） | read_error（例：Da 900→73） |
| `p_delta` | produce_point | 变化量 = 已知价格 / 奖励之和 | model_gap |
| `deck_delta` | deck 张数（考试里的山札+手牌+弃牌） | 变化 = 事件增减 | model_gap |
| `drink_capacity` | drinks | 张数 ≤ drink_capacity | read_error |
| `counters` | rerolls_left / excludes_left / wappen_left | 只减不增（换日 / 换页按主数据重置的除外） | read_error |

`read_error` 的处理：丢弃读数、最多重读 2 次（换帧），仍不合理就保留推算值、字段标 `pending` 且读数不交给决策层。

## 5. reduce 扩展

保持「纯函数、只由它改 GameState」：

1. `apply_event(state, event) -> (state', predictions)`：按事件表更新 `produce` / `ledger`，返回预测。推算函数（预测器）放 `hiro.state.events`（决策会话实现），reduce 只负责调用和记账。
2. `check(state, predictions, obs) -> checks`：对 obs 里出现的字段，先过不变量，再和到期的预测比较，产出 Check。
3. `reduce(state, obs, checks)`：
   - `consistent` → 写入读数，`field_status=verified`；
   - `read_error` → 不写，`field_status` 维持，记 `conflicts`，并让 Session 重读（`Session.observe` 带 `reread=field`）；
   - `model_gap` → 写入读数（读数稳定、合理时以画面为准），`field_status=pending`，加入 `pending_review`，同时生成 `UNMODELED_CHANGE` 事件；
   - `unpredicted`（没有预测）→ 按现行规则写入，`field_status=observed`。
4. 来源优先级按 §2.4。`None` 仍不清旧值；`pending` 字段在提示词里标「待复核」，与现在的「未识别」同等对待。
5. 防抖、页面转移图不变。

## 6. 主动查看（兜底）与考试内

**主动查看**（牌组页全量读，`Session.review_deck()`；读取器已有：识别-01 61c37ab 的 `hiro.vision.deck.read_deck_list` / `merge_deck_pages` / `find_deck_button`，页面 `DECK_LIST` 已登记进转移图与 reduce）只在三种情况触发：
- `pending_review` 含 `produce.deck`（事件没认出，例如认不出的页面上牌组变了、随机生成卡、自动处理基础卡认不出）；
- 每场考试开始前（选拔 ①②③、R1、R2 前的最后一个选行动页）复核一次；
- 调试模式（§7）。

结果作为 `origin=review` 的事件写入，覆盖牌组，并把「推算 vs 实际」的差异写进 trace，用来补事件表。不为读状态增加固定流程。

**考试内**：本地考试存档是真值（现有 `hiro.sim.live`）。屏幕读数和存档互验的路径保留；模拟器预测下一帧作为第二层校验；存档读不到或模拟器不支持时退回 §4 的不变量（score_monotonic、digit_count、turn_step）。

## 7. 调试模式

- 开关：`run_hif --debug-recog`，或环境变量 `HIRO_DEBUG_RECOG=1`；写进 `run.json`。
- 行为：
  1. 每个稳定帧都做全字段识别（不只决策点），并和状态机核对；
  2. 每个改变状态的事件之后立刻读一次相关页面核对；改了牌组的事件之后打开牌组页全量读；
  3. 所有 Check（含 consistent）写进 `Step.checks`；
  3b. **非决策页也存截图**（用户 2026-10-31 追加）：页面每次变化都存一帧，尤其是 SKILL_CARD_CHANGE_1/2、SKILL_CARD_ENHANCE、SKILL_CARD_REMOVAL、PDRINK_MAX、所持スキルカード页（DECK_LIST）、获得演出 / 获得弹窗——三局 trace 里这些页面 screenshot 全是 None，识别侧没有实机样本；
  4. 每局产出一致率报告 `reports/runs/<trace>-recog.json`：按字段、按事件类型统计一致 / 读错 / 模型缺口的比例，并列出 `UNMODELED_CHANGE` 清单。
- 额外耗时在报告里单独统计（`debug_overhead_seconds`），不计入正式效率度量（考试内截图→点击中位数照常只统计决策路径）。
- 退出条件：某字段连续 3 局一致率 ≥ 99% 且没有 `model_gap`，该字段就可以退出调试的高频识别；全部字段达标后关闭调试模式。

## 8. 分工与验收

| 会话 | 任务 | 交付 | 验收（三局实机 trace 离线回放） |
|---|---|---|---|
| 总控 | 契约 1.2（Event / Prediction / Check、GameState / Step 新增字段 + schema + contracts.md）；`apply_event` / `check` / reduce 扩展；`hif.py` 由动作发事件；主动查看触发；调试模式开关、trace 与一致率报告；离线回放脚本 `python -m hiro.loop.replay --events` | 代码 + 测试 | 回放三局：每个 Step 都有 events / checks；`read_error` 能抓出 3771（局2）、1403 / 2231（局3）、Da=73（局2 d12） |
| 决策 | 预测器 `hiro.state.events`：§3 每个事件的 `predict(state, event)`，依据主数据、`community_rules`、模拟器；初始牌组（偶像初始卡组 + 固有卡 + 支援卡带来的卡）；考试结算星星 / 属性区间 | 代码 + 单测（每个事件至少 1 个） | 三局选拔① 之前的 LESSON / STUDY / OUTING / GIFT 预测与实际属性、体力、P 点一致率 ≥ 90%（区间内即算一致）；初始牌组与开局确认页一致 |
| 识别 | 事件页面识别：获得卡 / 饮料弹窗（读出 card_id、upgrade）、强化 / 删除 / 换卡页、`PDRINK_MAX`、自动处理基础卡演出、ワッペン演出；牌组页读取（只用于兜底与开考前复核）；字段带置信度 | 代码 + 夹具 | 现有帧上事件弹窗识别准确率 ≥ 95%；牌组页需实机采样时找总控约时段 |
| 数据确认 | 不变量清单（§4）的实现与参数；回放验收报告：每个决策点牌组是否「已知或 pending」、预测 vs 观测差异、缺失事件清单 | 报告 + 不变量单测 | 选拔① 之后每个决策点牌组已知或明确 pending（100%）；差异报告列出事件表缺口 |

**依赖顺序**：契约（总控）→ 预测器与不变量（决策、数据确认并行）→ 事件页面识别（识别-01）→ 主循环接线与调试模式（总控）→ 实机调试局。现有三局 trace 只在决策点存了帧、也没有牌组页，所以离线回放验收以「能抓出读错、能列出缺口、牌组状态有明确的已知 / pending」为准；推算精度的最终验收要等调试模式实机局。

## 9. 待用户确认的点

1. 契约升到 1.2，`Step.checks` 在正常模式只写不一致的（减少 trace 体积），调试模式全写。是否同意？
2. 主动查看：牌组页的打开方式（选卡页旁的牌组按钮或 P 手帳）由识别 / 游戏执行实机确认；每场考试前固定复核一次，会让每局多 5 次翻页，预计每次 5–10 秒。
3. 随机性大的字段（SP 课程的属性、考试后星星）用区间预测，区间外才算不一致。区间取社区数据的 95% 范围。
4. 调试模式退出标准（§7）：连续 3 局、一致率 ≥ 99%、没有 model_gap。
5. 本方案确认前，已进行的识别-03（手牌 / 徽章）、分数掉位修复、第三局收尾照常继续，不受影响。
