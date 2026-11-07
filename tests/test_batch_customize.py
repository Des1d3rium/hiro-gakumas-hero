"""tools/batch_customize：轮间客制化的卡名 / 菜单项匹配和菜单读法（合成 OCR 框）。"""

import sys
from pathlib import Path

import pytest
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_customize as C  # noqa: E402

pytestmark = pytest.mark.synthetic


def box(text, x, y):
    return (text, 1.0, (x - 50, y - 12, x + 50, y + 12))


def test_same_card_tolerates_missing_plus_and_fullwidth():
    assert C.same_card("巻き返し＋", "巻き返し+") and C.same_card("巻き返し", "巻き返し+")
    assert not C.same_card("インフルエンサー+", "巻き返し+") and not C.same_card(None, "巻き返し+")


def test_item_match_handles_ocr_truncation():
    assert C.item_match("成長+", "成長追加") and C.item_match("レッスン中1回の制限削除", "制限削除")
    assert not C.item_match("強気追加", "温存追加") and not C.item_match(None, "強気追加")


def test_menu_items_reads_left_and_maxed():
    boxes = [box("あと1回", 141, 889), box("全力値コスト値-", 141, 926), box("レッスン中1回", 360, 917),
             box("の制限削除", 360, 940), box("カスタマイズ上限", 360, 997), box("あと3回", 578, 889), box("成長追加", 578, 926)]
    items = C.menu_items(boxes)
    assert [(i["name"], i["left"], i["maxed"]) for i in items] == [
        ("全力値コスト値-", 1, False), ("レッスン中1回の制限削除", None, True), ("成長追加", 3, False)]


class FakeGame:
    """轮间客制化的最小状态机：interval → grid → (点格子) 详情 → menu → 実行する → 直接出提示（没有「実行確認」，实机如此）。"""

    def __init__(self, start="interval", confirm_box=False):
        self.state, self.confirm_box, self.detail, self.clicks, self.swipes = start, confirm_box, None, [], 0

    def screenshot(self):
        import numpy as np

        return np.zeros((1280, 720, 3), np.uint8)

    def swipe(self, *a, **k):
        self.swipes += 1

    def click(self, x, y):
        self.clicks.append((x, y))
        st = self.state
        if st == "interval" and (x, y) == C.INTERVAL_BTN:
            self.state = "grid"
        elif st == "grid" and (x, y) == (120, 600):
            self.detail = "巻き返し+"
        elif st == "grid" and (x, y) == C.GRID_CUSTOMIZE and self.detail:
            self.state = "menu"
        elif st == "menu" and (x, y) == C.MENU_EXEC:
            self.state = "confirm" if self.confirm_box else "toast"
        elif st == "confirm" and (x, y) == C.CONFIRM_TAP:
            self.state = "toast"
        elif st == "toast" and (x, y) == C.TOAST_TAP:
            self.state = "interval_after"

    def ocr(self, img):
        crop = img.shape[0] < 1280
        if crop:  # 详情栏条带（y 60–200）
            return [box(self.detail, 300, 115 - 60)] if self.detail and self.state == "grid" else []
        page = {
            "interval": [box("インターバル", 100, 40), box("カスタマイズ可能枚数あと2枚", 360, 880), box("カスタマイズ", 458, 940)],
            "interval_after": [box("インターバル", 100, 40), box("Pポイントで利用するものを選んでください", 360, 300)],
            "grid": [box("カスタマイズするスキルカード", 360, 500)],
            "menu": [box("カスタマイズメニュー", 360, 700), box("一覧に戻る", 215, 1160), box("巻き返し+", 360, 160),
                     box("成長追加", 578, 935), box("あと1回", 578, 888)],
            "confirm": [box("実行確認", 360, 600), box("実行する", 500, 1160)],
            "toast": [box("インターバル", 100, 40), box("巻き返し＋をカスタマイズしました", 360, 1000)],
        }
        return page[self.state]


