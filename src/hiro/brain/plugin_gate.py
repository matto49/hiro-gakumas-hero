"""育成插件的闸门（2026-11-25 插件架构 spec 4.5 / 4.7 / 4.8）：按 strategy.json 找这一页的插件，带超时调用，校验输出，
不合格就交回内置决策器（ProduceDeciders.decide 现有逻辑一行不改）。

插件输出要过三道：类型是 Decision；动作类型在这一页的白名单里；ProduceDeciders 的 _invalid_decision（序号越界等）。
白名单 = 内置决策器在这一页（含它调用的辅助方法）会输出的动作类型，去掉 Recover（恢复是主循环的事）。
"""

from __future__ import annotations

import copy
import logging
import threading
from collections.abc import Callable
from typing import Any

from hiro.contracts import ActionKind, Decision
from hiro.contracts.decision import SOURCES

logger = logging.getLogger(__name__)

_W, _C, _N = ActionKind.WAIT, ActionKind.CONTINUE, ActionKind.NOOP
_COMMON = frozenset({_W, _C, _N})
ALLOWED_KINDS: dict[str, frozenset[ActionKind]] = {
    "ACTION_SELECT": _COMMON | {ActionKind.PRODUCE_NEXT},
    "SELECT_CARD": _COMMON | {ActionKind.ACTIVATE_EFFECT, ActionKind.EXCLUDE_CARD, ActionKind.REROLL},
    "SELECT_DRINK": _COMMON | {ActionKind.ACTIVATE_EFFECT, ActionKind.EXCLUDE_CARD, ActionKind.DISCARD_DRINK},
    "CONSULT": frozenset({ActionKind.REROLL, ActionKind.SHOP_BUY, ActionKind.SHOP_END, ActionKind.SHOP_UPGRADE_CARD}),
    "STUDY": frozenset({ActionKind.EVENT_SELECT}),
    "OUTING": frozenset({ActionKind.EVENT_SELECT}),
    "HIF_ROUND_INTERVAL": _COMMON
    | {ActionKind.INTERVAL_BUY, ActionKind.INTERVAL_END, ActionKind.INTERVAL_UPGRADE_CARD, ActionKind.REROLL},
    "SELECT_PITEM": _COMMON | {ActionKind.ACTIVATE_EFFECT},
    "EVENT_CHOICE": _COMMON | {ActionKind.EVENT_SELECT},
    "EXAM_CARD_MOVE": _COMMON | {ActionKind.ACTIVATE_EFFECT},
    "PDRINK_MAX": _COMMON | {ActionKind.DISCARD_DRINK},
    "SPECIAL_GUIDANCE": frozenset({ActionKind.CUSTOMIZE_CARD, ActionKind.CUSTOMIZE_END}),
    "FORMATION": frozenset({ActionKind.SELECT_FORMATION}),
    "TOWER_FORMATION": _COMMON | {ActionKind.SELECT_FORMATION},
    "TOWER_RETRY": frozenset({ActionKind.DIALOG}),
    "QUALIFY_MEMORY": _COMMON | {ActionKind.SELECT_QUALIFY_MEMORY},
    "SKILL_CARD_CHANGE_1": _COMMON | {ActionKind.CHANGE_CARD},
    "SKILL_CARD_CHANGE_2": frozenset({ActionKind.CHANGE_CARD}),
    "SKILL_CARD_REMOVAL": frozenset({ActionKind.REMOVE_CARD}),
    "SKILL_CARD_ENHANCE": _COMMON | {ActionKind.ENHANCE_CARD},
    "PDRINK_MAX_CONFIRM": frozenset({ActionKind.DIALOG}),
    "NIA_SALES_PLACE": _COMMON | {ActionKind.EVENT_SELECT},
    "NIA_SALES_EVENT": _COMMON | {ActionKind.EVENT_SELECT},
    "NIA_AUDITION_SELECT": _COMMON | {ActionKind.EVENT_SELECT},
    "EXAM_RETRY": frozenset({ActionKind.DIALOG}),
}

_begun: set[str] = set()
_warned: set[str] = set()


def reset() -> None:
    """测试用。"""
    _begun.clear()
    _warned.clear()
    _running.clear()


class PluginTimeout(Exception):
    pass


_running: dict[str, threading.Thread] = {}  # 插件 → 还没返回的那次调用的线程


