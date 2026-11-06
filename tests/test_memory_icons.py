# 来源：digital-hiro tests/test_memory_icons.py @ 95294d2；开源版：没有截图样例时跳过
"""回忆小图标认卡（第 3 步「メモリー選択」四格 + 回忆选择页详情栏）与开局回忆卡核对。"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from hiro.produce.formation_check import check_memory_cards
from hiro.vision.memory_icons import DETAIL_ICON, card_key, read_icon, read_step3_icons

FIX = Path(__file__).with_name("fixtures")
STEP3 = FIX / "support" / "step3-memory.jpg"
# 2026-11-04 第 3 步截图（メモリー編成 1）四格：人工对照卡面确认
STEP3_TRUTH = ["p_card-02-act-2_062", "p_card-00-men-3_012", "p_card-02-act-3_050", "p_card-02-men-1_035"]


def _need(*paths):
    return pytest.mark.skipif(not all(p.is_file() for p in paths), reason="需要实机截图 tests/fixtures/（游戏画面不随仓库发布）")


NEED_STEP3 = _need(STEP3)


def _img(path):
    img = cv2.imread(str(path))
    return cv2.resize(img, (720, 1280), interpolation=cv2.INTER_CUBIC) if img.shape[1] != 720 else img


def test_card_key():
    assert card_key("p_card-02-act-2_062") == "act-2_062" == card_key("img_general_skillcard_act-2_062-shro")
    assert card_key("x") is None


@NEED_STEP3
def test_step3_icons_recognized():
    reads = read_step3_icons(_img(STEP3), expect=STEP3_TRUTH)
    assert [r["card"] for r in reads] == [card_key(c) for c in STEP3_TRUTH]
    assert all(r["known"] and r["margin"] >= 3 for r in reads)


@pytest.mark.parametrize(("frame", "card"), [
    ("scan-0001-page000", "men-2_004"),  # ワクワクが止まらない
    ("scan-0002-page001", "act-3_049"),  # 至高のエンタメ（別的偶像培育的回忆：插画是 amao 版）
    ("scan-0003-page002", "men-3_011"),  # 叶えたい夢
    ("scan-0004-page003", "act-2_001"),  # シュプレヒコール
    ("picker-p1-daydreaming", "act-1_037"),
    ("picker-p1-kamifubuki", "act-2_062"),
    ("memscan-0-140-647", "men-2_099"),  # 一発勝負
    ("memscan-0-360-647", "men-2_074"),  # タフネス
])  # fmt: skip
def test_detail_icon_recognized(frame, card):
    if not (FIX / "memory" / f"{frame}.jpg").is_file():
        pytest.skip("需要实机截图 tests/fixtures/memory/")
    x1, y1, x2, y2 = DETAIL_ICON
    r = read_icon(_img(FIX / "memory" / f"{frame}.jpg")[y1:y2, x1:x2])
    assert r["card"] == card and r["known"]


def test_heavily_covered_icon_is_unknown_not_wrong():
    """360 宽低清帧、角标压满的アノマリー卡（翔び立て！）：认不准，但要落在「认不出」，不能当成有把握的别的卡。"""
    if not (FIX / "memory" / "memscan-0-140-787.jpg").is_file():
        pytest.skip("需要实机截图 tests/fixtures/memory/")
    x1, y1, x2, y2 = DETAIL_ICON
    r = read_icon(_img(FIX / "memory" / "memscan-0-140-787.jpg")[y1:y2, x1:x2])
    assert not r["known"]


def _plan(cards):
    return {"memory": {"set": 1, "cards": cards, "chosen": [{"card_id": c, "name": c} for c in cards]}}


@NEED_STEP3
def test_check_memory_cards_pass_and_order_free():
    reads = read_step3_icons(_img(STEP3), expect=STEP3_TRUTH[::-1])
    assert check_memory_cards(reads, _plan(STEP3_TRUTH[::-1])) == ([], [])


@NEED_STEP3
def test_check_memory_cards_flags_wrong_card():
    plan_cards = STEP3_TRUTH[:3] + ["p_card-02-act-3_039"]  # 方案第 4 张是 届いて！，画面上是 幸せのおまじない
    reads = read_step3_icons(_img(STEP3), expect=plan_cards)
    problems, _ = check_memory_cards(reads, _plan(plan_cards))
    assert len(problems) == 1 and "第 4 格" in problems[0]


def test_blank_screen_only_warns():
    reads = read_step3_icons(np.full((1280, 720, 3), 128, np.uint8), expect=STEP3_TRUTH)
    problems, warnings = check_memory_cards(reads, _plan(STEP3_TRUTH))
    assert problems == [] and any("布局" in w for w in warnings)


@NEED_STEP3
def test_kaa_patches_mode_switch(monkeypatch):
    pytest.importorskip("kotonebot")
    from hiro.loop import kaa_patches

    img = _img(STEP3)
    plan = _plan(STEP3_TRUTH[:3] + ["p_card-02-act-3_039"])
    monkeypatch.setenv("HIRO_MEMORY_CARD_CHECK", "warn")
    p, w = kaa_patches._check_memory_cards(img, plan)
    assert p == [] and any("不拦" in x for x in w)
    monkeypatch.setenv("HIRO_MEMORY_CARD_CHECK", "off")
    assert kaa_patches._check_memory_cards(img, plan) == ([], [])
    monkeypatch.delenv("HIRO_MEMORY_CARD_CHECK")
    assert kaa_patches._check_memory_cards(img, plan)[0]
