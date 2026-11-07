"""手牌卡名批量 OCR（hiro.vision.hif._hand_names）的拼图杂字清理：例子来自实机帧上批量与逐张 OCR 的差异。"""

import pytest

pytest.importorskip("kaa")

from hiro.vision.hif import _clean_batch_name  # noqa: E402


@pytest.mark.parametrize("raw, want", [
    ("｜ガタゴトすやすや+", "ガタゴトすやすや+"),
    ("「アイドルになります", "アイドルになります"),
    ("．教えてあげる+", "教えてあげる+"),
    ("｜仕切り直し+一", "仕切り直し+"),
    ("「ガタゴトすやすや++/", "ガタゴトすやすや++"),
    ("仕切り直し＋＋土", "仕切り直し＋＋"),
    ("エンターテイナー", "エンターテイナー"),  # 卡名本身以「ー」结尾、没有「+」：不动
    ("オーバードライブ+", "オーバードライブ+"),
])
def test_clean_batch_name(raw, want):
    assert _clean_batch_name(raw) == want
