# 识别素材与退路（AssetSource）

仓库带一套识别用素材包 `assets/pack/`（卡面、图标、字体、界面 Sprite、布局；权利属于游戏的权利人，见 `assets/pack/README.md`），
不带主数据原文件。识别层取素材只经插件 `AssetSource`（`hiro.plugins.get("asset_source")`，入口 `hiro.vision.assets`），
默认实现 `hiro.defaults.assets.PublicAssetSource` 用三个来源：

| 来源 | 位置（环境变量） | 内容 |
|---|---|---|
| KAA 公开数据 | `HIRO_KAA_GAME_DATA`，默认 `resources/game_data/`（KAA 运行时下载） | `game.db` 主数据、`skill_cards/` 技能卡卡面、`drinks/` P 饮料图（白底 RGB，29 种，与主数据 ProduceDrink 全部对上） |
| 随仓库素材包 | `HIRO_BUNDLED_ASSETS`，默认 `assets/pack/`；设 `0` 关掉 | 卡面、考试效果 / P 饮料 / P 道具图标、数字字体、界面 Sprite、支援卡图、布局 |
| 使用者自建素材 | `HIRO_USER_ASSETS`，默认 `resources/user_assets/` | 见下文「自建素材」；都可选。与素材包并用：同类图库取文件更多的一个，`layout.json` 和字体先自建后素材包 |

设了 `HIRO_KAA_GAME_DATA` 时，`import hiro.vision` 会把 KAA 自己的数据目录（`kaa.game_data.paths` 与 `kaa.db.sqlite`
的库路径）也指过去，两边用同一份数据；不在当前目录留下 `resources/`。

## 默认素材下各读数

下表是关掉素材包（`HIRO_BUNDLED_ASSETS=0`）后的情况，即「默认素材」= 只有 KAA 公开数据，没有任何自建素材；
带着素材包时，下表里标「跑一次 capture_icons 后可用」「要自建」的项都直接可用（支援卡 id 除外，素材包带的是支援卡图，对识别有效）。核对方法：识别层回归用例 + 主仓库 58 帧考试画面逐项对比
「有私有素材包 / 只有默认素材」两种读法。

| 读数 | 默认素材下 | 说明 |
|---|---|---|
| 判页（KAA check_scene、模板快速判页 quick_page、像素兜底、OCR 页面地图） | 可用 | 界面文字小模板随仓库（`hiro/vision/templates/`，截图裁的标签 / 按钮字样） |
| 过渡帧判页（页面签名） | 降级 | 签名要界面 Sprite。淡入中的选行动页等过渡帧判 unknown，下一帧正常判出；KAA 判不出时补判选行动页（「P手帳」标签）、Pドリンク所持上限、饮料已满的选饮料页 |
| HUD 数字：剩余回合、元気、体力 | 可用 | 没有数字字体时走 ddddocr：整框与「左边收到第一个白字」各读一次，不一致用 Apple Vision 判，都不对给 None。58 帧 174 个读数与有字体时全部一致 |
| 考试分数（排名栏玩家分数） | 可用（置信度略低） | 按字形范围裁图，ddddocr 与 Apple Vision 互相核对：一致 0.8；掉首 / 末位时取长的 0.6；不一致而 ddddocr 位数与整高字形数相同时 0.6；其余 None。11 帧回归全对（有字体时 0.9–1.0） |
| NPC 分数、名次、突破线、本战名次条 | 可用 | 本来就走 OCR；本战玩家分数三票投票少了字体一票，剩 Vision + ddddocr 两票须一致 |
| 手牌卡名 / 卡 id | 可用 | KAA 卡面 + 卡名栏 OCR |
| 手牌预计得分徽章 | 可用 | 没有字体时白描边同色字形串交给 ddddocr，位数与字形数一致才用；58 帧与有字体时一致 |
| P 饮料（考试 / 培育底栏、选饮料、相談货架） | 可用 | KAA 饮料图；名称按主数据 |
| 考试效果栏：数值 | 可用 | |
| 考试效果栏：效果名（やる気、好印象 等） | 跑一次 capture_icons 后可用 | 默认没有图标时效果名为 None，观测里 motivation / impression 为 None（策略层只能退回保守规则，分数明显掉）。用自己的考试截图跑一次 `tools/assets/capture_icons.py`（见下文）补齐 |
| 考试效果栏：卡牌效果（エンチャント）名 | 可用 | 用 KAA 卡面 |
| 考试 P 道具名 | 跑一次 capture_icons 后可用 | 默认为 None；剩余次数照常读 |
| 开始确认页支援卡 id | 不可用 | 要自建 `support/` |
| 主数据（卡 / 饮料 / P 道具目录） | 可用 | KAA `game.db` |
| H.I.F 日程（选拔 20 项 / 本战 9 项） | 可用 | 随仓库 `hiro/defaults/schedule_hif.json` |
| 支援卡效果、考试要求 / 倍率换算 / 星级基准 | 不可用 | 公开数据里没有可靠来源，`support_effects` / `exam_requirements` 返回 None，策略层按规则退路 |

