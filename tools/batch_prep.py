"""前六周（R1 之前）的固定流程：watch 读到这些停点时自己做完，不唤醒代理。

使用者 2026-11-07：「给前六周定下固定流程。永远选支给（差し入れ），和最后的商店。这样都不需要唤醒了。」
- 行动页：有「差し入れ」就选它；只剩「相談」（最后一天）就进相談。
- 相談：shop-auto 补高价值饮料（结束时自带 deck-sync 整副牌组对账，shop.deck_sync），然后「終了」。
- 差し入れ 选饮料：逐个点开读名字，按 DRINK_PRIORITY 选；都不在表里选最右（稀有度最高）。
- 差し入れ 选技能卡：默认停下交给代理（使用者 2026-11-07：「选技能卡暂时还是先唤起你，但是留下一个接口」）。
  接口：conf/batch-rules.json 的 prep.card_prefer / prep.card_avoid（tools/batch_rules.py），
  prefer 按顺序找，第一张命中的就选；卡名不带「+」也能匹配强化版。
  没有规则或没命中时，优先选一次性卡（第三张是一次性就选它，否则第一张一次性卡）；认不出一次性卡才停下。
- 「受け取らないで次へ進みます」确认页：点底部确认。
- 授業换卡：阶段 1 选一次性卡（第三列优先），阶段 2 换出 N 卡或本局之前从 N 卡换进来的牌（原有卡组保护）。
- VEIL：换卡阶段 1 和差し入れ技能卡兜底时按牌组全力値来源总量调优先级（fp_rank：会超过 7 的降低，不到 3 且有 アイドルになります 的提高；换卡先减去预计换出那张）。
- 考前说明页（優勝条件 + タップして次へ）：点 (360,300) 进入考试。
认不出、规则挑不到时返回 None，照旧停下交给代理。轮间（インターバル）和考试不归这里管。
"""

from __future__ import annotations

import re
import time
import unicodedata
from argparse import Namespace

import batch_rules  # 所有挑选规则都在 conf/batch-rules.json 的 prep 段，使用者可随时改

RECEIVE = (353, 1097)  # 差し入れ「受け取る」/ 确认页「受け取らない」
DRINK_X, DRINK_Y = (222, 360, 498), 870
DRINK_NAME_BAND = (480, 560)  # 点开后详情名字所在 y 段
SHOP_END = (640, 1085)
BRIEFING_TAP = (360, 300)
DECLINE = (359, 1006)  # 饮料选择页「受け取らない」（栏满时才有）  # 考前说明页「優勝条件」：点上方空白进入考试
DRINK_PRIORITY = ("センブリソーダ", "ブーストエキス", "初星ホエイプロテイン", "初星スーパーソーダ", "初星湯",
                  "ジンジャーエール", "厳選初星チャイ")  # fmt: skip
CHANGE_PICK = 2  # 换卡阶段 1：第三列优先（是一次性卡时）
BASIC_OUT = ("立ち回りの基本", "表現の基本", "アピールの基本")  # 换卡阶段 2：N 卡


def _norm(t: str) -> str:
    return "".join(unicodedata.normalize("NFKC", t).split())


def pick_drink(names: list[str | None]) -> int | None:
    """names：从左到右读到的饮料名。按 prep.drink_priority 选；都不在表里按 prep.drink_fallback（rightmost / hold）。"""
    for want in batch_rules.get("prep.drink_priority", list(DRINK_PRIORITY)):
        for i, n in enumerate(names):
            if n and _norm(want) in _norm(n):
                return i
    return len(names) - 1 if batch_rules.get("prep.drink_fallback", "rightmost") == "rightmost" else None


def load_card_rules(root=None) -> dict:
    return {"prefer": batch_rules.get("prep.card_prefer", []), "avoid": batch_rules.get("prep.card_avoid", [])}


