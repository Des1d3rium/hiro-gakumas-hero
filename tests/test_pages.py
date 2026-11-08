# 来源：digital-hiro tests/test_pages.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""页面地图（docs/pages-hif.md）：KAA 认不出的 H.I.F 页面判定与字段，PDRINK_MAX 溢出页，培育页饮料栏。

夹具 tests/fixtures/pages/ 取自 datasets/20261029-224103-hif-qualify、reports/live（实机探测图）与 KAA 参考截图，真值按截图目视。
"""

import os
from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision.extract import extract
from hiro.vision.observe import observation
from hiro.vision.offline import load
from hiro.vision.scene import classify_current

FIX = Path(__file__).with_name("fixtures")


def _page(rel: str):
    img = load(cv2.imread(str(FIX / rel)))
    scene = classify_current().scene
    raw, _ = extract(scene, img)
    return scene, raw, img


@pytest.mark.parametrize(
    ("name", "kind"),
    [("dialog-turn_end", "turn_end"), ("dialog-drink_detail", "drink_detail"), ("dialog-drink_use_confirm", "drink_use_confirm"),
     ("dialog-retire", "retire"), ("dialog-consult_end", "consult_end"), ("dialog-customize_confirm", "customize_confirm"),
     ("dialog-other-sort", "other"),
     # 本地流程截图（敗退后「再挑戦確認」：左プロデュース終了 = 失败，不是危险框但也不能自动点）
     ("../walkthrough/17-retry-confirm.png", "retry_confirm")],
)  # fmt: skip
def test_dialogs(name, kind):
    scene, raw, _ = _page(f"pages/{name}" if name.endswith(".png") else f"pages/{name}.jpg")
    assert scene == "DIALOG" and raw["kind"] == kind
    assert raw["danger"] is (kind == "retire")
    obs = observation(1, 0.0, scene, raw)
    assert obs.problems(strict=True) == [] and obs.fields["kind"] == kind


def test_retire_dialog_with_garbled_title():
    """リタイア确认框绝不能当成普通弹窗点确认。原来标题条定位偏、标题 OCR 乱码，靠正文认出；
    标题条改为白色不计入分母后（第二十局「プロデュース再開」）标题直接读对，仍是 retire / danger。"""
    _, raw, _ = _page("pages/dialog-retire.jpg")
    assert raw["kind"] == "retire" and raw["danger"] is True


def test_not_dialog():
    assert _page("pages/not-dialog-orange-scene.jpg")[0] == "unknown"  # 橙色背景的过渡画面
    scene, raw, _ = _page("pages/acquire-gold-bar.jpg")  # 获得演出的金色名称栏不是弹窗标题
    assert scene == "ACQUIRE" and (raw["name"], raw["upgrade"], raw["agree"]) == ("成長痛", 1, True)


@pytest.mark.parametrize(
    ("name", "want"),
    [("briefing-1", {"exam_no": 1, "rank_line": 64, "participants": 128, "star": 180, "params": {"Vo": 568, "Da": 559, "Vi": 528},
                     "multiplier_pct": {"Vo": 1413, "Da": 1247, "Vi": 1085}, "complete": True}),
     ("briefing-2", {"exam_no": 2, "rank_line": 32, "participants": 64, "star": 375, "params": {"Vo": 879, "Da": 1163, "Vi": 471},
                     "multiplier_pct": {"Vo": 1868, "Da": 1962, "Vi": 1112}, "complete": True}),
     ("briefing-animating", {"multiplier_pct": {"Vo": None, "Da": None, "Vi": None}, "complete": False})],
)  # fmt: skip
def test_exam_briefing(name, want):
    scene, raw, _ = _page(f"pages/{name}.jpg")
    assert scene == "HIF_EXAM_BRIEFING"
    assert {k: raw[k] for k in want} == want


@pytest.mark.parametrize(
    ("name", "page", "fields"),
    [("star_gain", "STAR_GAIN", {"gain": 15, "star": None, "star_shown": 100}),
     ("pitem_customize", "PITEM_CUSTOMIZE", {}),
     ("pitem_customize_done", "PITEM_CUSTOMIZE_DONE", {}),
     ("produce_end", "PRODUCE_END", None),
     ("menu-produce", "MENU", {"kind": "produce"}),
     ("upgrade_result", "UPGRADE_RESULT", {"name": "えいえいおー", "card_id": "p_card-02-men-1_031"}),
     ("transition-countdown", "TRANSITION", {"kind": "day_countdown"}),
     ("transition-loading", "TRANSITION", {"kind": "loading"})],
)  # fmt: skip
def test_map_pages(name, page, fields):
    scene, raw, _ = _page(f"pages/{name}.jpg")
    assert scene == page
    if fields is not None:
        assert {k: raw.get(k) for k in fields} == fields
        assert observation(1, 0.0, scene, raw).problems(strict=True) == []


def test_pdrink_max_rows():
    """溢出页（KAA 参考图，上面叠着「報酬スキップ」确认框、整体压暗）：新饮料与已有 3 瓶。"""
    from hiro.vision.items import read_pdrink_max

    r = read_pdrink_max(cv2.imread(str(FIX / "pages" / "pdrink_max-kaa.png")))
    assert r["incoming"]["name"] == "初星水"
    assert [(h["index"], h["name"]) for h in r["held"]] == [(0, "アイスコーヒー"), (1, "リカバリドリンク"), (2, "烏龍茶")]
    assert r["select_left"] == 0
    obs = observation(1, 0.0, "PDRINK_MAX", r)
    assert obs.problems(strict=True) == [] and obs.fields["incoming"]["drink_id"] == "pdrink_00-1-001"
    assert read_pdrink_max(cv2.imread(str(FIX / "shop" / "consult-r5.jpg"))) is None


def test_pdrink_max_two_incoming():
    """一次来两瓶新饮料（第十七局 calib 151258，执行 23e6965 报）：incomings 两瓶都要有（incoming 只是最下面那瓶）；
    「あと1個選択」在 y≈1210（原来只在 img[0:1150] 里找，select_left 一直是 None）。"""
    from hiro.vision.items import read_pdrink_max

    r = read_pdrink_max(cv2.imread(str(FIX / "pages" / "pdrink_max-two-incoming.jpg")))
    assert [(d["index"], d["name"]) for d in r["incomings"]] == [(0, "特製ハツボシエキス"), (1, "初星ホエイプロテイン")]
    assert r["incoming"]["name"] == "初星ホエイプロテイン" and r["select_left"] == 1
    assert [h["name"] for h in r["held"]] == ["初星スペシャル青汁", "初星水"]
    obs = observation(1, 0.0, "PDRINK_MAX", r)
    assert obs.problems(strict=True) == [] and [d["name"] for d in obs.fields["incomings"]] == ["特製ハツボシエキス", "初星ホエイプロテイン"]


@pytest.mark.parametrize(
    ("rel", "names", "y"),
    [("pages/select_drink-full.jpg", ["リカバリドリンク", "ホットコーヒー", "厳選初星ティー", "おしゃれハーブティー"], 1213),
     ("shop/consult-r5.jpg", ["特製ハツボシエキス", "パワフル漢方ドリンク", "烏龍茶", None], 1213),
     ("drinks/r2-00002.jpg", ["特製ハツボシエキス", "パワフル漢方ドリンク", None, None], 1200),
     # 本地 trace（tools/fixtures/build_local.py）：轮间页 4 格全空
     ("trace/interval-shop-empty-bar.jpg", [None, None, None, None], 1213),
     # 本地 trace 考试页：第 3 格是 初星ホエイプロテイン（考试记忆 veil_drinks 记着；当时执行层报「局面与画面对不上：drink:2」）
     # 原画模板下判成 unknown；统一模板（默认开，HIRO_DRINK_INSITU=0 关）补认，见下面 test_drink_bar_insitu
     pytest.param("trace/exam-strong1.jpg", ["ブーストエキス", "センブリソーダ", "初星ホエイプロテイン", "センブリソーダ"], 1200,
                  marks=pytest.mark.xfail(os.environ.get("HIRO_DRINK_INSITU") == "0", strict=True,
                                          reason="原画模板下 初星ホエイプロテイン 匹配分 0.46、判成 unknown")),
     ],
)  # fmt: skip
def test_drink_bar_on_produce_and_exam_pages(rel, names, y):
    """培育页饮料栏比考试页低 13 像素：两种页面都要认出（游戏执行报告培育页 4 格全判成 empty）。"""
    from hiro.vision.items import read_drink_slots

    slots = read_drink_slots(cv2.imread(str(FIX / rel)))
    assert [s["name"] for s in slots] == names
    assert {s["center"][1] for s in slots} == {y}


@pytest.mark.parametrize(
    ("name", "page", "kind"),
    [("transition-exam_title", "TRANSITION", "exam_title"),  # 考场入场「選抜試験1」
     ("transition-exam_result_anim", "TRANSITION", "exam_result_anim"),  # 结算前「突破」大字
     ("not-transition-rental-list", "unknown", None)],  # 台词「いつまでも…」含「まで」，不是倒计时（数据确认 00016）
)  # fmt: skip
def test_transition_rules(name, page, kind):
    scene, raw, _ = _page(f"pages/{name}.jpg")
    assert scene == page
    if kind is not None:
        assert raw["kind"] == kind


def test_acquire_over_consult():
    """相談购买后的获得演出盖在货架上：KAA 判 CONSULT，改判 ACQUIRE 并读出 デイドリーミング+（数据确认 00128–00137）。"""
    scene, raw, _ = _page("pages/acquire-over-consult.jpg")
    assert scene == "ACQUIRE" and (raw["card_id"], raw["upgrade"], raw["agree"]) == ("p_card-02-act-1_037", 1, True)
    assert _page("shop/consult-r5.jpg")[0] == "CONSULT"


@pytest.mark.parametrize(
    ("name", "incoming", "held", "complete"),
    [("pdrink_max-q4-00069", "特製ハツボシエキス", ["ブーストエキス", "烏龍茶", "センブリソーダ"], False),  # 第 3 瓶被视口切掉：露出的效果说明开头只对得上一瓶（D41，下一帧 00073 核实）
     ("pdrink_max-q4-00073", "初星水", ["ブーストエキス", "烏龍茶", "センブリソーダ"], False)],
)  # fmt: skip
def test_pdrink_max_hif_real(name, incoming, held, complete):
    """第四局实机 H.I.F 溢出弹窗：按效果说明与主数据匹配认饮料（图标匹配只有 0.3–0.5）；列表可滚动 → complete=False。"""
    scene, raw, _ = _page(f"pages/{name}.jpg")
    assert scene == "PDRINK_MAX"
    assert raw["incoming"]["name"] == incoming and [h["name"] for h in raw["held"]] == held
    assert raw["complete"] is complete and all(h["method"] in ("effect", "effect_prefix", None) for h in raw["held"])
    obs = observation(1, 0.0, scene, raw)
    assert obs.problems(strict=True) == [] and obs.fields["held"][0]["index"] == 0


def test_gift_box():
    """差し入れ礼物箱（第四局 r4 在此停了 63 s）：判 GIFT，给出发光礼物袋位置供点击。"""
    scene, raw, _ = _page("pages/gift-q4-00030.jpg")
    assert scene == "GIFT"
    x, y = raw["bag"]
    assert 330 <= x <= 410 and 650 <= y <= 740


@pytest.mark.parametrize(
    ("name", "page"),
    [("select_card-not-drink-q4", "SELECT_CARD"),  # 签名曾以 2/3 票判成 SELECT_DRINK（第四局 r3/r4）
     ("select_drink-full", "SELECT_DRINK"),  # 饮料满、已选中一瓶：KAA 提示文字没了，靠签名 + fetch_cards 判
     ("skill_card_change_1", "SKILL_CARD_CHANGE_1")],  # 换卡第 1 步布局同三选一
)  # fmt: skip
def test_card_drink_change_dialogs(name, page):
    assert _page(f"pages/{name}.jpg")[0] == page


def test_consult_incomplete_shelf_is_none():
    """相談入场剧情（老师坐着、没有货架）KAA 仍判 CONSULT，read_consult 会读出幻影格子（实机 r4 00092 读出「私がスター+」）：
    complete=False 时契约 shelf 给 None；正常货架照常给出。"""
    for rel, complete in (("pages/consult-entrance-q4-00092.jpg", False), ("shop/consult-r5.jpg", True)):
        img = load(cv2.imread(str(FIX / rel)))
        raw, _ = extract("CONSULT", img)
        obs = observation(1, 0.0, "CONSULT", raw)
        assert obs.fields["complete"] is complete
        assert (obs.fields["shelf"] is None) is (not complete)
        assert obs.problems(strict=True) == []


@pytest.mark.parametrize(
    ("name", "gain", "shown"),
    [("star_gain-00178", 45, 90),  # 左侧星形图标曾被读成「1」→ 190
     ("star_gain-00319", 15, None),  # 背景饮料说明的「+5」曾被当成 gain；累计数淡出中读不成整串
     ("star_gain-00320", 15, 159)],  # 150→165 滚动途中：star_shown 是画面上的数，star 恒为 None
)  # fmt: skip
def test_star_gain(name, gain, shown):
    scene, raw, _ = _page(f"pages/{name}.jpg")
    assert scene == "STAR_GAIN" and (raw["gain"], raw["star"], raw["star_shown"]) == (gain, None, shown)


@pytest.mark.parametrize(
    ("rel", "full", "held"),
    [("drinks/select_drink-full-q6-00409.jpg", True, ["ミックススムージー", "おしゃれハーブティー", "リカバリドリンク", "初星ホエイプロテイン"]),
     ("pages/select_drink-full.jpg", True, ["リカバリドリンク", "ホットコーヒー", "厳選初星ティー", "おしゃれハーブティー"]),
     ("events/not-acquire-select-drink.jpg", False, ["初星ホエイプロテイン", "初星スペシャル青汁"])],
)  # fmt: skip
def test_select_drink_held_when_full(rel, full, held):
    """差し入れ / 活動支給 的选饮料页：饮料满（「Pドリンク所持上限」、受け取る变灰）时要先丢一瓶再领，
    底栏已有饮料读成 held（格式同 PDRINK_MAX.held，index 为底栏格号）。qualify6 #409 因此跳过了特製ハツボシエキス。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load

    raw, _ = extract("SELECT_DRINK", load(cv2.imread(str(FIX / rel))))
    obs = observation(1, 0.0, "SELECT_DRINK", raw)
    assert obs.problems(strict=True) == []
    f = obs.fields
    assert f["full"] is full and [d["name"] for d in f["held"]] == held
    assert [d["index"] for d in f["held"]] == list(range(len(held)))
    assert set(f["held"][0]) == {"index", "drink_id", "name", "center", "method"} and f["held"][0]["center"][1] in (1200, 1213)
    assert all(d["drink_id"] for d in f["held"])


