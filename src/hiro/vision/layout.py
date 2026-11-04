# 来源：digital-hiro src/hiro/vision/layout.py（develop c3fc27f）；开源版改：游戏素材经插件 AssetSource（hiro.vision.assets），拿不到时退路见各处注释
"""界面元素坐标：素材包里的静态布局（tools/assets/layout）+ 截图校准。

静态布局按锚点算出，纯锚点元素（回合圆盘、状态栏首槽等）可直接用；位置或尺寸由运行时组件决定的元素
（文字字形、自动排列、自适应尺寸）在 layout_calib.json 里记录相对静态矩形的偏移，
由 tools/assets/calibrate 从回归截图测得。所有坐标为 720x1280 逻辑坐标。

    from hiro.vision import layout
    layout.box("ExamTypeAuditionView", "GaugeRoot/GenkiGauge/IconRoot/Icon/GenkiValue")  # (x1, y1, x2, y2)
"""

import json
from functools import cache
from pathlib import Path

from hiro.vision import assets

CALIB = Path(__file__).with_name("layout_calib.json")
DEFAULT = Path(__file__).with_name("layout_default.json")  # 识别层用到的少数节点的实测矩形（不是客户端布局文件）
EXAM = "ExamTypeAuditionView"


@cache
def _views() -> dict:
    views = assets.layout()  # AssetSource 给的完整 UI 布局（自建素材包）；没有时用随仓库的实测框
    return views if views else json.loads(DEFAULT.read_text())


@cache
def calibration() -> dict:
    return json.loads(CALIB.read_text()) if CALIB.exists() else {}


def node(view: str, suffix: str) -> tuple[str, dict]:
    """按路径后缀找唯一的激活节点，返回 (完整路径, 节点)。找不到或不唯一时报错，避免静默用错元素。"""
    hits = [(p, n) for p, n in _views()[view].items() if n["active"] and (p == suffix or p.endswith("/" + suffix))]
    if len(hits) != 1:
        raise KeyError(f"{view}: 「{suffix}」匹配到 {len(hits)} 个激活节点")
    return hits[0]


def static_box(view: str, suffix: str) -> tuple[float, float, float, float]:
    x, y, w, h = node(view, suffix)[1]["rect"]
    return x, y, x + w, y + h


def box(view: str, suffix: str) -> tuple[int, int, int, int]:
    """静态矩形加上校准偏移（若有），取整为 (x1, y1, x2, y2)。"""
    x1, y1, x2, y2 = static_box(view, suffix)
    d = calibration().get(view, {}).get(suffix, {}).get("delta", (0, 0, 0, 0))
    return round(x1 + d[0]), round(y1 + d[1]), round(x2 + d[2]), round(y2 + d[3])


def center(view: str, suffix: str) -> tuple[float, float]:
    x1, y1, x2, y2 = box(view, suffix)
    return (x1 + x2) / 2, (y1 + y2) / 2
