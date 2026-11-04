"""识别层取素材的唯一入口：经插件 AssetSource（hiro.plugins.get("asset_source")）。

开源仓库不带游戏素材。拿不到插件、或插件对某类素材返回 None 时，这里都返回 None，调用方各自退路：
- 卡面：匹配关闭，只按卡名 OCR；
- 图标（P 饮料 / P 道具 / 考试效果名）：给 None；
- 数字字体：HUD 数字、分数、手牌徽章改走 ddddocr，并用 Apple Vision / 字形个数核对（digits._number_without_font、
  digits._score_without_font、hif.read_badge）；
- 界面 Sprite：页面签名关闭，KAA 判不出时由 scene._fallback 像素补判；
- 支援卡图：支援卡 id 给 None。
图标 kind 用插件约定的名字：drink（P 饮料）/ pitem（P 道具）/ status（考试效果）。
默认素材下哪些读数可用 / 降级 / 不可用见 docs/assets.md。
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


USER_ASSETS = {
    "font": "数字字体（HUD 数字字形比对、手牌预计得分徽章）",
    "status": "考试效果图标（やる気 / 好印象 等状态名）",
    "pitem": "P 道具图标（P 道具名）",
    "drink": "P 饮料图标（KAA 公开数据自带 29 种）",
    "cards": "技能卡卡面（KAA 公开数据自带）",
    "sprites": "界面 Sprite（页面签名，过渡帧判页）",
    "support": "支援卡图（编成页支援卡 id）",
}


def available(kind: str) -> bool:
    """某类素材在当前 AssetSource 下拿不拿得到（kind 见 USER_ASSETS）。测试按它跳过依赖自建素材的用例。"""
    getter = {"font": font_path, "cards": card_art_dir, "sprites": sprites_dir, "support": support_dir}.get(kind)
    return (getter() if getter else icon_dir(kind)) is not None


def reset() -> None:
    """测试用：换了插件后清缓存。"""
    source.cache_clear()