@pytest.mark.parametrize(
    ("rel", "scene", "drinks"),
    [("scenes/00041-ACTION_SELECT.jpg", "ACTION_SELECT", []),
     ("shop/consult-r5.jpg", "CONSULT", ["pdrink_02-3-010", "pdrink_00-2-009", "pdrink_00-1-004"]),
     ("drinks/bar-mix-smoothie-q6-00293.jpg", "CONSULT", ["pdrink_00-2-004", "pdrink_00-2-010", "pdrink_00-2-001", "pdrink_00-3-001"])],
)  # fmt: skip
def test_bar_drinks_on_produce_pages(rel, scene, drinks):
    """决策复盘：相談 / 选行动页读不到持有饮料与 drink_capacity。底栏 4 格读成 ProduceState.drinks（drink_id 列表）+ drink_capacity=4。
    #293 第 3 格 ミックススムージー 图标分只有 0.445（次佳 0.19），以前判 unknown，整个 drinks 给不出。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation
    from hiro.vision.offline import load

    raw, _ = extract(scene, load(cv2.imread(str(FIX / rel))))
    f = observation(1, 0.0, scene, raw).fields
    assert (f["drinks"], f["drink_capacity"]) == (drinks, 4)


def test_produce_log_not_select_pitem():
    """qualify7：底栏文档图标打开的是行动记录（「1日目」日志），KAA 因「Pアイテム…を獲得」判成 SELECT_PITEM。
    现在判 PRODUCE_LOG，close 为底部 ✕；真正的 P 道具选择页照旧。"""
    scene, raw, _ = _page("pages/produce_log-q7-00016.jpg")
    assert (scene, raw) == ("PRODUCE_LOG", {"close": [360, 1160]})
    assert _page("select_pitem/e2-unselected.jpg")[0] == "SELECT_PITEM"


@pytest.mark.parametrize(("name", "complete"), [("consult-q12-00231", True), ("consult-q12-00232", False), ("consult-q12-00234", False)])
def test_consult_shelf_covered(name, complete):
    """第十二局第 12 天相談：#234 支援卡效果横幅盖住第一行、#232 强化结果演出盖住货架，说明行仍在 → 以前判完整，
    第 0 格读出幻影「アイドル宣言 4P」，决策去买、执行连失败 2 次。现在没买过的格子 ≥2 格读不出就不完整（shelf 给 None）。
    #231 正常货架：第 0 格 励まし+ SALE 70→49 照常读出。"""
    from hiro.vision.extract import extract
    from hiro.vision.observe import observation

    raw, _ = extract("CONSULT", cv2.imread(str(FIX / "shop" / f"{name}.jpg")))
    f = observation(1, 0.0, "CONSULT", raw).fields
    assert f["complete"] is complete
    if complete:
        first = f["shelf"][0]
        assert (first["name"], first["upgrade"], first["price"], first["sale"]) == ("励まし+", 1, 49, True)
    else:
        assert f["shelf"] is None


