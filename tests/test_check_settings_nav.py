# 来源：digital-hiro tests/test_check_settings_nav.py（fix_matto_prep_watchdog 5b5adff）
"""设置检查按 OCR 导航（第三十三局：按固定坐标点「設定」没进去，两张截图都是菜单，读成全 None 被当成「设置被重置」）。"""

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
FIX = Path(__file__).parent / "fixtures/settings"


def _boxes(name):
    from hiro.produce.ops import ocr_boxes

    return ocr_boxes(cv2.resize(cv2.imread(str(FIX / name)), (720, 1280)))


def test_menu_frame_finds_settings_icon_not_settings_page():
    from check_settings import is_menu, is_settings

    b = _boxes("menu-g33.jpg")
    pos = is_menu(b)
    assert pos is not None and abs(pos[0] - 605) <= 30 and abs(pos[1] - 938) <= 30, pos
    assert is_settings(b) is None


def test_settings_frame_finds_produce_tab():
    from check_settings import is_menu, is_settings

    b = _boxes("03-produce-01.png")
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
