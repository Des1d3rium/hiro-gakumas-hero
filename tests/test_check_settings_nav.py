# 来源：digital-hiro tests/test_check_settings_nav.py（fix_matto_prep_watchdog 5b5adff）
"""设置检查按 OCR 导航（第三十三局：按固定坐标点「設定」没进去，两张截图都是菜单，读成全 None 被当成「设置被重置」）。"""

import sys
from pathlib import Path

import json

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
BOXES = json.loads((Path(__file__).parent / "data" / "g33-ocr-boxes.json").read_text(encoding="utf-8"))  # 菜单 / 设置页实机帧的 OCR 框（随仓库发布）


def _boxes(key):
    return [(t, c, tuple(b)) for t, c, b in BOXES[key]]


@pytest.mark.synthetic
def test_menu_frame_finds_settings_icon_not_settings_page():
    from check_settings import is_menu, is_settings

    b = _boxes("settings/menu-g33")
    pos = is_menu(b)
    assert pos is not None and abs(pos[0] - 605) <= 30 and abs(pos[1] - 938) <= 30, pos
    assert is_settings(b) is None


@pytest.mark.synthetic
def test_settings_frame_finds_produce_tab():
    from check_settings import is_menu, is_settings

    b = _boxes("settings/03-produce-01")
    tab = is_settings(b)
    assert tab is not None and abs(tab[0] - 582) <= 30 and abs(tab[1] - 1076) <= 30, tab
    assert is_menu(b) is None


def test_verdict_tells_reset_from_unread():
    from check_settings import WANT, verdict

    assert verdict({k: "ON" for k in WANT}).startswith("ok")
    assert verdict({k: None for k in WANT}).startswith("读不到")
    v = verdict({**{k: "ON" for k in WANT}, "親愛度映像跳过": "OFF"})
    assert v.startswith("设置被重置") and "親愛度映像跳过" in v
    assert verdict({**{k: "ON" for k in WANT}, "未読早送り": None}).startswith("部分读不到")
