# 来源：digital-hiro tests/test_finals_exam.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""识别-05：本战（produce-008）考试画面与名次页。夹具取自第四局本战 R1（reports/runs/20261031-195627、traces/<run>-main4-20261031-194839-r2）。"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision.extract import extract
from hiro.vision.hif import read_hif_exam
from hiro.vision.observe import observation
from hiro.vision.offline import load
from hiro.vision.scene import classify_current

FIX = Path(__file__).with_name("fixtures") / "finals"


@pytest.mark.parametrize(
    ("name", "rnd", "turns", "hp", "score", "npc"),
    [("exam-r1-t9", 1, 9, 33, 0, [399645, 304890]), ("exam-r1-t8", 1, 8, 23, 1085, [399645, 304890]),
     ("exam-r2-t12", 2, 12, None, 0, [598516, 460505])],  # R2 开局画面压暗，「残りターン」模板只有 0.57
)  # fmt: skip
def test_finals_exam_screen(name, rnd, turns, hp, score, npc):
    img = load(cv2.imread(str(FIX / f"{name}.jpg")))
    assert classify_current().scene == "HIF_EXAM"  # 本战没有突破线，靠 1st / 2nd / 3rd 名次条认出
    r = read_hif_exam(img, cache=None)
    assert (r["finals"], r["finals_round"], r["remaining_turns"], r["my_score"], r["my_rank_index"]) == (True, rnd, turns, score, 3)
    assert r["npc_scores"] == npc and r["rank_line"] is None
    if hp is not None:
        assert r["hp"] == hp
    assert r["hand"] and r["drinks"]
    obs = observation(1, 0.0, "HIF_EXAM", r)
    assert obs.problems(strict=True) == [] and obs.fields["finals_round"] == rnd and obs.fields["score"] == score


def test_finals_ranking_page():
    img = load(cv2.imread(str(FIX / "ranking-before-r1.jpg")))
    scene = classify_current().scene
    assert scene == "HIF_FINALS_RANKING"
    raw, _ = extract(scene, img)
    assert raw == {"kind": "standings", "score": None, "round_score": None,
                   "entries": [{"rank": 1, "name": None, "score": 399645, "me": False}, {"rank": 2, "name": None, "score": 304890, "me": False}],
                   "next": True, "my_score": None, "rank": None, "finals_round": 1, "round_result": None, "retry": None,
                   "my_gain": None, "final": False, "outcome": None, "passed": None}  # fmt: skip
    assert observation(1, 0.0, scene, raw).problems(strict=True) == []


def test_qualifier_exam_not_finals():
    r = read_hif_exam(load(cv2.imread(str(FIX.parent / "hif_hud" / "01694.jpg"))), cache=None)
    assert r["finals"] is False and r["finals_round"] is None


@pytest.mark.parametrize(
    ("name", "rnd", "score", "npc"),
    [("exam-r2seg-00143", 1, 2133, [399645, 304890]),  # 「3rd」徽章盖住白框顶边左段
     ("exam-r2seg-00149", 1, 38984, [399645, 304890]),  # 背景亮白：第 1 张卡左缘外侧也是整列白，曾判成第 1 名
     ("exam-r2seg-00201", 2, 19802, [598516, 460505]),  # 同上，曾判成第 2 名
     ("exam-r2seg-00197", 2, 11415, [None, 460505])],  # 第一位 NPC 被加分特效挡住：按第二位（≥37 万）判 R2，不翻成 R1
)  # fmt: skip
def test_finals_hard_frames(name, rnd, score, npc):
    r = read_hif_exam(load(cv2.imread(str(FIX / f"{name}.jpg"))), cache=None)
    assert (r["finals_round"], r["my_score"], r["my_rank_index"], r["npc_scores"]) == (rnd, score, 3, npc)


