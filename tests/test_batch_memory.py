"""tools/batch_memory：按交接提示词的回忆优先级判命中、在「再生成前 / 再生成後」之间挑（合成 OCR 框）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_memory as BM  # noqa: E402

pytestmark = pytest.mark.synthetic


def box(t, x, y):
    return (t, 1.0, (x - 60, y - 12, x + 60, y + 12))


@pytest.mark.parametrize("texts,hit", [
    (["インフルエンサー+", "メンタルスキルカードの元気値増加+6", "強気に変更"], "インフルエンサー+：強気に変更"),
    (["エンターテイナー＋", "パラメータ+12"], "エンターテイナー+：パラメータ+12"),
    (["仕切り直し+", "消費体力減少+2"], "仕切り直し+：消費体力減少+2"),
    (["巻き返し+", "すべてのスキルカードのパラメータ値増加+1", "温存に変更"], "巻き返し+：パラメータ値増加+1・制限削除"),
])
def test_priority_hits(texts, hit):
    assert BM.evaluate(texts)["hit"] == hit


def test_plain_or_unupgraded_versions_do_not_hit():
    assert BM.evaluate(["インフルエンサー+", "スキルカードを引く"])["hit"] is None  # 同名普通版
    assert BM.evaluate(["インフルエンサー", "強気に変更"])["hit"] is None  # 没强化
    assert BM.evaluate(["巻き返し+", "パラメータ値増加+1", "レッスン中1回"])["hit"] is None  # 还有一次性限制


def test_choice_picks_hitting_side_and_its_keep_button():
    boxes = [box("再生成前", 180, 200), box("インフルエンサー+", 180, 260), box("強気に変更", 180, 300), box("こちらを残す", 180, 1000),
             box("再生成後", 540, 200), box("タフネス+", 540, 260), box("こちらを残す", 540, 1000)]
    assert BM.is_choice([b[0] for b in boxes])
    r = BM.choose(boxes)
    assert r["pick"] == "before" and r["tap"] == (180, 1000)


def test_choice_without_labels_or_hits_stops():
    assert BM.choose([box("こちらを残す", 180, 1000)])["tap"] is None
    boxes = [box("再生成前", 180, 200), box("タフネス+", 180, 260), box("再生成後", 540, 200), box("達成感+", 540, 260)]
    r = BM.choose(boxes)
    assert r["pick"] is None and r["tap"] is None
