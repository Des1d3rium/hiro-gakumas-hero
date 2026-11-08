"""牌组一览（所持スキルカード）：KAA 认不出的格子用图片认卡补（HIRO_CARD_ICON），22 张两屏正好盖满时按首屏 + 末屏拼。

实机帧：验证局 codex-20261107-1-i 相談前 deck-sync（本地 tests/fixtures/trace/deck/，不进仓库）。没有帧或渲染库时跳过。"""

from collections import Counter
from pathlib import Path

import pytest

DIR = Path(__file__).parent / "fixtures" / "trace" / "deck"
LIB = Path(__file__).resolve().parents[1] / "cache" / "card_icons.npz"
# 同一局账本（22 张）
LEDGER = ["ひと呼吸+", "みずたまりスキップ+", "アイドルになります+", "インフルエンサー+", "エンターテイナー+", "オーバードライブ+",
          "ガタゴトすやすや+", "タフネス+", "ティーパーティ+", "トレーニングの成果+", "フルスロットル+", "一心不乱+", "仕切り直し+",
          "全身全霊+", "嬉しい誤算+", "巻き返し+", "教えてあげる+", "星々を見下ろす一番星", "演出計画+", "第一印象+", "達成感+",
          "頂点へ+"]  # fmt: skip


@pytest.fixture
def screens():
    files = sorted(DIR.glob("deck-list-1i-*.jpg"))
    if len(files) < 2 or not LIB.exists():
        pytest.skip("缺实机帧或渲染库")
    import cv2

    from hiro.vision.offline import load

    return [load(cv2.imread(str(f))) for f in files]


@pytest.mark.fixtures
def test_icons_fill_first_screen_and_deck_matches_ledger(screens, monkeypatch):
    from hiro.vision import deck as D

    monkeypatch.setenv("HIRO_CARD_ICON", "1")
    first = D.read_deck_list(screens[0])
    filled = {(c["row"], c["col"]): c["name"] for c in first["cards"] if (c.get("icon") or {}).get("source") == "icon"}
    assert filled == {(0, 0): "全身全霊", (1, 1): "第一印象", (2, 2): "頂点へ"}
    m = D.merge_deck_list(screens)
    assert m["complete"] and len(m["deck"]) == 22
    assert Counter(c["name"] + "+" * c["upgrade"] for c in m["cards"]) == Counter(LEDGER)
    assert not any("滑过了头" in p for p in m["problems"])


@pytest.mark.fixtures
def test_without_icons_first_screen_has_gaps(screens, monkeypatch):
    from hiro.vision import deck as D

    monkeypatch.setenv("HIRO_CARD_ICON", "0")
    m = D.merge_deck_list(screens)
    assert not m["complete"]  # 原来的情况：3 格认不出，对账作废