可用 = 与有私有素材时结果一致；降级 = 能玩但个别帧 / 字段给 None 或置信度低；不可用 = 字段一直为 None。
Apple Vision 是 macOS 系统 OCR，识别层本来就只在 macOS 上跑（实机依赖 KAA 的 macOS 适配）。

## 补齐效果名 / P 道具名：`tools/assets/capture_icons.py`

**强烈建议跑一次**：考试效果名（尤其 やる気 / 好印象）是策略层的主要依据。工具只读你自己的截图，产物写进自建素材目录。

1. 准备截图：打一两场考试，存下考试画面（竖屏，720x1280 或同比例；录屏抽帧也行，装了 KAA 时会自动只取考试画面）。
   每个效果 / P 道具在几帧里出现过就够；效果栏里被特效盖住的帧会自动丢掉。
2. 收集：`python tools/assets/capture_icons.py collect <截图目录或文件…> --work .temp/icons`
   按固定位置裁出效果栏和 P 道具栏，按外观聚类，每类一张预览：`.temp/icons/sheet_status.png`、`sheet_pitem.png`（图上标编号与出现次数），
   以及要填的 `.temp/icons/labels.json`。P 道具栏只收「在不同背景前出现过、且上面一格也是道具」的类，空栏背景不会混进来。
3. 标名（二选一，或先 b 再 a 修正）：
   - a) 手填：对照预览，在 `labels.json` 里给每类填 `name`。效果名用游戏里的名字（候选列表 `candidates_status.txt`，来自主数据）；
     P 道具填道具名（含颜色等后缀，如「H.I.F応援棒（赤）」）。不认识、不是图标的填 `null`。
   - b) LLM：`python tools/assets/capture_icons.py label --work .temp/icons --llm claude:sonnet`（`--llm` 同 `HIRO_LLM` 的写法）。
     LLM 只填它有把握、且对得上主数据候选的名字；拿不准的写在 `llm` 字段供参考。**LLM 也会认错，apply 前请对照预览检查一遍**。
     本机实测（9 类效果）：やる気、好印象、消費体力減少 等常见效果标对；被特效弄残的一类被标成「のんびり」，需要人改成 `null`。
4. 写入：`python tools/assets/capture_icons.py apply --work .temp/icons`（`--out` 或 `HIRO_USER_ASSETS` 指定目录）。
   已有的 index.json 会合并；同一个名字分成几类时只写样本最多的那类。再跑 collect 时已写入的图标不会再出现，只标新的就行。

识别层对自建的效果图标（index.json 里 `source: capture`）用更严的门槛（分数 ≥ 0.72、领先 ≥ 0.12）：图库只收了几个图标时，
没收进来的效果会被「最像的那个」顶上（上箭头配成 好印象）；门槛下这类槽给 None，不给错名。
本机核对（主仓库 58 帧考试画面、181 个效果槽，对照私有素材包）：全部标名后 179 个一致、2 个 None、0 个错；
只用 LLM 标的 3 个效果时 140 个一致、41 个 None（没标的效果）、0 个错。

## 自建素材（`HIRO_USER_ASSETS`）

都可选，有哪项用哪项，从自己的截图 / 客户端取，不随仓库发布：

```
cards/            技能卡卡面（有则优先于 KAA）
icons/drink/      P 饮料图标（有则优先于 KAA）
icons/pitem/      P 道具图标（capture_icons 生成）
icons/status/     考试效果图标（capture_icons 生成；文件名 img_general_icon_exam-effect_<类型小写>.png，识别只用 alpha）
    *.png         RGBA（透明底）或白底 RGB；文件名 = 资源名
    index.json    可选：资源名 → 名称（P 饮料没有时按主数据取名）
font/             数字字体 Campus-Alphanumeric-SemiBold.ttf（有则 HUD 数字 / 分数 / 徽章先按字形比对）
layout.json       UI 布局（结构同 hiro.vision.layout；没有用仓库里的 layout_default.json）
sprites/          界面 Sprite（页面签名 signatures.json 引用的文件名）
support/          支援卡图
schedule_hif.json 日程（有则优先于仓库自带）
```

