"""离线识别：把截图缩放到 720x1280（识别函数用的逻辑坐标），设为当前画面（hiro.devices.active.show），测试和离线工具用。

经 hiro.devices.active 截图的执行层代码（没接上主循环时）读到的就是这张，点击不做事。
原来用 KAA 的 MockDevice 把图喂进 kotonebot 上下文，2026-11-24 随 KAA 主循环一起去掉了。
"""

import cv2
from cv2.typing import MatLike


def load(img: MatLike) -> MatLike:
    """把截图设为当前画面（缩放到 720x1280，已经是就原样），返回实际使用的图。"""
    from hiro.devices import active

    if img.shape[:2] != (1280, 720):
        img = cv2.resize(img, (720, 1280))
    active.show(img)
    return img
