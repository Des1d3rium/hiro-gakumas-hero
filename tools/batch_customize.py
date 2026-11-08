"""轮间（インターバル）技能卡客制化，给 batch_hif 的 customize 命令用。

轮间点「スキルカードカスタマイズ」后的选卡页 / 菜单页与特別指導同款，但版式不同（第一局本战轮间实机帧）：
- 选卡页：卡格 4 列（x≈140/287/434/580）、行距 ≈147，可滚动；上方详情栏显示选中卡名（y≈110）；
  底部「もどる」(215,1160)、「カスタマイズ」(500,1160)。
- 菜单页：3 个色块中心 (141/360/578, 935)，项名 y≈926，上方「あと N回」，做满的块下方「カスタマイズ上限」；
  「一覧に戻る」(215,1160)、「実行する」(500,1160)；详情卡名 y≈160。
- 实行 →「カスタマイズ実行確認」→「実行する」→ 回到轮间主画面并弹「〜をカスタマイズしました」（不回菜单）。
按图认卡；认不出的格才点一下读详情卡名。同名多张时用 need_maxed 指定「已做满的那一项」挑出正确的那张。
"""

from __future__ import annotations

import time

from batch_ledger import norm
from hiro.produce import hif_buttons as BTN

INTERVAL_BTN = BTN.INTERVAL_CUSTOMIZE
GRID = (40, 545, 680, 1060)
GRID_CUSTOMIZE, GRID_BACK = (500, 1160), (215, 1160)
MENU_X, MENU_Y = (141, 360, 578), 935
MENU_EXEC, MENU_BACK = (500, 1160), (215, 1160)
CONFIRM_TAP = BTN.DIALOG_OK
TOAST_TAP = BTN.TOAST
TICK = 0.3
CORNER = BTN.CORNER
DETAIL_BAND = (60, 200)  # 选卡页上方详情栏（卡名在 y 90–140）
DETAIL_POLL = 0.2
SAME = 1.5  # 缩略图平均差小于它 = 同一画面，沿用上次 OCR
MOVING = 4.0  # 与上一帧差大于它 = 画面还在切换
MOVING_SKIP_S = 1.0  # 画面在切换时最多这么久不 OCR
TOAST_BAND = (900, 1150)  # 「〜をカスタマイズしました」提示所在的横带
STEP_TIMEOUT, DONE_TIMEOUT = 4.0, 10.0


def _base(name: str) -> tuple[str, int]:
    n = norm(name).replace("＋", "+")
    return n.rstrip("+"), len(n) - len(n.rstrip("+"))


def same_card(text: str | None, want: str) -> bool:
    if not text:
        return False
    got_base, got_up = _base(text)
    want_base, want_up = _base(want)
    return got_base == want_base and (got_up == want_up or got_up == 0)  # 详情栏的「+」常被 OCR 漏掉


def item_match(name: str | None, want: str) -> bool:
    """菜单项名匹配：包含关系，或开头两字相同（OCR 会把「成長追加」读成「成長+」）。"""
    if not name:
        return False
    a, b = norm(name), norm(want)
    return b in a or a in b or (len(a) >= 2 and len(b) >= 2 and a[:2] == b[:2])