def pick_card(cands: list[dict], rules: dict | None) -> int | None:
    """差し入れ技能卡：按规则 prefer 顺序选第一张命中的候选（index）；avoid 里的不选；没有规则或都没命中返回 None（停下）。"""
    if not rules:
        return None
    base = lambda n: _norm(n or "").rstrip("+＋")  # noqa: E731
    avoid = {base(n) for n in rules.get("avoid") or []}
    for want in rules.get("prefer") or []:
        for c in cands:
            if c.get("name") and base(c["name"]) == base(want) and base(c["name"]) not in avoid:
                return c["index"]
    return None


def pick_change_in(cands: list[dict], column: int | None = None, rank=None) -> int | None:
    """一次性卡优先：第 column 列（从 1 数，默认 prep.change_column）是一次性就选它，否则第一张一次性卡；没有返回 None（停下）。
    rank(c) → 越小越优先（VEIL 全力値平衡，见 fp_rank），先比 rank 再比列。"""
    column = column or batch_rules.get("prep.change_column", 3)
    once = [c for c in cands if c.get("once")]
    if not once:
        return None
    return min(once, key=lambda c: ((rank(c) if rank else 1), c["col"] != column - 1))["col"]


# ---- VEIL 牌组全力値平衡（使用者 2026-11-07）：账本统计牌组里加全力値的手段；下一次抓卡 / 换卡会让总量超过 FP_HIGH（7）的降低优先级，
# 总量不到 FP_LOW 且牌组有 アイドルになります（全力値消費3）时加全力値的提高优先级。巻き返し 每次消費1，理想总量 5–7。
# 循环牌 オーバードライブ 每回合都打、+2 在循环里自己平衡，不计入。关掉：conf/batch-rules.json 的 prep.veil_fp_balance=false。
VEIL_IDOL = "i_card-skin-hmsz-3-016"
# 上限按理想区间上沿 7（使用者 2026-11-07：1-e 牌组已有 7 时又选了 モチベ+ +2、换入 成果+ +4，「进入全力不是好事」）
FP_HIGH, FP_LOW = 7, 3
FP_SKIP = ("オーバードライブ",)
FP_IDOL = "アイドルになります"


def card_fp(card_id: str | None = None, name: str | None = None, upgrade=None) -> int | None:
    """一张牌打出时立即加的全力値（主数据牌面，与 hiro.brain.veil.fp_effect 同口径）；查不到返回 None。"""
    try:
        from hiro.brain.cards import description_text
        from hiro.brain.veil import fp_effect
        from hiro.state.catalog import _decode, catalog

        cat = catalog()
        if card_id:
            row = cat.card(card_id, int(upgrade or 0))
        else:
            # 主数据的强化版名字本身带「+」（トレーニングの成果+ = upgradeCount 1）
            rows = [_decode(r) for r in cat.db.execute("SELECT * FROM ProduceCard WHERE name = ?",
                                                      (_norm(name or "").replace("＋", "+"),))]
            if not rows:
                return None
            row = rows[0]
        return fp_effect(description_text(row.get("produceDescriptions")))["gain"]
    except Exception:
        return None


def deck_fp(ledger: dict | None) -> tuple[int, bool] | None:
    """账本牌组的全力値来源总量（不计 オーバードライブ）和有没有 アイドルになります；没账本返回 None。"""
    if not ledger or not ledger.get("cards"):
        return None
    base = lambda n: _norm(n).rstrip("+＋")  # noqa: E731
    total = sum(card_fp(name=n) or 0 for n in ledger["cards"] if base(n) not in FP_SKIP)
    return total, any(base(n) == FP_IDOL for n in ledger["cards"])


def change_out_card(ledger: dict | None) -> str | None:
    """换卡阶段 2 预计换出的那张（账本卡名，同 pick_change_out 的顺序）；找不到 None。"""
    if not ledger or not ledger.get("cards"):
        return None
    base = lambda n: _norm(n or "").rstrip("+＋")  # noqa: E731
    order = change_order(ledger)
    for want in order:
        for name in ledger["cards"]:
            if base(name) == want:
                return name
    return None