def test_finals_round_result_page():
    """「H.I.F本戦 ラウンド1結果 現在順位」：玩家行带「ログ」；分数无 Pt；左下再挑戦。"""
    img = load(cv2.imread(str(FIX / "round1-result.jpg")))
    scene = classify_current().scene
    assert scene == "HIF_FINALS_RANKING"
    raw, _ = extract(scene, img)
    assert [(e["rank"], e["score"], e["me"]) for e in raw["entries"]] == [(1, 479574, False), (2, 365868, False), (3, 117846, True)]
    assert (raw["my_score"], raw["rank"], raw["round_result"], raw["finals_round"], raw["next"]) == (117846, 3, 1, 1, False)
    assert raw["retry"] == {"left": 3, "free": False}
    assert observation(1, 0.0, scene, raw).problems(strict=True) == []


def test_finals_final_result_page():
    """R2 后最终页「敗退 / 現在順位」：每行「总分 +本轮加分」并在一个 OCR 框里，按蓝色加分字切开。"""
    img = load(cv2.imread(str(FIX / "final-result.jpg")))
    scene = classify_current().scene
    raw, _ = extract(scene, img)
    assert scene == "HIF_FINALS_RANKING"
    assert [(e["rank"], e["score"], e["gain"], e["me"]) for e in raw["entries"]] == [
        (1, 1078090, 598516, False), (2, 826373, 460505, False), (3, 179915, 62069, True)]
    assert (raw["my_score"], raw["my_gain"], raw["rank"], raw["final"], raw["outcome"], raw["finals_round"], raw["passed"]) == (
        179915, 62069, 3, True, "敗退", 2, False)
    assert raw["retry"] == {"left": 3, "free": False}
    assert observation(1, 0.0, scene, raw).problems(strict=True) == []


def test_round_interval_header():
    img = load(cv2.imread(str(FIX / "interval-entry.jpg")))
    scene = classify_current().scene
    raw, _ = extract(scene, img)
    assert scene == "HIF_ROUND_INTERVAL"
    assert raw == {"stamina": 18, "max_stamina": 33, "produce_point": 380, "interval": None, "complete": False, "dialog": None}  # 入场过场：货架没出现；通用 OCR 曾把 380 读成 580


def test_result_score_alias():
    from hiro.vision.hif import read_hif_result

    r = read_hif_result(cv2.imread(str(FIX.parent / "hif_hud" / "result-pass-retry3-142118-00038.jpg")))
    assert r["score"] == r["my_score"] == 14062  # 主循环日志按 score 取（识别-06）


def test_finals_start_confirm():
    """本战「開始確認」：当前选中的选拔回忆概要（实机 540x960 截图；曾被判成 commu）。牌组 / 回忆列表待高清样本。"""
    img = load(cv2.imread(str(FIX / "start-confirm-540.png")))
    scene = classify_current().scene
    raw, _ = extract(scene, img)
    assert scene == "HIF_FINALS_START"
    assert raw["selected_memory"] == {"idol": "篠澤広", "star": 759, "params": {"Vo": 1697, "Da": 1936, "Vi": 815},
                                      "growth_pct": {"Vo": 41.3, "Da": 39.4, "Vi": 35.2}, "deck": None, "pitems": None}  # fmt: skip
    assert raw["qualify_memories"] is None
    assert observation(1, 0.0, scene, raw).problems(strict=True) == []


INTERVAL_CARDS = [(0, "デイドリーミング+", 1, 30), (1, "元気な挨拶+", 1, 30), (4, "ひなたぼっこ+", 1, 50), (5, "ありがとうの言葉+", 1, 50)]


