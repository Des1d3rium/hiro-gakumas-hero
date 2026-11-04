# 识别素材与退路（AssetSource）

仓库不带任何游戏素材（卡面、图标、字体、界面 Sprite、主数据原文件）。识别层取素材只经插件 `AssetSource`
（`hiro.plugins.get("asset_source")`，入口 `hiro.vision.assets`），默认实现 `hiro.defaults.assets.PublicAssetSource`
用两个来源：

| 来源 | 位置（环境变量） | 内容 |
|---|---|---|
| KAA 公开数据 | `HIRO_KAA_GAME_DATA`，默认 `resources/game_data/`（KAA 运行时下载） | `game.db` 主数据、`skill_cards/` 技能卡卡面、`drinks/` P 饮料图（白底 RGB，29 种，与主数据 ProduceDrink 全部对上） |
| 使用者自建素材 | `HIRO_USER_ASSETS`，默认 `resources/user_assets/` | 见下文「自建素材」；都可选 |

设了 `HIRO_KAA_GAME_DATA` 时，`import hiro.vision` 会把 KAA 自己的数据目录（`kaa.game_data.paths` 与 `kaa.db.sqlite`
的库路径）也指过去，两边用同一份数据；不在当前目录留下 `resources/`。

## 默认素材下各读数

「默认素材」= 只有 KAA 公开数据，没有任何自建素材。核对方法：识别层回归用例 + 主仓库 58 帧考试画面逐项对比
「有私有素材包 / 只有默认素材」两种读法。

| 读数 | 默认素材下 | 说明 |
|---|---|---|
| 判页（KAA check_scene、模板快速判页 quick_page、像素兜底、OCR 页面地图） | 可用 | 界面文字小模板随仓库（`hiro/vision/templates/`，截图裁的标签 / 按钮字样） |
| 过渡帧判页（页面签名） | 降级 | 签名要界面 Sprite。淡入中的选行动页等过渡帧判 unknown，下一帧正常判出；KAA 判不出时补判选行动页（「P手帳」标签）、Pドリンク所持上限、饮料已满的选饮料页 |
| HUD 数字：剩余回合、元気、体力 | 可用 | 没有数字字体时走 ddddocr：整框与「左边收到第一个白字」各读一次，不一致用 Apple Vision 判，都不对给 None。58 帧 174 个读数与有字体时全部一致 |
| 考试分数（排名栏玩家分数） | 可用（置信度略低） | 按字形范围裁图，ddddocr 与 Apple Vision 互相核对：一致 0.8；掉首 / 末位时取长的 0.6；不一致而 ddddocr 位数与整高字形数相同时 0.6；其余 None。11 帧回归全对（有字体时 0.9–1.0） |
| NPC 分数、名次、突破线、本战名次条 | 可用 | 本来就走 OCR；本战玩家分数三票投票少了字体一票，剩 Vision + ddddocr 两票须一致 |
| 手牌卡名 / 卡 id | 可用 | KAA 卡面 + 卡名栏 OCR |
| 手牌预计得分徽章 | 可用 | 没有字体时白描边同色字形串交给 ddddocr，位数与字形数一致才用；58 帧与有字体时一致 |
| P 饮料（考试 / 培育底栏、选饮料、相談货架） | 可用 | KAA 饮料图；名称按主数据 |
| 考试效果栏：数值 | 可用 | |
| 考试效果栏：效果名（やる気、好印象 等） | 不可用 | 要自建状态图标 `icons/status/`；没有时效果名为 None，观测里 motivation / impression 等按类型取的字段为 None |
| 考试效果栏：卡牌效果（エンチャント）名 | 可用 | 用 KAA 卡面 |
| 考试 P 道具名 | 不可用 | 要自建 `icons/pitem/`；剩余次数照常读 |
| 开始确认页支援卡 id | 不可用 | 要自建 `support/` |
| 主数据（卡 / 饮料 / P 道具目录） | 可用 | KAA `game.db` |
| H.I.F 日程（选拔 20 项 / 本战 9 项） | 可用 | 随仓库 `hiro/defaults/schedule_hif.json` |
| 支援卡效果、考试要求 / 倍率换算 / 星级基准 | 不可用 | 公开数据里没有可靠来源，`support_effects` / `exam_requirements` 返回 None，策略层按规则退路 |

可用 = 与有私有素材时结果一致；降级 = 能玩但个别帧 / 字段给 None 或置信度低；不可用 = 字段一直为 None。
Apple Vision 是 macOS 系统 OCR，识别层本来就只在 macOS 上跑（实机依赖 KAA 的 macOS 适配）。

## 自建素材（`HIRO_USER_ASSETS`）

都可选，有哪项用哪项，从自己的截图 / 客户端取，不随仓库发布：

```
cards/            技能卡卡面（有则优先于 KAA）
icons/drink/      P 饮料图标（有则优先于 KAA）
icons/pitem/      P 道具图标
icons/status/     考试效果图标
    *.png         RGBA（透明底）或白底 RGB；文件名 = 资源名
    index.json    可选：资源名 → 名称（P 饮料没有时按主数据取名）
font/             数字字体 Campus-Alphanumeric-SemiBold.ttf（有则 HUD 数字 / 分数 / 徽章先按字形比对）
layout.json       UI 布局（结构同 hiro.vision.layout；没有用仓库里的 layout_default.json）
sprites/          界面 Sprite（页面签名 signatures.json 引用的文件名）
support/          支援卡图
schedule_hif.json 日程（有则优先于仓库自带）
```

也可以自己实现 `AssetSource`（接口见 `hiro.plugins`），用 `HIRO_ASSET_SOURCE=包.模块:类名` 或 entry point 换掉默认实现。

## 测试

- 依赖自建素材的用例标 `@pytest.mark.user_assets("<kind>")`，素材不在时跳过，理由「需要自建素材（HIRO_USER_ASSETS）：kind」。
  kind 与 `hiro.vision.assets.USER_ASSETS` 一致：font、status、pitem、drink、cards、sprites、support。
- `test_hif_hud` 逐帧对照里，状态名 / P 道具名在素材不在时不比（数值照比）。
- 「没有字体」的读法另有用例（`test_no_font_*`），把字体模板换成空，有没有字体都跑。
- 跑带截图样例的测试：设 `HIRO_FIXTURES`（样例目录）、`HIRO_KAA_GAME_DATA`（KAA 公开数据），不要在仓库目录生成 `resources/`。
