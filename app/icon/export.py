"""从 icon/hiro-mark.png 导出 resources/icon.png（1024，macOS 图标网格：824 内容区、圆角 185、四周透明）
和 kit/logo.png（128，界面里的标志）。在 app/ 下用仓库 .venv 的 Python（带 OpenCV）运行：

    ../.venv/bin/python icon/export.py
"""

import cv2
import numpy as np

src = cv2.imread("icon/hiro-mark.png", cv2.IMREAD_COLOR)

# 1024 图标：824 的圆角方块居中
CANVAS, CONTENT, RADIUS = 1024, 824, 185
body = cv2.resize(src, (CONTENT, CONTENT), interpolation=cv2.INTER_AREA)
# 圆角蒙版先在 4 倍大小上画再缩回来，边缘是平滑的
k = 4
big = np.zeros((CONTENT * k, CONTENT * k), np.uint8)
r = RADIUS * k
cv2.rectangle(big, (r, 0), (CONTENT * k - r, CONTENT * k), 255, -1)
cv2.rectangle(big, (0, r), (CONTENT * k, CONTENT * k - r), 255, -1)
for cx, cy in ((r, r), (CONTENT * k - r - 1, r), (r, CONTENT * k - r - 1), (CONTENT * k - r - 1, CONTENT * k - r - 1)):
    cv2.circle(big, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
mask = cv2.resize(big, (CONTENT, CONTENT), interpolation=cv2.INTER_AREA)
icon = np.zeros((CANVAS, CANVAS, 4), np.uint8)
o = (CANVAS - CONTENT) // 2
icon[o : o + CONTENT, o : o + CONTENT, :3] = body
icon[o : o + CONTENT, o : o + CONTENT, 3] = mask
cv2.imwrite("resources/icon.png", icon)

# 128 标志（方图，界面里自己加圆角）
cv2.imwrite("kit/logo.png", cv2.resize(src, (128, 128), interpolation=cv2.INTER_AREA))
print("ok")
