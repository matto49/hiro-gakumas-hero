# 数据契约（hiro.contracts）

模块之间只经这些对象交换数据。代码：`src/hiro/contracts/`，schema 文件：`src/hiro/contracts/schemas/*.json`
（由 `python -m hiro.contracts.schemas` 从 dataclass 注解生成，测试核对一致）。契约版本 `hiro.contracts.VERSION`。

规则：字段只增不改名；改名必须同时改 `hiro.vision.observe` 与对应测试；每次变更在本文末尾「变更记录」加一行。
字段语义冲突时以客户端协议（`UserProduceProgress` / `Api.Produce*Request` / `ProduceStepType`）命名为准。

## 对象一览

| 契约 | 方向 | 产出者 | 消费者 |
|---|---|---|---|
| `Observation` | 识别 → 状态 | `hiro.vision.observe.observation()` | `hiro.state.reduce`（M-B） |
| `GameState` | 状态 → 决策 | `hiro.state.reduce` | 决策器、trace |
| `Action` | 决策 → 操作 | 决策器 | 操作层（查 layout / 识别位置） |
| `Decision` | 决策层输出 | `Decision.from_battle` / `from_pick` / 新决策器 | 主循环、trace、回放 |
| `Preview` | 预演层输出 | `Preview.from_slots`（考试）/ `hiro.state.context`（培育） | 决策器、trace |
| `ExecutionResult` | 操作 → 主循环 | 操作层 | 主循环、trace |
| `Step` | 一步的全部 | 主循环 `TraceWriter` | `hiro.state.replay`、报告 |

每个对象：`to_dict / to_json / from_dict / from_json / schema() / validate() / problems()`。
`from_dict` 遇到未知字段报错（防改名丢数据），缺字段用默认值。`None` 表示没读到，不是 0。
例外：1.3 插件架构加的 `by` / `trust` / `hidden` / `plugin` 是「可省略字段」（`field(…, metadata=hiro.contracts.base.OPTIONAL)`）——值是默认值（`None` / `{}`）时 `to_dict` 不写、schema 不要求出现，没装插件的记录和 1.2 一样。

## Observation

| 字段 | 含义 |
|---|---|
| `seq`, `t` | 帧序号、`time.time()` |
| `page` | `{name, area, confidence, stable_frames, source}`，`name` 为 `hiro.vision.scene` 场景名，`unknown` = 没认出；`source` = 哪一层判出的（见 `PageObs.source`，可空） |
| `fields` | 决策层字段名 → 值或 `None`；页面不提供的字段不出现。登记表 `FIELD_CATALOG` |
| `field_meta` | 字段 → `{method, confidence, note}`；`_error` 记识别异常 |
| `screenshot` | 截图相对路径 |
| `source` | `screen` / `save` / `dataset` / `replay` |
| `timings_ms` | `capture` / `classify` / `extract` |

字段来源（识别原始字段 → 契约字段的转换在 `hiro.vision.observe`）：

| 页面 | 契约字段 | 识别来源 |
|---|---|---|
| ACTION_SELECT | `mode` `step` `stamina` `max_stamina` `produce_point` `params` `growth_permil` `star` `param_cap` `options` `complete` `advice` | `read_action_select` → `produce_observation` |
| HIF_EXAM | `remaining_turns` `hp` `genki` `score` `hand[]`(CardView，含 `badge_score`) `drinks[]`(DrinkView) `motivation` `impression` `buffs` `deck_count`（恒为 None，见下）`pitem_charges` `attr` `multiplier_pct` `rank_line` `participants` `pitems` `unrecognized_cards` | `read_hif_exam` → `battle_observation` |
| SELECT_CARD | `candidates[] {slot, card_id, upgrade, name, recommended}` `recommended`（第一张带「おすすめ」的 index，可为 None）`rerolls_left` `excludes_left`（「あと N回」角标，读不到为 None） | `extract._select_card`；识别读不到 `recommended` 时 `produce/hif.py` 可用 KAA `fetch_recommend_card` 补（来源记 kaa）。`upgrade` 只能区分 0 / 1 |
| SELECT_DRINK | `candidates[] {slot, drink_id, name, score}` `held[] {index, drink_id, name, center, method}`（底栏已有饮料，index 为底栏格号，格式同 PDRINK_MAX.held）`full`（「Pドリンク所持上限」） | `read_drink_dialog`、`items.read_held_drinks` |
| HIF_EXAM_RESULT | `score`（玩家 Pt）`rank`（玩家在名单里的名次，名单滚动时为 None）`passed` `exam_no` `rank_line` `participants` `entries[] {name, score, passed, me}` | `read_hif_result` |

