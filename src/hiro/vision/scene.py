"""执行层（HifCore / Session.verify / resume）的判页入口：截一张当前画面分类，结果换回旧的 SceneResult 和旧页面名。

判页本身已经是 hiro.vision.classify.classify_scene（纯函数、不依赖 KAA，和原来 KAA check_scene 那条链逐帧一致）。
截图走 hiro.devices.active（主循环 Runtime 的设备）。
新主循环的逐拍判页直接用 classify.classify。
"""

import time
from dataclasses import dataclass

from hiro.devices.active import device
from hiro.vision.classify import classify_scene

# 新判页给的名字 → 旧主循环认的名字（行为不变）：开局二选一 KAA 判页时双击第一项后报 IDLE；横屏没有单独的页面
_OLD_NAME = {"INITIAL_CHOICE": "IDLE", "LIVE": "unknown"}


@dataclass
class SceneResult:
    area: str  # produce / outside / unknown
    scene: str
    elapsed_ms: float


def classify_current() -> SceneResult:
    """截一张当前画面分类。"""
    start = time.perf_counter()
    hit = classify_scene(device.screenshot())
    name = _OLD_NAME.get(hit.name, hit.name)
    return SceneResult("unknown" if name == "unknown" else hit.area, name, (time.perf_counter() - start) * 1000)
