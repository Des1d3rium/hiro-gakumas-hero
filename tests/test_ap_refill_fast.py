"""快速补 AP（hiro.loop.ap_refill.refill_fast）：固定位置点三下，第 2 下前只核对 APドリンク 那一行。"""

import numpy as np

from hiro.loop import ap_refill as A
from hiro.produce import hif_buttons as BTN


class Game:
    def __init__(self, row=("APドリンク", "APが20回復するドリンク", "使う")):
        self.step, self.row, self.clicks = 1, row, []

    def shoot(self):
        return np.zeros((1280, 720, 3), np.uint8)

    def ocr(self, img):
        return [(t, 1, None) for t in self.row] if self.step == 2 else []

    def header(self, img):
        return None if self.step == 4 else (550, 580)

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.step == 1 and (x, y) == BTN.DIALOG_OK:
            self.step = 2
        elif self.step == 2 and (x, y) == BTN.AP_USE:
            self.step = 3
        elif self.step == 3 and (x, y) == BTN.DIALOG_OK:
            self.step = 4


def run(g):
    return A.refill_fast(g.shoot, g.click, g.ocr, g.header, sleep=lambda s: None, gap=0.01, log=lambda m: None)


def test_three_taps():
    g = Game()
    out = run(g)
    assert out["ok"] and out["drinks"] == 1 and g.clicks == [BTN.DIALOG_OK, BTN.AP_USE, BTN.DIALOG_OK]


def test_jewel_row_is_never_used():
    g = Game(row=("ジュエル", "使う"))
    out = run(g)
    assert not out["ok"] and BTN.AP_USE not in g.clicks and g.clicks[-1] == BTN.AP_CLOSE
