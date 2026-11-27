# 识别素材与退路（AssetSource）

仓库带一套识别用素材包 `assets/pack/`（卡面、图标、字体、界面 Sprite、布局；权利属于游戏的权利人，见 `assets/pack/README.md`），
不带主数据原文件。识别层取素材只经插件 `AssetSource`（`hiro.plugins.get("asset_source")`，入口 `hiro.vision.assets`），
默认实现 `hiro.defaults.assets.PublicAssetSource` 用四个来源：

| 来源 | 位置（环境变量） | 内容 |
|---|---|---|
| 公开游戏图 | `HIRO_KAA_GAME_DATA`，默认 `resources/game_data/`（安装脚本调 `tools/assets/game_images.py` 下载） | `skill_cards/` 技能卡卡面、`idol_cards/` 偶像卡、`drinks/` P 饮料图（白底 RGB，29 种，与主数据 ProduceDrink 全部对上） |
| 主数据库 | `HIRO_MASTER_DB`，默认 `cache/master/game.db`（安装脚本调 `python -m hiro.state.master_db` 生成） | 卡 / 饮料 / P 道具 / 偶像等主数据表 |
| 随仓库素材包 | `HIRO_BUNDLED_ASSETS`，默认 `assets/pack/`；设 `0` 关掉 | 卡面、考试效果 / P 饮料 / P 道具图标、数字字体、界面 Sprite、支援卡图、布局 |
| 使用者自建素材 | `HIRO_USER_ASSETS`，默认 `resources/user_assets/` | 见下文「自建素材」；都可选。与素材包并用：同类图库取文件更多的一个，`layout.json` 和字体先自建后素材包 |

