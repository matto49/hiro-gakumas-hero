# 初 / N.I.A 机制说明

这篇讲初（定期公演『初』）和 N.I.A（NEXT IDOL AUDITION）培育的规则和数值。写决策、写模拟器时查这里。打法放在 [hajime-playbook.md](hajime-playbook.md) 和 [nia-playbook.md](nia-playbook.md)。

每条后面标了出处，代号是：

- 〔主〕：主数据。快照是 gakumasu-diff `928da0d`，读取方式是 `vendor/gakumas-player` 的 `Catalog`。
- 〔社〕：社区攻略，后面跟原文代号（例如 g8-PRO、ww-初M），原文摘录和链接见文末。
- 〔GK〕：GKMSProject（yuzeis）的实测。他们是对着服务器录到的整局数据核出来的，文件是 `GakumasCLI/produce/nia_produce.py`、`nia_route.py`。
- 〔实〕：我们自己在 Mac 账号 2 上跑出来的（篠澤広 Campus mode!!，2026-11-17）。
- 〔逆〕：客户端逆向（Il2Cpp 导出 + 反汇编），见第 10 节。
- 〔推〕：推断，还没核对，用的时候要小心。

模拟器是 `vendor/gakumas-player/gakumas_produce/hajime_nia.py`，测试是 `tests/test_hajime_nia_sim.py`。下文提到的函数都在这个模块里。

## 1. 模式总表

| produce | 模式 | 周数 | AP | 属性上限 | 考试 | 过关名次 | 设置 |
|---|---|---|---|---|---|---|---|
| produce-001 | 初 レギュラー | 13 | 15 | 1000 | 中間 + 最終 | 前 3 | p_setting-1 |
| produce-002 | 初 プロ | 16 | 20 | 1500 | 中間 + 最終 | 前 3 | p_setting-2 |
| produce-003 | 初 マスター | 18 | 20 | 1800 | 中間 + 最終 | 前 3 | p_setting-3 |
| produce-006 | 初 レジェンド | 18 | 20 | 3000 | 中間 + 最終 | 前 3 | p_setting-6 |
| produce-004 | N.I.A プロ | 27 | 20 | 2000 | 1 次 / 2 次 / 最終オーディション | 第 1 | p_setting-4 |
| produce-005 | N.I.A マスター | 26 | 20 | 2600 | 同上 | 第 1 | p_setting-5 |

以上全部来自〔主〕：`Produce`、`ProduceStepAuditionDifficulty.rankThreshold`、`Produce.idolCardParameterGrowthLimit`。

初的四档共用评价组 `produce_group-001`，N.I.A 用 `produce_group-002`。家族靠 produce id 区分，因为 `Produce.name` 里初和 N.I.A 都叫「プロ」「マスター」，从名字分不出来。这个映射写在 `hiro.state.modes`。

## 2. 培育设置（ProduceSetting）

| 字段 | 初（p_setting-1） | N.I.A（p_setting-4 / -5） | 含义 |
|---|---|---|---|
| refreshStaminaRecoveryPermil | 700 | 700 | 休息回最大体力的 70%，向下取整（最大 23 回 16，和 g8-休 一致） |
| beforeAuditionRefreshStaminaRecoveryPermil | 700 | 500 | 试镜前一周休息的回复比例；N.I.A 只有 50%〔GK〕 |
| stepSkipStaminaRecoveryPermil | 250 | 250 | 跳过一周回 25% |
| produceDrinkPossessLimit / MaxLimit | 3 / 4 | 3 / 4 | 饮料能拿 3 瓶，有道具时最多 4 瓶 |
| continueCount | 3 | 3 | 每场考试最多再挑戦 3 次 |
| customizeProduceCardCount | 1 | 1 / 2 | 一次特別指導能定制几张；MASTER 是 2 张，和 ww-NIAM 一致 |
| produceAuditionTrendAssessmentPermilUpper/Lower | 200 | 200 / 110 | 审查基准的上下浮动，含义〔推〕 |

## 3. 评价

### 3.1 初

```
评价 = floor(属性和 × 2.3) + 名次分 + 最終試験分数的分段折算
```

