# 应用图标

`app-icon.svg` 是源文件（用户 11-23 选的 A 版）：初星攻略站 logo 的三道斜杠（Vo 粉 #E5508A / Da 蓝 #3D7FD9 / Vi 黄 #F2B51E），
白底圆角方块、浅灰描边，按 macOS 图标网格画（1024 画布、824 内容区）。斜杠图形取自 gakumas-bench 的 `web/public/favicon.svg`。

`../resources/icon.png` 是它渲染的 1024×1024 PNG。`mygo build` 从这张 PNG 生成 macOS 的 `.icns` 和 Windows 的 `.ico`
（16、24、32、48、64、128、256 各一档），Dock、任务栏、安装包都用它。改图标时改 SVG，再导出 PNG 覆盖：

```bash
magick -background none icon/app-icon.svg -resize 1024x1024 resources/icon.png
```
