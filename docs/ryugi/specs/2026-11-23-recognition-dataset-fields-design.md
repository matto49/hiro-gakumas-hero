# 识别测试集 v2：按页面字段出真值（OCR + 图标匹配）

> 状态：待批准。取代 `2026-11-23-ocr-dataset-design.md` 里「整帧逐框标注」的部分；数据仓库、贡献流程、CI、pick 的分层抽样沿用。

## 1. 为什么改

第一批（302 帧、5000 多个文字框）审下来的问题（用户 11-23）：

- 很多框其实是图标（休息按钮、功能按钮、P 点金币），不该当字去识别；装饰符号（★、数字前的 *）读没读对无所谓。
- 系统的 OCR 是**带着目的划区识别**：每页只读决策要用的数字和名字，而且要读出「属性 + 数值」这样的对应关系。整帧逐框标注量的是「通用文字识别」，和系统要的不是一回事，很多相对固定、系统根本不读的文字也被拿来计分。
- 图标匹配（考试手牌、持有饮料、状态栏、按钮）是另一半识别，原来的测试集完全没覆盖。
- 读错代价最大的是：**考试页的手牌和分数、商店价格**。

## 2. 新的定义

**一帧的真值 = 这一页 extract 要读出的字段值**，结构和现有 DMM 回归清单 `tests/dmm_regression.json` 的 `fields` 一样（只留「稳定项」：ID / 数值 / 布尔 / 短枚举，长文本不比，规则同 `tools/audit/dmm_samples.stable`）。

例（选行动页）：

```json
{"stamina": 22, "max_stamina": 33, "p_points": 250,
 "params": {"vo": 1473, "da": 2336, "vi": 1207}, "growth_pct": {"vo": 45.5, "da": 44.4, "vi": 41.3},
 "options": [{"kind": "rest", "hp_gain": 11}, {"kind": "outing"}, {"kind": "present"}],
 "drinks": ["pdrink_00-3-001", "pdrink_00-2-008"]}
```

图标、装饰符号、系统不读的固定文字：不在字段里，自然不计分。

## 3. 字段的识别方法标签

每个字段路径标上它靠什么认出来（表放在 `tools/ocr_dataset/fields.py`，按 extract 代码整理，路径支持 `[]` 通配）：

| 方法 | 例子 |
|---|---|
| `ocr` | 体力、P 点、属性值 / 上限 / 成长率、考试分数、回合、倍率、商店价格、服务价格、刷新次数 |
| `icon` | 考试手牌 `hand[].card_id`（卡面插图）、持有饮料、货架上的卡 / 饮料、选卡候选、回忆小图标、状态栏 |
| `pixel` | 按钮种类 `options[].kind`、能不能点 `available`、选中 / 已买 / 打折 / 强化 / 定制标记 |

查不到的字段标 `other`，报告里列出来提醒补表。

**高代价字段**（用户定）单独标 `critical`：`HIF_EXAM.hand[].card_id`、`HIF_EXAM.my_score`（及分数类）、`CONSULT.shelf[].price` / `services.*.price`、`HIF_ROUND_INTERVAL.*.price`。

## 4. 标注格式（数据仓库 `core/<id>.json`）

```json
{"schema": 2, "id": "...", "image": "...", "sha256": "...", "source": {...}, "platform": {...},
 "page": "ACTION_SELECT",
 "fields": { ...真值... },
 "field_status": {"params.vo": "agreed", "hand[2].card_id": "resolved", "growth_pct.vi": "disputed"},
 "answers": {"extract_vision": { ... }, "llm": { ... }},
 "note": "..."}
```

- `fields`：真值；`field_status` 只记不是 agreed 的字段（disputed 不计分、ignored 不计分）。
- `answers`：生成真值时各方的原始读法（复查用）。
- 不再有 `boxes`。校验（`validate`）改成 schema 2；schema 1 的帧不再接受。