- 拿第 1 名时，每项属性先 +30，然后再算属性和〔社 ww-初M〕。属性和不会超过各项的上限。
- 名次分：第 1 名 1700、第 2 名 900〔社，实机核对〕。第 3 名 500 是〔推〕，还没对过。
- 分数折算的斜率按分段算：0–5000 段 0.30，5000–1 万段 0.15，1–2 万段 0.08，2–3 万段 0.04，3–4 万段 0.02，4 万以上 0.01。
- 核对方法：拿 ww-初M 的分数线表（属性和 3010 / 3410 时 A+、S、S+ 各要多少分）代进去算，各档都落在 11500 / 13000 / 14500 上，误差不超过 1 分。测试是 `test_hajime_evaluation_matches_wiki_table`。
- 档位阈值〔主 ProduceGrade〕：A 10000、A+ 11500、S 13000、S+ 14500、SS 16000、SS+ 18000、SSS 20000、SSS+ 23000、SSSS 26000。
- 〔实〕2026-11-17 REGULAR 篠澤広：培育履历显示 9,973pt（B+），属性 995 / 996 / 478，最終 14,314 分第 1。每项减掉 30 再代公式，正好 9,973。11-18 第二局：A 10,428，属性 914 / 1000 / 529，最終 21,519 第 1，同样逐分对上。11-18 初 PRO：B+ 8,790，属性 860 / 1250 / 657，最終 5,175 **第 2**——名次分反推正好 900，第 2 名不加 +30，逐分对上。11-18 初 MASTER：A+ 12,903，属性 932 / 1600 / 734，最終 44,250 第 1，逐分对上（4 万以上段每分 0.01 也核到了）。结算画面和履历上的属性**已经含了**第 1 名的 +30。
- 实现：`hajime_evaluation(params, final_score, rank, cap)`，`params` 传最終考试之前（不含 +30）的属性。

### 3.2 N.I.A

```
评价 = floor(属性和 × 2.3) + floor(票数 × m + c)
```

票数对应的 (m, c) 按区间取〔GK，出处是学マスwiki「最終プロデュース評価」；GKMSProject 拿 9 局录到的结算逐分核对过〕：

| 票数 | PRO m / c | MASTER m / c |
|---|---|---|
| ≤ 2 万 | 0.100 / 0 | 同 |
| ≤ 4 万 | 0.085 / 300 | 同 |
| ≤ 6 万 | 0.070 / 900 | 同 |
| ≤ 8 万 | 0.065 / 1200 | 同 |
| ≤ 10 万 | 0.060 / 1600 | 同 |
| > 10 万 | 0.055 / 2100 | ≤ 12 万 0.050 / 2600，≤ 14 万 0.040 / 3800，再往上 0.030 / 5200 |

- 属性按上限截断：PRO 2000，MASTER 2600。
- 实现：`nia_evaluation(params, votes, produce_id, cap)`。
- 〔实〕2026-11-18 N.I.A PRO 篠澤広：培育履历 15,428pt（S+），属性 1163 / 1475 / 1107，ファン投票数 86,919。floor(3745 × 2.3) + floor(86,919 × 0.060 + 1600) = 8,613 + 6,815 = 15,428，逐分对上。11-19 N.I.A MASTER：16,342pt（SS），属性 1178 / 1663 / 1354，ファン投票数 84,906，floor(4195 × 2.3) + floor(84,906 × 0.060 + 1600) = 9,648 + 6,694 = 16,342，MASTER 档也逐分对上。（11-17 那局 A 11,475 没截属性和票数，没核。）

## 4. 初的课程

### 4.1 每节课的回合数、过关线、满分线

数据来自 `ProduceStepLesson` → `ProduceStepLessonLevel`〔主〕。字段对应关系：`limitTurn` 是回合数，`successThreshold` 是过关线，`resultTargetValueLimit` 是满分线，也就是这节课属性收益的上限。

主数据有三种写法：

- REGULAR / PRO：按角色和属性写，例如 `p_step_lesson_level-001-shro-sp-da-002`。**序号不是「第几次上这种课」**：用哪一档由日程逐周决定（等级 = baseStepLevel + 日程 AddStepLevel〔逆〕）。〔实〕REGULAR 第一节 SP 课就是 7 回合 / 过关 70（sp-002 的数值）；REGULAR 的两节追い込み是 9 / 90 / 360 和 11 / 165 / 600，落在 produce-002 的档位上，不是下表 REGULAR 行的数值。模拟器 `Sim.lesson()` 因此要给 `level` 或画面过关线 `clear`（`lesson_by_clear()` 在初的全部档位里反查）。
- MASTER：只有追い込み，按考试流派写，例如 `p_step_lesson-produce-003-exam_card_play_aggressive_01-hard-01`。这份快照里**没有 MASTER 通常课和 SP 课的行**（缺口）。
- LEGEND：按流派写，三项属性共用，例如 `p_step_lesson-produce_006-card_play_aggressive-sp-03`。