def change_out_upgrade(ledger: dict | None) -> int | None:
    """换卡阶段 2 预计换出的那张（同 pick_change_out 的顺序，按账本卡名）的强化次数：换入的卡继承它（batch_ledger.record_change），
    阶段 1 候选画面上的强化不算数。账本里找不到要换出的卡返回 None。"""
    if not ledger or not ledger.get("cards"):
        return None
    base = lambda n: _norm(n or "").rstrip("+＋")  # noqa: E731
    order = change_order(ledger)
    for want in order:
        for name in ledger["cards"]:
            if base(name) == want:
                return len(_norm(name)) - len(_norm(name).rstrip("+＋"))
    return None


def fp_rank(ledger: dict | None, veil: bool, *, change: bool = False):
    """候选 → 0（提高）/ 1（照旧）/ 2（降低）。不是 VEIL、没账本、规则关掉时返回 None（不改原顺序）。
    change=True（授業换卡）：候选按换入后的强化算——继承换出那张的「+」（使用者 2026-11-07：强化过的换出、换入的也是强化卡）。"""
    if not veil or not batch_rules.get("prep.veil_fp_balance", True) or (info := deck_fp(ledger)) is None:
        return None
    total, idol = info
    inherit = change_out_upgrade(ledger) if change else None
    if change and (out := change_out_card(ledger)) and _norm(out).rstrip("+＋") not in FP_SKIP:
        # 换卡是一换一：预计换出的那张的全力値要先减掉（1-e 第二次换卡换出 忍耐力+ +3，换入 成果+ +4，实际 9→10）
        total -= card_fp(name=out) or 0

    def rank(c):
        up = max(int(c.get("upgrade") or 0), inherit or 0)
        if c.get("card_id"):
            gain = card_fp(c["card_id"], upgrade=up)
            if gain is None and up:
                gain = card_fp(c["card_id"], upgrade=c.get("upgrade"))  # 主数据没有这档强化：按画面上的算
        else:
            gain = card_fp(name=_norm(c.get("name") or "").rstrip("+＋") + "+" * up)
        if not gain:
            return 1
        if total + gain > FP_HIGH:
            return 2
        return 0 if total < FP_LOW and idol else 1

    rank.total = total
    return rank


def change_order(ledger: dict | None) -> list[str]:
    """换卡阶段 2 换出的先后（去「+」的基名）：prep.change_out 的卡 → 本局之前换进来的卡（新的在前）。
    prep.change_out_once（默认开，使用者 2026-11-08：「我只要变一次happy，剩下一次还是变happy变得卡，不是变两次happy」）：
    change_out 里的每个名字本局只换出一次，换过就跳到换进来的卡。"""
    base = lambda n: _norm(n or "").rstrip("+＋")  # noqa: E731
    order = [base(n) for n in batch_rules.get("prep.change_out", list(BASIC_OUT))]
    if batch_rules.get("prep.change_out_once", True) and ledger:
        done = {base(e.get("card")) for e in ledger.get("log", [])
                if e.get("op") == "remove" and str(e.get("why", "")).startswith("换卡换出")}
        order = [n for n in order if n not in done]
    if batch_rules.get("prep.change_out_swapped", True):
        order += [base(n) for n in swapped_in(ledger)]
    return order


def swapped_in(ledger: dict | None) -> list[str]:
    """本局换卡换进来的卡（账本 log 里 why 以「换卡换入」开头的 add），新的在前。"""
    if not ledger:
        return []
    names = [e["card"] for e in ledger.get("log", []) if e.get("op") == "add" and str(e.get("why", "")).startswith("换卡换入")]
    return names[::-1]


