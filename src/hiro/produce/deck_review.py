# 来源：digital-hiro hiro/produce/deck_review.py（c3fc27f）
"""主动查看牌组（docs/state-events.md §6）：选行动页底栏的牌组按钮 → 「所持スキルカード」一览 → 读一屏（不全就滑到底再读、拼起来）→ 关闭。

只在三种情况调用（hif.HiroHifStrategy._maybe_review_deck）：pending_review 含 produce.deck；每场考试之间第一次到选行动页；调试模式。
读不全不下结论（返回 deck=None + problems），由调用方记 pending。所有点击都可注入，便于离线测试。"""

from __future__ import annotations

import time
from collections.abc import Callable
from hiro.produce import hif_buttons as BTN

CLOSE_TEXTS = ("閉じる",)
CLOSE_TAP = BTN.BOTTOM_HIGH  # 一览底部「閉じる」（识屏：KAA OwnedSkillCardsDialog.ButtonClose）
RETAP_AFTER = 5.0  # 点了牌组按钮这么久一览还没出来、画面还是原页面：再点一次
LATE_WATCH = 4.0  # 放弃读牌后再盯这么久，一览晚出来就关掉
OPEN_TIMEOUT = 12.0  # 第二十局开局：一览 6 秒后才弹出（转场后第一次点），读牌放弃后一览留在画面上
LIST_SWIPE = (360, 950, 360, 650)  # 一览卡格视口（LIST_VIEW y 395–1010）里滑 300px ≈ 2 行（行距约 154，一屏整 3 行）：每两屏重叠 1 行，任何张数都能按行拼（识屏建议）
MAX_SCREENS = 6
SAME_SCREEN_DIFF = 2.0  # 视口灰度平均差小于这个 = 滑不动了（到底）
# 滑完等列表停稳再截：先等 SWIPE_SETTLE，之后每 STILL_EVERY 截一张，相邻两张差 < SAME_SCREEN_DIFF 算停稳，最多等 STILL_MAX。
# 第二十三局（录像、机器负载高）：滑到底后列表在回弹，每屏都截在动的时候，相邻屏一直不一样 → 拍满 6 屏，读整副 58.8s（平时 10–14s）
SWIPE_SETTLE, STILL_EVERY, STILL_MAX = 0.8, 0.3, 2.5
SAVE_DIR = None  # 每屏截图存这里（识屏要核对第一次实机的一览页帧）；None 不存