篠澤広属于 plan2、やる気流派（aggressive），下表是她的课程，格式为「回合 / 过关线 / 满分线」：

| 模式 | 通常（第 1、2、3…次） | SP | 追い込み |
|---|---|---|---|
| REGULAR | 6/25/50、6/25/50、7/40/80、7/40/80 | 6/40/80、7/70/140、7/70/140 | 主数据 9/75/300、9/100/400；**实机是 9/90/360、11/165/600** |
| PRO | 6/30/60、6/30/60、7/55/110、7/60/120、7/75/150 | 6/45/90、7/85/170、7/100/200、7/110/220 | 9/90/360、11/165/600 |
| MASTER | （快照里没有） | （没有） | 9/90/360、11/165/600 |
| LEGEND | 8 回合，满分 120/160/240/380/630 | 8 回合，满分 160/210/310/460/710 | — |

- 通常课和 SP 课，plan2（含 aggressive）比 plan1 多 1 回合，过关线和满分线一样；追い込み两者相同〔主〕。
- 主数据里 REGULAR 有通常 4 档、SP 3 档、追い込み 2 档，PRO 是 5 / 4 / 2 档（档数，不等于按顺序取）。SP 课是在通常课那一周按概率替换出来的〔社 g8-SP〕。
- PRO 有「追加レッスン」：授業有时会派生出一节课，7 回合，过关 45、满分 90〔主 school-event〕。

### 4.2 收益

- 属性收益 = 基础值 + floor(基础值 × 加成)。基础值是这节课打到的分数，最多到满分线。加成按画面读数的写法记，1472 表示 +47.2%。实现是 `grow()`。
  - 〔实〕REGULAR 第 9 周 Vo SP 课（第 2 次 SP，满分线 140，Vo 加成 1272）：Vo 393 → 571，+178 = 140 + floor(140 × 0.272)，正好对上。
  - 〔实〕第 10 周 Da SP 课按公式应是 +206，实得 +270。多出来的 64 推测是支援卡「SPレッスン終了時」一类的加成，模拟器不算这部分〔推〕。
- 打满（パーフェクト）以后课就结束了，剩下几回合，每回合回 2 体力〔社 g8-レッスン〕。
- 追い込み：达到目标以后进入「追い込みボーナス」，这段时间多涨的属性在下课时平分到三项〔社 g8-レッスン〕。模拟器用 `oikomi_extra` 参数表示这部分。
- 体力消耗取决于怎么出牌，主数据里没有。模拟器的 `stamina_used` 必须由调用方给。

### 4.3 考试（初）

| 模式 | 中間 基础分 / 封顶 | 最終 基础分 | 名次 |
|---|---|---|---|
| REGULAR | 600 / 900 | 2450 | 前 3 |
| PRO | 1150 / 1700 | 6950 | 前 3 |
| MASTER | 1380 / 不封顶 | 8340 | 前 3 |

以上是篠澤広的数据，来自 `ProduceStepAuditionDifficulty`〔主〕。中間打到封顶分就直接结束。〔实〕REGULAR 中間打了 900 分，第 1 名。

## 5. N.I.A 的周行动

### 5.1 自主レッスン（不打牌）

| 阶段 | 通常 体力 / 属性 | SP 体力 / 属性 | P 点 |
|---|---|---|---|
| 1 次试镜前 | 6 / 80 | 8 / 100 | +20 |
| 2 次试镜前 | 6 / 100 | 8 / 120 | +25 |
| 最终试镜前 | 6 / 120 | 8 / 150 | +30 |

- 体力和属性〔主 ProduceStepSelfLesson〕。主数据只有 produce_004 的这 6 行，MASTER 共用。P 点〔社 ww-NIA〕。
- 属性收益 = 基础值 + floor(基础值 × 加成)〔GK，7 节课里 7 节对上〕。
- 体力门槛：体力 **< 消耗**时不能上，体力正好等于消耗可以上〔逆，`ScheduleScreenPresenter.IsSelfLessonStaminaSufficient` 比较 `stamina < 消耗`，为真时弹提示、禁止〕。g8-NIAM「営業後の体力が 8 以上」是对的，g8-NIA 的「以下だとレッスンが行えません」说法不准。
- 实际消耗 = 基础 6 / 8 按 `SelfLessonTypeStaminaPermils` 调整（支援卡、P 道具的「自主课体力增减」效果给出）〔逆〕。
- 自主课等级 = 培育的 baseStepLevel + 日程这一周的 AddStepLevel〔逆，日程逐周下发〕，不一定严格按「第几次试镜前」分段；上表按段落写是 ww-NIA 的观察。