**识别尚未提供**（M-E 任务单）：`deck`、`drinks`（饮料栏）、`wappen_left`、`items`、
SP 标记与课程副属性、相談货架、授業 / 外出选项。

`field_meta` 按字段给出 `method`（`digits` 游戏字体比对 / `ocr` Apple Vision / `template` 图标·卡面·状态图标库 / `kaa` / `derived`）
与 `confidence`（识别分数，有的话），登记表 `hiro.vision.observe.FIELD_METHODS`。
HIF_EXAM 的 `hand[]`：卡面相关定卡，相关分 < 0.7 时 OCR 卡名栏交叉核对，不一致且卡名唯一对应一张卡时以卡名为准（`field_meta.hand.note` 记「卡名否决卡面」）；
两者都定不了的卡 `name="（未识别）"`、`card_id=None`。`hand[].badge_score` 是游戏在得分牌左上角显示的预计得分（无徽章或读不全为 None；
5 张手牌时前几张的末位可能压在下一张卡底下，此时给 None）。手牌不在 1–5 张的稳定布局（有卡被选中抬起 / 动画中）时整个 `hand` 为 None，
`field_meta.hand` 置信度 0 并写明原因。`pitem_charges` 是 P 道具图标右上角的剩余次数；H.I.F 考试画面没有山札张数，`deck_count` 恒为 None
（旧版 `deck_count` 读的其实是 `pitem_charges`）。
HIF_EXAM 的 `motivation` / `impression`：状态栏读到槽位且每个蓝底槽都认出图标时，没有该图标记 0（note 说明）；否则读不到为 None。

## GameState

| 字段 | 含义 |
|---|---|
| `mode` | `home` / `produce` / `contest` / `daily` / `unknown`，由 page 推出 |
| `page` | `{name, area, since_seq, stable, frames}` |
| `produce` | `hiro.state.produce.ProduceState` 的字段（协议名），允许部分 `None`；齐全时 `produce_state()` 给出对象 |
| `exam` | `BattleState.build` 的观测参数（与 HIF_EXAM 的 Observation.fields 同名） |
| `exam_session` | `{label, turn, last_remaining, brief_permil, brief_exam_no, lesson}`；`lesson` 只在初的练习课里有（见变更记录 2026-11-18） |
| `page_fields` | 只属于当前页面的字段（`options` `complete` `advice` `candidates` `recommended`），换页即清空 |
| `sources` | `"produce.stamina"` → `{origin, seq}`，`origin ∈ save > screen > infer` |
| `pending_page` | 防抖中的候选页面 |
| `conflicts` | 来源冲突日志 |

## Action

`{kind, args}`。`kind` 枚举（`ActionKind`）：

| kind | 协议 | args |
|---|---|---|
| `ProduceNext` | `ProduceNextRequest(stepType, selectNumber)` | `step_type`（`ProduceStepType` 名，如 `OpenLessonDanceSp` / `EventSchoolVocal` / `Shop` / `Present` / `Refresh` / `EventActivity`）、`option`（observe 选项名）、`attr`、`sp`、`select_number` |
| `ActivateEffect` | `ProduceActivateEffectRequest(pickIndexes)` | `pick_indexes[]` |
| `ExcludeCard` | `ProduceExcludeProduceCardRequest(pickIndex)` | `pick_index` |
| `Reroll` / `ShopBuy` / `ShopUpgradeCard` / `ShopEnd` / `EventSelect` / `PresentReceive` / `IntervalBuy` / `IntervalUpgradeCard` / `IntervalChangeCard` / `IntervalEnd` | 同名 `ProduceStep*Request` | 见 `action.py` 注释 |
| `PlayCard` / `UseDrink` / `Skip` | 考试内 | `slot` |
| `Continue` / `Wait` / `Recover` / `Noop` | 无（界面推进 / 故障恢复） | 自由 |