def call_with_timeout(fn: Callable[[], Any], seconds: float, *, key: str | None = None) -> Any:
    """在后台线程里跑 fn，最多等 seconds 秒；超时抛 PluginTimeout（线程是 daemon，留着跑完自己退出，结果丢掉）。
    给了 key 时，同一个 key 上一次调用还没返回就直接算超时、不再起新线程（插件挂住时线程不会越积越多）。"""
    if key is not None and (prev := _running.get(key)) is not None and prev.is_alive():
        raise PluginTimeout("上一次调用还没返回")
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001  插件的任何异常都交给调用方处理
            box["error"] = e

    t = threading.Thread(target=run, name="hiro-plugin", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        if key is not None:
            _running[key] = t
        raise PluginTimeout(f"超时（{seconds:g} 秒）")
    if key is not None:
        _running.pop(key, None)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _warn_once(key: str, text: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(text)
        from hiro.plugin_record import RECORD

        RECORD.warn(text)


def produce_decide(
    page: str,
    state: Any,
    n_options: int | None,
    kw: dict[str, Any],
    *,
    context: Callable[[], dict[str, Any]],
    invalid: Callable[[str, Decision, int | None, dict], str | None],
) -> tuple[Decision | None, str | None]:
    """这一页配了插件时让插件先定。返回 (插件的决定, None) 或 (None, 交回内置的原因；插件只是不管时为 None)。"""
    from hiro import plugins, strategy
    from hiro.plugin_record import RECORD

    pid = strategy.current().produce_for(page)
    if pid is None:
        return None, None
    point = f"produce.{page}"
    plugin = plugins.get_by_id("produce_advisor", pid)
    if plugin is None:
        _warn_once(f"missing:{pid}", f"strategy.json 配了育成插件 {pid!r}，但它没装或不能用，相关页面用内置决策器")
        return None, None
    if page not in (getattr(plugin, "pages", None) or ()):
        _warn_once(f"pages:{pid}:{page}", f"育成插件 {pid!r} 没声明接 {page}，这一页用内置决策器")
        return None, None
    lab = plugins.label(plugin)
    if pid not in _begun:
        _begun.add(pid)
        RECORD.on_exit(lambda: plugin.end(None))
        try:
            plugin.begin(context())
        except Exception as e:  # noqa: BLE001
            why = f"插件 {lab} begin 出错：{type(e).__name__}: {e}"
            RECORD.error(point, lab, why)
            return None, why
    from hiro import strategy as st

    view = copy.deepcopy(state)  # 插件拿副本：超时后它的线程还在跑，也碰不到主循环的状态
    try:
        d = call_with_timeout(
            lambda: plugin.decide(page, view, n_options=n_options, kw=copy.deepcopy(dict(kw))),
            st.current().timeout_for(pid),
            key=f"produce:{pid}",
        )
    except PluginTimeout as e:
        why = f"插件 {lab} {e}，交回内置决策器"
        RECORD.error(point, lab, why)
        return None, why
    except Exception as e:  # noqa: BLE001
        why = f"插件 {lab} 出错（{type(e).__name__}: {e}），交回内置决策器"
        RECORD.error(point, lab, why)
        return None, why
    if d is None:
        RECORD.deferred(point, lab)
        return None, None
    if not isinstance(d, Decision):
        why = f"插件 {lab} 返回的不是 Decision（{type(d).__name__}），交回内置决策器"
        RECORD.error(point, lab, why)
        return None, why
    try:  # 格式不对（action 不是 Action、参数类型错）时校验本身会抛，一律按不合法处理
        kind = d.action.kind
        if kind not in ALLOWED_KINDS.get(page, frozenset()):
            bad = f"在 {page} 输出了 {getattr(kind, 'value', kind)}，这一页不允许"
        else:
            bad = invalid(page, d, n_options, kw)
    except Exception as e:  # noqa: BLE001
        bad = f"格式不对（{type(e).__name__}: {e}）"
    if bad is not None:
        why = f"插件 {lab} 的输出不合法：{bad}，交回内置决策器"
        RECORD.error(point, lab, why)
        return None, why
    if d.source not in SOURCES:
        d.source = "plugin"
    d.plugin = lab
    RECORD.decided(point, lab)
    return d, None
