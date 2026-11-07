"""换卡执行侧（kaa_patches）不读截图的部分：阶段 1 角标漏检时按列距推算、阶段 2 牌组序号不当屏内格子号、
没核对上选中的卡时不把「阶段 2 画面不见了」当成换完。设备与识别全用替身。"""

from types import SimpleNamespace as NS

import pytest

pytest.importorskip("kaa")

from hiro.loop import kaa_patches as kp  # noqa: E402


def mark(x1, y1=900):
    return NS(rect=NS(x1=x1, y1=y1))


def test_stage1_inferred_point_from_other_column():
    x = kp.CHANGE1_MARK_X
    assert kp._stage1_inferred_point([mark(x[1] + 3)], 0) == (x[0] + 3 + kp.CHANGE1_CARD_DX, 900 + kp.CHANGE1_CARD_DY)
    assert kp._stage1_inferred_point([mark(x[0])], 2) == (x[2] + kp.CHANGE1_CARD_DX, 900 + kp.CHANGE1_CARD_DY)


def test_stage1_inferred_point_needs_aligned_mark():
    assert kp._stage1_inferred_point([mark(kp.CHANGE1_MARK_X[0] + kp.CHANGE1_ANCHOR_TOL + 30)], 1) is None
    assert kp._stage1_inferred_point([mark(kp.CHANGE1_MARK_X[0])], 5) is None


CARD = {"index": 2, "row": 0, "col": 2, "card_id": "c1", "name": "C1", "upgrade": 0, "rect": (0, 0, 118, 118), "center": (300, 700)}


@pytest.fixture
def stage2(monkeypatch):
    from kaa.tasks import R

    import hiro.vision.deck as deck

    clicks, title = [], {"on": True}
    monkeypatch.setattr(kp, "device", NS(screenshot=lambda: "img", click=lambda *a: clicks.append(a), swipe=lambda *a, **k: None))
    monkeypatch.setattr(kp, "_poll", lambda cond, timeout: True)
    monkeypatch.setattr(kp, "_change_run", lambda: NS(snap=lambda *a: None))
    monkeypatch.setattr(kp, "CHANGE_STAGE_TIMEOUT", 0.3)
    monkeypatch.setattr(kp, "CHANGE_POLL", 0.01)
    monkeypatch.setattr(kp, "protected_card_ids", lambda: set())
    monkeypatch.setattr(kp, "_change_offset_target", lambda *a: (False, None))
    monkeypatch.setattr(kp, "confirm_change_dialog", lambda img, run=None: False)
    monkeypatch.setattr(deck, "read_card_grid", lambda img, region, geometry=None: [dict(CARD)])
    monkeypatch.setattr(deck, "dialog_scroll", lambda img, region: {"scrollable": False, "thumb": None, "at_top": True})
    confirm = []
    monkeypatch.setattr(R.InProduce.DialogCardChange2.ButtonConfirm, "try_click", classmethod(lambda cls, *a, **k: confirm.append(1) or False))
    monkeypatch.setattr(R.InProduce.DialogCardChange2.Title, "exists", classmethod(lambda cls, *a, **k: title["on"]))
    return NS(clicks=clicks, title=title, confirm=confirm)


def test_stage2_unverified_and_gone_is_not_success(stage2, monkeypatch):
    def unreadable(img, target):
        stage2.title["on"] = False  # 点卡后阶段 2 画面被别的东西打断
        return {}

    monkeypatch.setattr(kp, "_read_change_selection", unreadable)
    assert kp.change_stage2(None, 0, card_id="c1") is False
    assert stage2.clicks == [(300, 700)] and stage2.confirm == []  # 没点「チェンジ」


def test_stage2_verified_and_gone_is_success(stage2, monkeypatch):
    def selected(img, target):
        stage2.title["on"] = False
        return {"card_id": "c1", "upgrade": 0}

    monkeypatch.setattr(kp, "_read_change_selection", selected)
    assert kp.change_stage2(None, 0, card_id="c1", remove_upgrade=0) is True


def test_stage2_layout_does_not_use_deck_index_as_screen_index(stage2, monkeypatch):
    """整副扫描过（给了 layout）时 remove_index 是牌组序号：偏移定位不上时退回按 card_id 找，不拿它去对这一屏的格子号。"""
    seen = []
    real = kp.pick_change_target
    monkeypatch.setattr(kp, "pick_change_target", lambda cards, index, card_id, **kw: seen.append(kw["remove_index"]) or real(
        cards, index, card_id, protected=set(), **kw))
    monkeypatch.setattr(kp, "_read_change_selection", lambda img, target: {"card_id": "c1", "upgrade": 0})
    kp.change_stage2(None, 0, card_id="c1", remove_index=2, remove_upgrade=0, layout=[{**CARD, "index": 14, "row": 3}])
    assert seen and set(seen) == {None}
    seen.clear()
    kp.change_stage2(None, 0, card_id="c1", remove_index=2, remove_upgrade=0)
    assert seen and set(seen) == {2}
