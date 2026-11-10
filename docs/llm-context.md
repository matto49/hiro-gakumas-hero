# 培育决策的 LLM 上下文与识别字段清单

决策会话 2026-10-30。代码：`src/hiro/state/`（状态 `produce.py`、社区知识 `knowledge.py`、上下文 `context.py`），
演示：`.venv/bin/python -m hiro.state.demo action_select|card_pick`。攻略提示词：`brain/prompts/hif_produce.md`、`idols/campus_hiro.md`。

## 原则

- 代码只算确定的事实（master、客户端公式、社区规则），不估长期价值；判断交给 LLM。
- 判断依据以社区攻略为准；我们自己的实机记录是随便打的，只作为状态数据。
- 上下文 = 公共局面（每个决策都带）+ 当前页面的预演。属性、星星都**对照目标给**：评定分档和「到下一档还差多少」、星星基准、选拔目标分。

## 识别需要提供的字段

字段名对齐客户端协议 `UserProduceProgress`（gakumas-player `mechanics/培育状态与操作（客户端协议）.md`）。

### 公共局面（所有考试外页面）

| ProduceState 字段 | 协议字段 | 画面位置（待识别会话确认） | 优先级 |
|---|---|---|---|
| `mode`、`step` | produceId、stepNumber | 日程条 / 页面标题 | 高 |
| `stamina`、`max_stamina` | stamina、maxStamina | 顶部体力 | 高 |
| `produce_point` | producePoint | 顶部 P 点 | 高 |
| `params`（Vo/Da/Vi） | vocal/dance/visual | 属性栏 | 高 |
| `star` | star | 星星图标数字 | 高 |
| `deck`（卡 ID、强化、是否定制） | ProduceCard | 牌组一览页 | 高（选卡页至少要牌组） |
| `drinks` | produceDrinkIds | 饮料栏 | 中 |
| `growth_permil` | *GrowthRatePermil | 准备页 / 属性详情；一局内基本不变，开局读一次即可 | 中 |
| `supports`（支援卡 ID、等级） | UserProduceProgressSupportCard | 开局支援卡编成页 / P手帳「編成」页（KAA 是自动编成，必须读）；一局只需读一次 | **高**：决定主属性，缺了课程会上错属性（2026-10-31 09:44 局第 2 天上了 Vo SP，应为 Da SP） |
| `main_attr` | —（运行配置） | 不识别；`ProduceDeciders(main_attr=...)` / 运行参数指定，优先于 `supports` | 识别到位前的手动兜底 |
| `star_permil` | starPermil | 由亲爱度推出（37 级 = 1500），可配置 | 低 |
| `wappen_left` | produceItems 触发次数 | P 道具详情；也可由获得卡的次数推算 | 中 |
| `items` | produceItems | P 道具栏 | 低 |
| `param_cap` | *AdditionalLimit | 由 H.I.F 加成等级推出，可配置 | 低 |

### 页面专属

| 页面 | 需要的字段 |
|---|---|
| 选行动 | 当天可选的行动；课程的主属性对应的副属性（日程页开局可见）；当天是否 SP |
| 三选一（选卡） | 候选卡 ID 和强化等级；方式是获得还是セレクトチェンジ；禁用、刷新剩余次数 |
| 相談 | 货架（卡 / 饮料 ID、强化、价格、是否 SALE）；强化、删除价格 |
| 授業 / 外出 | 选项文本（代价档位）；之后的候选同三选一 |
| 选拔考试结算 | 分数、获得的属性和星星（用来校准社区公式） |

## 实现现状（2026-10-31，lead 按 develop 整理）

决策器：`hiro.brain.deciders.ProduceDeciders`（GameState → Decision）覆盖 选行动 / 三选一 / 选饮料 / 相談 / 授業 / 外出 / 轮间 / P 道具；
考试 `hiro.brain.battle.BattleBrain`。提示词版本：`exam.md` 1.2、`produce_card_pick.md` 1.1、`idols/campus_hiro.md` 1.2。