def _view_diff(a, b) -> float:
    import cv2
    import numpy as np

    from hiro.vision.deck import LIST_VIEW

    x1, y1, x2, y2 = LIST_VIEW
    ga, gb = (cv2.cvtColor(i[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32) for i in (a, b))
    return float(np.abs(ga - gb).mean())


def _save(imgs) -> None:
    if SAVE_DIR is None:
        return
    try:
        import cv2

        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for i, im in enumerate(imgs):
            cv2.imwrite(str(SAVE_DIR / f"{stamp}-{i:02d}.jpg"), im)
    except Exception:
        pass


def merge_screens(imgs) -> dict:
    """一览页多屏截图（或已读好的 read_deck_list 结果）→ DECK_LIST 字段。优先用识屏的 merge_deck_list；还没有时两两用 merge_deck_pages 拼。"""
    from hiro.vision import deck as vd

    if hasattr(vd, "merge_deck_list"):
        return vd.merge_deck_list(imgs)
    reads = [im if isinstance(im, dict) else vd.read_deck_list(im) for im in imgs]
    page = reads[0]
    for r in reads[1:]:
        if page.get("deck") is not None:
            break
        page = vd.merge_deck_pages(page, r)
    return page


def _still(shoot, sleep, clock):
    """滑动后等列表停稳再返回这一帧（回弹 / 惯性还在动时相邻两帧差大）；最多等 STILL_MAX，超时返回最后一帧。"""
    sleep(SWIPE_SETTLE)
    prev = shoot()
    deadline = clock() + STILL_MAX
    while clock() < deadline:
        sleep(STILL_EVERY)
        cur = shoot()
        if prev is None or cur is None or _view_diff(prev, cur) < SAME_SCREEN_DIFF:
            return cur
        prev = cur
    return prev


def review_deck(*, shoot: Callable, click: Callable[[int, int], None], swipe: Callable[[int, int, int, int, float], None],
                ocr_texts: Callable, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                text_reader: Callable | None = None) -> dict:  # fmt: skip
    """返回 {deck, deck_size, problems, opened, closed}。deck 为 ProduceState.deck 格式或 None。
    text_reader：给了就在一览打开后改用它读（列表显示 + OCR 卡名，hiro.produce.deck_text），结果放 out["text"]，不读网格。"""
    from hiro.vision.deck import find_deck_button, is_deck_list, merge_deck_pages, read_deck_list

    out = {"deck": None, "deck_size": None, "problems": [], "opened": False, "closed": False}
    img = shoot()
    btn = find_deck_button(img)
    if btn is None:
        out["problems"].append("选行动页底栏没找到牌组按钮")
        return out
    click(*btn)
    start = clock()
    retapped = False
    while clock() - start < OPEN_TIMEOUT:
        sleep(0.5)
        img = shoot()
        if is_deck_list(img):
            out["opened"] = True
            break
        # 第二十局开局：点了之后一览很晚才出（02:29:59 那帧上还没有），可能第一下被转场吃掉 / 晚生效。
        # RETAP_AFTER 秒后画面还是带牌组按钮的原页面（没在加载、没开别的）就再点一次
        if not retapped and clock() - start >= RETAP_AFTER and find_deck_button(img) == btn:
            click(*btn)
            retapped = True
            out["problems"].append("一览没出来，再点了一次牌组按钮")
    if not out["opened"]:
        out["problems"].append("点了牌组按钮，没看到「所持スキルカード」一览")
        # 放弃前再看一会儿：一览晚出来就关掉，不留在画面上（第二十局一览留着，被 KAA 判成 SELECT_PITEM 停机）
        late_deadline = clock() + LATE_WATCH
        while clock() < late_deadline:
            sleep(0.5)
            if is_deck_list(shoot()):
                out["opened"] = True
                out["problems"].append("一览晚出来了，读牌已放弃，直接关掉")
                out["closed"] = _close(shoot, click, ocr_texts, sleep, clock, is_deck_list)
                break
        return out
    sleep(0.6)  # 入场动画
    if text_reader is not None:
        out["text"] = text_reader()
        out["problems"].extend(out["text"].get("problems") or [])
        out["closed"] = _close(shoot, click, ocr_texts, sleep, clock, is_deck_list)
        if not out["closed"]:
            out["problems"].append("牌组一览没关掉")
        return out
    imgs = [shoot()]
    reads = [read_deck_list(imgs[0])]
    page = reads[0]
    if page.get("deck") is None:  # 一屏不全：往下滑着每屏截一张（停稳后），读完就拼，拼出整副或滑不动了就停
        for _ in range(MAX_SCREENS - 1):
            swipe(*LIST_SWIPE, 0.6)
            im = _still(shoot, sleep, clock)
            if _view_diff(imgs[-1], im) < SAME_SCREEN_DIFF:
                break
            imgs.append(im)
            reads.append(read_deck_list(im))
            page = merge_screens(reads)
            if page.get("deck") is not None:
                break
    _save(imgs)
    out["page"] = page
    out["screens"] = len(imgs)
    out["deck"], out["deck_size"] = page.get("deck"), page.get("deck_size")
    out["problems"].extend(page.get("problems") or [])
    out["closed"] = _close(shoot, click, ocr_texts, sleep, clock, is_deck_list)
    if not out["closed"]:
        out["problems"].append("牌组一览没关掉")
    return out


def _close(shoot, click, ocr_texts, sleep, clock, is_deck_list) -> bool:
    deadline = clock() + OPEN_TIMEOUT
    while clock() < deadline:
        img = shoot()
        if not is_deck_list(img):
            return True
        box = next((t for t in ocr_texts(img) if t[0].strip() in CLOSE_TEXTS), None)
        click(*(box[1] if box is not None else CLOSE_TAP))
        sleep(0.8)
    return not is_deck_list(shoot())
