"""相談饮料栏满时先丢后买（用户 2026-11-07：「相談满的时候也换，丢了就有空位了」）。开关 HIRO_CONSULT_DRINK_SWAP，默认开，=0 关。"""

import numpy as np
import pytest

from hiro.brain.deciders import ProduceDeciders
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Action, ActionKind, GameState
from hiro.contracts.game_state import Source
from hiro.produce import shop
from hiro.state.produce import FINALS

WHEY, SENBURI, BOOST, TOKUSEI, ROOIBOS = "pdrink_00-3-001", "pdrink_00-2-010", "pdrink_00-2-008", "pdrink_02-3-010", "pdrink_02-1-006"
HELD = [WHEY, SENBURI, BOOST, ROOIBOS]  # ルイボス（好印象）在选拔里是 4 档：最该丢的那瓶


def _state(shelf_drink, *, mode="produce-007", p=300, drinks=HELD, seq=7, src_seq=7):
    shelf = [{"index": 4, "kind": "drink", "drink_id": shelf_drink, "name": None, "price": 100, "purchased": False, "center": [127, 730]}]
    produce = {"idol_card_id": CAMPUS_HIRO.idol_id, "mode": mode, "step": 10, "stamina": 30, "max_stamina": 40, "produce_point": p,
               "params": {"Vo": 500, "Da": 500, "Vi": 500}, "growth_permil": {"Vo": 0, "Da": 0, "Vi": 0}, "star": 0,
               "drinks": list(drinks), "drink_capacity": 4}  # fmt: skip
    st = GameState(mode="produce", produce=produce,
                   page_fields={"shelf": shelf, "drinks": list(drinks), "services": {}}, seq=seq)  # fmt: skip
    st.sources["produce.drinks"] = Source("screen", src_seq)
    return st


def _swap(st, **kw):
    d = ProduceDeciders(CAMPUS_HIRO, None)
    ps = d.produce_state(st)
    return d._consult_swap_drinks(st, ps, st.page_fields["shelf"], ps.produce_point, ps.produce_point, stage="selection", only_accident=False, **kw)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.delenv("HIRO_CONSULT_DRINK_SWAP", raising=False)  # 默认开


@pytest.mark.master
def test_full_bar_swaps_worst_for_better(on):
    dec = _swap(_state(TOKUSEI))
    assert dec is not None and dec.action.kind is ActionKind.SHOP_BUY
    assert dec.action.args == {"index": 4, "discard": 3}, dec.reason  # 丢底栏第 3 格（ルイボス），买特製


@pytest.mark.master
def test_not_better_keeps(on):
    assert _swap(_state(ROOIBOS)) is None  # 换进来的不比丢掉的好：不换


@pytest.mark.master
def test_switch_off(monkeypatch):
    monkeypatch.setenv("HIRO_CONSULT_DRINK_SWAP", "0")
    assert _swap(_state(TOKUSEI)) is None


@pytest.mark.master
def test_stale_bar_read_no_swap(on):
    """底栏饮料不是这一帧读的（下标可能对不上底栏格子）：不换。"""
    assert _swap(_state(TOKUSEI, src_seq=5)) is None


@pytest.mark.master
def test_unaffordable_no_swap(on):
    assert _swap(_state(TOKUSEI, p=80)) is None


@pytest.mark.master
def test_full_through_consult_entry(on):
    """走 _consult_drinks 入口：栏满时转到先丢后买。"""
    st = _state(TOKUSEI)
    d = ProduceDeciders(CAMPUS_HIRO, None)
    ps = d.produce_state(st)
    dec = d._consult_drinks(st, ps, st.page_fields["shelf"], 300, 300, stage="selection", only_accident=False)
    assert dec is not None and dec.action.args.get("discard") == 3


@pytest.mark.master
def test_finals_swap(on):
    st = _state(TOKUSEI, mode=FINALS, p=600)
    d = ProduceDeciders(CAMPUS_HIRO, None)
    ps = d.produce_state(st)
    dec = d._finals_consult_swap(st, ps, st.page_fields["shelf"], 600, None, key=("k",))
    assert dec is not None and dec.action.args.get("discard") is not None and dec.action.args["index"] == 4, dec.reason


# ---------------------------------------------------------------- 执行器：丢之前先核对能买，核对不过一下都不点


def test_executor_refuses_before_discard_when_p_short(monkeypatch):
    clicks = []
    img = np.zeros((1280, 720, 3), np.uint8)
    monkeypatch.setattr(shop, "read_p", lambda im, ocr: 50)
    item = {"index": 4, "kind": "drink", "drink_id": TOKUSEI, "price": 100, "purchased": False, "center": [127, 730]}
    res = shop.execute_shop(Action(ActionKind.SHOP_BUY, {"index": 4, "discard": 3}), {"shelf": [item]},
                            shoot=lambda: img, click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None, ocr=lambda im: [], calib=False)  # fmt: skip
    assert not res.ok and "不丢饮料" in res.error and clicks == []


def test_executor_discard_failure_does_not_buy(monkeypatch):
    clicks = []
    img = np.zeros((1280, 720, 3), np.uint8)
    monkeypatch.setattr(shop, "read_p", lambda im, ocr: 300)
    import hiro.produce.drink_swap as ds

    monkeypatch.setattr(ds, "discard_owned", lambda r, slot, slots: "点饮料栏第 3 格后没出现「Pドリンク詳細」")
    called = []
    monkeypatch.setattr(shop, "_buy", lambda *a, **k: called.append(1))
    item = {"index": 4, "kind": "drink", "drink_id": TOKUSEI, "price": 100, "purchased": False, "center": [127, 730]}
    res = shop.execute_shop(Action(ActionKind.SHOP_BUY, {"index": 4, "discard": 3}), {"shelf": [item]},
                            shoot=lambda: img, click=lambda x, y: clicks.append((x, y)), sleep=lambda s: None, ocr=lambda im: [], calib=False)  # fmt: skip
    assert not res.ok and "Pドリンク詳細" in res.error and called == []
