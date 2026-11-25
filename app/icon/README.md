# 应用图标

`hiro-mark.png` 是源图（用户 11-25 选的 3 号，人物放大到 120%，连星星一起上下居中）：橙底（#EE6A1F）白色单色剪影，Q 版篠澤広从下边探头、发夹和半睁眼是镂空、
旁边一颗四角星（初星）。照 DeepSeek Harness 的鲸鱼标志那种扁平单色画法，用 codex 生图画的（参考图是 gakumas-sprites 里的 Q 版常服广），
1254×1254，满版方图。

由它导出两张：

- `../resources/icon.png`：1024×1024，按 macOS 图标网格（824 内容区、圆角 185、四周透明）。`mygo build` 从这张生成 macOS 的 `.icns`
  和 Windows 的 `.ico`（16–256 各一档），Dock、任务栏、安装包都用它。
- `../kit/logo.png`：128×128，界面里的标志（侧栏、第一次使用、新对话页没有偶像卡面时），由 `kit.Logo` 画成圆角。

改图标时换掉 `hiro-mark.png`，再在 `app/` 下用仓库 `.venv` 的 Python（带 OpenCV）导出：

```bash
../.venv/bin/python icon/export.py
```

（ImageMagick 7 对没有透明通道的源图做圆角蒙版会整张变透明，所以不用它。）