def pick_change_out(cards: list[dict], ledger: dict | None = None) -> tuple[int, int] | None:
    """换卡阶段 2：先换出 N 卡（prep.change_out），再换出本局之前从 N 卡换进来的牌，再换出同名重复卡里没客制化的那张；
    客制化过的卡一律不换，挑不到返回 None（停下）。"""
    base = lambda n: _norm(n or "").rstrip("+＋")  # noqa: E731
    order = change_order(ledger)
    # 客制化过的卡一律不换——本局换卡换进来的牌除外（保护的是回忆里自己客制化的牌；换进来的被认成客制化多半是误认，
    # codex-20261109-1-b：カウントダウン 卡图绿色点阵 LED 招牌被当成客制化绿字，第二次换卡停下）
    mine = {base(n) for n in swapped_in(ledger)}
    free = [c for c in cards if c.get("name") and (not c.get("customized") or base(c["name"]) in mine)]
    for want in order:
        for c in free:
            if base(c["name"]) == want:
                return c["row"], c["col"]
    if batch_rules.get("prep.change_out_duplicate", True):
        # 使用者 2026-11-07：换卡后卡组里有两张同名卡时，换掉没客制化的那张没问题——只要不换客制化的卡
        names = [base(c["name"]) for c in cards if c.get("name")]
        for c in free:
            if names.count(base(c["name"])) >= 2:
                return c["row"], c["col"]
    return None


DRINK_ICON_MIN = 0.55  # 选饮料弹窗模板分下限：实机 6 格正确的 0.60–0.80；非饮料页（外出补给）≤ 0.55
DRINK_ICON_HALF = 52  # 统一模板库按弹窗图标裁的半边长（缩到 56 再比）


def drink_icon_names(img) -> list[str | None]:
    """选饮料弹窗三格的饮料名（图标识别）：多尺度原画模板（hiro.vision.drinks）和考试饮料栏用的统一模板库各认一次，
    两个给出同一种饮料且模板分 ≥ DRINK_ICON_MIN 才算认出，否则这一格给 None（调用方退回点开 + OCR）。"""
    import cv2

    from hiro.vision import items
    from hiro.vision.drinks import DIALOG_SLOT_Y, read_drink_dialog

    try:
        tpl = read_drink_dialog(img)
        lib = items._insitu_library(items.DRINK_SIZE)
        h = DRINK_ICON_HALF
        crops = [cv2.resize(img[DIALOG_SLOT_Y - h : DIALOG_SLOT_Y + h, x - h : x + h], (items.DRINK_SIZE, items.DRINK_SIZE),
                            interpolation=cv2.INTER_AREA) for x in DRINK_X]
        tops = lib.match_many(crops)
    except Exception:  # noqa: BLE001  素材 / 主数据缺：全部退回 OCR
        return [None] * len(DRINK_X)
    out = []
    for t, top in zip(tpl, tops):
        other = lib.names.get(top[0][1]) or top[0][1]
        name = t.get("name")
        out.append(_norm(name) if name and t["score"] >= DRINK_ICON_MIN and _norm(other) == _norm(name) else None)
    return out


def drink_source(boxes, *, pitem: bool = False) -> str:
    """饮料选择页的来源（N12：公開レッスン 奖励饮料原来也写成「差し入れ饮料」）：看左上角标签。"""
    top = [t for t, _, b in boxes if b[1] < 90 and b[0] < 260]
    if pitem:
        return "P道具"
    if any("差し入れ" in t for t in top):
        return "差し入れ"
    if any("公開レッスン" in t for t in top):
        return "公開レッスン"
    if any("レッスン" in t or "授業" in t for t in top):
        return "レッスン"
    return top[0] if top else "饮料页"


def _center(b):
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


CONSULT_READY_WAIT_S = 12.0  # 懒处理：等相談主画面就绪最多这么久（两瓶获得演出约 3s）
CONSULT_READY_GAP_S = 0.8
RETRY = {"consult": False}  # 本次 handle 是「相談没等到就绪」返回 None：watch 同一停点可以再试（不占普通停点的只试一次）