### 5.2 営業

| 场所（颜色） | 主数据 id 里的写法 | 投票（1 次前 / 2 次前 / 最终前） | 附带 |
|---|---|---|---|
| 商業施設（紫） | produce_card | 2400 / 3200 / 4000 | 技能卡已强化 |
| 企業イベント（青） | produce_drink | 同上 | 饮料 |
| 自治体イベント（黄） | produce_point | 同上 | P 点 +50 |
| リゾート施設（绿） | stamina | PRO 1700 / 2200 / 2800，MASTER 2200 / 2900 / 3700 | 体力 +10 |

- 投票数〔主 ProduceStepEventDetail〕。大成功用 `-ex` 那一行，正好是 1.3 倍（例如 3120 / 4160 / 5200）。〔实〕画面上看到的「大成功ボーナス」+720、+960，就是多出来的那 30%。
- 选项涨的属性：PRO 60 / 80 / 100，MASTER 80 / 110 / 130，**不吃加成**〔主 ProduceStepEventSuggestion；GK〕。三个选项分别涨 Vo、Da、Vi，选哪个就涨哪项。
- 实际到手的投票 = 票数 × (1000 + 支援卡营业投票加成‰) / 1000〔GK〕。
- 消耗：每个营业选项的**体力消耗和 P 点消耗都是服务器逐个下发的**（`UserProduceProgressBusiness.Stamina / ProducePoint`），界面上分「体力」「P 点」两种花费〔逆〕；能不能去由客户端判断：P 点 ≥ P 点消耗且体力 ≥ 体力消耗（等于也行）〔逆，`CheckIfGoBusiness`〕。GK 实录多数选项扣 5 体力〔GK〕。
- 大成功概率也是每个选项单独下发（`ExcellentPermil`，营业页「大成功」说明里显示的就是它）〔逆〕；支援卡、道具有「营业大成功率增减」「营业票数增减」效果〔逆，效果类型 117 / 118、115 / 116〕。
- 大成功的概率看属性合计：1 次前合计 ≥ 798、2 次前合计 ≥ 1468 时是 100%〔社 ww-NIA，PRO〕。最终段原文说概率曲线有 bug，没给数字。
- 〔实〕这一页认场所靠图标和颜色，不靠文字；用户定的规矩是「场所没读出来不重要，重点是匹配图标和对应的属性」。

### 5.3 オーディション（选档）

篠澤広 PRO〔主〕，格式为「要先攒到的票数 / 基础分」：

| 场次 | 第 1 档 | 第 2 档 | 第 3 档 | 第 4 档 |
|---|---|---|---|---|
| 1 次 | 0 / 1000 | 4000 / 1600 | 9000 / 2150 | — |
| 2 次 | 0 / 6400 | 14000 / 10300 | 25000 / 19500 | — |
| 最終 | 0 / 12800 | 28000 / 20400 | 40000 / 32550（亲爱度 14） | 57000 / 58150（亲爱度 17） |

MASTER：1 次 0 / 11000 / 13000 票，2 次 0 / 31000 / 38000 票，最終 0 / 51000 / 55000 / 62000 票〔主〕。

- 每档都要拿第 1 名才算过。
- 档名用的是中文补丁的叫法〔GK〕：1 次 Music Order / Harmony To Night / 节奏Bang！；2 次 MELODY Journey / POP Fair / GAL★AXY音乐；最终 Single Cut / IDOL Big Up！ / QUARTET / FINALE。
- 〔实〕我们那局：1 次选了 ToNight（第 2 档），2 次选了 POP Fair（第 2 档），最终选了第 2 档（IDOL Big up!，门槛 28,000，我们当时 36,036 票），三场都是第 1 名。
- 试镜奖励（票数、属性）随得分变化：属性按审查基准排名封顶（PRO 1 次 92 / 75 / 62，2 次 119 / 98 / 80，最终 172 / 145 / 116），票数是一条按得分取对数的单调曲线〔GK，拟合的录制点〕。模拟器不推算这两项，由调用方填结算画面上的数字。
- 衰减线：最终试镜的分数超过 PRO 约 20 万、MASTER 约 65 万以后，投票数大幅减少〔社 g8-NIAM〕。
- 試験后的 P 道具三选一：见 [nia-playbook.md](nia-playbook.md)。

