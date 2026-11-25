"""识屏层：截图 → 页面判定与字段读数（学園アイドルマスター H.I.F）。

卡面 / 图标 / 字体 / 界面 Sprite / 支援卡图通过插件 AssetSource（hiro.plugins，默认
hiro.defaults.assets.PublicAssetSource = 公开游戏图 resources/game_data + 从模拟器主数据生成的主数据库 + 随仓库素材包 assets/pack + 使用者自建目录）取；
拿不到时各读数退回 OCR 或给 None。界面文字小模板（templates/）是截图裁的标签 / 按钮字样。实机识别要装可选组 play（OCR / 模板匹配依赖）。

- 判页：classify.classify / classify_scene（纯函数）、quick.quick_page / fallback_page（模板 / 像素快速判页）、pages.classify_page（OCR 页面地图）
- 读数：extract.extract(scene, img) → 原始字段；observe.observation(...) → 契约 Observation
- 接入自己的识别器：observe.register_adapter(scene, fn) 注册某个页面的字段转换
"""