def test_produce_resume_dialog():
    """首页「プロデュース再開」（第二十局 r1 #207，主循环认不出 41 秒）：标题条右侧白色「リタイア」按钮把这几行的橙色占比压到 0.54，
    原来标题条被切断、整个框认不出；现在白色不计入分母。框里有「リタイア」按钮但不是危险框（要点的是「再開する」）。"""
    from hiro.vision.pages import read_dialog

    d = read_dialog(cv2.imread(str(FIX / "pages" / "dialog-produce-resume-main20.jpg")))
    assert d["kind"] == "produce_resume" and d["danger"] is False


def test_retry_confirm_dialog():
    """本战敗退后「再挑戦確認」（第二十局 r1 #218–233，挂了约 60 秒）：免费 / 不花ジュエル / 券 267→267 / 剩余 3/3 / 按钮位置。"""
    from hiro.vision.pages import page_fields

    f = page_fields("DIALOG", cv2.imread(str(FIX / "pages" / "dialog-retry-confirm-main20.jpg")))
    c = f["confirm"]
    assert f["kind"] == "retry_confirm" and f["danger"] is False
    assert (c["free"], c["jewel"], c["tickets_before"], c["tickets_after"], c["left"], c["total"]) == (True, False, 267, 267, 3, 3)
    assert abs(c["ok"][0] - 500) <= 25 and abs(c["cancel"][0] - 215) <= 25 and abs(c["ok"][1] - 1160) <= 20