### 5.4 其它周

| 行动 | 效果 | 出处 |
|---|---|---|
| 休む | 回最大体力 70%，试镜前一周 50%；最多 4 次 | 〔主〕〔社 g8-NIA〕 |
| お出かけ | 回体力，带删卡、强化或换卡选项；三个选项的内容几篇攻略说法不一 | 〔社〕 |
| 差し入れ | 和以前的活動支給差不多，给不给 P 点看概率 | 〔社 g8-NIA〕 |
| 特別指導 | 定制技能卡（カスタマイズ），PRO 一次 1 张、MASTER 2 张 | 〔主〕〔社〕 |
| 相談（商店） | 买卡、买饮料、删卡、强化；N.I.A 价格原文没写 | 〔社〕 |

## 6. 初的周行动

| 行动 | 效果 | 出处 |
|---|---|---|
| 授業 | 扣体力，涨的属性和数值随机（20 / 30 / 40 / 55 / 80 / 110）；PRO 起可以选「トラブル追加」，往牌堆塞一张眠気，换这个授業的最高值 | 〔社 g8-授業〕 |
| 活動支給 | 给 P 点、饮料或卡；MASTER 有几周强制踩 | 〔社〕 |
| お出かけ | 花 P 点回体力，带饮料或卡。例：100P 回 40%，50P 回 20%，0P 回 30% | 〔社 g8-外出〕 |
| 相談 | 删卡、强化：PRO 第 1 次 100P、第 2 次 125P | 〔社 g8-PRO〕 |
| 休む | 回最大体力 70%，体力满的时候不能选 | 〔主〕〔社 g8-休〕 |

## 7. 周表

主数据里没有周表，日程是服务器下发的。现在已知的情况记在 `vendor/gakumas-player/data/schedules/hajime_nia.json`：

- **初 MASTER**：完整。三种周表随机抽一种，第 3、4、6、9、10、12 周三种不一样；第 7 周追い込み、第 8 周中間、第 17 周追い込み、第 18 周最終〔社 ww-初M〕。`calendar("produce-003", pattern)` 能取到。〔实〕11-18 初 MASTER 在游戏内日程列表（选行动页左上「中間まで N週」框）截了全 18 周，和第 3 型逐周一致。
- **初 REGULAR**：完整，但只来自我们一局实机（篠澤広，2026-11-17）。用 `tools/weeks_from_traces.py` 从 trace 逐周还原：

  | 周 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | 可选 | 课程 | 活動支給 / 授業 | 课程 / 授業 | 外出 / 相談 | 追い込み | 中間 | 活動支給 / 外出 | 授業 / 外出 | 课程 | 课程 / 授業 | 活動支給 / 外出 / 相談 | 追い込み | 最終 |

  和社区的零散提示对得上：g8 推荐外出的第 4、7、11 周都有外出，SP 可能出现的第 1、3、9、10 周正好是课程周（这局第 3、9、10 周出了 SP）；课程 4 周、追い込み 2 周也和主数据的课程次数一致。休む 在课程、授業、外出周都能选（满体力时不出现），追い込み周没有。别的偶像是不是同一张表还没核对〔实〕。
- **初 PRO**：不完整。g8 说 PRO 的周表每个偶像不一样，原文是图片。
- **N.I.A PRO**：不完整。第 9、18、27 周是试镜〔社 g8-NIA〕。ww-NIA支 给了各种行动的次数：自主课 8、営業 7、お出かけ 5、特別指導 4，加 3 场试镜正好 27 周。〔实〕每周固定一种主行动，休む 一直可选，例如「休む + 三属性自主课」「休む + 営業」「休む + 特別指導」「休む + お出かけ / 相談 / 差し入れ」。
- **N.I.A MASTER**：不完整。比 PRO 少一周，去掉的是 PRO 第 16 周的自主课〔社 ww-NIAM〕。

`calendar()` 只给 complete=true 的模式（目前是初 REGULAR 和初 MASTER）。其它模式会抛 `Unsupported`，不会拿猜出来的周表冒充完整的。

