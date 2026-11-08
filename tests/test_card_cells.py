"""换卡卡格图片认卡（hiro.vision.card_cells）：合成卡图，不读截图、不需要渲染库。"""

import cv2
import numpy as np
import pytest

from hiro.vision import card_cells as C


def _icon(seed: int, size: int = 96) -> np.ndarray:
    """一张合成「卡图标」：平滑的随机色块 + 深色外框。"""
    rng = np.random.default_rng(seed)
    small = rng.integers(40, 255, (6, 6, 3), dtype=np.uint8)
    img = cv2.resize(small, (size, size), interpolation=cv2.INTER_CUBIC)
    cv2.rectangle(img, (0, 0), (size - 1, size - 1), (70, 60, 55), max(1, size // 32))
    return img


def _lib(n: int = 6) -> C.Library:
    imgs = np.stack([_icon(i) for i in range(n)])
    return C.Library([f"img_general_skillcard_act-1_{i:03d}" for i in range(n)], imgs, [(f"p_card-01-act-1_{i:03d}",) for i in range(n)])


def _badges(cell: np.ndarray) -> np.ndarray:
    """模拟角标：左列效果图标、右侧「+」、右下费用。"""
    out = cell.copy()
    s = cell.shape[0]
    cv2.circle(out, (s // 8, s // 4), s // 10, (255, 160, 30), -1)
    cv2.circle(out, (s // 8, s // 2), s // 10, (255, 160, 30), -1)
    cv2.rectangle(out, (s * 3 // 4, s * 2 // 5), (s * 9 // 10, s * 3 // 5), (40, 40, 230), -1)
    cv2.circle(out, (s * 5 // 6, s * 5 // 6), s // 9, (60, 200, 90), -1)
    return out


def _screen(cell: np.ndarray, at=(100, 200)) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    img = np.full((600, 600, 3), 235, np.uint8)
    x, y = at
    h, w = cell.shape[:2]
    img[y : y + h, x : x + w] = cell
    return img, (x, y, w, h)


@pytest.mark.synthetic
@pytest.mark.parametrize("size", [87, 118, 124, 141, 148])
def test_recognized_across_cell_sizes_with_badges(size):
    lib = _lib()
    cell = _badges(cv2.resize(_icon(3), (size, size), interpolation=cv2.INTER_AREA))
    m = C.match(*_screen(cell), lib=lib)
    assert m["known"] and m["card_id"] == "p_card-01-act-1_003"


@pytest.mark.synthetic
def test_dimmed_blurred_jpeg_cell_still_same_card():
    lib = _lib()
    cell = cv2.resize(_icon(4), (118, 118), interpolation=cv2.INTER_AREA)
    cell = cv2.GaussianBlur(cv2.convertScaleAbs(cell, alpha=0.5), (0, 0), 1.2)  # 压暗（不可选）+ 糊
    ok, buf = cv2.imencode(".jpg", cell, [cv2.IMWRITE_JPEG_QUALITY, 50])
    m = C.match(*_screen(cv2.imdecode(buf, cv2.IMREAD_COLOR)), lib=lib)
    assert m["known"] and m["card_id"] == "p_card-01-act-1_004"


@pytest.mark.synthetic
def test_dimming_is_cancelled_by_brightness_rescale():
    cell = cv2.resize(_icon(1), (118, 118), interpolation=cv2.INTER_AREA)
    a, b = C.features(cell), C.features(cv2.convertScaleAbs(cell, alpha=0.55))
    assert np.abs(a - b).mean() < 0.15


@pytest.mark.synthetic
def test_offset_frame_found_by_retry():
    lib = _lib()
    img, (x, y, w, h) = _screen(cv2.resize(_icon(2), (118, 118), interpolation=cv2.INTER_AREA))
    m = C.match(img, (x + 2, y - 2, w, h), lib=lib)
    assert m["known"] and m["card_id"] == "p_card-01-act-1_002"


@pytest.mark.synthetic
def test_flat_cell_and_unknown_card_stay_unknown():
    lib = _lib()
    flat = np.full((118, 118, 3), 230, np.uint8)
    assert C.match(*_screen(flat), lib=lib)["known"] is False
    other = cv2.resize(_icon(99), (118, 118), interpolation=cv2.INTER_AREA)  # 库里没有的卡
    assert C.match(*_screen(other), lib=lib)["card_id"] is None


@pytest.mark.synthetic
def test_shared_asset_gives_ids_but_no_single_card_id():
    imgs = np.stack([_icon(i) for i in range(3)])
    lib = C.Library(["a", "b", "c"], imgs, [("p_card-01-act-1_000", "p_card-02-act-1_000"), ("p_card-01-act-1_001",), ("p_card-01-act-1_002",)])
    m = C.match(*_screen(cv2.resize(_icon(0), (118, 118))), lib=lib)
    assert m["known"] and m["card_id"] is None and set(m["ids"]) == {"p_card-01-act-1_000", "p_card-02-act-1_000"}


@pytest.mark.synthetic
def test_resolve_policy():
    lib = _lib()
    img, rect = _screen(cv2.resize(_icon(5), (118, 118), interpolation=cv2.INTER_AREA))
    assert C.resolve(None, img, rect, lib)[0] == "p_card-01-act-1_005"  # KAA 认不出 → 用图片
    assert C.resolve("p_card-01-act-1_005", img, rect, lib)[0] == "p_card-01-act-1_005"  # 一致
    card, m = C.resolve("p_card-01-act-1_001", img, rect, lib)  # 都认出、不一致 → 不猜
    assert card is None and m["conflict"] == "p_card-01-act-1_001"
    flat, frect = _screen(np.full((118, 118, 3), 230, np.uint8))
    assert C.resolve("p_card-01-act-1_001", flat, frect, lib)[0] == "p_card-01-act-1_001"  # 图片认不出 → 照用 KAA


@pytest.mark.synthetic
def test_switch_off_keeps_kaa_result(monkeypatch):
    from hiro.vision import deck

    class Card:
        _id, name = "p_card-01-act-1_001", "KAA の卡"

    called = []
    monkeypatch.setattr(C, "resolve", lambda *a, **k: called.append(a) or (None, {}))
    monkeypatch.delenv("HIRO_CARD_ICON", raising=False)
    img = np.zeros((300, 300, 3), np.uint8)
    assert deck._with_icons(Card(), img, (0, 0, 118, 118), True) == ("p_card-01-act-1_001", "KAA の卡", None)
    assert deck._with_icons(None, img, (0, 0, 118, 118), True) == (None, None, None)
    monkeypatch.setenv("HIRO_CARD_ICON", "1")
    assert deck._with_icons(Card(), img, (0, 0, 118, 118), False) == ("p_card-01-act-1_001", "KAA の卡", None)  # 非换卡页不用
    assert not called


@pytest.mark.synthetic
def test_switch_on_fills_unknown_and_drops_conflict(monkeypatch):
    from hiro.vision import cards, deck

    class Card:
        _id, name = "p_card-01-act-1_001", "KAA の卡"

    class Hit:
        name = "图片の卡"

    monkeypatch.setenv("HIRO_CARD_ICON", "1")
    monkeypatch.setattr(cards, "card_version", lambda cid, up: Hit())
    img = np.zeros((300, 300, 3), np.uint8)
    base = {"score": 0.3, "margin": 0.4, "asset": "x", "ids": ["p_card-01-act-1_009"]}
    monkeypatch.setattr(C, "resolve", lambda kaa, *a, **k: ("p_card-01-act-1_009", base))
    card_id, name, icon = deck._with_icons(None, img, (0, 0, 118, 118), True)
    assert (card_id, name, icon["source"]) == ("p_card-01-act-1_009", "图片の卡", "icon")
    monkeypatch.setattr(C, "resolve", lambda kaa, *a, **k: (None, {**base, "conflict": kaa}))
    card_id, name, icon = deck._with_icons(Card(), img, (0, 0, 118, 118), True)
    assert card_id is None and name is None and icon["conflict"] == {"kaa": "p_card-01-act-1_001", "icon": ["p_card-01-act-1_009"]}


@pytest.mark.synthetic
def test_no_library_means_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv("HIRO_CARD_ICON_LIB", str(tmp_path / "missing.npz"))
    C.library.cache_clear()
    try:
        m = C.match(np.zeros((200, 200, 3), np.uint8), (10, 10, 118, 118))
        assert m["known"] is False and m["card_id"] is None
    finally:
        C.library.cache_clear()


@pytest.mark.synthetic
def test_check_tool_perturbations_shapes():
    from tools.assets.check_card_cells import perturbations

    out = perturbations(cv2.resize(_icon(0), (118, 118)))
    assert {k: v.shape[0] for k, v in out.items()} == {"size87": 87, "size124": 124, "size148": 148, "blur": 118, "dim": 118, "jpeg50": 118}