def consult_lazy() -> bool:
    """相談懒处理（使用者 2026-11-08「等到画面稳定的时候才从 watch 交给 shop-auto」，2026-11-09 定为开关、默认关）：
    不再判断「是不是获得演出」，只认「相談主画面就绪」才跑 shop-auto；没就绪就点货架空隙跳过盖着的东西再看。
    HIRO_CONSULT_LAZY=1 或 conf prep.consult_lazy=true 打开。"""
    import os

    v = os.environ.get("HIRO_CONSULT_LAZY")
    return bool(batch_rules.get("prep.consult_lazy", False)) if v is None else v != "0"


def consult_state(img) -> str:
    """相談页现在是什么样：ready = 主画面就绪（右下白色「終了」正常亮度、没有获得名称栏、没有白色キャンセル）；
    covered = 被获得演出盖着（「終了」被星星层压暗，或有获得名称栏），点货架空隙能跳过；other = 别的（弹窗把「終了」提亮等），不点。"""
    from batch_hif import acquire_bar
    from hiro.produce.ops import button_sv
    from hiro.produce.shop import END_PILL, END_SAT_MAX, END_VAL, cancel_visible

    if img is None or img.shape[:2] != (1280, 720):
        return "other"
    sat, val = button_sv(img, END_PILL)
    bar = acquire_bar(img)
    if sat <= END_SAT_MAX and END_VAL[0] <= val <= END_VAL[1] and not bar and not cancel_visible(img):
        return "ready"
    if bar or (sat <= END_SAT_MAX and 150 <= val < END_VAL[0]):
        return "covered"
    return "other"


def wait_consult_ready(device, img, *, timeout=CONSULT_READY_WAIT_S, gap=CONSULT_READY_GAP_S, clock=time.monotonic,
                       sleep=time.sleep, log=print):  # fmt: skip
    """等相談主画面就绪：盖着获得演出就点货架空隙（SHELF_GAP，回到主画面后误点也不选中商品）再看；别的状态只等不点。
    就绪返回这一帧，超时返回 None（照旧停给代理）。"""
    from hiro.produce.hif_buttons import SHELF_GAP

    deadline = clock() + timeout
    taps = 0
    while True:
        state = consult_state(img)
        if state == "ready":
            if taps:
                log(f"相談懒处理：点了 {taps} 下才就绪")
            return img
        if clock() >= deadline:
            log(f"相談懒处理：{timeout:.0f}s 没等到主画面就绪（最后状态 {state}，点了 {taps} 下）")
            return None
        if state == "covered":
            device.click(*SHELF_GAP)
            taps += 1
        sleep(gap)
        img = device.screenshot()


def acquire_overlay(img) -> bool:
    """获得演出盖在页面上（快识别 ACQUIRE + 名称栏像素）。"""
    from batch_hif import acquire_bar
    from hiro.vision.quick import quick_page

    try:
        page = quick_page(img)
    except Exception:  # noqa: BLE001
        return False
    return page is not None and page["page"] == "ACQUIRE" and acquire_bar(img)