## 8. 模拟器能做什么、不能做什么

能做：

- `rules(produce_id, idol_card_id)`：读出这个偶像卡在这个模式下的全部主数据规则，包括课程、自主课、营业、考试档位、评价阈值和培育设置。
- `Sim`：一周一周推进状态（体力、P 点、三项属性、票数、上课次数、休息次数），规则不允许时抛 `Unsupported`。会拦下的情况有：体力不够、试镜档位票数不够、满体力休息、超出周数、N.I.A 里调用初的动作等。
- `evaluation()`：按 3.1 / 3.2 的公式算最终评价和档位。

不做（必须由调用方给，或者交给别的模块）：

- 打牌：考试和初的课程分数用 `gakumas_exam` 或 UINet 去打，或者用实机读数。
- 随机结果：授業涨什么、SP 课出不出、事件、支援卡和 P 道具的触发、营业会不会大成功。
- 试镜结算的票数和属性奖励（见 5.3）。
- 卡组和饮料的增减。

## 9. 缺口

1. 初 PRO 和 N.I.A 两档的逐周表，初 REGULAR 其它偶像的周表。要从实机 trace 补（`tools/weeks_from_traces.py`），或者截游戏里的日程页。
2. 初 MASTER 通常课和 SP 课的主数据行（这份快照里没有）。
3. 初第 3 名的名次分（500 是推的；第 2 名 900 已用初 PRO 实机核对）。
4. N.I.A 营业的体力 / P 点消耗、大成功概率：服务器逐个下发，画面上能读到（营业页的花费角标和「大成功」说明），不必再推算；要平台读出来。
5. ~~N.I.A 自主课的体力门槛~~：已由客户端逆向确认，等于消耗可以上。
6. N.I.A 试镜结算的票数和属性曲线，只有 GK 用录制点拟合的结果，我们自己还没有数据。
7. 初课程收益公式只核了一节（第 9 周 Vo SP）；支援卡在课后加的属性没有建模。初 REGULAR 追い込み为什么用 produce-002 的档位（日程下发的等级）没有逆向到具体规则。

## 10. 客户端逆向（Il2Cpp，2026-11-14 客户端）

用 Il2CppDumper 对当前安装的客户端重新导出（11-02 那份导出的地址已经对不上），再反汇编相关方法。只读本机客户端，不改任何文件。

**先说边界**：培育的结算数（最终评价 ProduceScore、试镜给的票数和属性、营业是否大成功）都是服务器算好下发的，客户端里没有这些公式；客户端里有的是「能不能做」的判断、服务器下发了哪些状态、有哪些效果类型。所以评价公式仍然靠 3.1 / 3.2 的核对，不靠逆向。

### 10.1 客户端判断（反汇编确认）

| 判断 | 结论 | 方法 |
|---|---|---|
| 自主课能不能上 | 体力 ≥ 实际消耗 | `ScheduleScreenPresenter.IsSelfLessonStaminaSufficient`、`ProduceStepSelfLesson.GetAffectedConsumptionStamina` |
| 营业能不能去 | P 点 ≥ 该营业 P 点消耗，且体力 ≥ 该营业体力消耗 | `ScheduleBusinessScreenView.CheckIfGoBusiness` |
| 结算页票数的字母档 | 主数据 `ResultGradePattern` 的 ProduceVoteCount 组：3000 E、5000 D、1 万 C、1.5 万 C+、2 万 B、2.5 万 B+、3 万 A、4 万 A+、6 万 S…（只是显示，不进评价） | `ResultGradeData.CreateProduceVoteCount` |

### 10.2 服务器下发的状态（protobuf 字段，决定了哪些东西是「逐局不同」的）