| 字段 | 现在从哪来 | 状态 |
|---|---|---|
| mode / step / stamina / max_stamina / produce_point / params / growth_permil / star / param_cap | 选行动页识别（`read_action_select`） | 有；Da 曾读成 73，靠 state-events 的不变量兜 |
| supports / main_attr | 开局确认页 `PRODUCE_START_CONFIRM`（识别 b750299）；`run_hif --main-attr` 兜底 | 有（一局一次） |
| deck | 牌组一览页 `DECK_LIST`（识别 61c37ab，按需调用） | 读取器有，主循环尚未调用；按 `docs/state-events.md` 由事件维护 + 开考前复核 |
| drinks | 考试内饮料栏（`read_drink_slots`）；培育中无 | 培育中缺，按事件维护 |
| rerolls_left / excludes_left | 三选一页角标 | 有；未观测时标「未识别」 |
| choices（授業 / 外出） | 结构化 `{index, text, cost_hp, cost_p, trouble, selected, center}` + effect | 有 |
| shelf / services（相談） | 识别 d780108 / a49fdf7 | 有；执行器 `produce.shop.execute_shop` 已接 |
| interval（轮间） | 无（本战才有，未采样） | 缺 |
| pitems（背包 / 定制） | 识别颜色 + lead 按毛绒颜色认种类 | 背包、第一段定制有；第二段（装饰）待样本 |
| 考试：hand[].badge_score / pitem_charges / score | 识别-03、识别-01 39801b7 | 有；score 掉位已修 |
| wappen_left / star_permil / items | 无 | 提示词按「未识别」处理（1cf1cb8） |

未识别字段一律经 `GameState.unobserved_produce_fields()` 在上下文里标「未识别（不是 0）」，不按默认值当事实。

## 下一步

1. 状态推算与识别互验（`docs/state-events.md`，待用户确认）：牌组 / 饮料 / 属性 / P 点由事件维护，识别互验。
2. 选卡收紧到单一打法（决策，等牌组）。
3. 轮间 interval 识别（本战实机采样）。

## 商店字段（决策会话提案 2026-10-31，待识别 / lead 确认）

决策器 `ProduceDeciders.consult / interval` 按下面的 `GameState.page_fields` 格式读；字段对齐协议
`UserProduceProgressShop` / `UserProduceProgressInterval`。价格一律是**画面上的实付价**（已含 SALE 与折扣），读不到的项给 `null`。
P 点、体力、饮料栏、牌组仍从 `GameState.produce` 取（`produce_point`、`stamina`、`max_stamina`、`drinks`、`deck`）。

### CONSULT（相談）

```json
{
  "shelf": [
    {"index": 0, "kind": "card", "card_id": "p_card-…", "upgrade": 1, "price": 105, "sale": false, "purchased": false},
    {"index": 4, "kind": "drink", "drink_id": "pdrink_…", "price": 52, "sale": true, "purchased": false}
  ],
  "services": {"delete": {"price": 100, "available": true}, "upgrade": {"price": 100, "available": true}},
  "rerolls_left": 1
}
```

- `index`：货架位置（协议 positionNumber 的顺序，从左上起）；`kind` ∈ card / drink。
- 输出动作：买货架商品 `ShopBuy{index}`；删卡 `ShopBuy{service: "delete", card_index}`；强化 `ShopUpgradeCard{card_index}`；离开 `ShopEnd`。
  `card_index` 是 `produce.deck` 的下标（= 协议 deckProduceCardIndex，牌组一览的顺序），操作层点服务后在选卡画面按它选。

### HIF_ROUND_INTERVAL（轮间）

```json
{
  "interval": {
    "cards":  [{"index": 0, "card_id": "p_card-…", "upgrade": 1, "price": 80, "purchased": false}],
    "changes": [{"index": 4, "card_id": "p_card-…", "upgrade": 0, "price": 50, "purchased": false}],
    "drinks": [{"index": 6, "drink_id": "pdrink_…", "price": 100, "purchased": false}],
    "recover": {"available": true, "price_per_2hp": 10},
    "upgrade": {"price": 100, "available": true},
    "refresh": {"price": 10}
  }
}
```

- 画面构成按社区：换卡购买 4 格、セレクトチェンジ 2 格、饮料 2 格，下方强化 / 定制 / 回复。
- 输出动作：买 `IntervalBuy{index}`；回复体力 `IntervalBuy{service: "recover"}`（操作层点回复，按画面能回的量；每 2 体力 10P）；
  强化 `IntervalUpgradeCard{card_index}`；结束 `IntervalEnd`。セレクトチェンジ（`changes`）、定制、刷新暂不决策。
