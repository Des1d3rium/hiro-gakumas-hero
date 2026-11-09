"""按钮注册表（hiro.ui.buttons）：迁移前后每个按钮的点击点逐像素相同；覆盖文件只能改值；防回潮（不许新写坐标字面量）。"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from hiro.ui import buttons as B

pytestmark = pytest.mark.synthetic

# 迁移前 hiro.produce.hif_buttons 的全部常量（2026-11-08，commit 9649c29）
OLD_HIF_BUTTONS = {
    'CORNER': (12, 12),
    'BAR_GAP': (386, 1200),
    'BOTTOM_LOW': (360, 1085),
    'BOTTOM_HIGH': (360, 1160),
    'SETTLE': (360, 1170),
    'BOTTOM_BLANK': (360, 1180),
    'TOP_BLANK': (360, 250),
    'STAGE_BLANK': (360, 330),
    'TOAST': (360, 1000),
    'SHELF_GAP': (360, 700),
    'DIALOG_CANCEL': (211, 1160),
    'DIALOG_OK': (520, 1160),
    'THREE_X': (196, 360, 521),
    'THREE_Y': 1000,
    'OPTION_ROWS': (698, 799, 901),
    'REST': (645, 889),
    'EXAM_DRINK_DISCARD': (606, 890),
    'SHOP_END': (640, 1085),
    'SHOP_UPGRADE': (220, 968),
    'SHOP_DELETE': (535, 968),
    'REROLL': {'consult': (108, 1085), 'interval': (108, 1066)},
    'INTERVAL_CUSTOMIZE': (458, 940),
    'INTERVAL_UPGRADE': (176, 928),
    'INTERVAL_RECOVER': (622, 930),
    'EXCLUDE': (133, 1096),
    'RESULT_RETRY': (129, 1128),
    'RESULT_NEXT': (373, 1133),
    'RETRY_DIALOG_END': (233, 1210),
    'RETRY_DIALOG_RETRY': (533, 1210),
    'MEMORY_GENERATE': (360, 975),
    'MEMORY_REGEN': (110, 1162),
    'MEMORY_PREV': (220, 1027),
    'MEMORY_NEXT': (498, 1027),
    'MEMORY_DOTS_Y': 1050,
    'REGEN_CONFIRM_DY': 621,
    'FINAL_CONFIRM_DY': 292,
    'AP_USE': (574, 947),
    'AP_CLOSE': (359, 1172),
    'AP_PLUS': (584, 1033),
    'IDOL_NEXT': (360, 1085),
    'START': (363, 1042),
    'MEMORY_CHANGE': (636, 577),
    'MEMORY_SORT': (527, 860),
    'MEMORY_LIST_OK': (360, 1085),
}
# 迁移前 tools/batch_* 模块级点击点
OLD_TOOLS = {
    ("batch_prep", "RECEIVE"): (353, 1097), ("batch_prep", "DRINK_X"): (222, 360, 498), ("batch_prep", "DRINK_Y"): 870,
    ("batch_prep", "SHOP_END"): (640, 1085), ("batch_prep", "BRIEFING_TAP"): (360, 300), ("batch_prep", "DECLINE"): (359, 1006),
    ("batch_customize", "GRID_CUSTOMIZE"): (500, 1160), ("batch_customize", "GRID_BACK"): (215, 1160),
    ("batch_customize", "MENU_X"): (141, 360, 578), ("batch_customize", "MENU_Y"): 935,
    ("batch_customize", "MENU_EXEC"): (500, 1160), ("batch_customize", "MENU_BACK"): (215, 1160),
    ("batch_hif", "LIVE_REVEAL"): (0.50, 0.50), ("batch_hif", "LIVE_SKIP"): (0.938, 0.146),
    ("batch_hif", "LIVE_SKIP_FIXED"): (0.938, 0.110), ("batch_hif", "LIVE_END_OK"): (0.61, 0.89),
    ("batch_hif", "SETTLE_POPUP_TAP"): (360, 1170), ("batch_hif", "INTERVAL_GO"): (513, 1160),
    ("batch_hif", "BRIEFING_TAP"): (360, 300),
}


def test_hif_buttons_unchanged():
    import hiro.produce.hif_buttons as H

    got = {k: v for k, v in vars(H).items() if k.isupper() and len(k) > 1}
    assert got == OLD_HIF_BUTTONS


def test_tools_click_points_unchanged():
    import importlib

    for (mod, name), value in OLD_TOOLS.items():
        assert getattr(importlib.import_module(mod), name) == value, (mod, name)


def test_registry_valid_and_danger_marked():
    from hiro.strategies import schema

    assert schema.check(json.loads(B.DEFAULT.read_text(encoding="utf-8")), "buttons") == []
    for bid in ("START", "RESULT_RETRY", "RETRY_DIALOG_END", "AP_USE", "SHOP_DELETE"):
        assert B.is_danger(bid), bid
    assert B.get("IDOL_NEXT") == B.get("BOTTOM_LOW")  # 别名：同一位置只存一次
    assert B.entry("live.SKIP_FIXED")["space"] == "landscape"


def test_user_override_only_changes_values(tmp_path, monkeypatch):
    f = tmp_path / "buttons.json"
    monkeypatch.setenv("HIRO_BUTTONS", str(f))
    f.write_text(json.dumps({"buttons": {"CORNER": {"value": [16, 16]}}}), encoding="utf-8")
    B.reload()
    try:
        assert B.point("CORNER") == (16, 16) and B.entry("CORNER")["overridden"]
        for bad in ({"NOPE": {"value": [1, 1]}}, {"CORNER": {"danger": "x"}}, {"IDOL_NEXT": {"value": [1, 1]}},
                    {"CORNER": {"value": "far"}}):
            f.write_text(json.dumps({"buttons": bad}), encoding="utf-8")
            B.reload()
            with pytest.raises(B.ButtonError):
                B.table()
    finally:
        monkeypatch.delenv("HIRO_BUTTONS")
        B.reload()


# ---------------------------------------------------------------- 防回潮
COORD = re.compile(r"\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)")
BASELINE = ROOT / "tests" / "coord_baseline.json"


def coord_counts() -> dict[str, int]:
    out = {}
    for base in (ROOT / "tools", ROOT / "src" / "hiro"):
        for p in sorted(base.rglob("*.py")):
            n = sum(len(COORD.findall(line.split("#", 1)[0])) for line in p.read_text(encoding="utf-8").splitlines())
            if n:
                out[str(p.relative_to(ROOT))] = n
    return out


def test_no_new_coordinate_literals():
    """tools/ 和 src/hiro/ 里 (数, 数) 字面量（不算注释）按文件计数，只许减少不许增加。
    新按钮写进 hiro/ui/buttons.json（读区框进 layout*.json）；确实要加时（不是坐标的二元组）更新 tests/coord_baseline.json 并写明理由。
    迁移掉的文件计数变少后，跑 `python tests/test_ui_buttons.py` 重写基线。"""
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["counts"]
    grown = {f: (base.get(f, 0), n) for f, n in coord_counts().items() if n > base.get(f, 0)}
    assert not grown, f"新写了坐标字面量（基线, 现在）：{grown}"


if __name__ == "__main__":
    counts = coord_counts()
    BASELINE.write_text(json.dumps({"note": "防回潮基线（tests/test_ui_buttons.py）：各文件 (数, 数) 字面量个数，只许减少", "total": sum(counts.values()),
                                     "counts": counts}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(sum(counts.values()))