def _comm_error_img():
    img = cv2.imread(str(FIX / "pages" / "dialog-comm-error-main22-540.png"))  # 第二十二局本战轮间实机（540x960）
    return cv2.resize(img, (720, 1280), interpolation=cv2.INTER_CUBIC)


def test_comm_error_dialog_retry():
    """「通信エラー / 通信中にエラーが発生しました」（第二十二局轮间挂了 195 秒）：kind=comm_error，带リトライ / タイトルへ 位置，可以リトライ。"""
    from hiro.vision.pages import page_fields

    f = page_fields("DIALOG", _comm_error_img())
    c = f["confirm"]
    assert f["kind"] == "comm_error" and f["danger"] is False and c["forced_title"] is False and c["close"] is None
    assert abs(c["retry"][0] - 518) <= 25 and abs(c["retry"][1] - 1160) <= 20 and abs(c["title"][0] - 213) <= 25


def test_comm_error_forced_title(monkeypatch):
    """「時間が経っているのでタイトルに戻します」只有「閉じる」（还没有实拍帧，用 OCR 结果模拟）：forced_title=True、retry=None。"""
    import hiro.vision.ocr as O
    from hiro.vision.pages import read_comm_error

    fake = [("時間が経っているのでタイトルに戻します", 0.9, (60, 40, 600, 70)), ("閉じる", 0.9, (300, 160, 420, 190))]
    monkeypatch.setattr(O, "ocr_boxes", lambda img, *a, **k: fake)
    c = read_comm_error(_comm_error_img(), 973)
    assert c["forced_title"] is True and c["retry"] is None and c["title"] is None and c["close"] == [360, 1178]


