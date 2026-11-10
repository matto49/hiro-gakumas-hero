# 全局游戏状态机：观测 → 状态 → 预演

目标：任何时刻都有一份全局状态 JSON，说清「在做什么（mode）、在哪个页面（page）、这个 mode 已知的一切」。
识别结果只经过一个稳定的更新函数进入状态；决策层只读状态，并能对每个候选动作拿到「执行后会怎样」的预演。

## 1. 三个对象

```
Observation   一帧截图（+ 可选的本地存档快照）的识别结果。无状态、可离线重放。
GameState     全局状态。只由 reduce() 修改。
Preview       对 (GameState, Action) 的预演：结果状态的差异 + 奖励/惩罚信号。
```

```
截图 ─┐
      ├─ observe() ─→ Observation ─→ reduce(state, obs) ─→ GameState ─→ 决策层
存档 ─┘                                                        │
                                              legal_actions(state)
                                              preview(state, action) ─→ Preview
```

## 1.5 权威结构：客户端协议

`GameState.produce` 的字段命名与含义对齐客户端的 `Transaction.UserProduceProgress` 及子表（服务端下发的培育进度），
动作对齐 `Api.Produce*Request`（例如 `ProduceNextRequest(stepType, selectNumber)`、`ProduceActivateEffectRequest(pickIndexes)`、
`ProduceExcludeProduceCardRequest(pickIndex)`）。完整字段、枚举、观测来源见 gakumas-player
`mechanics/培育状态与操作（客户端协议）.md` 与 `native/recovered-3.4.1/produce-progress-schema.json`。
`page` 以 `UserProduceProgress.status`（ProduceProgressStatus）× `stepType`（ProduceStepType）为骨架。

## 2. GameState 结构

培育部分直接使用决策会话的 `hiro.state.produce.ProduceState`（字段名即协议名，见 1.5），本文件不再另起字段名。
GameState 只在外面包一层：当前做什么、在哪个页面、字段来源。

```jsonc
{
  "mode": "produce",              // home | produce | contest | daily | unknown
  "page": {"name": "ACTION_SELECT", "since_seq": 1764, "stable": true},
  "produce": { ...ProduceState... },          // mode=produce 时存在
  "exam": { ...BattleState 观测参数... },       // 考试中才有，见 hiro.vision.observe.battle_observation
  "sources": {"produce.stamina": {"from": "screen", "seq": 1790}}  // 每个字段的来源与时间
}
```

### 识别 ↔ 决策的对接（hiro.vision.observe）

识别模块各自保留原始输出；`hiro.vision.observe` 是两边唯一的对接点，命名一律跟决策层走：

| 识别原始字段（read_action_select） | ProduceState 字段 | 转换 |
|---|---|---|
| `exam`「選抜試験1」+ `days_left` | `mode`、`step` | 选拔①②③位于日程第 7/13/20 位，step = 考试位置 − 天数；本战头部文字待采样 |
| `stamina` / `max_stamina` / `star` | 同名 | — |
| `p_points` | `produce_point` | 改名 |
| `params` {vo,da,vi} | `params` {Vo,Da,Vi} | 键名 |
| `growth_pct` 41.3 | `growth_permil` 1413 | 1000 + 百分比 × 10 |
| `param_caps` | `param_cap` | 三项一致时取其值 |
| `options[].kind` | 选项名对齐 gakumas_produce 日程 | open_lesson→training、consult→shop、present→gift、special_training→special_guidance；rest 不在日程里 |

考试：`battle_observation(read_hif_exam(img))` 直接作为 `BattleState.build(memory, profile, **obs)` 的参数，
手牌经 `card_version(card_id, upgrade)` 走决策层自己的 `card_view()`，饮料按图标查 `Drink` 走 `drink_view()`；
状态栏 やる気→`motivation`、好印象→`impression`，其余进 `buffs`。

**识别尚未提供、决策层已列为需要的字段**（按优先级）：选行动页的 SP 标记与课程副属性（日程页）、牌组 `deck`（牌组一览页）、
饮料栏 `drinks`、`wappen_left` / 刷新与排除次数、`items`（P 道具图标对应多个版本 ID，需结合获得记录）。