def menu_items(boxes) -> list[dict]:
    """菜单 3 项：[{index, name, left, maxed, center}]。"""
    import re

    out = []
    for i, x in enumerate(MENU_X):
        col = [(norm(t), (b[1] + b[3]) // 2) for t, _, b in boxes if abs((b[0] + b[2]) // 2 - x) <= 100]
        name = "".join(t for t, y in col if 900 <= y <= 960 and not t.isdigit())
        left = next((int(m.group(1)) for t, y in col if 870 <= y <= 905 and (m := re.search(r"あと(\d+)回", t))), None)
        maxed = any("上限" in t for t, y in col if 975 <= y <= 1020)
        out.append({"index": i, "name": name or None, "left": left, "maxed": maxed, "center": [x, MENU_Y]})
    return out


class _Ops:
    def __init__(self, device):
        from hiro.produce.ops import find_text, ocr_boxes

        self.d, self.ocr, self.find = device, ocr_boxes, find_text

        self.th = self.boxes = self.prev = None
        self.read_at = -1e9

    @staticmethod
    def _thumb(img):
        import cv2

        return cv2.cvtColor(cv2.resize(img, (36, 64), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype("float32")

    def read(self, img):
        """整帧 OCR，画面和上次读时几乎一样（缩略图平均差 < SAME）就沿用上次结果。"""
        th = self._thumb(img)
        if self.th is not None and self.boxes is not None and float(abs(th - self.th).mean()) < SAME:
            return self.boxes
        self.th, self.boxes, self.read_at = th, self.ocr(img), time.monotonic()
        return self.boxes

    def shot(self):
        img = self.d.screenshot()
        return img, self.read(img)

    def moving(self, img):
        th = self._thumb(img)
        prev, self.prev = self.prev, th
        return prev is not None and float(abs(th - prev).mean()) > MOVING

    def band(self, y1, y2):
        """只识别 y1–y2 这一条（详情栏卡名约 0.1s，整帧 0.5–1s），坐标换回整帧。"""
        img = self.d.screenshot()
        return img, [(t, sc, (b[0], b[1] + y1, b[2], b[3] + y1)) for t, sc, b in self.ocr(img[y1:y2])]

    def wait(self, check, timeout=STEP_TIMEOUT, *, corner=True):
        """等 check 成立；没成立时每轮点一下左上角 (12,12)：切页 / 提示动画会快很多，这几页上它不碰任何按钮。"""
        deadline = time.monotonic() + timeout
        img, boxes = self.shot()
        while not check(boxes) and time.monotonic() < deadline:
            if corner:
                self.d.click(*CORNER)
            time.sleep(TICK)
            img = self.d.screenshot()
            # 切页 / 提示动画中途（画面还在变）不读，最多 MOVING_SKIP_S 强制读一次
            if self.moving(img) and time.monotonic() - self.read_at < MOVING_SKIP_S and time.monotonic() < deadline:
                continue
            boxes = self.read(img)
        return check(boxes), img, boxes

    def has(self, boxes, *words):
        return self.find(boxes, words) is not None


def _on_grid(o, b):
    return o.has(b, "カスタマイズするスキルカード", "カスタマイズ可能スキルカード") and not o.has(b, "一覧に戻る")


def _on_menu(o, b):
    return o.has(b, "カスタマイズメニュー") and o.has(b, "一覧に戻る")


def _back_to_interval(o, device, tries: int = 4) -> bool:
    """菜单页 →「もどる」→ 选卡页 →「もどる」→ 轮间主画面；每步看画面再点，没变就补点。回到轮间返回 True。"""
    for _ in range(tries):
        _, boxes = o.shot()
        if _on_interval(o, boxes):
            return True
        if _toast(o, boxes):
            device.click(*TOAST_TAP)
        elif _on_menu(o, boxes):
            device.click(*MENU_BACK)
        elif _on_grid(o, boxes):
            # 选卡页回轮间的按钮：交接提示词记「終了」(643,1052)，代码原来用 (215,1160)，按文字找，找不到才用旧坐标
            device.click(*(o.find(boxes, ("終了", "もどる", "戻る"), region=(0, 1000, 720, 1230)) or GRID_BACK))
        o.wait(lambda b: _on_interval(o, b) or _on_grid(o, b) and not _on_menu(o, b), timeout=2.5, corner=False)
    return _on_interval(o, o.shot()[1])


def _on_interval(o, b):
    # 做过一项后轮间主画面读不到「カスタマイズ可能枚数」，只有「Pポイントで利用するものを選んでください」（实机 OCR）
    return o.has(b, "インターバル") and (o.has(b, "カスタマイズ可能枚数") or o.has(b, "Pポイントで利用するもの"))


def _toast(o, b):
    return o.find(b, ("をカスタマイズ", "カスタマイズしまし"), region=(0, 900, 720, 1150)) is not None


def _detail_name(boxes, y_band) -> str | None:
    texts = [norm(t) for t, _, b in boxes if b[0] >= 170 and y_band[0] <= (b[1] + b[3]) // 2 <= y_band[1]]
    return texts[0] if texts else None


def customize(device, card: str, item: str, *, need_maxed: str | None = None) -> dict:
    from hiro.vision.deck import read_card_grid

    o = _Ops(device)
    img, boxes = o.shot()
    if _toast(o, boxes):
        # 上一项刚做完、提示还没关（实机第二次调用报「不在轮间主画面」）：关掉提示再看
        device.click(*TOAST_TAP)
        o.wait(lambda b: _on_interval(o, b) or _on_grid(o, b))
        img, boxes = o.shot()
    if _on_menu(o, boxes):
        # 上一项做完后游戏停在菜单页（实机）：回选卡页再找
        device.click(*MENU_BACK)
        o.wait(lambda b: _on_grid(o, b))
        img, boxes = o.shot()
    fresh = True
    if _on_interval(o, boxes):
        if o.has(boxes, "カスタマイズ可能枚数あと0枚"):
            return {"ok": False, "why": "轮间客制化次数已用完（あと0枚）"}
        device.click(*(o.find(boxes, "カスタマイズ", region=(330, 900, 600, 970), exact=True) or INTERVAL_BTN))
        ok, img, boxes = o.wait(lambda b: _on_grid(o, b))
        if not ok:
            return {"ok": False, "why": "点「スキルカードカスタマイズ」后没进选卡页"}
    elif _on_grid(o, boxes):
        fresh = False
    else:
        return {"ok": False, "why": "不在轮间主画面或客制化选卡页"}
    if not fresh:
        # 选卡页可能停在上次滑过的位置（实机：从中间开始只往下滑，巻き返し+ 在第一屏，三屏都没找到）：先滑回顶部
        _scroll_top(device)
        img, boxes = o.shot()

    want_base, _ = _base(card)
    tried = []
    for screen in range(SCREENS):
        if screen:
            _swipe_down(device)
            img, boxes = o.shot()
        result = _try_screen(o, device, img, card, item, need_maxed, want_base, tried, read_card_grid)
        if result is not None:
            return result
    device.click(*GRID_BACK)
    return {"ok": False, "why": f"选卡页没找到「{card}」" + (f"（已做过「{need_maxed}」的那张）" if need_maxed else "") + "，已退回",
            "tried": tried}  # fmt: skip


SCREENS = 3


def _swipe_down(device):
    from hiro.loop.kaa_patches import CARD_OP_SCROLL_SETTLE, CARD_OP_SWIPE_DURATION, _dialog_swipe

    _dialog_swipe(GRID, True, lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=CARD_OP_SWIPE_DURATION))
    time.sleep(CARD_OP_SCROLL_SETTLE)
    _wait_grid_still(device)


def _scroll_top(device):
    from hiro.loop.kaa_patches import CARD_OP_SCROLL_SETTLE, CARD_OP_SWIPE_DURATION, _dialog_swipe

    for _ in range(SCREENS):
        _dialog_swipe(GRID, False, lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=CARD_OP_SWIPE_DURATION))
    time.sleep(CARD_OP_SCROLL_SETTLE)
    _wait_grid_still(device)  # 惯性停稳前读到的格子坐标会偏（使用者 2026-11-07：第一行点击明显偏移）


GRID_STILL_POINTS = ((140, 626), (434, 773), (580, 920))  # 三行格子里的点：列表还在滚动时这几块一定在变


def _wait_grid_still(device):
    from hiro.vision.stable import wait_stable

    wait_stable(device.screenshot, GRID_STILL_POINTS, frames=2, threshold=1.5, timeout=2.5, poll=0.1)


def _read_detail_name(o, prev=None, timeout=1.2):
    """点格子后读上方详情栏的卡名（只识别这一条）：等到读出和上一张不同的名字，最多 timeout 秒。"""
    deadline = time.monotonic() + timeout
    name = None
    while True:
        time.sleep(DETAIL_POLL)
        _, boxes = o.band(*DETAIL_BAND)
        name = _detail_name(boxes, (90, 140))
        if (name and name != prev) or time.monotonic() >= deadline:
            return name


def _all_maxed(boxes) -> str | None:
    """选卡页选中一张卡后，上方「カスタマイズメニュー」下列出各项；每项都标「カスタマイズ上限」= 这张卡做满了（实机 エンターテイナー+）。
    返回各项名（「元気追加 / パラメータ+」），没做满或读不到返回 None。"""
    menu = next((b for t, _, b in boxes if "カスタマイズメニュー" in t), None)
    if menu is None:
        return None
    top = menu[3]
    names = [norm(t) for t, _, b in boxes if top < (b[1] + b[3]) // 2 <= top + 90 and not norm(t).isdigit() and norm(t) not in ("P",)]
    maxed = sum("カスタマイズ上限" in t for t, _, b in boxes if top < (b[1] + b[3]) // 2 <= top + 160)
    names = [n for n in names if "上限" not in n]
    return " / ".join(names) if names and maxed >= len(names) else None


def _try_screen(o, device, img, card, item, need_maxed, want_base, tried, read_card_grid):
    """在当前这一屏找目标卡并做客制化：做完 / 出错返回结果，这一屏没有返回 None（继续往下滑）。"""
    cells = read_card_grid(img, GRID)
    known = [c for c in cells if c.get("name") and _base(c["name"])[0] == want_base]
    unknown = [c for c in cells if not c.get("name")]
    # 先读详情栏现在显示的卡名：点格子后名字没变 = 这一下没被接收（实机：刚滑回顶部时前几下全丢，读到的都是上一张），补点一次
    _, now = o.band(*DETAIL_BAND)
    prev = _detail_name(now, (90, 140))
    for cell in known + unknown:
        device.click(*cell["center"])
        name = _read_detail_name(o, prev)
        if name == prev:
            time.sleep(0.3)
            device.click(*cell["center"])
            name = _read_detail_name(o, prev)
        prev = name
        tried.append([cell["row"], cell["col"], name])
        if not same_card(name, card):
            continue
        img, boxes = o.shot()
        if (full := _all_maxed(boxes)) is not None:
            tried[-1].append(f"已做满（{full}）")
            if need_maxed:
                continue  # 同名的另一张可能还能做
            return {"ok": False, "why": f"「{card}」的客制化项都已到上限（{full}）", "tried": tried}
        device.click(*GRID_CUSTOMIZE)
        ok, img, boxes = o.wait(lambda b: _on_menu(o, b))
        if not ok:
            return {"ok": False, "why": "点「カスタマイズ」后没进菜单页", "tried": tried}
        # 切页过渡时选卡页和菜单页叠在一起，卡名会读早（实机 頂点へ+）：在菜单页上重读卡名，最多 1.5s
        menu_deadline = time.monotonic() + 1.5
        while not same_card(_detail_name(boxes, (140, 200)), card) and time.monotonic() < menu_deadline:
            time.sleep(0.2)
            img, boxes = o.shot()
        if not same_card(_detail_name(boxes, (140, 200)), card):
            device.click(*MENU_BACK)
            return {"ok": False, "why": f"菜单页的卡不是「{card}」，已回到一览", "tried": tried}
        items = menu_items(boxes)
        if need_maxed and not any(need_maxed in (it["name"] or "") and it["maxed"] for it in items):
            device.click(*MENU_BACK)  # 同名的另一张（还没做过 need_maxed 那项）：回一览试下一张
            o.wait(lambda b: _on_grid(o, b))
            tried[-1].append(f"没做过「{need_maxed}」")
            continue
        target = next((it for it in items if item_match(it["name"], item)), None)
        if target is None or target["maxed"] or target["left"] == 0:
            device.click(*MENU_BACK)
            return {"ok": False, "why": f"菜单里没有可做的「{item}」", "menu": items, "tried": tried}
        device.click(*target["center"])
        time.sleep(0.5)
        device.click(*MENU_EXEC)
        # 实机（第一局本战轮间，两项）：点「実行する」后直接出「…をカスタマイズしました」，没有「実行確認」框。
        # 以前死等确认框 4s 后报失败（其实已成功），代理每次都要手动核对。现在：确认框出现才点，看到提示就算成功。
        from hiro.vision.pages import dialog_header

        deadline, seen_toast, confirmed = time.monotonic() + DONE_TIMEOUT, False, False
        while time.monotonic() < deadline:
            time.sleep(TICK)
            img = device.screenshot()
            if not seen_toast and dialog_header(img) is None:
                # 还没看到提示、也没有弹窗（像素）：只读提示那一条带（约 0.07s，整帧约 0.27s）
                toast = [(t, sc, (b[0], b[1] + TOAST_BAND[0], b[2], b[3] + TOAST_BAND[0]))
                         for t, sc, b in o.ocr(img[TOAST_BAND[0]:TOAST_BAND[1]])]
                if _toast(o, toast):
                    seen_toast = True
                    device.click(*TOAST_TAP)
                continue
            boxes = o.read(img)
            if not confirmed and o.has(boxes, "実行確認"):
                device.click(*(o.find(boxes, ("実行する",), region=(360, 1100, 720, 1220)) or CONFIRM_TAP))
                confirmed = True
                continue
            if _toast(o, boxes):
                seen_toast = True
                device.click(*TOAST_TAP)
                continue
            if seen_toast and (_on_interval(o, boxes) or _on_menu(o, boxes) or _on_grid(o, boxes)):
                # 做完一律退回轮间主画面（文档写的就是「回到轮间」）。以前只从菜单退回选卡页、也不核对：
                # 实机 codex-20261107-1 第 1 局第二项做完一直停在菜单页，代理手点「もどる」才回轮间
                back = _back_to_interval(o, device)
                return {"ok": True, "card": card, "item": target["name"], "toast": True, "confirm_box": confirmed,
                        "page": "interval" if back else "customize",
                        **({} if back else {"why": "客制化已生效，但没能退回轮间主画面，手动点「もどる」"})}  # fmt: skip
        if seen_toast:
            return {"ok": True, "card": card, "item": target["name"], "toast": True, "confirm_box": confirmed,
                    "page": "unknown", "note": "提示出现过（已成功），但没认出之后的页面"}  # fmt: skip
        return {"ok": False, "why": "确认后没回到轮间 / 客制化页", "toast": seen_toast}
    return None