- **日程整局下发**（`UserProduceProgressSchedule`，每周一条）：`StepNumber`、`StepTypes`（这周能选的行动）、`SelectedStepType`、`AddStepLevel`、各属性自主课 id、`RefreshStamina`（这周休息回多少）、`AuditionRank`、`StepSelectNumber` / `MaxAuditionStepSelectNumber`（试镜选档）。游戏里有日程列表界面（`ScheduleListView`），**读这个界面就能拿到每一局准确的逐周表**，不用靠 trace 拼。
- **营业**（`UserProduceProgressBusiness`，每个选项一条）：类型、名字、体力消耗、P 点消耗、大成功概率、普通 / 大成功各自的事件 id。
- **试镜**（`UserProduceProgressAudition`）：三属性倍率、`VoteBonusPermil`（票数加成）、选的档、名次、分数、对手（`AuditionNpcs`）。
- **整局修正值**（`UserProduceProgress`）：属性上限追加（`VocalAdditionalLimit` 等）、体力回复 / 消耗倍率、P 点获得 / 消耗倍率、`VoteCount`、`AuditionEffectParameterBonusPermil`、`AuditionNpcEnhancePermil`（对手分数增减）、`AuditionParameterGrowthRatePermil`、`EventSchoolStaminaPermil`（授業体力）、`EventActivityProducePointPermil`（活動支給 P 点）、`EventBusinessVoteCountPermil`（营业票数）、三属性 SP 出现率（`Lesson*SpChangeRatePermil`）、`SelfLessonTypeStaminaPermils`、`LessonLimitUpScore`、相談价格（强化 / 删卡价格和各种折扣）、定制折扣。`Star` / `StarPermil` 字段也在，但只在 H.I.F 用；初和 N.I.A 没有星星，决策和模拟都不碰。

### 10.3 步骤和效果类型（枚举全表）

- `ProduceType`：FirstStar 1（初）、NextIdolAudition 2（N.I.A）、HatsuboshiIdolFestival 3（H.I.F）。
- `ProduceStepType`（初 / N.I.A 用到的）：课程 1–9（Vo/Da/Vi × 通常 / SP / 追い込み）、お出かけ等事件 10、活動支給 11、授業 12、相談 13、休む 14、Present 15、试镜 16–18、自主课 19–24、営業 25、営業事件 26、FanPresent 27、特別指導 28、レジェンドレッスン 29–34、授業分属性 48–50。
- 营业类型 `ProduceStepBusinessType`：ProduceCard 1（紫）、ProduceDrink 2（青）、ProducePoint 3（黄）、Stamina 4（绿）。营业奖励图标另有 UpgradeCard / Card / Drink / ProducePoint / Stamina 五种。
- `ProduceEffectType` 里只跟初 / N.I.A 有关的修正（支援卡、P 道具、チャレンジP道具都通过这些生效）：营业票数增减 115 / 116、营业大成功率增减 117 / 118、试镜票数增减 119 / 120、自主课体力增减 121 / 122、试镜前休息回复增减 107 / 108、对手分数增强 / 减弱 97 / 128、直接加票 114、授業体力增减 103 / 104、活動支給 P 点增减 105 / 106、各属性 SP 率 54–58、属性上限提升 90–93、课程上限提升 89、定制张数 111、定制价格 112 / 113、饮料持有上限 133、相談各类价格 65–72 / 98–102。

### 10.4 新发现

- **N.I.A PRO 有「简单模式」**（账号偏好 `ProduceNextIdolAuditionProEasyMode`）。打开后开局拿到 P 道具「N.I.Aキー（黄緑）」（`pitem_00-3-330-0`），效果是**对手分数减少 80%**（主数据 `Produce.easyProduceItemIds`）。MASTER 没有。我们账号开没开还没看。
- **考试引擎没实现对手分数的增减**（`AuditionNpcEnhance` 97 / `AuditionNpcWeaken` 128 只在 `gakumas_exam/enums.json` 里有名字）。N.I.A 简单模式的 −80%、MASTER チャレンジP道具让对手 +40%，模拟里都没算，按「第 1 名」做目标搜索时会偏。

## 出处

- 社区原文摘录：16 篇 game8 和 wikiwiki 攻略（作者本机整理的摘录，不随仓库发布；各条都标了原文代号，链接见下）。
  - g8-PRO <https://game8.jp/gakuen-idolmaster/610357>、g8-NIA <https://game8.jp/gakuen-idolmaster/661665>、g8-NIAM <https://game8.jp/gakuen-idolmaster/688368>
  - ww-初M <https://wikiwiki.jp/gakumas/初_マスター育成>、ww-NIA <https://wikiwiki.jp/gakumas/N.I.A立ち回り>、ww-NIAM <https://wikiwiki.jp/gakumas/N.I.Aマスター/基本>
- GKMSProject（yuzeis）：`GakumasCLI/produce/nia_produce.py`、`nia_route.py`、`scenario/nia.py`（41c8778）
- 实机：作者 Mac 账号的对局截图和 trace（2026-11-17，不随仓库发布）
