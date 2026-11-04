# 来源：digital-hiro hiro/produce/rental.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""开局第 2 步租借格（用户 2026-11-02：不用おまかせ，租借格由我们挑）。

过渡规则（支援卡选择系统 choose_rental 接入前，用户指定）：在租借列表里挑「Da、等级最高」的一张。
流程：点「レンタルのみ」空格 → 读列表（读两屏：顶部 + 往下滑一次）→ 挑 → 点该行 → 「決定」→ 等第 2 步「次へ」亮。
只点租借列表里的行和「決定」；读不到合格行就点「キャンセル」返回 False（交 RentalPending 停下）。
"""

from __future__ import annotations

import time

from kotonebot import device, logging

logger = logging.getLogger(__name__)

RENTAL_SLOT = (588, 906)
WANT_ATTR = "Da"


def _rows():
    from hiro.vision.support_screens import read_rental_list

    out = read_rental_list(device.screenshot())
    return out or {"rows": [], "controls": {}}


def best_row(rows: list[dict], attr: str = WANT_ATTR, exclude: set | None = None) -> dict | None:
    """主属性、等级最高（同级突破多的优先）；没有主属性的就返回 None（不乱租别的属性）。exclude：已知プラン不一致的卡名。"""
    exclude = exclude or set()
    cand = [r for r in rows if not r.get("covered") and (r.get("card") or {}).get("attr") == attr and (r.get("card") or {}).get("level")
            and (r.get("card") or {}).get("name") not in exclude]  # fmt: skip
    if not cand:
        return None
    return max(cand, key=lambda r: ((r["card"].get("level") or 0), (r["card"].get("breaks") or 0)))


SWIPE = (360, 1000, 360, 640, 0.8)  # 约一行半
MAX_SCREENS = 10  # 每页最多滑几次
TOP_LEVEL = 60


def _to_top():
    for _ in range(MAX_SCREENS):
        device.swipe(360, 640, 360, 1000, 0.5)
    time.sleep(1.0)


def _goto_page(page: int, pager: dict):
    for _ in range(3):
        cur = (_rows().get("pager") or {}).get("page")
        if cur == page or cur is None:
            return
        device.click(*(pager["next"] if cur < page else pager["prev"]))
        time.sleep(1.5)


def _tap_and_decide(row: dict, ctrl: dict) -> bool:
    c = row["card"]
    logger.info("租借：选 %s（%s Lv%s 突破%s，玩家 %s）", c.get("name"), c.get("attr"), c.get("level"), c.get("breaks"), row.get("player"))
    device.click(*row["center"])
    time.sleep(1.0)
    device.click(*(ctrl.get("decide") or (497, 1157)))
    time.sleep(2.5)
    return True


def _ensure_plan_fit():
    """勾上租借列表的「プラン適正」（KAA 原流程也是这么做的），只显示本方案能用的卡。"""
    try:
        from kaa.tasks import R

        device.screenshot()
        if chk := R.Produce.Step2.Rent.CheckboxPlan.q(checked=False).find():
            chk.set_checked(True)
            logger.info("租借：勾上「プラン適正」")
            time.sleep(1.5)
    except Exception as e:  # noqa: BLE001
        logger.warning("租借：勾「プラン適正」失败（%s），继续，靠决定后的不一致检查兜底", e)


def rental_mismatch() -> bool:
    """第 2 步页面：租借格（右下 tile）上有没有「プラン不一致」。"""
    from hiro.produce.ops import ocr_boxes

    return any("不一致" in t and (b[0] + b[2]) // 2 > 480 and 600 <= (b[1] + b[3]) // 2 <= 900 for t, _, b in ocr_boxes(device.screenshot()))


def pick_rental(attr: str = WANT_ATTR, tries: int = 3) -> bool:
    """挑一张；决定后租借格标「プラン不一致」就记下卡名、重开列表换下一张（第十七局：勾选没生效，租到了别的方案的卡）。"""
    exclude: set = set()
    for _ in range(tries):
        name = _pick_once(attr, exclude)
        if name is None:
            return False
        if not rental_mismatch():
            return True
        logger.warning("租借：「%s」是プラン不一致，换一张", name)
        exclude.add(name)
    return False


def progress(why: str) -> None:
    from hiro.loop.repeat_guard import progress as _p

    _p(why)


def _pick_once(attr: str, exclude: set) -> str | None:
    """两页都从顶滑到底：遇到满级（Lv60）主属性卡立刻选；否则记下最好的一张（页、滑动次数、玩家名），回去点它。返回选中的卡名。"""
    device.click(*RENTAL_SLOT)
    time.sleep(2.5)
    _ensure_plan_fit()
    first = _rows()
    ctrl, pager = first.get("controls") or {}, first.get("pager") or {}
    pages = pager.get("pages") or 1
    best = None  # (level, breaks, page, screen, player)
    for page in range(1, pages + 1):
        if page > 1:
            _goto_page(page, pager)
            _to_top()
        last_players = None
        for screen in range(MAX_SCREENS):
            progress(f"租借：第 {page} 页第 {screen + 1} 屏")  # 两页翻完约 55s，每屏报一次进展（D38）
            lst = _rows()
            rows = lst["rows"]
            players = [r.get("player") for r in rows]
            if players == last_players:
                break  # 到底了
            last_players = players
            row = best_row(rows, attr, exclude)
            if row is not None:
                c = row["card"]
                if (c.get("level") or 0) >= TOP_LEVEL:
                    _tap_and_decide(row, ctrl)
                    return c.get("name")
                key = (c.get("level") or 0, c.get("breaks") or 0)
                if best is None or key > best[:2]:
                    best = (*key, page, screen, row.get("player"))
            device.swipe(*SWIPE)
            time.sleep(1.2)
    if best is None:
        logger.warning("租借：两页都没读到 %s 卡，取消", attr)
        device.click(*(ctrl.get("cancel") or (220, 1157)))
        return None
    _, _, page, screen, player = best
    _goto_page(page, pager)
    _to_top()
    for _ in range(screen):
        device.swipe(*SWIPE)
        time.sleep(1.2)
    lst = _rows()
    row = next((r for r in lst["rows"] if r.get("player") == player and r.get("card")), None) or best_row(lst["rows"], attr, exclude)
    if row is None:
        logger.warning("租借：回到最佳行时没找到（玩家 %s），取消", player)
        device.click(*(ctrl.get("cancel") or (220, 1157)))
        return None
    _tap_and_decide(row, ctrl)
    return row["card"].get("name")