@pytest.mark.parametrize(
    ("name", "hp", "p", "recover"),
    [("interval-shelf-17hp", 17, 380, True), ("interval-shelf-fullhp", 33, 300, False)],  # 满体力时「回復」变灰
)  # fmt: skip
def test_round_interval_shelf(name, hp, p, recover):
    """第五局本战轮间货架（reports/live/calib/20261031-2125*-轮间）：cards 0,1,4,5 / changes 2,3 / drinks 6,7。"""
    img = load(cv2.imread(str(FIX / f"{name}.jpg")))
    scene = classify_current().scene
    raw, _ = extract(scene, img)
    assert scene == "HIF_ROUND_INTERVAL" and raw["complete"] is True
    assert (raw["stamina"], raw["max_stamina"], raw["produce_point"]) == (hp, 33, p)
    iv = raw["interval"]
    assert [(c["index"], c["name"], c["upgrade"], c["price"]) for c in iv["cards"]] == INTERVAL_CARDS
    assert [(c["index"], c["name"], c["price"]) for c in iv["changes"]] == [(2, "成長痛", 50), (3, "アイドル宣言", 50)]
    assert [(d["index"], d["name"], d["price"]) for d in iv["drinks"]] == [(6, "特製ハツボシエキス", 100), (7, "厳選初星ティー", 100)]
    assert iv["upgrade"] == {"price": 100, "available": True, "left": 1} and iv["customize"] == {"left": 2}
    assert iv["recover"] == {"available": recover, "price_per_2hp": 10} and iv["refresh"] == {"price": 10, "available": True}
    assert not any(e["purchased"] for k in ("cards", "changes", "drinks") for e in iv[k])
    assert observation(1, 0.0, scene, raw).problems(strict=True) == []


def test_round_interval_fadein_is_incomplete():
    """货架淡入（半透明）帧：格子会读成基础版、回复读成灰——complete=False、interval=None（数据确认 trace 00159）。"""
    img = load(cv2.imread(str(FIX / "interval-fadein.jpg")))
    raw, _ = extract(classify_current().scene, img)
    assert raw["complete"] is False and raw["interval"] is None


def test_round_interval_cell_price_fallback():
    """整块 OCR 漏掉格 5「ありがとうの言葉+」的价格（隔帧 None）：单格放大重读。"""
    img = load(cv2.imread(str(FIX / "interval-price-miss.jpg")))
    raw, _ = extract(classify_current().scene, img)
    assert raw["complete"] is True
    assert [(c["index"], c["price"]) for c in raw["interval"]["cards"]] == [(0, 30), (1, 30), (4, 50), (5, 50)]


