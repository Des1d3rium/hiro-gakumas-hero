"""tools/batch_prep：前六周固定流程的挑选规则。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import batch_prep as P  # noqa: E402

pytestmark = pytest.mark.synthetic


def test_pick_drink_by_priority_then_rightmost():
    assert P.pick_drink(["初星湯", "初星スーパーソーダ", "厳選初星チャイ"]) == 1
    assert P.pick_drink(["初星水", "センブリソーダ", None]) == 1
    assert P.pick_drink(["初星水", "烏龍茶", "ほうじ茶"]) == 2


def test_change_in_prefers_third_once_card():
    c = lambda col, once: {"col": col, "once": once}  # noqa: E731
    assert P.pick_change_in([c(0, True), c(1, True), c(2, True), c(3, False)]) == 2
    assert P.pick_change_in([c(0, False), c(1, True), c(2, False)]) == 1
    assert P.pick_change_in([c(0, False), c(1, False)]) is None


def test_change_out_protects_original_deck():
    cards = [{"row": 0, "col": 0, "name": "トレーニングの成果"}, {"row": 2, "col": 2, "name": "立ち回りの基本"},
             {"row": 3, "col": 1, "name": "涙の思い出"}]
    assert P.pick_change_out(cards) == (2, 2)  # N 卡先换
    ledger = {"log": [{"op": "add", "card": "涙の思い出+", "why": "换卡换入 授業换卡"}]}
    assert P.pick_change_out(cards[:1] + cards[2:], ledger) == (3, 1)  # 再换之前从 N 卡换进来的
    assert P.pick_change_out(cards[:1], ledger) is None  # 只剩一张原有卡：不换，停下
    dup = [{"row": 0, "col": 0, "name": "トレーニングの成果", "customized": True},
           {"row": 1, "col": 2, "name": "トレーニングの成果", "customized": False}]
    assert P.pick_change_out(dup) == (1, 2)  # 同名两张：换没客制化的那张
    assert P.pick_change_out([{"row": 0, "col": 0, "name": "立ち回りの基本", "customized": True}]) is None  # 客制化过的不换


def test_card_pick_needs_rules():
    cands = [{"index": 0, "name": "気合十分！"}, {"index": 1, "name": "オープニングアクト"}, {"index": 2, "name": "プライド"}]
    assert P.pick_card(cands, None) is None  # 没有规则：交给一次性优先 / 停下
    rules = {"prefer": ["国民的アイドル+", "プライド+", "オープニングアクト"], "avoid": ["気合十分！"]}
    assert P.pick_card(cands, rules) == 2  # 卡名带不带 + 都算
    assert P.pick_card(cands, {"prefer": ["気合十分！"], "avoid": ["気合十分！"]}) is None


def test_rules_file_overrides_defaults(tmp_path, monkeypatch):
    import batch_rules

    f = tmp_path / "rules.json"
    f.write_text('{"prep": {"drink_priority": ["初星水"], "drink_fallback": "hold"}}', encoding="utf-8")
    monkeypatch.setenv("HIRO_BATCH_RULES", str(f))
    assert P.pick_drink(["烏龍茶", "初星水", "センブリソーダ"]) == 1  # 改了优先表马上生效
    assert P.pick_drink(["烏龍茶", "ほうじ茶"]) is None  # fallback=hold：停下
    assert batch_rules.get("memory.no_hit") == "first"  # 没写的项用默认


def test_drink_source_label():
    b = lambda t, x, y: (t, 1.0, (x - 40, y - 12, x + 40, y + 12))  # noqa: E731
    assert P.drink_source([b("差し入れ", 110, 52)]) == "差し入れ"
    assert P.drink_source([b("Vo.公開レッスン", 109, 52)]) == "公開レッスン"
    assert P.drink_source([b("Vo.公開レッスン", 109, 52)], pitem=True) == "P道具"


class _Dev:
    def __init__(self, frames):
        self.frames, self.clicks = frames, []

    def screenshot(self):
        import numpy as np

        return np.zeros((1280, 720, 3), np.uint8)

    def click(self, x, y):
        self.clicks.append((x, y))


def test_pitem_drink_page_full_declines_low_value(monkeypatch):
    """N3：P 道具「レッスン終了時、ランダムな特定のPドリンクを獲得」的饮料页（实机 08:20 OCR）：栏满、候选不是高价值 → 受け取らない。"""
    page = [("Vo.公開レッスン", 1.0, (60, 40, 160, 64)), ("レッスン終了時、", 1.0, (150, 440, 300, 464)),
            ("ランがんか性定のロドリンカ左猫相", 0.3, (180, 460, 450, 484)), ("受け取らない", 1.0, (300, 994, 420, 1018)),
            ("受け取る", 1.0, (300, 1085, 400, 1109)), ("Pドリンク所持上限", 1.0, (280, 1120, 440, 1144))]
    names = iter(["初星水", "烏龍茶", "烏龍茶"])
    calls = {"n": 0}

    def ocr(img):
        calls["n"] += 1
        if calls["n"] == 1:
            return page
        return [(next(names), 1.0, (300, 20, 420, 40))]

    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    dev = _Dev(None)
    out = P.handle(dev, ocr, None, {"active": {"ledger": None}}, "b", 1, persist=lambda: None)
    assert out.startswith("P道具饮料：栏满且无高价值") and dev.clicks[-1] == P.DECLINE


def _fp_table(monkeypatch, table):
    monkeypatch.setattr(P, "card_fp", lambda card_id=None, name=None, upgrade=None: table.get(name or card_id, 0))


def test_veil_fp_balance_demotes_overflow_and_promotes_when_low(monkeypatch):
    """使用者 2026-11-07（VEIL）：抓卡 / 换卡会让牌组全力値来源超过 10 的降低优先级；不到 3 且有 アイドルになります 时加全力値的提高。"""
    _fp_table(monkeypatch, {"成果+": 4, "心・技・体": 3, "涙+": 4, "オーバードライブ+": 2, "id_tears": 4})
    cands = [{"col": 0, "once": True, "name": "ハッピー"}, {"col": 2, "once": True, "card_id": "id_tears"}]
    high = {"cards": ["オーバードライブ+", "成果+", "心・技・体", "アイドルになります+"]}  # 7（不计 オーバードライブ）
    rank = P.fp_rank(high, veil=True)
    assert rank.total == 7 and P.pick_change_in(cands, 3, rank=rank) == 0  # 第 3 列 +4 会到 11：让位
    assert P.pick_change_in(cands, 3, rank=P.fp_rank(high, veil=False)) == 2  # 不是 VEIL：照旧第 3 列
    low = {"cards": ["オーバードライブ+", "アイドルになります+"]}
    cands = [{"col": 0, "once": True, "card_id": "id_tears"}, {"col": 2, "once": True, "name": "ハッピー"}]
    assert P.pick_change_in(cands, 3, rank=P.fp_rank(low, veil=True)) == 0  # 总量 0 < 3：加全力値的优先
    no_idol = {"cards": ["オーバードライブ+"]}
    assert P.pick_change_in(cands, 3, rank=P.fp_rank(no_idol, veil=True)) == 2


def test_card_fp_reads_master_names_with_plus():
    pytest.importorskip("kaa")
    try:
        assert P.card_fp(name="トレーニングの成果+") == 4 and P.card_fp(name="プライド") == 2
    except AssertionError:
        if P.card_fp(name="プライド") is None:
            pytest.skip("没有主数据")
        raise


def test_veil_fp_change_counts_inherited_upgrade(monkeypatch):
    """换出「立ち回りの基本+」→ 换入的卡也带「+」：全力値按强化版算（涙の思い出 +3 → 涙の思い出+ +4）。"""
    seen = []

    def fp(card_id=None, name=None, upgrade=None):
        seen.append((card_id, upgrade))
        return {0: 3, 1: 4}[int(upgrade or 0)] if card_id == "tears" else {"成果+": 4}.get(name, 0)

    monkeypatch.setattr(P, "card_fp", fp)
    ledger = {"cards": ["立ち回りの基本+", "成果+", "アイドルになります+"]}  # 总量 4
    cands = [{"col": 0, "once": True, "name": "ハッピー"}, {"col": 2, "once": True, "card_id": "tears", "upgrade": 0}]
    assert P.change_out_upgrade(ledger) == 1
    rank = P.fp_rank(ledger, veil=True, change=True)
    assert rank(cands[1]) == 2 and ("tears", 1) in seen  # 4 + 4 = 8 > 7：降低（按强化版 +4 算）
    ledger_small = {"cards": ["立ち回りの基本+", "アイドルになります+"]}
    assert P.fp_rank(ledger_small, veil=True, change=True)(cands[1]) == 0  # 总量 0 < 3 且有アイドル：提高
    ledger["cards"].append("その他+3")
    monkeypatch.setattr(P, "card_fp", lambda card_id=None, name=None, upgrade=None:
                        ({0: 3, 1: 4}[int(upgrade or 0)] if card_id == "tears" else {"成果+": 4, "その他+3": 3}.get(name, 0)))
    assert P.fp_rank(ledger, veil=True, change=True)(cands[1]) == 2  # 7 + 4 = 11：降低
    assert P.fp_rank(ledger, veil=True, change=False)(cands[1]) == 2  # 差し入れ按画面 +3：7 + 3 = 10 > 7


def test_veil_fp_change_subtracts_card_going_out(monkeypatch):
    """1-e：第二次换卡换出 忍耐力+（+3）先减掉；换入 成果+（+4）后 10 > 7 仍降低（使用者：进入全力不是好事）。"""
    _fp_table(monkeypatch, {"忍耐力+": 3, "成果+": 4, "モチベ+": 2, "id_seika": 4})
    ledger = {"cards": ["成果+", "忍耐力+", "モチベ+", "アイドルになります+"],
              "log": [{"op": "add", "card": "忍耐力+", "why": "换卡换入 授業"}]}
    rank = P.fp_rank(ledger, veil=True, change=True)
    assert rank.total == 6 and rank({"card_id": "id_seika", "upgrade": 1}) == 2  # 9 - 3 + 4 = 10 > 7


def test_prep_change_pick_namespace_has_every_cli_arg(monkeypatch):
    # 2026-11-08 第 2 局：change-pick 加了 --name/--customized，prep 自己拼的 Namespace 没带 → AttributeError，阶段 1、2 自动换卡全挂
    import batch_change
    import batch_hif

    sub = next(a for a in batch_hif.build_parser()._actions if a.dest == "command").choices["change-pick"]
    dests = {a.dest for a in sub._actions if a.dest != "help"}
    seen = []

    def change(state, ns, persist=None):
        if ns.command == "change-scan":
            return {"candidates": [{"col": 0, "once": True}], "cards": [{"row": 2, "col": 2, "name": "立ち回りの基本"}]}
        seen.append(ns)
        assert dests <= set(vars(ns)), dests - set(vars(ns))
        return {"ok": False}

    class Dev:
        def screenshot(self):
            return None

    monkeypatch.setattr(P, "fp_rank", lambda *a, **k: None)
    state = {"active": {"ledger": None}}
    for stage in (1, 2):
        monkeypatch.setattr(batch_change, "stage_of", lambda texts, s=stage: s)
        P.handle(Dev(), lambda img: [], change, state, "b", 1, persist=lambda: None)
    assert len(seen) == 2


def test_drink_page_uses_icon_names_and_ocr_only_for_unsure_slots(monkeypatch):
    """使用者 2026-11-08：选饮料页先按图标认，没把握的格才点开 + OCR。"""
    page = [("Vo.公開レッスン", 1.0, (60, 40, 160, 64)), ("レッスン終了時、", 1.0, (150, 440, 300, 464)),
            ("受け取らない", 1.0, (300, 994, 420, 1018)), ("受け取る", 1.0, (300, 1085, 400, 1109)),
            ("Pドリンク所持上限", 1.0, (280, 1120, 440, 1144))]
    calls = {"n": 0}

    def ocr(img):
        calls["n"] += 1
        return page if calls["n"] == 1 else [("烏龍茶", 1.0, (300, 20, 420, 40))]

    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    monkeypatch.setattr(P, "drink_icon_names", lambda img: ["初星水", None, "ほうじ茶"])
    dev = _Dev(None)
    out = P.handle(dev, ocr, None, {"active": {"ledger": None}}, "b", 1, persist=lambda: None)
    assert out.startswith("P道具饮料：栏满且无高价值") and "初星水" in out and "烏龍茶" in out
    assert calls["n"] == 2 and dev.clicks[0] == (P.DRINK_X[1], P.DRINK_Y)  # 只点开了没把握的第 2 格
    # 图标认出高价值（验证局 1-c 漏掉的 ブーストエキス）：不再当成低价值直接不收
    calls["n"] = 0
    monkeypatch.setattr(P, "drink_icon_names", lambda img: ["ブーストエキス", "ミックススムージー", "ほうじ茶"])
    dev = _Dev(None)
    out = P.handle(dev, ocr, None, {"active": {"ledger": None}}, "b", 1, persist=lambda: None)
    assert out is None or "無高価値" not in out and "无高价值" not in out
    assert P.DECLINE not in dev.clicks


def test_drink_icon_names_real_pitem_page():
    """验证局 1-c 实机：P 道具饮料页（栏满）三格 = ブーストエキス / ミックススムージー / ほうじ茶。"""
    import cv2

    path = Path(__file__).parent / "fixtures" / "trace" / "drink-pitem-full-1c.png"
    if not path.exists():
        pytest.skip("缺样例")
    names = P.drink_icon_names(cv2.imread(str(path)))
    if names == [None, None, None]:
        pytest.skip("没有饮料素材")
    assert names == ["ブーストエキス", "ミックススムージー", "ほうじ茶"]
