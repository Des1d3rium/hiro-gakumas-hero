"""换卡阶段 2 实机帧（2026-11-07 验证局 codex-20261107-1-i 第 1 局，第二次授業换卡）上的图片认卡（HIRO_CARD_ICON）。

帧不进仓库：tests/fixtures/trace/change/。没有帧或没有渲染库（cache/card_icons.npz）时跳过。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

FRAME = Path(__file__).parent / "fixtures" / "trace" / "change" / "stage2-top-1i.png"
LIB = Path(__file__).resolve().parents[1] / "cache" / "card_icons.npz"

# 本帧 KAA 认得出的 9 格（行, 列）→ 卡名；(0,1) KAA 认不出、图片认卡补成第一印象（游戏里是第一印象+，目视核对过）
KAA = {(0, 0): "トレーニングの成果", (0, 2): "オーバードライブ", (1, 1): "教えてあげる", (1, 2): "ガタゴトすやすや",
       (1, 3): "仕切り直し", (2, 0): "ティーパーティ", (2, 1): "みずたまりスキップ", (2, 2): "ひと呼吸", (2, 3): "演出計画"}


@pytest.fixture
def frame():
    if not FRAME.exists() or not LIB.exists():
        pytest.skip("缺实机帧或渲染库")
    import cv2

    return cv2.imread(str(FRAME))


def read(img, flag, monkeypatch):
    from hiro.loop.kaa_patches import CHANGE2_CARD_AREA
    from hiro.vision.deck import read_card_grid

    monkeypatch.setenv("HIRO_CARD_ICON", flag)
    return {(c["row"], c["col"]): c for c in read_card_grid(img, CHANGE2_CARD_AREA, "dialog", icons=True)}


@pytest.mark.fixtures
def test_icon_fills_unknown_cell_without_touching_kaa(frame, monkeypatch):
    off, on = read(frame, "0", monkeypatch), read(frame, "1", monkeypatch)
    assert set(off) == set(on) and len(on) == 12
    for pos, name in KAA.items():
        assert off[pos]["name"] == on[pos]["name"] == name and on[pos]["icon"]["source"] == "kaa"
        assert "conflict" not in on[pos]["icon"]
    assert off[(0, 1)]["card_id"] is None
    assert on[(0, 1)]["name"] == "第一印象" and on[(0, 1)]["upgrade"] == 1 and on[(0, 1)]["icon"]["source"] == "icon"


@pytest.mark.fixtures
def test_scan_without_icons_still_aligns_with_icon_screen(frame, monkeypatch):
    """扫描表和当前屏用的认卡不一样时（扫描没开开关、点选时开了），补认的格在扫描表里是通配，不能对不上。"""
    import batch_change as BC

    off, on = read(frame, "0", monkeypatch), read(frame, "1", monkeypatch)
    for scan, screen in ((off, on), (on, off), (on, on)):
        assert BC.screen_offset(list(screen.values()), BC.deck_rows(list(scan.values()))) == 0
