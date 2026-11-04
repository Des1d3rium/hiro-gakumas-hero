"""识别层取素材的唯一入口：经插件 AssetSource（hiro.plugins.get("asset_source")）。

开源仓库不带游戏素材。拿不到插件、或插件对某类素材返回 None 时，这里都返回 None，调用方各自退路：
卡面匹配关闭（只按卡名 OCR）、图标读数给 None、数字全走 ddddocr、页面签名关闭、支援卡 id 给 None。
图标 kind 用插件约定的名字：drink（P 饮料）/ pitem（P 道具）/ status（考试效果）。
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@cache
def source() -> Any | None:
    try:
        from hiro import plugins

        return plugins.get("asset_source")
    except Exception as e:  # noqa: BLE001 插件加载失败不拖垮识别，按「没有素材」处理
        logger.warning("AssetSource 不可用，按没有素材处理：%r", e)
        return None


def _call(name: str, *args) -> Any | None:
    src = source()
    if src is None:
        return None
    try:
        return getattr(src, name)(*args)
    except Exception as e:  # noqa: BLE001
        logger.warning("AssetSource.%s%r 出错，按没有素材处理：%r", name, args, e)
        return None


def _dir(p: Path | None) -> Path | None:
    return p if p is not None and Path(p).is_dir() else None


def card_art_dir() -> Path | None:
    return _dir(_call("card_art_dir"))


def icon_dir(kind: str) -> Path | None:
    return _dir(_call("icon_dir", kind))


def font_path() -> Path | None:
    p = _call("font_path")
    return p if p is not None and Path(p).is_file() else None


def layout() -> dict | None:
    return _call("layout")


def sprites_dir() -> Path | None:
    return _dir(_call("sprites_dir"))


def support_dir() -> Path | None:
    return _dir(_call("support_dir"))


def reset() -> None:
    """测试用：换了插件后清缓存。"""
    source.cache_clear()
