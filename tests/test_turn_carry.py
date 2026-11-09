"""HIRO_TURN_CARRY（使用者 2026-11-08）：状态栏、名次 / 分数、P 道具、属性只在回合开头读一次，回合内沿用；手牌、数字、饮料每帧读。"""
import numpy as np
import pytest

hif = pytest.importorskip("hiro.vision.hif")


@pytest.fixture
def fake(monkeypatch):
    calls = {"status": 0, "ranking": 0, "hand": 0}
    turns = {"v": 9}
    status = {"v": [{"name": "強気", "value": 1}]}
    monkeypatch.setattr(hif, "_locate_letters", lambda img: [])
    monkeypatch.setattr(hif, "is_finals_strip", lambda img: True)
    monkeypatch.setattr(hif, "read_number", lambda img, box, align=False: turns["v"] if box == hif.BOX_TURNS else 30)

    def hand(*a, **k):
        calls["hand"] += 1
        return [], 0

    def status_col(*a, **k):
        calls["status"] += 1
        return status["v"]

    def ranking(img):
        calls["ranking"] += 1
        return {"my_score": 1000, "my_rank_index": 1, "npc_scores": [], "my_score_read": None, "finals_round": 1}

    monkeypatch.setattr(hif, "read_hand", hand)
    monkeypatch.setattr(hif, "read_status_column", status_col)
    monkeypatch.setattr(hif, "read_ranking", ranking)
    monkeypatch.setattr(hif, "read_drink_slots", lambda img, cache=None: [])
    monkeypatch.setattr(hif, "read_pitems", lambda img, cache=None: [])
    monkeypatch.setattr(hif, "_texts", lambda regions, cache: {"attr": "", "break": ""})
    monkeypatch.setattr(hif, "_vote_multiplier", lambda img, pct, cache, attr: (None, []))
    monkeypatch.setattr(hif, "status_summary", lambda s: {})
    monkeypatch.setattr(hif, "_overlay_on_hand", lambda img, letters: False)
    hif.TURN_CARRY.clear()
    return calls, turns, status


def test_carry_reads_status_and_ranking_once_per_turn(fake):
    calls, turns, _ = fake
    img = np.zeros((1280, 720, 3), np.uint8)
    first = hif._read_hif_exam(img, None, carry="x")
    assert "carried" not in first and calls == {"status": 1, "ranking": 1, "hand": 1}
    second = hif._read_hif_exam(img, None, carry="x")
    assert second["carried"]["fields"] and second["statuses"] == first["statuses"]
    assert calls == {"status": 1, "ranking": 1, "hand": 2}  # 手牌每帧读
    turns["v"] = 8  # 新回合：重读
    hif._read_hif_exam(img, None, carry="x")
    assert calls["status"] == 2 and calls["ranking"] == 2
    hif._read_hif_exam(img, None, carry="y")  # 另一场考试：不沿用
    assert calls["status"] == 3
    hif._read_hif_exam(img, None)  # 不开：每帧全读
    assert calls["status"] == 4


def test_carry_waits_for_stance_at_turn_start(fake):
    calls, _, status = fake
    img = np.zeros((1280, 720, 3), np.uint8)
    status["v"] = []  # 回合开头状态栏没读出姿态：不留，下一帧再读
    hif._read_hif_exam(img, None, carry="x")
    hif._read_hif_exam(img, None, carry="x")
    assert calls["status"] == 2
    status["v"] = [{"name": "のんびり", "value": None}]
    hif._read_hif_exam(img, None, carry="x")
    hif._read_hif_exam(img, None, carry="x")
    assert calls["status"] == 3
