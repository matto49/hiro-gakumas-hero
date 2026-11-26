# 写 hiro 插件

hiro 的运行时有五个地方可以换成你自己的实现，主循环不用改。设计见 `docs/ryugi/specs/2026-11-25-strategy-plugins-design.md`，能跑的例子在 `examples/plugins/hiro_example_plugins/`。

| 种类 | 回答什么 | 接口（`hiro.plugins`） | 怎么选用 |
|---|---|---|---|
| `observation_source` | 不看画面也能拿到的游戏数据（hook、存档…） | `ObservationSource` | `strategy.json` 的 `sources` |
| `produce_advisor` | 育成某几页怎么选 | `ProduceAdvisor` | `strategy.json` 的 `produce.<页面>` |
| `exam_advisor` | 考试每一步怎么出牌 | `ExamAdvisor` | `strategy.json` 的 `exam` / `exam.<模式>`；没配时照旧 `HIRO_EXAM_ADVISOR` |
| `exam_state_source` | 考试局面从哪来 | `ExamStateSource` | `HIRO_EXAM_STATE_SOURCE`；配了数据来源时自动包一层转接 |
| `asset_source` | 素材和主数据从哪来 | `AssetSource` | `HIRO_ASSET_SOURCE` |

插件只能**交数据**（数据来源）和**出主意**（`Decision`）。点击、页面判断、安全拦截（不花ジュエル、不按破棄、不点タイトルへ）都在 hiro 里，插件碰不到也绕不过。

## 1. 写一个类

每个插件类带三个属性：`id`（不能叫 `builtin`）、`version`、`contracts`（认的契约主版本，现在是 `"1"`；和 `hiro.contracts.VERSION` 的第一段对不上就不加载）。

**育成插件**：

```python
class MyAdvisor:
    id = "uinet_produce"
    version = "0.1"
    contracts = "1"
    pages = frozenset({"ACTION_SELECT", "SELECT_CARD"})  # 只接这几页

    def begin(self, context): ...  # 每段第一次用到时调：mode（培育 id）、idol、profile、main_attr、trace_dir
    def decide(self, page, state, *, n_options, kw):
        return None  # 不管 → 交回内置决策器；否则返回 hiro.contracts.Decision

    def end(self, result): ...  # 进程结束时调
```

- `state` 是 `hiro.contracts.GameState` 的**副本**（改它不影响主循环）；`state.hidden` 里是数据来源给的、画面上看不到的信息。
- 返回的 `Decision` 要过三道检查，任何一道不过都交回内置决策器，并在这一步的 `warnings` 里写明原因：
  1. 动作类型在这一页的白名单里（`hiro.brain.plugin_gate.ALLOWED_KINDS`）；
  2. 序号不越界等（`ProduceDeciders` 的 `_invalid_decision`）；
  3. 单步超时（默认 60 秒，`strategy.json` 的 `timeouts` 按插件改）；上一步还没返回时这一步直接算超时，不会叠线程。
  格式不对（`action` 不是 `Action`、参数类型错）也算不合法。
- `Decision.source` 不在 `hiro.contracts.decision.SOURCES` 里时记成 `plugin`。
- 内置决策器自己的本局状态（已拿的必拿卡、相談次数…）不会因为你接管了某页而更新，插件要自己管。

**数据来源插件**：

```python
from hiro.plugins import SourceRead


class Hook:
    id = "gkms_hook"
    version = "0.1"
    contracts = "1"
    trust = "save"  # save / screen / infer：和画面冲突时谁赢
    pages = frozenset({"ACTION_SELECT", "HIF_EXAM"})  # None = 全部页面

    def start(self, context): ...
    def read(self, page, frame_t, state):
        return SourceRead(t=..., fields={...}, hidden={...})  # 或 None

    def stop(self): ...
```