也可以自己实现 `AssetSource`（接口见 `hiro.plugins`），用 `HIRO_ASSET_SOURCE=包.模块:类名` 或 entry point 换掉默认实现。

## 测试

- 依赖自建素材的用例标 `@pytest.mark.user_assets("<kind>")`，素材不在时跳过，理由「需要自建素材（HIRO_USER_ASSETS）：kind」。
  kind 与 `hiro.vision.assets.USER_ASSETS` 一致：font、status、pitem、drink、cards、sprites、support。
- `test_hif_hud` 逐帧对照里，状态名 / P 道具名在素材不在时不比（数值照比）。
- 「没有字体」的读法另有用例（`test_no_font_*`），把字体模板换成空，有没有字体都跑。
- `test_capture_icons.py` 只用合成图（标 `@pytest.mark.synthetic`，不按「读样例截图」跳过），CI 上也跑。
- 跑带截图样例的测试：设 `HIRO_FIXTURES`（样例目录）、`HIRO_KAA_GAME_DATA`（KAA 公开数据），不要在仓库目录生成 `resources/`。

## 文字识别后端：Apple Vision 与 RapidOCR（`HIRO_OCR`）

macOS 缺省用 Apple Vision，其他系统自动改用 RapidOCR（`rapidocr_onnxruntime`，日文 / 英文 PaddleOCR PP-OCRv3 模型；
2026-11-13 起直接调用，不再经过 kotonebot，同一帧的结果与原来逐字节一致）。
2026-11-06 在作者的实机截图回归集上对比（同一份代码、完整主数据和缓存，只换 OCR 后端；Mac M4）：

| | Apple Vision | RapidOCR |
|---|---|---|
| 回归用例（897 个，20 个跳过） | 875 通过，0 失败 | 802 通过，77 失败 |
| 全套回归耗时 | 约 6 分钟 | 约 18 分钟（约 3 倍） |

RapidOCR 的 77 个失败集中在：考试画面与本战分数、轮间货架、卡牌获得 / 删除 / 强化确认框、自定义菜单、事件选项、
选行动页、星数获得页。已按 Windows 实机暴露的问题修过几处（公開レッスン 读成「V0公開レッズン」时按颜色补属性、
说明页倍率漏「%」、「タイトルへ」读成片假名「ヘ」时危险词拦截照样生效，见 `hiro.vision.textnorm`）。
也就是说，非 macOS 平台上判页和读数比 macOS 弱一截、也更慢；能玩，但遇到「认不出就停下」的情况会更多。
换到 Windows / 模拟器后，建议先用自己的截图跑一遍识别回归（`HIRO_FIXTURES` 指向截图目录，`HIRO_OCR=rapidocr`），
再决定要不要为常错的读数加模板或调阈值。

### 可选：SIMD PaddleOCR

在 Windows 上可显式设置 `HIRO_OCR=simd`，让 Hiro 和 KAA 使用同一个本机 PP-OCRv6 medium 进程。现有自动选择、字体数字校验和模板匹配保持不变。下面用 PowerShell 和 .NET 10 SDK 构建；运行时只需 .NET 10 Runtime 和这些模型文件，不把截图发到网络上。

1. 将 [SimdPaddleOCR a9324e3](https://github.com/sdcb/SimdPaddleOCR/tree/a9324e3efadaed999e5f354a8f8e4b536c4687b6) 解压到 `.deps/SimdPaddleOCR`（其中 `models/` 要有 `medium_det.onnx`、`medium_rec.onnx` 和 `rec_keys.txt`）。
2. 将该版本用的 [SimdOnnx cb80be7](https://github.com/sdcb/SimdOnnx/tree/cb80be7370f280c4ca62f1679c71f05688a66001) 解压到 `.deps/SimdOnnx`。从仓库根目录执行：

   ```powershell
   .\tools\build-simd-ocr.ps1
   $env:HIRO_OCR = 'simd'
   ```

3. 用原来的命令启动 Hiro。要恢复自动选择，设置 `$env:HIRO_OCR = 'auto'`。如果用非默认的依赖目录，在构建和启动前都设置 `HIRO_SIMD_DEPS` 为其绝对路径。显式选择 SIMD 时，缺模型或 helper 会给出错误，不会静默换后端。
