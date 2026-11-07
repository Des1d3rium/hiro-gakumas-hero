"""tools/batch_ledger：回忆栏 → 本局账本，变卡 / 拿卡 / 强化 / 饮料栏 1–4 的记账规则。纯函数。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_ledger as L  # noqa: E402

pytestmark = pytest.mark.synthetic  # 只用合成数据，不读截图

MEMORY = {"memory": "veil-a", "cards": ["巻き返し+", "眠気", "タフネス+", "タフネス+"], "once": ["タフネス+"],
          "rebound_repeatable": True, "evidence": "deck.png"}  # fmt: skip


@pytest.fixture
def ledger():
    return L.new_ledger(MEMORY, ["ブーストエキス", "センブリソーダ"])


def test_new_ledger_pads_slots_and_rejects_gaps(ledger):
    assert ledger["drinks"] == ["ブーストエキス", "センブリソーダ", None, None]
    with pytest.raises(ValueError):
        L.new_ledger(MEMORY, [None, "ブーストエキス"])


def test_change_card_moves_once_flag(ledger):
    L.change_card(ledger, "タフネス+", "忍耐力", True, why="授業")
    assert ledger["cards"].count("タフネス+") == 1 and "タフネス+" in ledger["once"]  # 还剩一张，仍是一次性
    L.remove_card(ledger, "タフネス+")
    assert "タフネス+" not in ledger["once"] and "忍耐力" in ledger["once"]


def test_add_requires_once_and_consistency(ledger):
    with pytest.raises(ValueError):
        L.add_card(ledger, "忍耐力", None)
    with pytest.raises(ValueError):
        L.add_card(ledger, "タフネス+", False)  # 同名卡一次性状态不一致


def test_upgrade_keeps_once(ledger):
    L.add_card(ledger, "忍耐力", True)
    L.upgrade_card(ledger, "忍耐力")
    assert "忍耐力+" in ledger["cards"] and "忍耐力+" in ledger["once"] and "忍耐力" not in ledger["cards"]


def test_drinks_fill_left_and_shift_on_remove(ledger):
    assert L.drink_add(ledger, "初星ホエイプロテイン") == 2
    assert L.drink_remove(ledger, 0) == "ブーストエキス"
    assert ledger["drinks"] == ["センブリソーダ", "初星ホエイプロテイン", None, None]


def test_drink_add_full_refuses():
    ledger = L.new_ledger(MEMORY, ["a", "b", "c", "d"])
    with pytest.raises(ValueError):
        L.drink_add(ledger, "e")


def test_after_exam_drops_consumed_and_compacts(ledger):
    exam = [{"state": "consumed", "name": "ブーストエキス"}, {"state": "drink", "name": "センブリソーダ"},
            {"state": "empty"}, None]  # fmt: skip
    L.after_exam(ledger, exam, why="R1")
    assert ledger["drinks"] == ["センブリソーダ", None, None, None]


def test_compare_drinks_skips_unknown(ledger):
    observed = [{"slot": 0, "state": "unknown"}, {"slot": 1, "state": "drink", "name": "ブーストエキス"},
                {"slot": 2, "state": "empty"}, {"slot": 3, "state": "empty"}]  # fmt: skip
    assert L.compare_drinks(ledger, observed) == ["第 2 格：账本 センブリソーダ，画面 ブーストエキス"]


def test_to_deck_and_card_name(ledger):
    assert L.card_name("タフネス", 1) == "タフネス+" and L.card_name("忍耐力", 0) == "忍耐力"
    deck = L.to_deck(ledger)
    assert deck["cards"] == ledger["cards"] and deck["rebound_repeatable"] is True and deck["evidence"]


def test_memory_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "MEMORY_DIR", tmp_path)
    L.save_memory("veil-a", MEMORY, "i_test")
    assert L.load_memory("veil-a")["cards"] == MEMORY["cards"]
    with pytest.raises(ValueError):
        L.save_memory("veil-a", {**MEMORY, "cards": ["眠気"]}, "i_test")  # 不同牌组不覆盖
    with pytest.raises(ValueError):
        L.memory_path("../x")