- `dialog`：盖在轮间上的确认框（没有为 null）。刷新后的「リフレッシュ確認」是 `{"kind": "refresh_confirm", "confirm": {"p_before": 335, "p_after": 325, "cost": 10, "jewel": false, "cancel": [x, y], "ok": [x, y]}}`；
  它挂着时 `interval` 为 null，但仍在轮间（不是已离开）。

### PDRINK_MAX（饮料满了）

决策器 `ProduceDeciders.drink_overflow` 读：

```json
{"held": [{"index": 0, "drink_id": "pdrink_…", "name": "リカバリドリンク"}], "incoming": {"drink_id": "pdrink_02-3-010", "name": "特製ハツボシエキス"}}
```

- 一次可能来两瓶新饮料：`incomings` 是全部新饮料（从上到下），`incoming` 只是最下面那瓶；`select_left` 是「あと N個選択」。
- `held` 的行序与底栏饮料顺序一致（第三十三局两次核对）。列表会滚动：`complete=False` 时下面还有没看到的已有饮料，
  最后一行常被视口切掉（D41）——这一行带 `partial: true` 与 `candidates`（按露出的效果说明开头匹配的 drink_id，可能多个）；
  只剩一个候选时已填 `drink_id`（`method: "effect_prefix"`）。多个候选时用 `produce.drinks[index]` 核对；仍认不出的瓶**不要当最差的丢**，
  应先滚动列表看全再决策（第三十三局 18:18 因此执行失败，KAA 默认「残す」把新来的 特製ハツボシエキス 丢了）。
- `held` 缺失时用 `produce.drinks`。输出 `DiscardDrink{discard: [index], receive: true}`（丢掉第 index 瓶再收下新的）
  或 `DiscardDrink{discard: [], receive: false}`（不收）。按社区优先表（`knowledge.DRINK_PRIORITY`）比较，表外饮料最差。

### SPECIAL_GUIDANCE（特別指導）

决策器 `ProduceDeciders.special_guidance` 读：

```json
{"cards": [{"index": 3, "card_id": "p_card-02-act-3_039", "upgrade": 1,
            "customizations": [{"id": "p_card_custom-100-g_effect-lesson_depend_block_add-1000", "price": 100, "available": true}]}]}
```

- `index` 是牌组下标（协议 deckProduceCardIndex），`customizations[].id` 是 master `ProduceCardCustomize.id`，`price` 是画面实付价。
- 输出 `CustomizeCard{card_index, card_id, customize_id, item_index}`（一次一项，执行后重新观测再调用）或 `CustomizeEnd`。
  `item_index` 是定制项在画面上从上到下的序号（游戏执行 `special_guidance.customize(card_index, item_index)` 用它）：
  page_fields 的 `customizations[]` 可直接带 `item_index`；不带时取它在列表里的位置（识别应按画面顺序给出）。
  master 推出时按 `produceCardCustomizeIds` 的顺序，**假设与画面顺序一致，尚未用实机截图核对**。
  优先顺序见 `knowledge.CUSTOMIZE_PRIORITY`（届いて！ パラメータ+ > せのびの魔法 元気 > デイドリーミング 持续 > 紙吹雪 パラメータ+ > ワクワク 开局入手 > えいえいおー やる気+ …）。
- `cards` 缺失且牌组已知时，按 master 定制项与基础价推出（customize_id 后附 grow effect，执行器以画面为准）。

### FORMATION（开局编成）

用户 2026-10-31：记忆和支援卡不用 KAA 自动编成，改成显式配置，并由决策层选。`ProduceDeciders.formation`（`brain/formation.py`）从候选编成里选第几号：

```json
{"support_sets": [{"index": 2, "cards": [{"support_card_id": "s_card-…", "level": 60, "limit_rank": 4}]}],
 "memory_sets":  [{"index": 1, "memories": [{"card_id": "p_card-02-act-3_039", "upgrade": 1, "customizations": ["p_card_custom-100-g_effect-lesson_depend_block_add-1000"]}]}]}
```

- 来源优先级：page_fields（识别读编成页）> 参数 > `conf/formations.json`（识别到位前由用户手写游戏里预存的编成内容，格式同上）。
- 支援卡：主属性 = 张数最多的属性，比较「主属性张数（封顶 4）→ SP 发生率张数 → SP 结束奖励 → 成长率 → 等级」[seesaa][もりし][もなみ]；
  技能用 `gakumas_produce.supports.compile_card` 从 master 编译，`limit_rank` 缺省时取等级能达到的突破段。