## 5. 真值怎么来

1. **回归帧（104 张）**：直接用 `dmm_regression.json` 里已经逐条人工核过的期望值，全部字段 `resolved`。
2. **其余帧**：
   - 候选 A：Mac 上用 Vision 跑这一页的 extract（`tools.audit.dmm_samples.read`），取稳定项。
   - 候选 B：LLM 按这一页的**字段清单**独立读（提示词里列出要读的字段和含义，比如「体力 当前 / 上限」「Vo / Da / Vi 三个属性值」「每张手牌的卡名和 + 号」），**不给它看候选 A**。卡名 / 饮料名由 LLM 读出名字后，用主数据换成 ID。
   - 逐字段比：一致 → agreed；不一致 → disputed，进核对页。
   - 纯图标字段（饮料图标、状态图标这类画面上没有名字的）LLM 认不了：候选 A 直接进核对页，页面把裁图和匹配到的模板图并排放，人点 ✓ / ✗（✗ 时从候选里选或手填）。
3. **核对页**：按字段列（不是按框），每项显示裁图、各方读法；人看过的字段记 `resolved`。

## 6. 评测

```
python -m tools.ocr_dataset eval --backend vision,rapidocr,simd --set core [--method ocr|icon|pixel] [--critical]
```

- 每个后端各跑一遍实机 extract（切 `HIRO_OCR` 后端），取稳定项，和真值**逐字段严格相等**比对（容错和区间检查是识别代码该做的事，测试不放宽）。
- 指标：字段准确率；按方法（ocr / icon / pixel）、页面、字段、平台分组；**高代价字段单独一张表放最前面**；每字段的错例清单（裁图 + 真值 + 读成什么）。
- 换 OCR 后端只影响 `ocr` 字段，`icon` / `pixel` 字段的准确率单独看（DMM 540 放大对模板匹配的影响在这里量）。
- 待定字段占比 > 5% 时报告标「非正式」；报告写出抽查结果。

## 7. 页面范围与现有数据

- 只收有字段 extract 的页面（现有 27 种）；塔列表、首页、普通弹窗这类只需判页的页面这次不收（以后单做判页回归）。
- 现有 302 帧：图片保留，按新格式重出真值；没有 extract 的页面的帧从 core 移除（约三分之一）。旧的框级标注删掉。
- 第一批里发现的真值错误（如 24/33 定成 24/35）随重建修掉。
- 高代价页面（`HIF_EXAM`、`CONSULT`、`HIF_ROUND_INTERVAL`）的分层抽样每格上限放宽到 6，进阶集优先补。

## 8. 工具改动

- 保留：`pick`（分层抽样，加 `--require-extract` 只收有字段读取的页面）、`validate`、`review` 页框架、`comments_server`、`lexicon`（LLM 读出的名字换 ID）。
- 重写：`label`（字段版：extract 候选 + LLM 按字段清单读 + 比对）、`eval`（字段版）、`fields.py`（字段 → 方法 / critical 表）。
- 删除：框级的出框合并、`decide` 的框级多数决、`charset_rapid` 字表屏蔽实验（结论已出：无效）。

## 9. 交付顺序

1. `fields.py` 方法表 + schema 2 + validate。
2. 回归帧 104 张转成新格式入库（真值现成）；用它先跑一份字段评测（Vision / PP-OCRv3），这一步就能回答「DMM 上 PP-OCRv3 哪些字段读错」。
3. 字段版 `label` + 核对页；重建其余帧。
4. 样例页（ycy）换成字段版，继续收意见。
5. Windows 上跑 SIMD 和 PP-OCRv3 的字段评测。

## 10. 待确认

- 10.1 `fields.py` 的方法表是按代码整理的初版，交付后列给用户核对。
- 10.2 LLM 读字段时卡名带不带「+」由画面决定；卡面插图匹配的手牌在 LLM 侧只能读卡名，读不到时该字段进核对页。
