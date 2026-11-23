"""偶像之路（Idol Road）在新主循环上的打法规则：选哪一层、打完一层之后重试 / 下一层 / 停，以及再挑戦确认框上的消耗词。
判页和读字段在 hiro.vision（等 Mac 采样补）；编成交决策层 TOWER_FORMATION（3940a9），考试走 run_exam + bench 中转层（8a3a1e7）。

用户 / 协调 11-14 定的规则：
- 目标三星通关：选层 = 第一个「没满三星」的层（没通过的层自然也没满三星），不选这一轮已经放弃三星的层；配置可限最高层。
- 每层最多打 MAX_TRIES 次（再挑戦免费）。三星 → 下一层；没到三星且还有次数 → 重试；
  次数用完：已过关 → 记下「过关但没满三星」，往下一层走，不停机；没过关 → 停机交给人。
- 再挑戦确认框上只要出现消耗字样（DIALOG_WORDS）就停，宁严勿宽；同一批词也交给 GuardedDevice（Runtime.dialog_words），不只靠判页。

星按名次给（GKMS 抓包，8a3a1e7 11-14）：★1 = 第 3 名、★2 = 第 2 名、★3 = 第 1 名；26、27 层的★3 另要一个通关时条件。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_TRIES = 3
DIALOG_WORDS = ("ジュエル", "アイテム", "チケット", "回数")  # 再挑戦确认框上出现任一个就不点 OK


def retry_cost(dialog: dict | None) -> str | None:
    """再挑戦确认框（hiro.vision.pages.read_dialog 的结果）上的消耗字样；没有返回 None。读不到弹窗也当有消耗（不确定就不点）。"""
    if not dialog:
        return "（没读到确认框）"
    text = dialog.get("title", "") + dialog.get("body", "")
    return next((w for w in DIALOG_WORDS if w in text), None)


@dataclass
class TowerRun:
    """一次运行里偶像之路的进度：每层打了几次、最好几颗星、哪些层过关但放弃了三星。"""

    tower_id: str
    max_layer: int | None = None  # 最多打到第几层（含）；None 不限
    tries: dict[int, int] = field(default_factory=dict)
    best: dict[int, int] = field(default_factory=dict)
    cleared: set[int] = field(default_factory=set)  # 结果页判过关（クリア）的层；过关和星数分开记，不从星数推
    gave_up_three: list[int] = field(default_factory=list)  # 过关但次数用完没满三星的层（往下走，报告里列出）
    rankings: dict[int, list[dict]] = field(default_factory=dict)  # 每层每次考完的名次页读数（名次 + 各人分数）

    def pick_layer(self, layers: list[dict]) -> int | None:
        """层选择页读到的各层 [{layer, stars, unlocked}] → 要打的层；没有可打的（都满三星 / 没解锁 / 超过上限 / 放弃）返回 None。"""
        for row in sorted(layers, key=lambda r: r["layer"]):
            n = row["layer"]
            if self.max_layer is not None and n > self.max_layer:
                return None
            if not row.get("unlocked", True):
                return None  # 后面的层更没解锁
            if (row.get("stars") or 0) >= 3 or n in self.gave_up_three:
                continue
            return n
        return None

    def after_result(self, layer: int, cleared: bool, stars: int) -> str:
        """打完一次 → "next"（下一层）/ "retry"（同层再挑戦）/ "stop"（停机交给人）。"""
        self.tries[layer] = self.tries.get(layer, 0) + 1
        self.best[layer] = max(self.best.get(layer, 0), stars)
        if cleared:
            self.cleared.add(layer)
        if cleared and stars >= 3:
            return "next"
        if self.tries[layer] < MAX_TRIES:
            return "retry"
        if layer in self.cleared:  # 这层某一次过关了，只是没满三星
            self.gave_up_three.append(layer)
            return "next"
        return "stop"

    def summary(self) -> dict:
        return {
            "tower_id": self.tower_id,
            "tries": dict(self.tries),
            "best_stars": dict(self.best),
            "cleared": sorted(self.cleared),
            "gave_up_three": list(self.gave_up_three),
            "rankings": dict(self.rankings),
        }