- 记忆：`knowledge.MEMORY_WANT`（决策-07 按 game8：デイドリーミング 5 > 紙吹雪 4 > 仕切り直し / アイドル魂 3 > 届いて！ / せのび / ワクワク / アイドル宣言 2，带推荐定制 +1）。
- 输出 `SelectFormation{support_set, memory_set, main_attr}`；`main_attr` 同时写进决策器，之后选行动用它（不再需要 `--main-attr`）。

### 本战得分核心（决策-07，用户 2026-10-31 批准）

- knowledge：`YARUKI_CARDS[*].role`（finisher / delayed / sustained / loop / compress / extend / cash / motivation / genki / hp / item）、`CORE_ROLES`、
  `CONFLICT_CARDS`（ハイテンション）、`OFF_PLAN_CARDS`（好印象系）、`is_conflict`、`UPGRADE_ORDER`、`protected_card_ids()`（执行侧换卡保护名单，含 protect 标记的 がむしゃら）。
  デイドリーミング 升为必拿（本战持续核心）。
- 选卡：缺核心时禁用 / 刷新先换掉冲突牌；候选全是冲突牌时禁用或刷新；兜底和 LLM 都不拿冲突牌（有别的候选时）。
- 相談：删眠気 → 买缺的核心卡 → 删冲突牌（本战第 6 天也删）→（本战结束）→ 强化（UPGRADE_ORDER）→ 优先饮料 → S → A → 牌组 >22 张时删基础卡。
- 特別指導：CUSTOMIZE_PRIORITY 第 2 位是 デイドリーミング 持续定制。
- 选拔回忆验收：`qualify_memory_profile` 统计核心角色与冲突牌，缺核心或带冲突牌时提醒「建议重做选拔」。

### 三选一刷新 / 禁用预算（决策-09）

第 6 局选拔第 1 天两页用掉刷新 6 次、禁用 3 次。依据：次数整局不回复，剩余随选拔回忆带进本战 [wiki]；第 5 天外出的确定强化卡「再抽選を使い切ってでも」拿主力火力牌 [もなみ]。

- `_pick_budget`：候选全带 +（确定强化页）时可以用光；其余每页刷新、禁用各最多 1 次，且要留预留：本战 1 次，选拔第 5 天前再多留 1 次；次数未识别时每页最多 1 次。LLM 输入里有 `选卡.本页预算`，超预算的 LLM 刷新 / 禁用会退回兜底。
- 候选里有 S / A 级卡（必拿、protect 的 がむしゃら 按 A 算）时不刷新；只有一张高价值、其余都是社区没提到的卡时直接拿。
- 刷新后候选完全没变（刷新没生效）：本页不再刷新。同一页次数从 >0 突然读成 0、而我们没用过：等一帧再定（只等一次）。
- 记忆编成带进来的卡（formation 选中的记忆）计入已拿，不再当「必拿还缺」去找。

### 事件页与兜底（决策-08：所有页面都由我们的逻辑处理，不落到 KAA 默认的 commit(0)）

- `SKILL_CARD_REMOVAL`（删卡）：`page_fields.cards`（识别卡格，同换卡阶段 2）→ `RemoveCard{card_id, index, upgrade}`。
  删除顺序：眠気等トラブル > 冲突牌（ハイテンション）> 好印象系 > 基础卡 > card_value 最低。`knowledge.is_protected`（保护名单 + 偶像固有卡 `-ido-`）永不删；
  卡格没读到 / 只剩保护卡 → `RemoveCard{cancel: True}`。
- `SKILL_CARD_ENHANCE`（强化）：未强化、有 + 版的卡里按 UPGRADE_ORDER > 社区评级 > card_value，冲突牌最后 → `EnhanceCard{card_id, index, upgrade}`；没有可强化的 → cancel。
- `PDRINK_MAX_CONFIRM`（「報酬スキップ」确认）：上一次 PDRINK_MAX 决定「不收新饮料」才 `Dialog{kind: "reward_skip", confirm: True}`，否则 `confirm: False`（一次 PDRINK_MAX 对应一次确认）。
- 入口 `ProduceDeciders.decide(page, state, n_options=…, can_skip=…)`：决策器抛异常、序号越界、不能跳过的页输出了跳过时，自动走本页社区模板兜底
  （`source="fallback"`、带 warnings）：授業 → STUDY_LAYOUT 的本人档；外出 → 第 5 天眠気档 / P≥150 付费档 / 免费档；饮料 → DRINK_PRIORITY > 本计划 > 稀有度；
  三选一 → 社区评级；P 道具 → 背包颜色 黄 > 緑 > 赤；删卡 / 强化 → 取消。没有模板可用时输出 `Wait`（主循环记 recover），**不选第 0 项**。

