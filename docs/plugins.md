# 写 hiro 插件

hiro 的运行时有五个地方可以换成你自己的实现，主循环不用改。设计见 `docs/ryugi/specs/2026-11-25-strategy-plugins-design.md`，能跑的例子在 `examples/plugins/hiro_example_plugins/`。

| 种类 | 回答什么 | 接口（`hiro.plugins`） | 怎么选用 |
|---|---|---|---|
| `observation_source` | 不看画面也能拿到的游戏数据（hook、存档…） | `ObservationSource` | `strategy.json` 的 `sources`（只能按 id 取用，没有环境变量） |
| `produce_advisor` | 育成某几页怎么选 | `ProduceAdvisor` | `strategy.json` 的 `produce.<页面>`（只能按 id 取用，没有环境变量） |
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

    def begin(self, context): ...  # 每段第一次用到时调，内容见下
    def decide(self, page, state, *, n_options, kw):
        return None  # 不管 → 交回内置决策器；否则返回 hiro.contracts.Decision

    def end(self, result): ...  # 进程结束时调，result 见下
```

- `begin(context)` 拿到：`mode`（培育 id）、`family`（hif / hajime / nia）、`difficulty`（regular / pro / master / legend，H.I.F 是 selection / finals）、`idol`、`profile`、`main_attr`、`formation_plan`（开局编成方案的副本，没用方案时 `None`）、`guides`（偶像卡攻略包概况：`enabled`、`dir`、`packs`（模式 → 包名）、`pack`（这一局模式的包名）；条目本身用 `hiro.brain.guides.entries(模式)` / `lookup()` 取）、`trace_dir`。认不出的培育 id，`family` / `difficulty` 是 `None`。
- `begin` 抛异常：记一次错，这个插件之后的页面都交回内置，不再调它的 `decide` / `end`。
- `end(result)` 的 `result` 是本进程这一段攒下的结果：`trace_dir`、`final_page`（最后停在哪一页）、`results`（每次看到的结算页读数，按先后排，画面读到的和数据来源插件补读 / 覆盖的字段并在同一条里，和游戏状态里采用的一致：每个键是最后采用的值；`page` 是 `HIF_EXAM_RESULT` / `HIF_FINALS_RANKING`，其余是读到的字段，如 `exam_no`、`score`、`rank`、`passed`；整张排行榜 `entries` 不放）、`last_result`（`results` 的最后一条，没有为 `None`）。续跑的上一段不在里面，要的话去那一段的 trace。

- `state` 是 `hiro.contracts.GameState` 的**副本**（改它不影响主循环）；`state.hidden` 里是数据来源给的、画面上看不到的信息，和页面字段一样只管当前页，换页就清空（要跨页留着自己存）。
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
- `read` 每帧同步调用，默认超时 1 秒（`timeouts` 里按插件改）；`state` 同样是副本。`start` 抛异常：记一次错，之后不再问这个来源，也不调它的 `stop`。
- 页面始终由画面判断：插件说在 A 页、画面在 B 页时，插件这次的数据不用。
- **只要收过非空的 `hidden`，这一段对局就会打上 `uses_hidden`**：报告标题写明「用了隐藏信息」，战绩要单独统计，不要和只看画面的对局放在一起比。
- 考试循环通过转接层（`hiro.defaults.exam_bridge.ObservationExamStateSource`）用同样的规则问你的插件，`hidden` 进 `BattleState.hidden`。手牌、饮料可以给完整的 `CardView` / `DrinkView` 字典，也可以只给 `{card_id, upgrade, available, slot}` / `{drink_id}`，框架按主数据补全（和画面识别同一条路）。
  手牌来自插件时，出牌按卡 id 对到屏幕位置，插件的手牌顺序和屏幕不同也不会点错；某格认不出就留占位，一张都认不出就不用插件的手牌。只有体力、分数这类数值来自存档级插件、手牌还是画面的，出牌照画面顺序点，不按卡 id 找位置（局面可信度仍记「存档」）。原来的局面来源（私有存档插件）按存档顺序给手牌的，这个规则照旧保留，数据来源插件只补数值也不会改掉它。

## 2. 注册

在你的包的 `pyproject.toml` 里写入口点，名字是 `<种类>.<id>`，然后装进 hiro 用的 venv（`pip install -e <你的包>`）：

```toml
[project.entry-points."hiro_gakumas.plugins"]
"produce_advisor.uinet_produce" = "my_pkg.produce:MyAdvisor"
"observation_source.gkms_hook" = "my_pkg.hook:Hook"
```

`hiro plugins list` 能看到装了什么、能不能用、为什么不能用。注意：`plugins list`、`hiro doctor` 和每局开局的资源清单，都要把已装的插件实例化一次才知道能不能用，所以插件的 `__init__` 要尽量轻（别在里面连设备、读大文件、起线程）。

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
- 出招插件出错、返回的动作不在合法动作里时，这一步交给原来的出招，计入出错。`on_round_start` 抛异常时，这一场剩下的步骤都交给原来的出招，下一场开局再试。
- 「定几步」按动作真的在游戏里生效来计：决策帧过时没点、画面对不上、牌没打出去、点了画面没变的步骤作废，不算插件定的；只是补点了停在选中态的 SELECT、或再点超限被取消选中（本回合禁用）的也不算。
- 单局覆盖：`hiro run start hif … --plugins produce.ACTION_SELECT=uinet_produce,exam=gnrpa_pro --sources gkms_hook`。
- 育成决策点就是 `ProduceDeciders.decide` 分派表里的页面名，清单在 `hiro.strategy.PRODUCE_PAGES`。

## 4. 看效果

- 每一步：trace 里 `decision.plugin` 是「插件 id@版本」；初星助手对局卡上的来源标签显示「插件 id」。
- 没装插件、没配 `strategy.json` 时，trace 和每局报告里不会多出 `by` / `trust` / `hidden` / `plugin` / `plugins` / `uses_hidden` 这些字段，和插件架构之前一样。
- 每一段：trace 目录的 `plugins.json`（对应表、每个决策点定了几步 / 交回几步 / 出错几步、加载警告、`uses_hidden`）；`python -m hiro.loop.report <trace>` 的报告里有插件一节。
- 自检：`hiro doctor` 在配了 `strategy.json` 时核对里面的插件都能用。