考试动作与 `hiro.brain.rules.Decision.action` 字符串互转：`Action.from_legal("play:2")`、`action.legal_key()`。
`special_guidance`（特別指導）在 3.4.1 `ProduceStepType` 枚举里没有可确认的对应项，`step_type` 暂为 `SpecialGuidance`。

## Decision

`action` `reason` `source`（rule / plan / sim / llm / fallback / recommended / first）`confidence` `candidates[]{action, preview, expected, note}`
`plan[]` `warnings[]` `line` `llm[]{adapter, model, seconds, raw, error, prompt_version, input_tokens, output_tokens}` `seconds` `decider`。
`Decision.from_battle(rules.Decision, calls=brain.calls[n:])`、`to_battle()`、`Decision.from_pick(PickDecision)`。

## Preview

`action` `immediate`（字段 → 值或 `[min, max]`）`expected` `spread` `vs_best` `vs_best_se` `turn_passed` `summary` `engine`（sim / formula）。
`Preview.from_slots(slot_previews(...))` 把 `hiro.sim.preview` 的结果按槽位转成契约。
考试预演带可观测字段（都可空）：`worlds`（这个动作实际算了几个世界）、`preview_ms`（这一步预演总耗时）、`worlds_planned` / `worlds_done`（计划 / 实际算完的世界数，截止时间到了会少于计划）、`eliminated`（没算或中途淘汰的原因，剪枝二期起用）。汇总：`python tools/preview_stats.py traces/<run>…`。

## ExecutionResult

`action` `ok` `taps[[x, y]]`（720x1280）`waited_s` `page_before` `page_after` `expected_transition` `retries` `error` `seconds`。

## Step / Trace

`traces/<run>/steps.jsonl` 每行一个 `Step{seq, t, observation, state, decision, previews, execution, notes, timings_ms}`，
截图在 `frames/`，`run.json` 为运行元数据。`hiro.contracts.trace.TraceWriter` 写，`read_steps` 读。

## 数据集样本