- `t` 是跑 hiro 那台机器的本机时间（epoch 秒）；和截图时间差超过 0.5 秒（不管早晚）就不用。
- `fields` 的键必须登记在 `hiro.contracts.FIELD_CATALOG[页面]`，其余的丢掉；画面看不到的东西放 `hidden`。
- `trust` 高于 `screen` 时以插件为准；不高于时，画面这一帧读到的字段保留，插件只补没读到的。值不同会在 trace 里记一条冲突。
- `trust` 是 `screen` / `infer` 的观测和画面读数一样过互验、读错拦截；`save` 档和存档一样当真值。获得弹窗之类的事件只由画面那份发，插件观测不重复发。
- `read` 每帧同步调用，默认超时 1 秒（`timeouts` 里按插件改）；`state` 同样是副本。
- 页面始终由画面判断：插件说在 A 页、画面在 B 页时，插件这次的数据不用。
- **只要收过非空的 `hidden`，这一段对局就会打上 `uses_hidden`**：报告标题写明「用了隐藏信息」，战绩要单独统计，不要和只看画面的对局放在一起比。
- 考试循环通过转接层（`hiro.defaults.exam_bridge.ObservationExamStateSource`）用同样的规则问你的插件，`hidden` 进 `BattleState.hidden`。手牌、饮料可以给完整的 `CardView` / `DrinkView` 字典，也可以只给 `{card_id, upgrade, available, slot}` / `{drink_id}`，框架按主数据补全（和画面识别同一条路）。
  手牌来自插件时，出牌按卡 id 对到屏幕位置，插件的手牌顺序和屏幕不同也不会点错；某格认不出就留占位，一张都认不出就不用插件的手牌。

## 2. 注册

在你的包的 `pyproject.toml` 里写入口点，名字是 `<种类>.<id>`，然后装进 hiro 用的 venv（`pip install -e <你的包>`）：

```toml
[project.entry-points."hiro_gakumas.plugins"]
"produce_advisor.uinet_produce" = "my_pkg.produce:MyAdvisor"
"observation_source.gkms_hook" = "my_pkg.hook:Hook"
```

`hiro plugins list` 能看到装了什么、能不能用、为什么不能用。

## 3. 选用：strategy.json

放在 `HIRO_HOME/strategy.json`（初星助手开局时会把它和本局参数合成一份放进任务目录，设 `HIRO_STRATEGY_FILE`）；直接跑 `run_hif` / `play_game` 时自己设 `HIRO_STRATEGY_FILE`。

```json
{
  "version": 1,
  "sources": ["gkms_hook"],
  "exam": "gnrpa_pro",
  "exam.tower": "builtin",
  "produce": {"ACTION_SELECT": "uinet_produce", "SELECT_CARD": "uinet_produce"},
  "timeouts": {"uinet_produce": 30}
}
```

- 没写的决策点、写 `builtin` 的、配了但没装 / 不能用的，都用内置逻辑，开局照常；配置问题在开局时写进任务事件。`exam.<模式>: "builtin"` 能盖掉全局的 `exam`。
- 出招插件出错、返回的动作不在合法动作里时，这一步交给原来的出招，计入出错。
- 单局覆盖：`hiro run start hif … --plugins produce.ACTION_SELECT=uinet_produce,exam=gnrpa_pro --sources gkms_hook`。
- 育成决策点就是 `ProduceDeciders.decide` 分派表里的页面名，清单在 `hiro.strategy.PRODUCE_PAGES`。

## 4. 看效果

- 每一步：trace 里 `decision.plugin` 是「插件 id@版本」；初星助手对局卡上的来源标签显示「插件 id」。
- 每一段：trace 目录的 `plugins.json`（对应表、每个决策点定了几步 / 交回几步 / 出错几步、加载警告、`uses_hidden`）；`python -m hiro.loop.report <trace>` 的报告里有插件一节。
- 自检：`hiro doctor` 在配了 `strategy.json` 时核对里面的插件都能用。

## 5. 随包插件：GKMS 育成插件（gkms）

