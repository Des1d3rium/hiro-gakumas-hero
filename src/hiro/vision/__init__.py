"""识屏层：截图 → 页面判定与字段读数（学園アイドルマスター H.I.F）。

不带任何游戏素材：卡面 / 图标 / 字体 / 界面 Sprite / 支援卡图通过插件 AssetSource（hiro.plugins，默认
hiro.defaults.assets.PublicAssetSource = KAA 公开数据 + 使用者自建目录）取；拿不到时各读数退回 OCR 或给 None。
界面文字小模板（templates/）是截图裁的标签 / 按钮字样。实机识别依赖 KAA（可选组 play，子模块 vendor/kaa）。

- 判页：scene.classify_current（KAA + 签名）、quick.quick_page / fallback_page（模板 / 像素快速判页）、pages.classify_page（OCR 页面地图）
- 读数：extract.extract(scene, img) → 原始字段；observe.observation(...) → 契约 Observation
- 接入自己的识别器：observe.register_adapter(scene, fn) 注册某个页面的字段转换
"""

import os as _os
from pathlib import Path as _Path


def _point_kaa_game_data() -> None:
    """KAA 自己只认「当前目录/resources/game_data」（主数据 game.db、技能卡 / 饮料图）。设了 HIRO_KAA_GAME_DATA 时把 KAA 指过去，
    和 PublicAssetSource 用同一份公开数据；否则在别的目录运行时 KAA 会自建一个空 game.db，所有主数据查询和卡面库都失效。"""
    d = _os.environ.get("HIRO_KAA_GAME_DATA")
    if not d:
        return
    local = _Path("resources")
    existed = local.exists()
    try:
        import kaa.db  # noqa: F401  先导入 db，避免 kaa.game_data.paths 的循环导入（导入时 KAA 会在当前目录建 resources/game_data）
        import kaa.db.sqlite as _sqlite
        import kaa.game_data.paths as _paths
    except Exception:  # noqa: BLE001 没装 KAA（基础安装）：识别层本来就用不了，什么也不做
        return
    target = _Path(d).resolve()
    _paths._GAME_DATA_DIR = target
    _sqlite._db_path = str(target / "game.db")  # KAA 导入时就把库路径算成了字符串，要一起改
    _sqlite.invalidate_connections()
    _paths.skill_card_index.cache_clear()
    if not existed and local.exists() and not any(p.is_file() for p in local.rglob("*")):
        import shutil

        shutil.rmtree(local, ignore_errors=True)  # 导入时 KAA 顺手建的空目录：数据在 HIRO_KAA_GAME_DATA，不留在当前目录


_point_kaa_game_data()