## 3. reduce() 的规则（稳定性来自这里）

实现：`hiro.loop.reduce.reduce(state, obs)`（契约对象见 `docs/contracts.md`；`hiro.state` 提交后迁回 `hiro.state.reduce`），转移图 `hiro.loop.transitions`，
重放 `python -m hiro.loop.replay <observations.jsonl | traces/<run>/steps.jsonl>`。

1. 纯函数：同样的状态 + 观测，结果永远一样；整份数据集可以重放，结果可做回归。
2. 观测不完整不清空：读不到（None）就保留旧值；`unknown`/转场帧不改 page。
3. 来源优先级：存档 > 截图识别 > 推断。低优先级不覆盖高优先级的新值；冲突记入日志，不静默吞掉。
4. 页面切换按允许的转移图校验（例：HIF_EXAM 之后只能是 HIF_EXAM / HIF_EXAM_RESULT / 弹窗）；不合法的跳变要连续 3 帧才接受，防抖。
   IDLE（在培育里但没认出具体页面；弹窗选中项时 KAA 也判 IDLE）是弱证据，同样要连续 3 帧才离开当前页面。
6. 只属于当前页面的字段（选项、候选、推荐、complete、老师建议、牌组页 cards / problems）进 `page_fields`，换页清空；进考试清空 `exam`，按 `remaining_turns` 变小计回合。
5. mode 由 page 推出（培育内页面 → produce，首页 → home …），不单独猜。

## 4. preview()：每类页面的预演

| 页面 | 动作 | 预演内容 | 依据 |
|---|---|---|---|
| 考试出牌 | 出牌 / 喝饮料 / 跳过 | 分数、元气、体力、状态的变化，对后续回合的影响 | `hiro.sim`（考试引擎 gakumas_exam），已能对存档局面预演 |
| 选行动 | 上课 / 外出 / 商店 / 休息 … | 三维、体力、P 点数变化；离考试天数 | 主数据 + `schedules/hif.json` |
| 三选一 | 选哪张卡 / 哪瓶饮料 | 牌组变化及牌组强度变化 | 模拟器跑牌组评估（`sim/bench`） |
| 商店 / 强化 / 删卡 | 买 / 强化 / 删 | P 点数与牌组变化 | 主数据 |

预演只用公开信息（不看山札顺序、未公开的回合属性）。

## 5. 信息源

- 截图识别：`hiro.vision`（场景、HUD、手牌、弹窗候选）。
- 本地考试存档：`hiro.sim.live`，H.I.F 选拔赛（produce-007）与本选（produce-008）都支持。客户端 3.5.1 写的存档仍可用同一密钥材料解密；
  3.5.1 选拔赛结束存档的日志对账 26/26 一致。存档只在考试中存在，内容是完整考试局面（手牌、牌堆、状态、体力、元气、分数、道具、饮料、回合属性、日志）。
- 培育进度没有本地存档：已按「文件名 = 类型全名的 MD5」把容器里的本地存档逐个对上类型，培育相关的只有
  `ProduceLocalSaveData`（是否在培育中等标记）。属性、星星、P 点、日程、牌组等由服务端下发，考试外只能靠截图识别。
- 主数据：`gakumas-player/data/master`（286 张表）与 H.I.F 日程表。

## 6. 分阶段

1. 考试识别做全：状态栏 buff、考试中饮料与 P 道具。用「截图 + 同时刻存档」自动标注图标模板。
2. GameState + reduce() + 页面转移图；用现有数据集整段重放验证稳定性。
3. 培育非考试部分：没有本地存档，靠截图识别读选行动页、三选一、商店等，再做预演。
4. 竞技场、日常任务两个 mode。

## 7. 识别素材包（hiro.assets）

