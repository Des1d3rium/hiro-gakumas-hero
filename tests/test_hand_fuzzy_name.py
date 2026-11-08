"""手牌：卡面分数不够、卡名 OCR 错一个字时，两者指向同一张卡就认（验证局 codex-20261107-1-i R1 实机）。"""

from pathlib import Path

import pytest

FRAME = Path(__file__).parent / "fixtures" / "trace" / "exam" / "hand-ureshii-1i.png"
ART = "img_general_skillcard_men-1_049"  # 嬉しい誤算


def test_one_char_off_matches_art_candidate():
    from hiro.vision.hif import _card_from_name

    card, exact = _card_from_name("薅しい誤算", [(ART, 0.484)])
    if card is None and _card_from_name("嬉しい誤算", [])[0] is None:
        pytest.skip("没有主数据")
    assert card is not None and card.name == "嬉しい誤算" and not exact


@pytest.mark.parametrize("text, top", [
    ("薅しい誤算", [("img_general_skillcard_men-3_062", 0.5)]),  # 卡面候选是别的卡：不认
    ("薅しい誤", [(ART, 0.5)]),                                   # 长度不同：不认
    ("薅い誤算", [(ART, 0.5)]),
    ("薅し誤", [(ART, 0.5)]),
])
def test_one_char_off_needs_same_card_and_length(text, top):
    from hiro.vision.hif import _card_from_name

    if _card_from_name("嬉しい誤算", [])[0] is None:
        pytest.skip("没有主数据")
    assert _card_from_name(text, top)[0] is None


@pytest.mark.fixtures
def test_real_hand_reads_all_five():
    import cv2

    from hiro.vision.hif import read_hand
    from hiro.vision.offline import load

    if not FRAME.exists():
        pytest.skip("缺样例")
    hand, missing = read_hand(load(cv2.imread(str(FRAME))), cache=None, batch_names=True)
    assert missing == 0
    assert [c["name"] for c in hand] == ["頂点へ", "エンターテイナー", "嬉しい誤算", "アイドルになります", "第一印象+"] or \
        [c["name"].rstrip("+") for c in hand] == ["頂点へ", "エンターテイナー", "嬉しい誤算", "アイドルになります", "第一印象"]