公开游戏图原来由 KAA 的游戏数据更新器从 [kaa-game-data](https://github.com/kotonebot/kaa-game-data) 下载。识别一直按
`data-b9577da` 这一版调；这一版上游已经下架，现在能下的新版是重新导出的图，像素有细微差别，2026-11-25 实测换成 edc305a 后
换卡对话框里点选的卡会认错。所以 2026-11-25 起由本仓库 GitHub Release `game-images-b9577da` 托管这份逐字节相同的副本，
`tools/assets/game_images.py` 按钉在脚本里的 SHA256 下载，校验对了才替换目录；已是这个版本（或目录里是 KAA 当年下好的同一版）就不重复下载。
图片不进 git 仓库；权利属于游戏的权利人（见 Release 里的 NOTICE.txt）。换版本前先跑识别回归（`tests/dmm_regression.json`、`test_change_index` 等）。

主数据原来用 kaa-game-data 里的 `game.db`，2026-11-25 起由 `hiro.state.master_db` 从模拟器主数据
（`vendor/gakumas-player/data/master/<提交>/catalog.sqlite`，社区公开的 gakumasu-diff 固定提交）生成同结构的库：
每张表一列一个字段，列表 / 对象写 JSON 文本、布尔写 0 / 1，和原来的 `game.db` 一样，`hiro.master` 里的 SQL 原样能用。
模拟器主数据换了提交（`fetch_master.py` 换版本）时自动重建。2026-11-25 核对：原 `game.db` 的表和列都在、行数不少，抽查的行字段值一致；
只少 `PhotoLookEffectorCharacter` 一张表（hiro 不用）。`HIRO_MASTER_DB` 指向一份已有的库时原样用、不重建。

主数据换版本的 PR（例如自动升级 CI 推的 `fix_bot_master_<提交>`）合入后，本地要跟着重下：安装版由 setup（`scripts/setup.sh` /
`setup.ps1`）自动跑 `fetch_master.py`；开发用的 worktree 要自己再跑一次 `python vendor/gakumas-player/scripts/fetch_master.py`，
让 `data/master/current.json` 指到 `fetch_master.py` 里钉的那个提交。不重下时 `gakumas_produce.schedule` 的版本检查会报
`Schedule/master version mismatch`（这个检查是有意的：日程表和主数据要是同一份，别绕过）。

## 默认素材下各读数

下表是关掉素材包（`HIRO_BUNDLED_ASSETS=0`）后的情况，即「默认素材」= 只有公开游戏图和主数据库，没有任何自建素材；
带着素材包时，下表里标「跑一次 capture_icons 后可用」「要自建」的项都直接可用（支援卡 id 除外，素材包带的是支援卡图，对识别有效）。核对方法：识别层回归用例 + 主仓库 58 帧考试画面逐项对比
「有私有素材包 / 只有默认素材」两种读法。

| 读数 | 默认素材下 | 说明 |
|---|---|---|
| 判页（模板快速判页 quick_page、像素兜底、OCR 页面地图） | 可用 | 界面文字小模板随仓库（`hiro/vision/templates/`，截图裁的标签 / 按钮字样） |
| 过渡帧判页（页面签名） | 降级 | 签名要界面 Sprite。淡入中的选行动页等过渡帧判 unknown，下一帧正常判出；模板判不出时补判选行动页（「P手帳」标签）、Pドリンク所持上限、饮料已满的选饮料页 |
| HUD 数字：剩余回合、元気、体力 | 可用 | 没有数字字体时走 ddddocr：整框与「左边收到第一个白字」各读一次，不一致用 Apple Vision 判，都不对给 None。58 帧 174 个读数与有字体时全部一致 |
| 考试分数（排名栏玩家分数） | 可用（置信度略低） | 按字形范围裁图，ddddocr 与 Apple Vision 互相核对：一致 0.8；掉首 / 末位时取长的 0.6；不一致而 ddddocr 位数与整高字形数相同时 0.6；其余 None。11 帧回归全对（有字体时 0.9–1.0） |
| NPC 分数、名次、突破线、本战名次条 | 可用 | 本来就走 OCR；本战玩家分数三票投票少了字体一票，剩 Vision + ddddocr 两票须一致 |
| 手牌卡名 / 卡 id | 可用 | 公开卡面 + 卡名栏 OCR |
| 手牌预计得分徽章 | 可用 | 没有字体时白描边同色字形串交给 ddddocr，位数与字形数一致才用；58 帧与有字体时一致 |
| P 饮料（考试 / 培育底栏、选饮料、相談货架） | 可用 | 公开饮料图；名称按主数据 |
| 考试效果栏：数值 | 可用 | |
| 考试效果栏：效果名（やる気、好印象 等） | 跑一次 capture_icons 后可用 | 默认没有图标时效果名为 None，观测里 motivation / impression 为 None（策略层只能退回保守规则，分数明显掉）。用自己的考试截图跑一次 `tools/assets/capture_icons.py`（见下文）补齐 |
| 考试效果栏：卡牌效果（エンチャント）名 | 可用 | 用公开卡面 |
| 考试 P 道具名 | 跑一次 capture_icons 后可用 | 默认为 None；剩余次数照常读 |
| 开始确认页支援卡 id | 不可用 | 要自建 `support/` |
| 主数据（卡 / 饮料 / P 道具目录） | 可用 | 主数据库（从模拟器主数据生成） |
| H.I.F 日程（选拔 20 项 / 本战 9 项） | 可用 | 随仓库 `hiro/defaults/schedule_hif.json` |
| 支援卡效果、考试要求 / 倍率换算 / 星级基准 | 不可用 | 公开数据里没有可靠来源，`support_effects` / `exam_requirements` 返回 None，策略层按规则退路 |

可用 = 与有私有素材时结果一致；降级 = 能玩但个别帧 / 字段给 None 或置信度低；不可用 = 字段一直为 None。
Apple Vision 是 macOS 系统 OCR，只在 macOS 上有；其他系统用 RapidOCR / SIMD（见下文）。

## 补齐效果名 / P 道具名：`tools/assets/capture_icons.py`

**强烈建议跑一次**：考试效果名（尤其 やる気 / 好印象）是策略层的主要依据。工具只读你自己的截图，产物写进自建素材目录。

1. 准备截图：打一两场考试，存下考试画面（竖屏，720x1280 或同比例；录屏抽帧也行，装了可选组 play 时会自动只取考试画面）。
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
cards/            技能卡卡面（有则优先于公开卡面）
icons/drink/      P 饮料图标（有则优先于公开饮料图）
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
- 跑带截图样例的测试：设 `HIRO_FIXTURES`（样例目录）、`HIRO_KAA_GAME_DATA`（公开游戏图）、`HIRO_MASTER_DB`（主数据库，可不设：缺省用 `cache/master/game.db`）。

## 文字识别后端：Apple Vision 与 RapidOCR（`HIRO_OCR`）

macOS 缺省用 Apple Vision；Windows 装好了 [SIMD PaddleOCR](#windows-缺省simd-paddleocr) 就用它，没装时和其他系统一样用 RapidOCR（`rapidocr_onnxruntime`，日文 / 英文 PaddleOCR PP-OCRv3 模型；
2026-11-13 起直接调用，不再经过 kotonebot，同一帧的结果与原来逐字节一致）。
识别模型放在 `cache/rapidocr/`：原来随 KAA 带，2026-11-25 起托管在本仓库 GitHub Release `ocr-models-v1`，安装脚本调 `python tools/assets/rapid_models.py` 下载并校验 SHA256（模型来自 PaddleOCR，Apache-2.0，见 Release 里的 NOTICE.txt）。
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

### Windows 缺省：SIMD PaddleOCR

Windows 上装好了 SIMD（辅助程序、模型、.NET 10 Runtime）就自动用它，使用本机 PP-OCRv6 medium 进程，GPU 走 Vulkan；没装或启动失败时自动退回 RapidOCR，日志写明原因。字体数字校验和模板匹配不变。

识别测试集（2026-11-24，90 帧，Ryzen 7 9700X + RTX 5060）：

| Windows 后端 · 设备 | 准确率（核心 / 进阶） | 单帧耗时中位数 |
|---|---|---|
| SIMD · Vulkan | 92.8% / 86.6% | 0.51s / 0.42s |
| RapidOCR · DirectML（`HIRO_OCR_DML_DEVICE=1` 指到独显） | 91.9% / 85.6% | 1.04s / 0.95s |
| SIMD · CPU | 92.8% / 86.6% | 1.23s / 1.08s |
| RapidOCR · CPU | 91.9% / 85.6% | 3.07s / 2.74s |

（同一批帧上 macOS 的 Apple Vision 是 93.2% / 87.5%。）下面用 PowerShell 和 .NET 10 SDK 构建；运行时只需 .NET 10 Runtime 和这些模型文件，不把截图发到网络上。
辅助程序是跨平台的 .NET 程序集，在 Mac 上构建的也能直接拷到 Windows 用。

**自动安装（Windows，用户不用做什么）**：`scripts/setup.ps1` 和桌面版首次安装都会装好 SIMD，装不上不影响安装，识别退回 RapidOCR。装在 `.deps`（或 `HIRO_SIMD_DEPS`）下，不要管理员权限、不改系统 PATH：

- 辅助程序 + 模型：本仓库 GitHub Release `ocr-simd-v1`（一个 zip，约 96 MB）。`python tools/assets/simd_ocr.py` 下载、校验 SHA256、解压到 `.deps/simd-ocr` 和 `.deps/SimdPaddleOCR/models`；已装好同一版本、或自己编的（`.deps` 根没有 `.simd-asset-sha256`）不重复下载、不覆盖；`--force` 强制重装。
- .NET 10 Runtime：系统里有就用系统的；没有就用微软官方 `dotnet-install.ps1`（`-Runtime dotnet -Channel 10.0 -NoPath`）装到 `.deps/dotnet`，辅助程序启动时设 `DOTNET_ROOT` 指过去。
- 桌面版里这一步单独列出（「安装 SIMD 文字识别（可选）」），失败只提示；`setup.ps1 -NoSimd` 跳过它。设置 → 运行环境 → 「重装运行环境」会再试一次。`hiro doctor` 的 `ocr` 项和设置页「运行环境」会写现在用的是哪个后端。
- 发布资产（维护者）：在已经编好 `.deps` 的机器上 `python tools/assets/pack_simd_ocr.py --out ocr-simd-v1.zip`（打包可重复，同样内容 SHA256 不变），`gh release create ocr-simd-v1 ocr-simd-v1.zip --latest=false`，再把打印的 SHA256 填进 `tools/assets/simd_ocr.py` 的 `SHA256` 常量。填之前下载这一步会直接跳过。

手动从源码构建（开发用，也可以用来替换自动装好的版本）：

1. 将 [SimdPaddleOCR a9324e3](https://github.com/sdcb/SimdPaddleOCR/tree/a9324e3efadaed999e5f354a8f8e4b536c4687b6) 解压到 `.deps/SimdPaddleOCR`（其中 `models/` 要有 `medium_det.onnx`、`medium_rec.onnx` 和 `rec_keys.txt`）。
2. 将该版本用的 [SimdOnnx cb80be7](https://github.com/sdcb/SimdOnnx/tree/cb80be7370f280c4ca62f1679c71f05688a66001) 解压到 `.deps/SimdOnnx`。从仓库根目录执行：

   ```powershell
   .\tools\build-simd-ocr.ps1
   ```

3. 用原来的命令启动 Hiro，自动选择会用 SIMD（日志里能看到）。要固定用某个后端，设 `$env:HIRO_OCR = 'simd'` / `'rapidocr'`。如果用非默认的依赖目录，在构建和启动前都设置 `HIRO_SIMD_DEPS` 为其绝对路径。显式选择 SIMD 时，缺模型或 helper 会直接报错，不会静默换后端。
   强制 CPU 或 GPU：`$env:SIMD_OCR_BACKEND = 'cpu'` / `'vulkan'`（缺省自动，GPU 可用就用）。