识别用的游戏素材统一由 `tools/assets/build_pack` 生成到 `resources/recog/r<资源版本>/`，代码只经 `hiro.assets.pack()` 取用：
卡面（全量 2290）、考试状态 / 培育效果图标、P 饮料、P 道具、客户端原始字体。游戏更新后重跑；采集器启动时检查版本。

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 素材包框架，卡面与图标接入 | 完成 |
| M1 | 游戏字体数字识别（字体比对，不确定时退回 ddddocr） | 完成 |
| M2 | 客户端 UI 布局（锚点规则，190 个界面）+ 截图校准 → `hiro.vision.layout`；考试 HUD 数值与状态栏几何已改用布局坐标 | 完成（方案 B） |
| M3 | 页面签名：客户端 1813 个界面 Sprite 中自动挖掘各页面的区分性 Sprite 与位置，作为场景识别快速通道（命中 5–20 倍提速），未命中走 KAA | 培育内完成；竞技场、日常待采样 |
| M4 | 选行动页读取（`hiro.vision.action`）：天数、体力、P 点数、三维/上限/成长率、星数、老师建议、各行动（种类、属性、体力消耗、休息回复量、点击位置） | 读取完成；接入状态机待 GameState/reduce |

### 布局坐标的来源与边界（M2 方案 B）

- `tools/assets/layout.py`：从 data.unity3d 按 RectTransform 锚点规则导出 Exam*/Produce*/Hif*/Lesson*/Audition* 预制体的节点矩形
  （参考分辨率 1080×1920 → 720×1280），并按挂载的脚本类名打标记：`moved`（受 LayoutGroup 排列）、`fitted`、`text`。
- 这些运行时组件的参数在 IL2CPP 构建里没有类型信息，读不出来；受影响的元素由 `tools/assets/calibrate` 用回归截图测出相对静态矩形的偏移，
  存于 `src/hiro/vision/layout_calib.json`（随代码提交）。纯锚点元素（回合圆盘等）不需要校准。
- 尚未迁移、仍为实测常量：手牌（沿用 KAA 的卡名字母定位）、P 饮料栏、P 道具列、NPC 排名卡。
- 完整还原（用 global-metadata.dat 重建脚本类型树、实现 LayoutGroup/TextMeshPro 排版）留作独立研究。

### 页面签名（M3）

- Sprite 与界面节点的对应关系记在 Image 组件里（IL2CPP，读不出），所以从数据反向挖掘：`tools/assets/label_scenes` 用现有识别给数据集打标签，
  `tools/assets/mine_signatures` 为每类页面挑出「本页面每帧都高分、其他页面同位置低分」的 Sprite 与位置，写入 `src/hiro/vision/signatures.json`。
- 运行时 `hiro.vision.signature.match`：每个签名只在 ±10 像素窗口内匹配，约 5ms；`scene.classify_current` 在 H.I.F 判定之后先查签名，命中即返回。
- 现有签名：ACTION_SELECT、SELECT_CARD、SELECT_DRINK、SKILL_CARD_REMOVAL、HIF_EXAM_RESULT、OUTING（与 STUDY 同一界面，交由 KAA 区分）。
  CONSULT、STUDY 没有区分度足够的 Sprite。已知误判：行动选择页上打开暂停菜单时仍判为 ACTION_SELECT。
- 新页面（竞技场、日常）需要先采集带标签的截图，再重跑挖掘。

### 行动选择页（M4）

- 客户端 data.unity3d 没有这一页的布局（在按需下载的资源包里），坐标为截图实测。
- 按钮文字、天数、建议用 Apple Vision；三维、星数、休息回复量用游戏字体识别（不确定时 ddddocr）；体力、P 点数、上限单独裁剪识别。
- 变体：主页（相談/差し入れ/特別指導）、公开课（Vo./Da./Vi.公開レッスン，SP 课消耗不同）、授業（按右下角图标颜色定属性）+ 休む、おでかけ。
- `complete` 为假（按钮滑入动画、暂停菜单盖住）时调用方应等下一帧；数据集 102 帧中 95 帧完整。
- 每帧约 0.5 秒；各行动的数值收益不在画面上，由 preview() 按主数据与 H.I.F 日程计算。

## 8. 主循环与 trace（hiro.loop，2026-10-31 实机版）