def handle(device, ocr, change, state, batch: str, run: int, persist, log=print) -> str | None:
    """当前画面属于前六周固定流程就做完并返回说明；否则返回 None。change = batch_hif.change（复用记账）。"""
    import batch_change

    RETRY["consult"] = False
    if (state.get("active", {}).get("ledger") or {}).get("pending_drink_replace"):
        return None

    img = device.screenshot()
    boxes = [(_norm(t), c, b) for t, c, b in ocr(img)]
    texts = [t for t, _, _ in boxes]
    has = lambda *w: any(x in t for t in texts for x in w)  # noqa: E731

    def run_cmd(command, **kw):
        return change(state, Namespace(batch=batch, command=command, run=run, **kw), persist=persist)

    if has("インターバル", "ジュエル", "破棄", "リタイア"):
        return None

    stage = batch_change.stage_of(texts)
    if stage == 1:
        if batch_rules.get("prep.change_in", "once") != "once":
            return None
        scan = run_cmd("change-scan")
        rank = fp_rank(state["active"].get("ledger"), (state.get("preflight") or {}).get("idol") == VEIL_IDOL, change=True)
        col = pick_change_in(scan.get("candidates") or [], rank=rank)
        if col is None:
            return None
        out = run_cmd("change-pick", col=col, row=None, commit=False, scan=None, name=None, customized=None)
        # 每个候选的排序（0 提高 / 1 照旧 / 2 降低）和一次性写进 prep 行，审计看得出为什么选这张
        fp_note = (f"（牌组全力値 {rank.total}；候选 "
                   + "，".join(f"{c['col']}:{c.get('name')}{'' if c.get('once') else '(非一次性)'}=r{rank(c)}"
                              for c in scan.get("candidates") or []) + "）") if rank else ""
        return f"换卡阶段 1 选第 {col} 列{fp_note}" if out.get("ok") else None
    if stage == 2:
        scan = run_cmd("change-scan")
        target = pick_change_out(scan.get("cards") or [], state["active"].get("ledger"))
        if target is None:
            return None
        out = run_cmd("change-pick", col=target[1], row=target[0], commit=True, scan=None, name=None, customized=None)
        if out.get("ok"):
            # 记账失败时（ledger_todo）游戏里也已经换了：写进 prep 行，代理从 Monitor 看到后补记
            todo = out.get("ledger_todo")
            return f"换卡阶段 2 换出 r{target[0]}c{target[1]}" + (f"；账本待补：{todo}" if todo else "")
        # 实机：点「チェンジ」后直接出「…にチェンジしました」，工具偶尔报确认框没出现——看画面补账
        time.sleep(0.5)
        done = [t for t, _, _ in ocr(device.screenshot()) if "チェンジしました" in _norm(t) or "にチェ" in _norm(t)]
        ledger = state["active"].get("ledger")
        if done and ledger is not None and ledger.get("pending_add"):
            import batch_ledger as L

            todo = L.record_change(ledger, out.get("target"), ledger.get("pending_add"), why="授業换卡（按画面提示补账）")
            persist()
            return "换卡阶段 2（画面提示已换成）" + (f"；账本待补：{todo}" if todo else "")
        return None

    if has("優勝条件") and has("タップ") and batch_rules.get("prep.exam_briefing", True):
        # 考前说明页（交接提示词：点 (360,300) 进入考试）。之后出牌页照旧停下交给代理跑 exam
        device.click(*BRIEFING_TAP)
        return "考前说明页：进入考试"

    if has("受け取らないで次へ"):
        device.click(*RECEIVE)
        return "受け取らない 确认"

    # N3：P 道具「レッスン終了時、ランダムな特定のPドリンクを獲得」给的饮料页没有「受け取るPドリンクを選んでください」标题，
    # 只有候选 + 「受け取る」（栏满时多「受け取らない」和「Pドリンク所持上限」）：同一套饮料规则
    pitem_drink = (not has("受け取るスキルカード") and any(t == "受け取る" for t in texts)
                   and has("Pドリンク所持上限", "ランダムな特定のPドリンク", "特定のPドリンク", "レッスン終了時"))
    if has("受け取るPドリンクを選んでください") or pitem_drink:
        source = drink_source(boxes, pitem=pitem_drink)
        # 使用者 2026-11-08：「更改饮料识别：从ocr识别改为现在又快又好的饮料识别」。先按图标认三格（约 0.2 秒），
        # 没把握的格才退回逐格点开 + OCR 名字（验证局 1-c：OCR 只认出 ほうじ茶，漏了 ブーストエキス，结果没收）
        names = drink_icon_names(img) if batch_rules.get("prep.drink_icons", True) else [None] * len(DRINK_X)
        for i, x in enumerate(DRINK_X):
            if names[i] is not None:
                continue
            device.click(x, DRINK_Y)
            time.sleep(0.7)
            y1, y2 = DRINK_NAME_BAND
            band = [_norm(t) for t, _, _ in ocr(device.screenshot()[y1:y2])]
            names[i] = band[0] if band else None
        full = has("受け取らない")  # 栏满时同一页多出「受け取らない」(359,1006)
        if full and (ledger := state["active"].get("ledger")) is not None:
            import batch_ledger as L

            if L.mark_full(ledger, why=f"{source}饮料页"):
                persist()
        if full:
            high = [h for h in batch_rules.get("shop.high_value", []) if any(n and _norm(h) in _norm(n) for n in names)]
            if not high:
                # 交接提示词：栏满、候选全是低价值 → 受け取らない（下一步「受け取らないで次へ進みます」由上面的分支确认）
                device.click(*DECLINE)
                return f"{source}饮料：栏满且无高价值，受け取らない（{names}）"
            held = list((state["active"].get("ledger") or {}).get("drinks") or [])
            rules = batch_rules.get("shop.high_value", [])
            if held and all(d and any(_norm(h) in _norm(d) for h in rules) for d in held):
                # 手里 4 瓶都认得、都是高价值：没有可丢的，不收（1-e 代理手动做的就是这个）。DECLINE 是勾选框，下一页确认
                device.click(*DECLINE)
                return f"{source}饮料：栏满且持有全是高价值（{held}），受け取らない（{names}）"
            if batch_rules.get("prep.auto_drink_replace", False):
                from batch_driver import replacement
                from hiro.produce.drink_swap import discard_then_receive
                import batch_shop
                import batch_ledger as L

                ledger = state["active"].get("ledger")
                bar = batch_shop.read_bar(device)
                # 实读和账本两边都按 L.norm 统一写法再比（OCR / 识别名和账本写法不同不算对不上）
                seen = [L.norm(d["name"]) if d.get("state") == "drink" and d.get("name") else None for d in bar]
                if ledger is None or seen != [L.norm(n) if n else None for n in ledger.get("drinks") or []]:
                    return None  # 不用旧账本决定丢哪瓶
                choice = replacement(seen, names, batch_rules.get("prep.drink_priority", []), rules, norm=L.norm)
                if choice is None:
                    return None
                # Durable intent: a crash after dropping must not drop a second bottle on restart.
                if ledger.get("pending_drink_replace"):
                    return None
                ledger["pending_drink_replace"] = choice
                persist()
                physical = [{**d, "index": i} for i, d in enumerate(bar)]
                result = discard_then_receive(choice["discard"], choice["pick"], held=physical,
                                              shoot=device.screenshot, click=device.click)
                after = batch_shop.read_bar(device)
                expected = list(seen)
                expected.pop(choice["discard"])
                expected.append(choice["incoming"])
                actual = [L.norm(d["name"]) if d.get("state") == "drink" and d.get("name") else None for d in after]
                if not result.ok or actual != expected:
                    return "饮料替换后账本待补：保留 pending_drink_replace，请核对"
                L.drink_set(ledger, actual, why="固定流程：实读核验后替换饮料")
                ledger.pop("pending_drink_replace")
                persist()
                return f"{source}饮料：替换 {choice['held']} → {choice['incoming']}"
            return None  # 默认仍由代理处理
        i = pick_drink(names)
        if i is None:
            return None
        device.click(DRINK_X[i], DRINK_Y)
        time.sleep(0.6)
        device.click(*RECEIVE)
        ledger = state["active"].get("ledger")
        if ledger is not None and names[i]:
            import batch_ledger as L

            L.drink_add(ledger, names[i], why=f"{source}三选一 {names} 固定流程")
            persist()
        return f"{source}饮料：{names[i]}（{names}）"

    if has("受け取るスキルカードを選んでください"):
        from hiro.state.knowledge import is_once
        from hiro.vision.deck import read_change_candidates

        from pathlib import Path

        cands = read_change_candidates(img)["candidates"]
        index = pick_card(cands, load_card_rules())
        if index is None and batch_rules.get("prep.card_fallback", "once") == "once":
            # 使用者 2026-11-07：规则没定之前，能认出一次性卡就优先选一次性的（第三张是一次性时选它）
            rank = fp_rank(state["active"].get("ledger"), (state.get("preflight") or {}).get("idol") == VEIL_IDOL)
            col = pick_change_in([{"col": c["index"], "once": is_once(c.get("card_id")), "card_id": c.get("card_id"),
                                   "upgrade": c.get("upgrade"), "name": c.get("name")} for c in cands],
                                 batch_rules.get("prep.card_column", 3), rank=rank)
            index = col
        target = next((c for c in cands if c["index"] == index), None) if index is not None else None
        if target is None:
            return None  # 没有规则 / 规则没命中：停下叫代理
        device.click(*target["center"])
        time.sleep(0.6)
        device.click(*RECEIVE)
        ledger = state["active"].get("ledger")
        if ledger is not None and target.get("name"):
            import batch_ledger as L

            L.add_card(ledger, L.card_name(target["name"], target.get("upgrade")), is_once(target.get("card_id")),
                       why="差し入れ技能卡（规则文件 / 一次性优先）")  # fmt: skip
            persist()
        import batch_ledger as L

        # 写账本里的卡名（带「+」），和账本对得上（1-e：这里写「モチベ」、账本记「モチベ+」，审计以为记错）
        return f"差し入れ技能卡：{L.card_name(target['name'], target.get('upgrade')) if target.get('name') else None}"

    # 不再要求读到「行動を選択してください」：这行字压在人物身上，实机 codex-20261109-1 第 1 局 4 日只读出「ください」，
    # 固定路线没认出、watch 停下叫代理（使用者：「去掉第二段吧」）。watch 已按画面布局认出是行动页才会走到这里；
    # 这里只看顶部倒计时 + 底部按钮名整字相等（不再用包含，选饮料 / 选卡页的「差し入れ」标签不在底部）。
    action_page = any("本戦まで" in t for t, _, b in boxes if b[1] < 150)
    if action_page:
        bottom = [(t, b) for t, _, b in boxes if b[1] >= 950]
        gift = next((b for t, b in bottom if t == batch_rules.get("prep.action", "差し入れ")), None)
        consult = next((b for t, b in bottom if t == batch_rules.get("prep.last_day", "相談")), None)
        if gift is not None:
            device.click(*_center(gift))
            return "行动页：差し入れ"
        if consult is not None and not any(w in t for t, _ in bottom for w in ("授業", "レッスン", "おでかけ")):
            device.click(*_center(consult))
            return "行动页：相談"
        return None

    consult_page = any(t == "相談" for t, _, b in boxes if b[1] < 80) and has("Pポイントと交換するもの") and has("終了")
    if consult_page:
        if consult_lazy():
            if wait_consult_ready(device, img, log=log) is None:
                RETRY["consult"] = True
                return None
        elif acquire_overlay(img):
            # 使用者 2026-11-08：P 道具「相談選択時、ランダムな特定のPドリンクを2つ獲得」的获得演出盖在相談页上，
            # 原来在这帧跑 shop-auto（失败「不在相談主画面」）、点「終了」没生效还报 done。交回 watch 连点过去并记账
            return None
        out = None
        if batch_rules.get("prep.consult_shop", True):
            out = run_cmd("shop-auto", max_refresh=batch_rules.get("driver.max_refresh", 10))  # 使用者 2026-11-08：取消刷新上限（conf 设 9999），刷到栏满或 P 不够
            log(f"shop-auto: {out.get('why') or out.get('stop')}")
            if not out.get("ok"):
                return None  # shop-auto 没跑成：不点「終了」、不报 done，留在停点
        # 牌组对账在 shop-auto 里做（退出商店前记账，见 batch_hif shop-auto / shop.deck_sync）
        sync = (out or {}).get("deck_sync") if batch_rules.get("prep.consult_shop", True) else None
        sync_note = f"；牌组对账 {sync.get('diff') if sync.get('diff') is not None else sync.get('why') or sync.get('problems')}" if sync else ""
        time.sleep(0.6)
        img = device.screenshot()
        if any("Pポイントと交換するもの" in _norm(t) for t, _, _ in ocr(img)):
            device.click(*SHOP_END)
            return "相談：shop-auto 后終了" + sync_note
        return None
    return None