@pytest.mark.parametrize(
    ("name", "names", "cands"),
    [("pdrink_max-cut-q33-00111", ["特製ハツボシエキス", "厳選初星ブレンド", None], ["センブリソーダ", "ブーストエキス"]),
     ("pdrink_max-cut-q33-00390", ["パワフル漢方ドリンク", "センブリソーダ"], ["センブリソーダ"])],
)  # fmt: skip
def test_pdrink_max_cut_row(name, names, cands):
    """D41（第三十三局 18:18 / 18:30）：「手持ちのドリンク」最后一行被列表视口切掉，只露出效果说明开头，原来读成未知饮料，
    决策按「未知最差」丢它、执行找不到这一行而失败，KAA 默认「残す」把新饮料丢了。现在标 partial、给前缀候选，唯一候选直接认出。"""
    from hiro.vision.items import _drink_effects, read_pdrink_max

    r = read_pdrink_max(cv2.imread(str(FIX / "pages" / f"{name}.jpg")))
    assert r["complete"] is False
    assert [h["name"] for h in r["held"]] == names
    last = r["held"][-1]
    assert last["partial"] is True and [_drink_effects()[c][0] for c in last["candidates"]] == cands
    assert all("partial" not in h for h in r["held"][:-1])
    obs = observation(1, 0.0, "PDRINK_MAX", r)
    assert obs.fields["held"][-1]["partial"] is True and obs.fields["held"][-1]["candidates"] == last["candidates"]