@pytest.mark.parametrize(
    ("rel", "kind", "score", "round_score", "rank", "rows"),
    [("round1-result.jpg", "round_result", 117846, 117846, 3,
      [("十王星南", 1, 479574, 479574), ("倉本千奈", 2, 365868, 365868), ("篠澤広", 3, 117846, 117846)]),
     ("final-result.jpg", "final", 179915, 62069, 3,
      [("十王星南", 1, 1078090, 598516), ("倉本千奈", 2, 826373, 460505), ("篠澤広", 3, 179915, 62069)]),
     # 加分逗号读成「、」（820,463+455、281）：以前整行丢掉、我方名次错成 2
     ("final-gain-comma.jpg", "final", 188089, 127705, 3,
      [("十王星南", 1, 1082244, 602535), ("倉本千奈", 2, 820463, 455281), ("篠澤広", 3, 188089, 127705)]),
     # 第二十一局 R2 第 1 次：我方行加分漏了逗号（1,045,084+504068）：以前整行丢掉、「我方第 None 名，总分 None」
     ("final-gain-nocomma-main21-246.jpg", "final", 1045084, 504068, 2,
      [("十王星南", 1, 1081287, 599432), ("篠澤広", 2, 1045084, 504068), ("倉本千奈", 3, 806132, 440026)]),
     # 第二十九局 R2（协调 D28）：加分蓝色小字的 6 被两票 OCR 一起读成 0（402,477），ddddocr 462,477 才对（= 941,193 − R1 478,716）
     ("final-gain-6as0-main29-241.jpg", "final", 941193, 462477, 2,
      [("十王星南", 1, 1082489, 601653), ("篠澤広", 2, 941193, 462477), ("倉本千奈", 3, 814399, 447219)]),
     # Windows 第三局 R2 第 2 次（RapidOCR）：我方行「+」读成「#」整行丢掉；Windows 上另一行也丢过，名次按行序错成第 2
     ("final-plus-hash-win3-228.jpg", "final", 242948, 82260, 3,
      [("十王星南", 1, 1077462, 597988), ("倉本千奈", 2, 814317, 450618), ("篠澤広", 3, 242948, 82260)])],
)  # fmt: skip
def test_finals_round_scores(rel, kind, score, round_score, rank, rows):
    """用户指示：本战 R1 / R2 打完后的「現在順位」弹窗读所有人的分数与名次（字段对齐 HIF_EXAM_RESULT：entries[{name, score, rank, me}] / score / rank）。
    round_score = 本轮得分：轮次结果页 = 总分（R1）；R2 后最终页 = 「+」后的加分。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load
    from hiro.vision.scene import classify_current

    img = load(cv2.imread(str(Path(__file__).with_name("fixtures") / "finals" / rel)))
    scene = classify_current().scene
    assert scene == "HIF_FINALS_RANKING"
    raw, _ = extract(scene, img)
    obs = observation(1, 0.0, scene, raw)
    assert obs.problems(strict=True) == []
    f = obs.fields
    assert (f["kind"], f["score"], f["round_score"], f["rank"]) == (kind, score, round_score, rank)
    assert [(e["name"], e["rank"], e["score"], e["round_score"]) for e in f["entries"]] == rows
    assert [e["me"] for e in f["entries"]] == [e[1] == rank for e in rows]  # 我方 = 名次等于 rank 的那行


def test_six_digit_badge():
    """main8 R2 最后一回合：届いて！+ 的预计得分 106,837 被读成 10,683（读取框到 cx+58，末位被切掉、切剩的半个字被过滤）。
    读取框扩到卡右缘；没有白描边的同色块（卡框角）不当作字。"""
    r = read_hif_exam(load(cv2.imread(str(FIX / "badge-6digit-main8-00155.jpg"))), cache=None)
    assert [(c["name"], c["badge_score"]) for c in r["hand"]] == [("ゆめみごこち+", None), ("きらきら紙吹雪+", 45431), ("届いて！+", 106837)]


@pytest.mark.parametrize(("name", "bought"), [("interval-bought-main8-00097", [4, 7]), ("interval-bought-main8-00101", [0, 4, 7])])
def test_interval_after_purchase(name, bought):
    """main8 R2 轮间：买了第 0 格后连续 4 帧 complete=False（完整性只看第一格的饱和度，买过的格子变灰），interval 给 None，
    reduce 留着旧货架、决策又去买第 0 格；「交換済み」整帧 OCR 也常漏（第 4 格）。现在看没买过的格子，并用白字模板补认已购买。"""
    from hiro.vision.shop import read_interval_shelf

    r = read_interval_shelf(cv2.imread(str(FIX / f"{name}.jpg")))
    assert r["complete"] is True
    iv = r["interval"]
    assert sorted(e["index"] for k in ("cards", "changes", "drinks") for e in iv[k] if e["purchased"]) == bought


@pytest.mark.parametrize(
    ("votes", "want"),
    [([20441, 204412, 20441], 204412),  # main9 #198 实机：放大 OCR 与 ddddocr 都掉了末位，旧的多数票取了 20441
     ([107265, 107265, 10726], 107265), ([11415, 15, 11415], 11415),  # 字体比对掉了开头几位
     ([None, 49060, 4906], 49060), ([58984, 38984, 38984], 38984), ([None, None, 5], 5)],
)  # fmt: skip
def test_vote_truncated(votes, want):
    from hiro.vision.hif import vote_truncated

    assert vote_truncated(votes)[0] == want


def test_finals_hud_six_digit_score():
    r = read_hif_exam(load(cv2.imread(str(FIX / "hud-score-main9-00198.jpg"))), cache=None)
    assert (r["finals"], r["my_score"], r["remaining_turns"]) == (True, 204412, 1)


def test_six_digit_badge_without_font(monkeypatch):
    """没有数字字体：白描边同色字形串交给 ddddocr，位数与字形数一致才用；卡名前异色的属性图标不当起点。"""
    from hiro.vision import digits, hif

    monkeypatch.setattr(digits, "_templates", dict)
    monkeypatch.setattr(hif, "_templates", dict)
    r = read_hif_exam(load(cv2.imread(str(FIX / "badge-6digit-main8-00155.jpg"))), cache=None)
    assert [(c["name"], c["badge_score"]) for c in r["hand"]] == [("ゆめみごこち+", None), ("きらきら紙吹雪+", 45431), ("届いて！+", 106837)]
