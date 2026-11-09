"""牌组一览列表显示读客制化（hiro.produce.deck_customs）：绿字判定 + 主数据逐项认。"""

import numpy as np
import pytest

from hiro.produce import deck_customs as dc


def _hsv(strokes: int, icon: bool, stroke_h: int = 65):
    """100x24 的 HSV 白底图：strokes 道 2px 宽的竖笔画（色相 stroke_h），可选一个黄→绿渐变的实心图标（心形コスト那种）。"""
    hsv = np.zeros((24, 100, 3), np.uint8)
    hsv[:, :, 2] = 255
    for k in range(strokes):
        x = 40 + k * 5
        hsv[4:20, x : x + 2] = (stroke_h, 200, 200)
    if icon:
        for x in range(4, 30):
            hsv[4:20, x] = (30 + (x - 4) * 2, 200, 200)  # 色相 30→80 渐变
    return hsv


def test_green_line():
    box = (0, 0, 100, 24)
    assert dc.green_line(_hsv(4, False), box)  # 绿字笔画
    assert dc.green_line(_hsv(4, True), box)  # 绿字 + 旁边的渐变图标
    assert not dc.green_line(_hsv(0, True), box)  # 只有渐变图标（巻き返し+「♥コスト値減」实机误判）
    assert not dc.green_line(_hsv(4, False, stroke_h=110), box)  # 蓝字
    assert not dc.green_line(_hsv(1, False), box)  # 太少


def _need_master():
    try:
        if not dc.master_items("インフルエンサー+"):
            pytest.skip("没有主数据")
    except Exception:  # noqa: BLE001
        pytest.skip("没有主数据")


def test_identify_veil_deck2():
    """veil-deck2 回忆（2026-11-08 实机列表显示截图读到的四张）。"""
    _need_master()
    assert dc.identify("インフルエンサー+", ["▶強気に変更"], True, 1) == {"items": ["強気追加"], "ok": True, "why": None}
    assert dc.identify("エンターテイナー+", ["◆パラメータ+24"], False, 1)["items"] == ["パラメータ+12"]
    assert dc.identify("仕切り直し+", ["消費体力減少6ターン"], True, 1)["items"] == ["消費体力減少ターン+2"]
    r = dc.identify("巻き返し+", [], False, None)
    assert r["ok"] and r["items"] == ["レッスン中1回の制限削除"]


def test_identify_mismatch():
    _need_master()
    r = dc.identify("インフルエンサー+", ["▶強気に変更"], True, 2)  # 角标 2 项只认出 1 项
    assert not r["ok"]
    r = dc.identify("インフルエンサー+", ["なにか別の効果"], True, None)  # 绿字对不上主数据
    assert not r["ok"]


def test_memory_customs_copy():
    """同名多张客制化不同：按 copy 记到第几张（使用者 2026-11-08「不报问题，就记成两种」）。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import batch_ledger as L

    memory = {"memory": "t", "cards": ["巻き返し+", "ハッピー♪", "巻き返し+"], "once": ["ハッピー♪"], "rebound_repeatable": None,
              "evidence": "test", "customs_known": True,
              "custom": [{"card": "巻き返し+", "item": "レッスン中1回の制限削除", "copy": 1}]}
    ledger = L.new_ledger(memory)
    rebound = [c for c in ledger["deck"] if c["name"] == "巻き返し+"]
    assert rebound[0]["customs"] == [] and rebound[1]["customs"] == ["レッスン中1回の制限削除"]
    assert rebound[1]["once"] is False
    with pytest.raises(ValueError):
        L.memory_customs({"cards": ["巻き返し+", "巻き返し+"], "custom": [{"card": "巻き返し+", "item": "x"}]})


def test_change_order_once(monkeypatch):
    """ハッピー♪ 只换出一次：第二次换卡换前面换进来的卡。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import batch_prep as P

    rules = {"change.out": ["ハッピー♪"], "change.out_once": True, "change.out_swapped": True}
    monkeypatch.setattr(P.S, "get", lambda k, d=None: rules.get(k, d))
    ledger = {"log": []}
    assert P.change_order(ledger)[0] == "ハッピー♪"
    ledger["log"] = [{"op": "remove", "card": "ハッピー♪", "why": "换卡换出 授業换卡"},
                     {"op": "add", "card": "フルスロットル", "why": "换卡换入 授業换卡"}]
    assert P.change_order(ledger) == ["フルスロットル"]
    cards = [{"row": 0, "col": 0, "name": "ハッピー♪", "customized": False},
             {"row": 0, "col": 1, "name": "フルスロットル", "customized": False}]
    assert P.pick_change_out(cards, ledger) == (0, 1)