仓库自带一个育成插件 `gkms`（`src/hiro/gkms/`，设计稿 `docs/ryugi/specs/2026-11-26-gkms-produce-plugin-design.md`）。它调用本机装的 [GKMS](https://github.com/yuzeis/GKMSProject)：初的选行动用 GKMS 的路线规划（`hajime_route`），选卡类页面用 GKMS 的选卡包 pickv（每张偶像卡 H.I.F 排行榜前 100 条记录里的卡牌频次 V）。GKMS 的代码不在本仓库里，插件只在 GKMS 自己的 Python 子进程里调用它。

**装了 GKMS 就默认启用**，没装照旧用内置逻辑：

| 页面 | GKMS 插件怎么定 |
|---|---|
| `ACTION_SELECT` | 初（四个难度）：GKMS 路线规划从本周排到最后一周，选计划里本周那一项。H.I.F、N.I.A 这一版不管 |
| `SELECT_CARD` | V 最高的那张；刷新收益超过 pickv 的阈值且还有刷新次数时刷新 |
| `SKILL_CARD_CHANGE_1` / `_2` | 换入 V 最高的；换出 V 最低的（跳过トラブル、定制过的卡） |
| `SKILL_CARD_REMOVAL` | 先删トラブル，再删 V 最低的；没有能删的就取消 |
| `SKILL_CARD_ENHANCE` | 没强化过的卡里「V × 强化率」最高的 |
| `CONSULT` / `HIF_ROUND_INTERVAL` | 内置先看去掉卡的页面（饮料、回体力照它做）；它要离开时插件按 V 买 / 删 / 强化卡。H.I.F 選抜留出特別指導的钱，本戦的相談给轮间留 200P |

初、N.I.A 没有自己的选卡包，用同一张偶像卡的 H.I.F 包近似，理由里会写「pickv（H.I.F 的包）」。

- **什么算装了**：能找到 GKMS 目录（`HIRO_GKMS_HOME` → Windows `%LOCALAPPDATA%\Programs\GKMS` → `~/github/_ext/GKMSProject`），里面有 `GakumasCLI/produce/{hajime_route,nia_route,pickv}.py` 和主数据目录。Python 用 GKMS 自带的（安装版 `python/`、克隆的 `.venv`），没有就用 hiro 自己的；`HIRO_GKMS_PYTHON` 可以指定。`hiro plugins list` 会写清能不能用、为什么。
- **选卡包**：从 GKMS 网站公开接口下载（默认 `http://m5.ctymc.cn:23331`，设 `GKMS_HUB_URL` 可换），缓存在 GKMS 目录里；第一次用到某张偶像卡最多等 10 秒，下不到这一步就交回内置。
- **日程列表**：GKMS 接管选行动时，初 / N.I.A 第一次到选行动页会自动点开日程列表读整局日程（`HIRO_SCHEDULE_PROBE=0` 关掉；关掉后选行动拿不到日程，这一步交回内置）。
- **关掉**：某页在 `strategy.json` 写 `"builtin"`（例如 `"produce": {"ACTION_SELECT": "builtin"}`）只关这一页；`HIRO_GKMS=0` 整个关。
- **记录**：每步的理由写明用的是「GKMS 路线」还是「pickv」；trace 目录的 `gkms.jsonl` 记每步的输入摘要、GKMS 的输出和不管的原因；`plugins.json` 的 `mapping.defaults` 列出默认接管的决策点。
- **离线对比**：`PYTHONPATH=src python tools/gkms_compare.py <trace 目录…> [--idol-default <偶像卡>]`，在记录过的局面上问 GKMS 插件、和当时的选择比，报告写到 `reports/gkms-compare/`。
- **测试**：`tests/conftest.py` 默认设 `HIRO_GKMS=0`，免得本机装没装 GKMS 影响其余测试；`tests/test_gkms_*.py` 里要真 GKMS 的用例在没装时跳过（`HIRO_GKMS_HOME` 指过去就跑）。