@pytest.fixture
def fake(monkeypatch):
    import hiro.produce.ops as ops
    import hiro.vision.deck as deck

    def setup(**kw):
        g = FakeGame(**kw)
        monkeypatch.setattr(ops, "ocr_boxes", g.ocr)
        monkeypatch.setattr(deck, "read_card_grid", lambda img, grid: [{"row": 0, "col": 0, "name": None, "center": (120, 600)}])
        monkeypatch.setattr(C.time, "sleep", lambda s: None)
        return g

    return setup


@pytest.mark.parametrize("confirm_box", [False, True])
def test_customize_succeeds_on_toast_with_or_without_confirm_box(fake, confirm_box):
    pytest.importorskip("kaa")
    g = fake(confirm_box=confirm_box)
    r = C.customize(g, "巻き返し+", "成長追加")
    assert r["ok"] and r["toast"] and r["confirm_box"] == confirm_box and r["page"] == "interval"


def test_customize_from_grid_scrolls_to_top_first(fake):
    pytest.importorskip("kaa")
    g = fake(start="grid")
    r = C.customize(g, "巻き返し+", "成長追加")
    assert r["ok"] and g.swipes >= C.SCREENS


def test_interval_page_after_one_customize_is_recognized():
    o = NS(has=lambda b, w: any(w in t for t, _, _ in b))
    assert C._on_interval(o, [box("インターバル", 100, 40), box("Pポイントで利用するものを選んでください", 360, 300)])


def test_shop_auto_buys_high_value_then_refreshes_until_p_runs_out(monkeypatch):
    import batch_shop as S

    shelves = [[{"index": 6, "name": "初星スペシャル青汁", "purchased": False}],
               [{"index": 7, "name": "ブーストエキス", "price": 30, "purchased": False}],
               [{"index": 7, "name": "ブーストエキス", "purchased": True}]]
    state = {"shelf": 0, "p": 40, "bought": []}
    monkeypatch.setattr(S, "_page", lambda d: ("interval", None))
    monkeypatch.setattr(S, "_shelf", lambda k, img: ({"interval": {"refresh": {"price": 10}}}, shelves[state["shelf"]]))
    monkeypatch.setitem(sys.modules, "hiro.produce.shop", NS(read_p=lambda img, ocr: state["p"]))
    monkeypatch.setitem(sys.modules, "hiro.produce.ops", NS(ocr_boxes=None))

    def buy(device, index):
        state["shelf"], state["p"] = 2, state["p"] - 30
        return {"ok": True, "drink": "ブーストエキス"}

    def reroll(action, fields, shoot, click):
        state["shelf"], state["p"] = state["shelf"] + 1, state["p"] - 10
        return NS(ok=True, error=None)

    monkeypatch.setattr(S, "buy", buy)
    monkeypatch.setattr(S, "_executor", lambda kind: (reroll, None))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    r = S.auto(NS(screenshot=None, click=None), free_slots=lambda: 4 - len(state["bought"]), on_buy=state["bought"].append)
    assert r["ok"] and state["bought"] == ["ブーストエキス"] and "P 不够" in r["stop"]
    assert [s["op"] for s in r["steps"]] == ["refresh", "buy"]  # 记账的 P 和画面一致：没有 p_mismatch


def test_read_p_uses_digit_reader_first(monkeypatch):
    pytest.importorskip("kaa")
    import hiro.produce.shop as shop
    import hiro.vision.digits as digits

    monkeypatch.setattr(digits, "read_number", lambda img, box: 0)
    assert shop.read_p(object(), lambda img: []) == 0  # 单个「0」整帧 OCR 读不出，数字读取器能读
    monkeypatch.setattr(digits, "read_number", lambda img, box: None)
    assert shop.read_p(object(), lambda img: [("340", 1.0, (360, 100, 420, 130))]) == 340


def test_refresh_price_schedule():
    import batch_shop as S

    seq, tens, price = [10], 1, 10
    for _ in range(6):
        price = S.next_refresh_price(price, tens)
        tens += price == 10
        seq.append(price)
    assert seq == [10, 10, 20, 30, 40, 50, 50]
