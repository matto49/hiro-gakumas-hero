"""三个插件接口：素材从哪来、考试局面从哪来、考试怎么打。

开源仓库只带默认实现（公开数据 + 画面识别 + 社区规则 / LLM）。自己有更强实现的人（比如自建素材包、
别的局面来源、带模拟器的搜索）可以写一个类实现同名方法，再用下面任一方式接进来，主循环不用改：

- 环境变量：HIRO_ASSET_SOURCE / HIRO_EXAM_STATE_SOURCE / HIRO_EXAM_ADVISOR = "包.模块:类名"
- 入口点（pyproject.toml）：[project.entry-points."hiro_gakumas.plugins"]，名字用 asset_source / exam_state_source / exam_advisor
- 代码里：hiro.plugins.register("exam_advisor", 你的类)

查找顺序：register() > 环境变量 > 入口点 > 默认实现。每个方法拿不到就返回 None（不要猜），调用方各自有退路。
"""

from __future__ import annotations

import importlib
import os
import sqlite3
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:  # 只用于类型标注，避免 plugins 依赖具体层
    from hiro.contracts import Action, Decision, ExecutionResult, Preview

Confidence = Literal["save", "screen"]


@runtime_checkable
class AssetSource(Protocol):
    """游戏素材与主数据。默认实现：KAA 公开数据 + 随仓库的识别素材包 assets/pack/ + 使用者自建目录（见 hiro.defaults.assets）。"""

    def master_db(self) -> sqlite3.Connection | None:
        """卡牌 / 饮料 / P 道具 / 支援卡主数据（KAA game.db 同结构）。"""

    def card_art_dir(self) -> Path | None:
        """技能卡卡面图（识别手牌、选卡）。"""

    def icon_dir(self, kind: str) -> Path | None:
        """图标目录，kind 如 "drink" / "pitem" / "status"。"""

    def font_path(self) -> Path | None:
        """数字字体（HUD 读数比对）。"""

    def layout(self) -> dict[str, Any] | None:
        """界面布局校准（各区域坐标）。"""

    def sprites_dir(self) -> Path | None: ...

    def support_dir(self) -> Path | None:
        """支援卡图（编成页识别）。"""

    def card_catalog(self) -> dict[str, dict[str, Any]] | None:
        """卡牌目录：卡 id → 名称、稀有度、效果摘要、是否一次性等。"""

    def drink_catalog(self) -> dict[str, dict[str, Any]] | None: ...

    def pitem_catalog(self) -> dict[str, dict[str, Any]] | None: ...

    def schedule(self, scenario: str) -> list[dict[str, Any]] | None:
        """培育日程，scenario 如 "qualify" / "main"。列表下标 0 对应第 1 个阶段，每项：
        {"day": int, "kind": "choice" | "exam" | "round1" | ..., "options": [...], "training_focus"?: str,
         "open_lesson_ids"?: [...], "exam_number"?: int}。
        **选行动页（ACTION_SELECT）离不开它**：返回 None 时决策器只能等待，整局会卡住，所以默认实现必须给出。"""

    def support_effects(self, support_id: str, level: int, *, limit_rank: str | None = None, plan: str | None = None) -> list[dict[str, Any]] | None:
        """支援卡技能：[{"effect_type", "value", "trigger_id", "limit"}]；None 时编成只按属性张数 / 等级排。
        limit_rank：突破段（None = 等级够得着的最低段）；plan：培育方案（ProducePlanType_*，None = 卡自己的方案）。
        老实现只接 (support_id, level) 也行：调用方先带这两个参数，不接受时退回两参数调用。"""

    def exam_requirements(self, scenario: str) -> dict[str, Any] | None:
        """考试要求、属性 → 倍率、星级基准（选课 / 课程预演用）。返回函数表：
        {"preview": f(mode, pos, params, idol_card_id), "assessment": f(value, requirement),
         "star_baseline": f(mode, pos, idol_card_id), "star_factor": f(star, baseline),
         "exam_bonus": f(mode, pos, params, star, idol_card_id)}。
        公开数据里没有可靠来源时默认返回 None：选课退回补最低属性 / 主属性规则，相关提示行写「未知」。"""