```
run_hif new|resume
  ├ new：上一局停在结算画面 → finish_produce_end() 点回首页；guard_new 拦「プロデュースデータの破棄」（--allow-discard 才放行）
  ├ resume：hiro.loop.resume.enter_produce（只认再開，遇破弃只取消）
  └ _supervised_resume：主循环异常 → recover:crash_restart，从当前画面续跑（≤5 次）；ResumeBlocked 不重试

HiroController._update（每周期）
  check_scene（KAA）→ 补判：
    UNKNOWN/IDLE + is_hif_exam            → EXAM（KAA 只认普通考试）
    UNKNOWN + H.I.F 确认框表（battle.HIF_DIALOGS）→ 点掉，下一轮重判
    UNKNOWN + 「完了」「ファン数」「プロデュース…」→ 培育结束：finish_produce_end，结束本局
    UNKNOWN + 「受け取らない」+ 饮料说明     → SELECT_DRINK（差し入れ）
    UNKNOWN + 「Pアイテムを選択」/「カスタム後を選択」→ SELECT_PITEM（存帧）
    IDLE + hiro.vision.scene               → SELECT_CARD / SELECT_DRINK
  _watch_unknown：持续未识别 30s 起每 30s 打 WARNING（附画面文字）并存帧
  → Session.observe(页面) → reduce → KAA dispatch → 策略钩子（hiro.produce.hif）

决策点（hif.py 各 on_* / battle.run_exam）：
  observe(截图 + 字段) → reduce → ProduceDeciders / BattleBrain.decide(GameState) → act → verify → record(Step)
```

- **培育页面**（`hiro.produce.hif`）：选行动、三选一（含「再抽選」）、选饮料（饮料满时勾「受け取らない」）、授業 / 外出（不信任 KAA commit，页面没变再双击）、相談（`hiro.produce.shop.execute_shop` 逐步执行到 ShopEnd）、P 道具背包 / 定制（`kaa_patches.pitem_commit`，按颜色 / 毛绒认名称 → 决策器）。
- **考试**（`hiro.produce.battle.run_exam`）：标量用识别层 HUD（`read_hif_hud`），手牌用识别层（卡面 + 卡名核对、`badge_score`），KAA 只管点击坐标；存档（`hiro.sim.live`）可读时为真值并做预演，卡牌结算中先 `settle`，模拟器不支持的效果本场降级为截图；H.I.F 确认框（使用確認 / ターン終了 / ドリンク詳細）按表处理；卡停在 SELECT 态再点；考试记忆落盘 `trace/exam_memory.json`，续跑读回。
- **结算**：等玩家行「突破 / 敗退」出现（≤8s）；敗退且有免费再挑戦才点（付费不点）。
- **trace**：`traces/<run>/steps.jsonl`，每步 Observation（+ 同步并入的存档 / 页面观测）、`state`（**决策输入**，契约 1.1）、`state_after`、Decision、Preview、ExecutionResult、notes、耗时；`frames/` 截图；`run.json` 含 ResourceManifest、llm、main_attr。
- **故障恢复**统一记 `recover:<kind>`：`page_misjudged`（转移校验 / 补判）、`missing_fields`（最多等 5 帧）、`llm_invalid`（规则兜底）、`tap_no_effect`（重试 / 跳过）、`dialog`（KAA 中断 / H.I.F 确认框）、`crash_restart`（主循环异常续跑）。
- **LLM 用量上限**：`ClaudeCLI` 识别到（`duration_api_ms=0`）后打 ERROR、每 5 分钟重试、最长 6 小时，不静默兜底；报告单独计 `usage_limit`。
- **报告** `python -m hiro.loop.report traces/<run>`：考试内截图→点击耗时分布、LLM 调用 / 失败 / 用量上限 / token、恢复次数、每场考试。
- **入口** `python -m hiro.run_hif resume|new [--mode qualify|main] [--idol <skin>] [--main-attr Vo|Da|Vi] [--exams-done N] [--llm claude:sonnet|openai:<model>] [--no-save] [--trace 名]`，运行前核对 `hiro.loop.manifest`。
- **下一步**：状态推算与识别互验（事件 → 预测 → 互验 → 主动查看），方案见 `docs/state-events.md`（待用户确认）。
