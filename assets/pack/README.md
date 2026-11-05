# 随仓库的识别素材包

识别层认牌、认图标、读 HUD 数字、判页面用的素材，开箱即用，不需要再跑 `capture_icons`。目录结构同 `docs/assets.md` 的「自建素材」：

| 目录 | 内容 |
|---|---|
| `cards/` | 技能卡卡面（2290 张） |
| `icons/status/` | 考试效果图标（やる気、好印象 等，93 个） |
| `icons/drink/` | P 饮料图标（29 个） |
| `icons/pitem/` | P 道具图标（532 个，`index.json` 为资源名到名称） |
| `font/` | HUD 数字字体（`Campus-Alphanumeric-SemiBold.ttf` 等 4 个） |
| `sprites/` | 界面 Sprite（页面签名 `hiro/vision/signatures.json` 引用） |
| `support/` | 支援卡图（编成页识别，210 张） |
| `layout.json` | 界面布局（各控件的位置） |
| `manifest.json` | 生成时的客户端版本、主数据提交和各类数量 |

## 版权

这些图片、字体和布局数据来自《学園アイドルマスター》的客户端，**权利属于游戏的权利人（QualiArts、バンダイナムコエンターテインメント 等），
不属于本仓库的 GPL-3.0-or-later**。本仓库的许可只覆盖代码和文档。

- 仅用于识别游戏画面这一研究用途，不要用作其他目的，不要在别处单独再分发或用于商业用途。
- 权利人要求下架时，会立即移除整个 `assets/pack/`；识别层会退回只用 KAA 公开数据和 `capture_icons`（见 `docs/assets.md`）。
- 不想使用时：设 `HIRO_BUNDLED_ASSETS=0`，或直接删掉 `assets/pack/`。

## 加载顺序

`hiro.defaults.assets.PublicAssetSource` 同时看自建目录（`HIRO_USER_ASSETS`，默认 `resources/user_assets/`）和这个目录：
同类图库取文件更多的一个，`layout.json` 和字体先自建后随仓库。

## 重新生成

素材包由 `build_pack` 从客户端提取生成，该工具不在本仓库。客户端版本更新后，新界面可能认不出，这时用自己的截图跑
`tools/assets/capture_icons.py` 补图标，其余等素材包更新。
