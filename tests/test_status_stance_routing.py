"""状态栏：不是蓝底的指针（姿态）图标按字形读成状态，不被当成卡牌效果（第一局本战 R1 veil_stance_unreadable）。
匹配器用替身，只验证分流规则。"""

import numpy as np
import pytest

pytest.importorskip("kaa")

import hiro.vision.status as V  # noqa: E402

pytestmark = pytest.mark.synthetic

META = {"examconcentration": {"name": "強気", "type": "ProduceExamEffectType_ExamConcentration"},
        "examoverpreservation": {"name": "のんびり", "type": "ProduceExamEffectType_ExamOverPreservation"},
        "examlessonbuff": {"name": "集中", "type": "ProduceExamEffectType_ExamLessonBuff"}}  # fmt: skip


@pytest.fixture
def slot(monkeypatch):
    def setup(glyph, enchant):
        monkeypatch.setattr(V, "_icon_index", lambda: (META, None))
        monkeypatch.setattr(V, "_slot", lambda img, k: np.zeros((56, 56, 3), np.uint8))  # 没有蓝底
        monkeypatch.setattr(V, "match_status_icon", lambda patch: glyph)
        monkeypatch.setattr(V, "match_enchant_card", lambda img, k: enchant)
        monkeypatch.setattr(V, "_card_from_asset", lambda asset: type("C", (), {"_id": asset, "name": f"卡:{asset}"})())
        monkeypatch.setattr(V, "_value", lambda img, k, kind, card_id: (1, None))
        return V._read_slot(None, 0)

    return setup


def test_strong_stance_not_misread_as_card(slot):
    e = slot([(0.929, "examconcentration"), (0.803, "examlessonbuff")], (0.603, "act-3_045"))
    assert e["kind"] == "status" and e["name"] == "強気" and e["value"] == 1


def test_relaxed_stance_low_score_still_status(slot):
    e = slot([(0.621, "examoverpreservation"), (0.573, "examlessonbuff")], (0.516, "ido-1_077"))
    assert e["kind"] == "status" and e["name"] == "のんびり"


def test_card_enchant_beats_stance_glyph(slot):
    e = slot([(0.70, "examconcentration"), (0.5, "examlessonbuff")], (0.91, "act-1_041"))
    assert e["kind"] == "enchant" and e["card_id"] == "act-1_041"


def test_non_stance_glyph_keeps_old_routing(slot):
    e = slot([(0.95, "examlessonbuff"), (0.5, "examconcentration")], (0.65, "act-1_027"))
    assert e["kind"] == "enchant"  # 非指针的字形照旧：卡面认得出就是卡牌效果