`PYTHONPATH=src python tools/contract_samples.py datasets/<名> [--reread]` 把采集 manifest 转成 Observation 样本并过 schema（输出 `reports/contracts/`）。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-31 | 契约 1.0 落地：Observation / GameState / Action / Decision / Preview / ExecutionResult / Step。`observe.observation()` 产出 Observation；`run_exam` 的决策记录附 `contract` 字段。 |
| 2026-10-31 | GameState 加 `page_fields`（页面局部字段，换页清空）。 |
| 2026-10-31 | M-B：`hiro.loop.reduce.reduce(state, obs)`（纯函数；瞬态页不改；IDLE 为弱证据与不合法跳变一样需连续 3 帧；save > screen；进考试重置 exam）、`hiro.loop.transitions`（转移图）、`hiro.loop.replay`（重放与抖动统计）。reduce 待 `hiro.state` 提交后迁回 `hiro.state.reduce`。 |
| 2026-10-31 | M-D：`hiro.brain.llm.LLMAdapter` 协议（`ask_json`），`ClaudeCLI` / `OpenAICompatible`（标准库 HTTP，OPENAI_BASE_URL / OPENAI_API_KEY）/ `MockLLM`；`from_spec("claude:sonnet" / "openai:<model>" / "mock")`，环境变量 `HIRO_LLM`；`LLMResult.record()` 带 adapter / model / token / prompt_version，`llm_trace_from_call` 直接读入 `LLMTrace`。提示词首行 `<!-- version: x.y -->`。`run_hif --llm <spec>`、`brain.replay --adapter <spec>`。 |
| 2026-10-31 | M-C：Step 加 `observations_before` / `observations_after`（一步内并入状态的全部观测，重放按此顺序）；HIF_EXAM 字段加 `exam_label`（qualify-1/2/3、r1、r2，来源 infer，进 `exam_session.label`）；登记过的字段读不到时 `fields` 里显式 `None`。 |
| 2026-10-31 | 数据确认会话核对后：HIF_EXAM 登记 `npc_scores`（对手分数列表）`my_rank_index`、`multiplier_permil`（千分比，与协议一致；`multiplier_pct` 保留为百分比旧字段）、`statuses[] {type, name, value, unit}`（`type` 为 `ProduceExamEffectType_*`，`unit` ∈ stack / percent / turns / none；`buffs` 保留旧格式）。`attr` 取值统一为 `Vo` / `Da` / `Vi`（协议命名，observe 负责转换）。`GameState.unobserved_produce_fields()` 列出未观测、会被默认值补上的 ProduceState 字段，决策须当作未知。SELECT_CARD.recommended 来源更正。 |
| 2026-10-31 | 识别-01：HIF_EXAM_RESULT 字段（`read_hif_result`）；SELECT_CARD 加 `rerolls_left` `excludes_left` 与候选 `recommended`；`field_meta` 细化到字段级；HIF_EXAM 兼容早期数据集的 `status` 键；`hiro.collect` 的 manifest 每行加 `observation`。 |
| 2026-10-31 | e826952：observe 产出 d1e05bc 登记的 HIF_EXAM 字段。`statuses[].type` 只给图标库 / game.db 能查到的 `ProduceExamEffectType_*`；H.I.F 新增的 15 种状态（examforecast、examstancelock* 等）与卡牌持续效果 `type=None`，`name` 用图标键或卡名。`unit`：「%」→ percent，「ターン」→ turns，「回」与无单位状态 → stack，卡牌常驻效果 → none。`multiplier_permil = multiplier_pct × 10`。 |
| 2026-10-31 | 契约 1.1（数据确认-01 第 1 条）：`Step.state` 改为**决策输入**（第一次执行校验开始时的状态，页面与 page_fields 都还在）；新增 `Step.state_after`（本步结束、并入 observations_after 之后的状态，与 state 相同时为 None）。1.0 的 trace 里 state 是执行后的状态。重放组末比对用 `state_after or state`。`samples` 加 `--force-reread`（图片在就全部重读）与 `--reclassify`（用当前分类器重判页面）。`RECOVER_KINDS` 加 `crash_restart`（run_hif 主循环异常后从当前画面续跑）。 |
| 2026-10-31 | STUDY / OUTING（授業 / おでかけ 三选一）：`choices[] {index, label, hp_cost, p_cost, trouble, selected, center, text, effect?}`、`selected`、`effect`、`complete`。`text` = 选项名｜体力-N｜Pポイント-N｜トラブル追加（眠気）｜效果，供决策器按文字判断眠気档 / 付费档；效果只有游戏当前高亮项可见（未点选的项不去点）。实机测试中授業默认高亮的是やる気档（第 2 项）。 |
| 2026-10-31 | 识别-01 第二轮：HIF_EXAM 加 `drink_slots[{slot, state, drink_id, asset, name, rect, center, available, score, margin, confidence}]`；新页面 SELECT_PITEM（`pitems` `selected` `confirm_enabled`）；手牌条目加 `plus`（卡名栏「+」个数，被下一张卡盖住时 None）。 |
| 2026-10-31 | 识别-03：HIF_EXAM `hand[]` 加 `badge_score`（预计得分徽章，CardView 新增可选字段，默认 None，旧 JSON 兼容）；卡面没把握时 OCR 卡名栏交叉核对、以卡名为准；手牌不在稳定布局时 `hand=None`。新增 `pitem_charges`（P 道具剩余次数）；`deck_count` 改为恒 None（旧值其实是 P 道具次数，画面上没有山札张数）。考试截图兜底在识别与 KAA 手牌张数相同时改用识别手牌（不再被 KAA 的 HOG 误认覆盖）。 |
| 2026-10-31 | 识别-01：新页面 DECK_LIST（所持スキルカード一览，`hiro.vision.deck`）：`deck`（ProduceState.deck 格式 `[{card_id, upgrade, customized}]`，一览顺序 = deckProduceCardIndex；只在看到全部张数、每张认出且强化次数确定时给出，否则 None）、`deck_size`（标签上的总张数）、`cards[] {index, row, col, card_id, name, upgrade, customized, center}`（本屏 / 拼接后可见的卡）、`complete`、`problems`（deck 为 None 的原因）。一屏装不下时由调用方滑到底再读，`merge_deck_pages(top, bottom)` 按行号拼接。`upgrade` 只分 0 / 1（++ 无样本）。按需调用，不进固定流程。 |
| 2026-10-31 | 识别-01：HIF_EXAM `score` 的 `field_meta` 带置信度与说明（字体比对 1.0；OCR 兜底与字体一致 0.8–0.9；OCR 补位 0.6；对不上给 None 并在 note 说明）。识别原始结果 `read_ranking` 加 `my_score_read {method, confidence, note}`。 |
| 2026-10-31 | 识别-01 事件页面：SKILL_CARD_REMOVAL / SKILL_CARD_ENHANCE / SKILL_CARD_CHANGE_2 登记字段 `cards[]`、`selected`（橙色角框所在卡格 index）、`selected_card {card_id, name, upgrade}`（角框与详情栏卡名互验，对不上为 None）、`agree`、`confirm`（确认弹窗 `{kind: exchange/delete, item, p_before, p_after, price, sale}`）。另有按需函数 `hiro.vision.deck.read_confirm_dialog`（相談交換確認 / スキルカード削除）、`read_acquire_popup`（获得演出名称栏 → card / drink / pitem，卡再用缩略图核对），尚无页面名，待 Event 契约（docs/state-events.md）定后接入。 |
| 2026-10-31 | 识别-02：HIF_EXAM_RESULT 加 `retry {left, free}`（左下「本日あと1回無料」→ 1 / True，「あと2回」→ 2 / False；有「再挑戦」按钮但角标读不出为 None 值；没有按钮为 None）。名单入场动画中玩家行还没有「突破 / 敗退」时仍读出 `score`，`passed` 按该行在突破线横幅之上 / 之下推出，`field_meta.passed.method = derived`（附 note）；`entries[].passed` 可为 None。 |
| 2026-10-31 | 决策：ActionKind 加 `DiscardDrink`（`ProduceDiscardDrinkRequest(drinkIndexes)`），args `{discard: [持有饮料下标], receive: bool}`，用于饮料满了（PDRINK_MAX）的取舍；页面操作由游戏执行实现。 |
| 2026-10-31 | 决策：ActionKind 加 `CustomizeCard`（`ProduceStepCustomizeSelectRequest`，args `{card_index, card_id, customize_id}`）与 `CustomizeEnd`，用于特別指導；页面操作由游戏执行实现。 |
| 2026-10-31 | 决策：`CustomizeCard` args 加 `item_index`（定制项在画面上从上到下的序号，对齐游戏执行 `special_guidance.customize(card_index, item_index)`）。 |
| 2026-10-31 | 识别-01 页面地图（`docs/pages-hif.md`，`hiro.vision.pages`）：新页面 DIALOG（`kind` `title`）、ACQUIRE、STAR_GAIN、HIF_EXAM_BRIEFING、PITEM_CUSTOMIZE、PITEM_CUSTOMIZE_DONE、MENU、UPGRADE_RESULT、TRANSITION（`kind`），只在 KAA 给 None / IDLE 时判定；H.I.F 版 PRODUCE_END。PDRINK_MAX 字段 `held[{index, drink_id, name, center}]` `incoming` `select_left`。培育页饮料栏（比考试页低 13 像素）`read_drink_slots` 两行都比。 |
| 2026-10-31 | 识别-01（第四局复盘）：新页面 GIFT（差し入れ礼物箱，`bag`）；PDRINK_MAX 改按效果说明认饮料，held 加 `y` `method`，新增 `complete`；签名判出 SELECT_CARD / SELECT_DRINK 时再按 KAA 提示文字 / fetch_cards 区分（也认 SKILL_CARD_CHANGE_1）。 |
| 2026-10-31 | 识别-01：STAR_GAIN 加 `star_shown`（画面上的累计星数，可能是滚动动画中间值，只供核对）；`star` 改为恒 None（星数以选行动页为准）；gain 只取金字塔左侧气泡。相談 `complete=False` 时 `shelf` 为 None。 |
| 2026-10-31 | 决策：ActionKind 加 `SelectFormation`，args `{support_set, memory_set, main_attr}`（KAA prepare 的编成编号），由 `ProduceDeciders.formation` 按社区规则从识别出的候选编成里选。 |
| 2026-10-31 | 识别-05：本战考试画面（顶部 1st / 2nd / 3rd 名次条，没有突破线）判 HIF_EXAM；HIF_EXAM 加 `finals`（是否本战）`finals_round`（1 / 2，按 NPC 最高分 ≥ 50 万为 R2）；本战时 score / my_rank_index / npc_scores 由 `read_finals_ranking` 给出，rank_line / participants 为 None。新页面 HIF_FINALS_RANKING（「現在順位」：`entries[{rank, score, me}]` `next` `my_score` `rank` `finals_round`）。 |
| 2026-10-31 | 识别-06：read_hif_result 顶层加 `score`（= my_score）；HIF_FINALS_RANKING 只增 `my_gain` `final` `outcome` `passed`（敗退 → False，突破 / 優勝 → True），entries 可带 `gain`（R2 后最终页「总分 +本轮加分」）；HIF_ROUND_INTERVAL 登记 `stamina` `max_stamina` `produce_point` `interval`（货架，暂 None）`complete`。 |
| 2026-10-31 | 决策：ActionKind 加 `SelectQualifyMemory`，args `{index}`（本战用哪份选拔回忆，列表顺序 0 起），由 `ProduceDeciders.qualify_memory` 从 `page_fields["qualify_memories"]` 选（格式见 docs/llm-context.md「QUALIFY_MEMORY」）。 |
| 2026-10-31 | 识别-01：新页面 HIF_FINALS_START（本战開始確認：`selected_memory{idol, star, params, growth_pct, deck, pitems}`、`qualify_memories`——回忆列表 / 详情页与牌组待高清样本，暂为 None）。 |
| 2026-10-31 | 识别-01：HIF_ROUND_INTERVAL 的 `interval` 货架落地（`hiro.vision.shop.read_interval_shelf`）：cards（0,1,4,5）/ changes（2,3）/ drinks（6,7）各 {index, card_id|drink_id, name, upgrade, price, purchased, center}，upgrade {price, available, left}、customize {left}（新增）、recover {available, price_per_2hp=10}、refresh {price}；货架没完整出现时 interval=None。stamina / max_stamina / produce_point 改为 OCR / 游戏字体 / ddddocr 投票。 |
| 2026-10-31 | 决策：ActionKind 加 `ChangeCard`（授業セレクトチェンジ）：阶段 1 `{stage: 1, pick_index}`，阶段 2 `{stage: 2, remove_card_id, remove_index, remove_upgrade}`，`{cancel: True}` 为取消；`ProduceDeciders` 页面 SKILL_CARD_CHANGE_1 / _2（docs/llm-context.md）。 |
| 2026-10-31 | 识别-07：SKILL_CARD_CHANGE_1 登记 `candidates[{index, slot, card_id, name, upgrade, recommended, center}]` `recommended` `rerolls_left` `excludes_left` `complete`（格式同 SELECT_CARD，KAA 参考图核对，待实机）；SKILL_CARD_ENHANCE 卡格区域单独给（y 640–1200，删卡页区域只读到 8 格）。 |
| 2026-10-31 | 决策-08：ActionKind 加 `RemoveCard` / `EnhanceCard`（`{card_id, index, upgrade}` 或 `{cancel: True}`）、`Dialog`（`{kind, confirm}`，如 `reward_skip`）；`ProduceDeciders` 页面 SKILL_CARD_REMOVAL / SKILL_CARD_ENHANCE / PDRINK_MAX_CONFIRM；`decide(page, state, n_options=)` 失败时走社区模板兜底，不选第 0 项（docs/llm-context.md「事件页与兜底」）。 |
| 2026-11-01 | 识别-01（H1 §3.1）：`hiro.vision.events.observed_events(obs, prev)` 把事件页面转成 Event（字段名按 docs/state-events.md §2.1，另带 `confidence` `note`）；`deck.deck_diff` 比较推算牌组与牌组页；DIALOG 加 `confirm`（exchange / delete 时）；UPGRADE_RESULT 加 `upgrade`（强化后）；SELECT_DRINK 加 `held` `full`；SKILL_CARD_CHANGE_1 去掉效果图标误检出的重复候选。 |
| 2026-11-01 | 识别-01：ACTION_SELECT / CONSULT 加 `drinks`（底栏饮料，ProduceState.drinks 格式，有一格认不出时 None）`drink_capacity`（4 格都是饮料或空槽时 = 4）；HIF_EXAM `multiplier_pct` / `multiplier_permil` 改为 OCR 与 ddddocr 两读一致才给（不一致时取该属性上次一致的读数，否则 None，field_meta 写两次读数）；`hiro.vision.events` 改为返回 `hiro.contracts.Event`。 |
| 2026-11-01 | 识别-01：新页面 PRODUCE_LOG（行动记录，`close`）；`deck.find_deck_button` 改认礼盒图标（KAA OwnedCardsButton），文档图标是行动记录。 |
| 2026-11-01 | 决策：`Decision.source` 加 `expert`（考试预演专家出牌，`run_hif --exam-policy expert`；hiro.produce.exam_policy，reports/analysis/expert-preview-pilot.md）。 |
| 2026-11-01 | 识别-01（用户指示：本战每轮打完的「現在順位」弹窗读所有人分数）：HIF_FINALS_RANKING 加 `kind`（standings / round_result / final）`score`（= my_score，对齐 HIF_EXAM_RESULT）`round_score`（本轮得分）；entries 加 `name`（按主数据角色名纠正）`round_score`；名次改读左侧大字；加分逗号读成「、」也认。 |
| 2026-11-01 | 识别-01（第九局复盘）：新页面 SPECIAL_GUIDANCE（`cards` `selected` `selected_card` `agree` `scrollable` `complete`；定制项暂按主数据）；选卡对话框区域常量 `deck.REMOVAL_GRID` / `ENHANCE_GRID`，`dialog_scroll` / `merge_dialog_screens`（相談强化 / 删除滚动读整副）。 |
| 2026-11-01 | 识别-01（q10 特別指導实机帧）：SPECIAL_GUIDANCE 加 `view` `menu`{items[{item_index, id, name, price, left, maxed, available, selected, center}], total_left} `cards_left` `at_top`，cards 改为实测网格（guidance 几何）并带 available（压暗）；新页面 CUSTOMIZE_RESULT（`name` `card_id` `upgrade`，事件 CARD_CUSTOMIZE）；DIALOG 认「カスタマイズ実行確認」「特別指導終了確認」（kind customize_confirm / guidance_end）。 |
| 2026-11-01 | 识别-01（main10 相談強化）：选卡对话框 cards 加 `dimmed`（压暗；强化对话框里 = 已满级不能再强化，特別指導里 = 不能定制），压暗格先提亮再判「+」/ 定制；SKILL_CARD_ENHANCE 加 `upgradable`（详情框「強化できません / これ以上強化不可能」→ False）。 |
| 2026-11-01 | 识别-01（q11 特別指導）：SPECIAL_GUIDANCE 加 `produce_point` `stamina` `max_stamina`（头部 P / 体力，与选行动页同位置，`pages.read_hud_resources` 三票；轮间页同用）。 |
| 2026-11-02 | 识别-01（协调：每局 4–7 次认不出 30 秒）：新页面 DELETE_RESULT（`cards` `text`，「〜を削除しました」，事件 CARD_DELETE confirmed=True，覆盖考试后自动处理基础卡）；`hiro.vision.quick.quick_page` 快速判页接入主循环（HiroController._quick_page）。 |
| 2026-11-02 | 识别-01（第十七局选拔认不出逐处核对）：快速判页补 LOADING（右下「NOW LOADING」）、TRANSITION `kind` flash（不点）/ custom_cutin / exam_bonus（点 (12,12)）；PDRINK_MAX 加 `incomings`（全部新饮料，一次可能两瓶；`incoming` 仍是最下面那瓶），`select_left` 改整屏读（原来只看 y<1150，长弹窗一直 None）。 |
| 2026-11-02 | 识别-01（用户：考试开头的倍率弹窗最准）：考前说明页由快速判页在「タップして次へ」出来后读成 HIF_EXAM_BRIEFING（加 `finals`，本战「優勝条件」也认），演出中不读不点；brief_permil 读到几个属性记几个；不变量 attr_multiplier 差 ≤ 1% 算一致、note 写差值；新规则 model_permil（存档 / finals_context 倍率 vs 说明页，考试第一帧，差 > 1% 为 model_gap，一致的也记）；审计加「读数 / 模型不一致」（model_mismatch）。 |
| 2026-11-02 | `Preview` 加可观测字段 `worlds` / `preview_ms` / `worlds_planned` / `worlds_done` / `eliminated`（可空、只增不改，版本号不变；协调预演加速一期第 5 项）。汇总脚本 `tools/preview_stats.py`。 |
| 2026-11-02 | 识别-01（第十八局本战轮间「リフレッシュ確認」挂 30 秒）：DIALOG kind `refresh_confirm`，`confirm` = {p_before, p_after, cost, jewel, cancel, ok}（`pages.read_refresh_confirm`）；HIF_ROUND_INTERVAL 加 `dialog`（盖在轮间上的确认框，`pages.overlay_dialog`，没有为 None）；快速判页认出后点「キャンセル」（刷新动作自己按更新，落到主循环的是没有刷新决策时挂着的框）。 |
| 2026-11-03 | 识别-01（决策 da6c8e5 牌组读全）：`deck.merge_deck_list(screens)` 一览页多屏 → DECK_LIST 字段（deck 只在恰好拼出标签张数且全认出时给，重叠拼不上退回首屏 + 末屏），本战开局写 produce.deck；`merge_dialog_screens` 的 cards 加 `dimmed` `screen`；SKILL_CARD_REMOVAL / ENHANCE / CHANGE_2 加 `deck_complete` `deck_screens`（执行侧滑动读整副后给，cards 换成整副、带 `deck_index` `screen`）；轮间 changes 的 upgrade 不再因橙色插画给 None。 |
| 2026-11-13 | 去 KAA 第 0 步：`PageObs.source`（可空、只增不改，版本号不变）记哪一层判出的页面：主循环 `quick` / `kaa` / 我们的补判 `interval_title` / `exam_ocr` / `pixel` / `drink_ocr` / `pitem_ocr` / `idle_scene` / `produce_end_ocr` / `none`，`Session.observe` 自己分类时为 `classify`，钩子按上下文给页面时为 None。只记不用；`tools/page_sources.py` 按 trace 出基线表（KAA 出手次数）。 |
| 2026-11-14 | 去 KAA 第 1 步：判页 `hiro.vision.classify.classify(img) -> PageHit`（纯函数、不依赖 KAA），`PageObs.source` 新增取值 `template`（自有模板判页，接替 `kaa`）、`signature`、`page_ocr`、`start_ocr`、`deck_ocr`、`landscape`；新页面名 `INITIAL_CHOICE`（开局二选一，原来 KAA 判页时双击第一项后报 IDLE）、`LIVE`（横屏）。旧主循环经 `scene.classify_current` 用它，两个新名字映射回 `IDLE` / `unknown`。 |
| 2026-11-18 | 初课程：`ExamSession.lesson`（可空、只增不改）= `{kind, attr, nth, turns_total, clear_target, perfect_target, param_start, level_ids, exam_extra_turn, lesson_limit_up_score}`，`hiro.runtime.hajime.LessonInfo` 从练习课画面读、按主数据 `ProduceStepLessonLevel` 对照；读不到 / 对不上为 None。考试（非课程）进场时清成 None。 |
| 2026-11-25 | **1.3**（插件架构，`docs/ryugi/specs/2026-11-25-strategy-plugins-design.md`）：`Observation.source` 新增 `plugin`，加 `by`（插件 id@版本）、`trust`（插件声明的档位 save / screen / infer，reduce 当来源用）、`hidden`（画面上看不到的字段）；`GameState.hidden`、`Source.by`；`Decision.SOURCES` 新增 `plugin`，加 `Decision.plugin`。只增字段，旧 trace 照常读。 |
| 2026-11-26 | 1.3 的 `by` / `trust` / `hidden` / `plugin` 改成可省略字段（默认值时不写，schema 的 required 里去掉）；`GameState.hidden` 换页清空（和 `page_fields` 一样只管当前页）。读旧 trace 不受影响。 |
