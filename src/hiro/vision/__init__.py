"""识屏层：截图 → 页面判定与字段读数（学園アイドルマスター H.I.F）。

不带任何游戏素材：卡面 / 图标 / 字体 / 界面 Sprite / 支援卡图通过插件 AssetSource（hiro.plugins，默认
hiro.defaults.assets.PublicAssetSource = KAA 公开数据 + 使用者自建目录）取；拿不到时各读数退回 OCR 或给 None。
界面文字小模板（templates/）是截图裁的标签 / 按钮字样。实机识别依赖 KAA（可选组 play，子模块 vendor/kaa）。

- 判页：scene.classify_current（KAA + 签名）、quick.quick_page / fallback_page（模板 / 像素快速判页）、pages.classify_page（OCR 页面地图）
- 读数：extract.extract(scene, img) → 原始字段；observe.observation(...) → 契约 Observation
- 接入自己的识别器：observe.register_adapter(scene, fn) 注册某个页面的字段转换
"""