### SKILL_CARD_CHANGE_1 / _2（授業セレクトチェンジ 换卡）

第 4 局本战两次授業选了セレクトチェンジ档，KAA 固定换第 0 张，换走了 デイドリーミング、がむしゃら（reports/analysis/q4-daydreaming-missing-exec.md）。

- 阶段 1 `ProduceDeciders.change_pick`：读 `page_fields.candidates`（格式同 SELECT_CARD：`[{index, card_id, upgrade}]`），输出 `ChangeCard{stage: 1, pick_index}`。牌组已知、且候选都不比牌组里最差的可换出卡强时，输出 `ChangeCard{cancel: True}`。
- 阶段 2 `ProduceDeciders.change_remove`：读 `page_fields.cards`（识别 `read_card_dialog` 的卡格 `[{index, card_id, upgrade, center}]`），输出 `ChangeCard{stage: 2, remove_card_id, remove_index, remove_upgrade}`，执行侧按 card_id 点击。
  - 永不换出：核心 / 必拿（knowledge.CORE_CARDS：届いて！、せのびの魔法、デイドリーミング、きらきら紙吹雪）、トラブル（游戏不许选）、没认出的格。
  - 其余按 `card_value` 最低的换出：冲突卡（ハイテンション）0 < 基本卡 0.5 < 好印象系 1 < 未知 1.5 < 社区提到的其他卡 2 < B 3 < 循环 5 < A 6 < S 8，强化 +0.5。
  - 卡格没读到、只剩核心卡、或换入的卡不比换出的强时，输出取消（不盲换）。
- `hiro.brain.deciders.choose_card_to_change(deck, incoming=None) -> card_id | None`：阶段 2 换出哪张（deck 为卡格或 ProduceState.deck，incoming 为换入卡）；None = 不换。lead 接到 `change_stage2(card_id=)`。
- 授業：档位是セレクトチェンジ（文字 / 效果说明含「セレクトチェンジ」，或日程 选拔第 10、17 天 / 本战第 1、4 天 [wiki]）且 `change_controlled=False` 时：
  本战改选眠気档（换进冲突牌没有机会再删）；选拔照常选本人おすすめ効果档并提醒（执行侧保护核心卡，之后相談删冲突牌）。受控后都照常。

### QUALIFY_MEMORY（本战用哪份选拔回忆，goal H4）

本战开始前在选拔回忆列表里选一份（开不开本战仍由用户决定，每局先请示）。`ProduceDeciders.qualify_memory` 读 `page_fields["qualify_memories"]`：

```json
{"qualify_memories": [{"index": 0, "idol_card_id": "i_card-shro-3-007", "event_mode": false, "ichibanboshi": false,
                       "star": 520, "params": {"Vo": 1950, "Da": 2100, "Vi": 1100},
                       "deck": [{"card_id": "p_card-02-act-3_039", "upgrade": 1, "customized": true}], "pitems": ["…"]}]}
```

- 回忆保存选拔通过时的属性、星星、牌组、P 道具；P 点、饮料、再挑戦次数、体力不继承（本战满体力开始）[wiki]。
- 比较顺序（`brain/formation.qualify_memory_profile`）：
  1. 能用：同一张偶像卡；活动模式生成的回忆只能打活动模式本战 [wiki]。
  2. 必拿卡（届いて！、せのびの魔法）几张 [game8][もりし]。
  3. 一番星专属卡：一番星解放后才开始的回忆，本战开始时拿到专属传奇卡 [wiki]。
  4. 星星：本战审查线 R1 600 / R2 800，达标倍率 2–3 倍，未达约 1.85–2 倍 [wiki]。
  5. 属性过本战审查线几项（`knowledge.FINALS_PARAM_LINES`，wiki 只有部分阈值）：任一项 △ / × 会压低另外两项。
  6. S/A 张数 → 眠気少 → 牌组离 22 张近（轮间不足 22 张会混入基本卡 [game8]）→ 属性合计。
- 输出 `SelectQualifyMemory{index}`；列表没识别时 `Wait`。识别（回忆列表页、详情页的星星 / 属性 / 牌组）待做。