@pytest.mark.parametrize(
    ("rel", "page", "cancelable"),
    [("enhance/forced-main33-00070.jpg", "SKILL_CARD_ENHANCE", False),  # P 道具触发的必选强化：只有居中的「強化」
     ("interval_enhance/main17-r1-deck-bottom.jpg", "SKILL_CARD_ENHANCE", True),
     ("events/removal-none-00141.jpg", "SKILL_CARD_REMOVAL", True)],
)  # fmt: skip
def test_card_dialog_cancelable(rel, page, cancelable):
    """D42（第三十三局本战 #70 停机）：强化 / 删卡对话框有没有「キャンセル」→ page_fields.cancelable；False 时决策不能给取消。"""
    from hiro.vision.extract import extract

    raw, _ = extract(page, load(cv2.imread(str(FIX / rel))))
    assert raw["cancelable"] is cancelable
    obs = observation(1, 0.0, page, raw)
    assert obs.fields["cancelable"] is cancelable and obs.problems(strict=True) == []


@pytest.mark.parametrize(
    ("rel", "hud"),
    [("shop/consult-main33-00014.jpg", (13, 33, 380)), ("shop/consult-r5.jpg", (26, 33, 370)), ("shop/consult-q12-00231.jpg", (16, 33, 440))],
)  # fmt: skip
def test_consult_hud(rel, hud):
    """D43（第三十三局本战续跑落在相談，seq 14）：相談页也读头部体力 / P 点（与选行动同一位置），state.produce 才有 P 可决策。"""
    from hiro.vision.extract import extract

    raw, _ = extract("CONSULT", load(cv2.imread(str(FIX / rel))))
    obs = observation(1, 0.0, "CONSULT", raw)
    assert (obs.fields["stamina"], obs.fields["max_stamina"], obs.fields["produce_point"]) == hud
    assert obs.problems(strict=True) == [] and obs.field_meta["produce_point"].method == "ocr"


@pytest.mark.parametrize(
    ("name", "page", "fallback"),
    [("04-lesson-three", None, "ACTION_SELECT"), ("07-public-lesson", None, "ACTION_SELECT"),
     ("09-outing-supply", None, "ACTION_SELECT"), ("11-consult-action", None, "ACTION_SELECT"),
     ("13-r1-result", "HIF_FINALS_RANKING", None), ("16-r2-lost", "HIF_FINALS_RANKING", None),
     ("17-retry-confirm", "DIALOG", None), ("21-produce-complete", "PRODUCE_END", None)],
)  # fmt: skip
def test_walkthrough_page_classes(name, page, fallback):
    """本地流程截图（使用者 2026-11-07 的笔记）：行动页四种（授業 / 公開レッスン / おでかけ・差し入れ / 相談）都由 fallback 认成
    ACTION_SELECT；结果页、再挑戦确认、培育完成由 classify_page 认出。截图是手动框选后缩放的，只核对页面类型。"""
    from hiro.vision.pages import classify_page
    from hiro.vision.quick import fallback_page

    img = cv2.imread(str(FIX / "walkthrough" / f"{name}.png"))
    got = classify_page(img)
    assert (got[0] if got else None) == page
    assert fallback_page(img) == fallback


def test_drink_bar_insitu(monkeypatch):
    """开实机模板（HIRO_DRINK_INSITU=1，分支 claude/drink-insitu-templates）后，考试页第 3 格 初星ホエイプロテイン 认得出、
    其余格不变。没合入那个分支或本机没建实机模板（tools/assets/capture_drinks.py build）时跳过。"""
    from hiro.vision import items

    if not hasattr(items, "insitu_enabled"):
        pytest.skip("还没合入饮料实机模板（claude/drink-insitu-templates）")
    if not items._insitu_library(items.DRINK_SIZE).keys:
        pytest.skip("本机没有饮料实机模板（HIRO_USER_ASSETS/icons/drink_exam）")
    monkeypatch.setenv("HIRO_DRINK_INSITU", "1")
    slots = items.read_drink_slots(cv2.imread(str(FIX / "trace" / "exam-strong1.jpg")))
    assert [s["name"] for s in slots] == ["ブーストエキス", "センブリソーダ", "初星ホエイプロテイン", "センブリソーダ"]
    assert slots[2].get("method") == "insitu"
