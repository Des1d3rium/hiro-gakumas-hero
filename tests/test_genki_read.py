# 来源：digital-hiro tests/test_genki_read.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""考试 HUD 元気读数（hiro.vision.hif.BOX_GENKI）：四位数首位「1」不再掉位。

数据确认 d412748（和存档对比）：第二十～二十五局 80 处元気 ≥1000 读成去掉首位的数（存档 1,388 → 388、1,010 → 10）。
布局框按三位数宽，四位数首位在框左外、末位在框右外；框左放宽 20、右放宽 10。真值按截图目视 / 存档。"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision.digits import read_number
from hiro.vision.hif import BOX_GENKI, read_hif_hud

FIX = Path(__file__).with_name("fixtures") / "genki"


@pytest.mark.parametrize(
    ("name", "genki"),
    [("m25r1-198-1185", 1185), ("m25r1-202-1283", 1283), ("m25r1-203-1388", 1388),  # 第二十五局 R1 末几回合：原来 185 / 28 / 388
     ("q21r2-163-1010", 1010), ("m24-214-1096", 1096), ("m24-215-1263", 1263),  # 原来 10 / 96 / 263
     ("m20r1-141-1024", 1024),  # 四位数末位也在原框右外：只放宽左边会读成 102
     ("m14-136-464", 464)],  # 三位数首位也被切半：原来 164
)  # fmt: skip
def test_genki_four_digits(name, genki):
    img = cv2.imread(str(FIX / f"{name}.jpg"))
    assert read_number(img, BOX_GENKI, align=True) == genki
    assert read_hif_hud(img, fallback=True)["genki"] == genki


def test_genki_lost_head_invariant():
    """兜底：元気从 ≥1000 变成「去掉开头一两位」的尾巴 → read_error（重读）；正常下降、变化不大的不拦。"""
    from hiro.contracts import GameState, Observation, PageObs
    from hiro.state.invariants import _genki_lost_head, check_invariants

    assert _genki_lost_head(1388, 388) and _genki_lost_head(1010, 10) and _genki_lost_head(1185, 185)
    assert not _genki_lost_head(1388, 1300) and not _genki_lost_head(989, 185) and not _genki_lost_head(1500, 480)
    s = GameState()
    s.page.name = "HIF_EXAM"
    s.exam = {"genki": 1283, "remaining_turns": 3}
    obs = Observation(seq=2, t=0.0, page=PageObs("HIF_EXAM", "produce"), fields={"genki": 283, "remaining_turns": 2})
    assert [(c.field, c.verdict, c.rule) for c in check_invariants(s, obs)] == [("exam.genki", "read_error", "digit_count")]
    ok = Observation(seq=2, t=0.0, page=PageObs("HIF_EXAM", "produce"), fields={"genki": 1100, "remaining_turns": 2})
    assert check_invariants(s, ok) == []
