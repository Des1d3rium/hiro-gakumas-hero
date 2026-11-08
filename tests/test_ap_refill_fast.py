"""快速补 AP 的合成弹窗。"""

import numpy as np
import pytest

from hiro.loop import ap_refill as A
from hiro.produce import hif_buttons as BTN


class Game:
    def __init__(self, row=("APドリンク", "使う"), confirmation="APドリンクを消費して、APを回復しますか？", delay=0, title="AP回復"):
        self.step, self.row, self.clicks = 1, row, []
        self.confirmation, self.delay, self.title = confirmation, delay, title
        self.now, self.opened = 0, None

    def shoot(self):
        return np.zeros((1280, 720, 3), np.uint8)

    def ocr(self, img):
        if self.step == 2:
            return [(t, 1, None) for t in self.row]
        if self.step == 3 and self.now >= self.opened + self.delay:
            return [(self.title, 1, (100, 550, 300, 580)),
                    (self.confirmation, 1, (100, 700, 620, 760)),
                    ("キャンセル", 1, (150, 1140, 250, 1180)),
                    ("回復する", 1, (460, 1140, 580, 1180))]
        return []

    def header(self, img):
        return None if self.step == 4 else (550, 580)

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.step == 1 and (x, y) == BTN.DIALOG_OK:
            self.step = 2
        elif self.step == 2 and (x, y) == BTN.AP_USE:
            self.step, self.opened = 3, self.now
        elif self.step == 3 and (x, y) == BTN.DIALOG_OK:
            assert self.now >= self.opened + self.delay
            self.step = 4

    def sleep(self, seconds):
        self.now += seconds


def run(g):
    return A.refill_fast(g.shoot, g.click, g.ocr, g.header, sleep=g.sleep, clock=lambda: g.now, log=lambda m: None)


@pytest.mark.parametrize("delay", [0, 2.5])
def test_three_taps(delay):
    g = Game(delay=delay)
    out = run(g)
    assert out["ok"] and out["drinks"] == 1 and g.clicks == [BTN.DIALOG_OK, BTN.AP_USE, BTN.DIALOG_OK]


@pytest.mark.parametrize("confirmation, extra", [("ジュエルを消費しますか？", [(200, 1160)]), ("予期しない確認", [])])
def test_final_payment_or_unknown_modal_does_not_confirm(confirmation, extra):
    g = Game(confirmation=confirmation)
    out = run(g)
    assert not out["ok"] and out["drinks"] == 0
    assert g.clicks == [BTN.DIALOG_OK, BTN.AP_USE, *extra]


def test_jewel_row_is_never_used():
    g = Game(row=("ジュエル", "使う"))
    out = run(g)
    assert not out["ok"] and BTN.AP_USE not in g.clicks and g.clicks[-1] == BTN.AP_CLOSE


class LateJewel(Game):
    """确认框第一帧只有 APドリンク 文案，下一帧才画出ジュエル提示（淡入中途）：不能在第一帧就点确认。"""

    def ocr(self, img):
        boxes = super().ocr(img)
        if self.step == 3:
            self.frames = getattr(self, "frames", 0) + 1
            if self.frames >= 2:
                boxes.append(("ジュエルを消費します", 1, (100, 800, 620, 840)))
        return boxes


def test_jewel_drawn_after_first_frame_is_cancelled():
    g = LateJewel()
    out = run(g)
    assert not out["ok"] and out["drinks"] == 0
    assert g.clicks == [BTN.DIALOG_OK, BTN.AP_USE, (200, 1160)]


def test_confirmation_never_appears_times_out_without_clicking():
    g = Game(delay=A.STEP_TIMEOUT + 5)
    out = run(g)
    assert not out["ok"] and out["drinks"] == 0 and "未点确认" in out["error"]
    assert g.clicks == [BTN.DIALOG_OK, BTN.AP_USE]


def test_use_count_other_than_one_is_not_confirmed():
    class Many(Game):
        def ocr(self, img):
            boxes = super().ocr(img)
            if self.step == 3 and boxes:
                boxes += [("使用数", 1, (100, 1000, 180, 1020)), ("3", 1, (500, 1000, 520, 1020))]
            return boxes

    g = Many()
    out = run(g)
    assert not out["ok"] and g.clicks == [BTN.DIALOG_OK, BTN.AP_USE]


def test_unknown_modal_title_does_not_use_visible_drink_text():
    g = Game(title="予期しない確認")
    out = run(g)
    assert not out["ok"] and out["drinks"] == 0
    assert g.clicks == [BTN.DIALOG_OK, BTN.AP_USE]