@runtime_checkable
class ExamStateSource(Protocol):
    """考试局面从哪来。默认实现：画面识别 + 状态机记账（confidence="screen"，牌库顺序未知）。"""

    def begin(self, context: dict[str, Any]) -> None:
        """一场考试开始（context：轮次、角色、牌组、饮料、P 道具等已知信息）。"""

    def read(self, frame: Any, memory: Any) -> tuple[Any | None, str]:
        """当前局面 → (局面, 来源说明)。局面类型是 hiro.brain.state.BattleState；读不到返回 (None, 原因)。"""

    def confidence(self) -> Confidence:
        """上一次 read 的可信度：save = 精确局面；screen = 画面推算，决策不要依赖牌库顺序。"""

    def executed(self, action: Action, result: ExecutionResult) -> None:
        """执行器做完一个动作后通知（用于记账 / 续推）。"""

    def previews(self, state: Any) -> list[Preview] | None:
        """可选：给决策用的各候选预演结果；默认实现没有，返回 None。"""

    def end(self, result: dict[str, Any] | None) -> None: ...


@runtime_checkable
class ExamAdvisor(Protocol):
    """考试里每一步怎么打。默认实现：社区规则 + LLM 兜底，不需要模拟器。"""

    def on_round_start(self, round_no: int, context: dict[str, Any]) -> None: ...

    def decide(self, state: Any, memory: Any, previews: list[Preview] | None, legal: list[Action]) -> Decision:
        """返回契约 Decision；只能从 legal 里选。previews 可能是 None。"""

    def on_round_end(self, result: dict[str, Any] | None) -> None: ...


KINDS: dict[str, tuple[type, str, str]] = {
    # 种类 → (接口, 环境变量, 默认实现 "模块:类")
    "asset_source": (AssetSource, "HIRO_ASSET_SOURCE", "hiro.defaults.assets:PublicAssetSource"),
    "exam_state_source": (ExamStateSource, "HIRO_EXAM_STATE_SOURCE", "hiro.defaults.exam_state:ScreenExamStateSource"),
    "exam_advisor": (ExamAdvisor, "HIRO_EXAM_ADVISOR", "hiro.defaults.advisor:RuleLLMAdvisor"),
}
_registered: dict[str, Callable[[], Any]] = {}
_cache: dict[str, Any] = {}


def register(kind: str, factory: Callable[[], Any]) -> None:
    """代码里直接指定实现（优先级最高）。factory 可以是类本身。"""
    if kind not in KINDS:
        raise KeyError(f"未知插件种类：{kind}（可选 {sorted(KINDS)}）")
    _registered[kind] = factory
    _cache.pop(kind, None)


def _load(spec: str) -> Callable[[], Any]:
    mod, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"插件写法应为 包.模块:类名，收到 {spec!r}")
    return getattr(importlib.import_module(mod), attr)


def _from_entry_point(kind: str) -> Callable[[], Any] | None:
    eps = [ep for ep in entry_points(group="hiro_gakumas.plugins") if ep.name == kind]
    return eps[0].load() if eps else None


def source(kind: str) -> str:
    """当前生效的实现来自哪里（写进 trace / 日志，方便复现）。"""
    if kind in _registered:
        return "register"
    if os.environ.get(KINDS[kind][1]):
        return f"env:{os.environ[KINDS[kind][1]]}"
    try:
        if _from_entry_point(kind):
            return "entry_point"
    except Exception:  # noqa: BLE001 入口点损坏时按默认处理，get() 会再报
        pass
    return f"default:{KINDS[kind][2]}"


def get(kind: str) -> Any:
    """取某种插件的实例（进程内单例）。实例要满足对应 Protocol，否则报错。"""
    if kind in _cache:
        return _cache[kind]
    proto, env, default = KINDS[kind]
    factory = _registered.get(kind)
    if factory is None and os.environ.get(env):
        factory = _load(os.environ[env])
    if factory is None:
        factory = _from_entry_point(kind)
    if factory is None:
        factory = _load(default)
    obj = factory()
    if not isinstance(obj, proto):
        missing = [m for m in dir(proto) if not m.startswith("_") and not hasattr(obj, m)]
        raise TypeError(f"{kind} 实现 {type(obj).__name__} 缺少方法：{missing}")
    _cache[kind] = obj
    return obj


def reset() -> None:
    """测试用：清掉注册和缓存。"""
    _registered.clear()
    _cache.clear()
