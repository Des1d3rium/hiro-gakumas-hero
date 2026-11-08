"""牌组一览「列表显示」读法（使用者 2026-11-07）：一览默认是卡图网格（考试里还按 保留 / 手札 / 山札 / 捨札 / 除外 分组），
网格靠卡图认牌，滑屏拼接容易出错（1-e R2：滑过头、6 张认不出）。一览右下角「卡格 / 列表」切换按钮（固定像素 TOGGLE）
点一下变成列表显示，每行有卡名文字，直接 OCR 对账；读完再点回网格，不改变游戏里的显示设置。

列表显示的版面还没有实机帧核对（第一次实机跑时每屏存帧到 SAVE_DIR），所以读法只依赖两样东西：
- 分组标题「手札 (3)」这类文字（有就按组记，并用括号里的张数核对读全没有）；
- 卡名文字：和主数据 ProduceCard 的卡名比（名字带「+」），认不出的文字丢掉。
"""

from __future__ import annotations

import difflib
import re
import time
import unicodedata
from functools import lru_cache
from typing import Callable

TOGGLE = (662, 967)  # 一览右下「卡格 / 列表」切换（1-f 实机帧 reports/live/deck-list/20261107-162843-00.jpg，720x1280）
# 列表显示的卡区从标题栏下（y≈110）到底部标签栏上（y≈1000），每张卡一块、卡名在块左上。原来沿用网格一览的 y≥395，
# 每屏上面 1/3 的卡名读不到（1-h：首张 心・技・体 没读，相邻两屏没有共同卡名，拼接漏牌）
VIEW = (20, 110, 700, 1000)
SWIPE = (360, 950, 360, 650)  # 带惯性，实际滚动约 480px（1-h 帧 00→01），一屏 890px 还留一张卡的重叠
MAX_SCREENS = 20  # 21 张卡约 7000px，8 屏只滚到一半（1-h：第 8 屏停在 達成感+，滚动条在中间）
SAME_DIFF = 2.0
GROUPS = ("保留", "手札", "山札", "捨札", "除外")
_TOTAL = re.compile(r"スキルカード[(（](\d+)[)）]")
_HEADER = re.compile(r"^(保留|手札|山札|捨札|除外)[(（]?(\d+)(?:/\d+)?[)）]?$")
FUZZY = 0.8


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


@lru_cache(maxsize=1)
def card_names() -> frozenset[str]:
    try:
        from hiro.state.context import catalog

        return frozenset(norm(r[0]) for r in catalog().db.execute("SELECT DISTINCT name FROM ProduceCard").fetchall() if r[0])
    except Exception:  # noqa: BLE001  没有主数据：认不出任何卡名
        return frozenset()


def match_name(text: str, names=None) -> str | None:
    """OCR 文字 → 主数据卡名（带「+」）。先精确；再按去「+」的基名 + 文字末尾的「+」个数；最后模糊（OCR 错字）。"""
    names = card_names() if names is None else names
    t = norm(text).replace("＋", "+")
    if not t or len(t) < 2:
        return None
    if t in names:
        return t
    plus = len(t) - len(t.rstrip("+"))
    base = t.rstrip("+")
    if base + "+" * plus in names:
        return base + "+" * plus
    bases = sorted({n.rstrip("+") for n in names})
    hit = difflib.get_close_matches(base, bases, n=1, cutoff=FUZZY)
    if not hit:
        return None
    name = hit[0] + "+" * plus
    return name if name in names else (hit[0] if hit[0] in names else None)


def parse_screen(boxes, names=None) -> dict:
    """一屏 OCR（[(文字, 置信度, (x1, y1, x2, y2))]）→ {rows: [(组 or None, 卡名)], headers: {组: 张数}, unknown: [文字]}。
    按 y（行）再 x 排序；一行里有多个卡名都算（列表显示一行几张还没核实）。"""
    x1, y1, x2, y2 = VIEW
    inside = [(t, b) for t, _, b in boxes if x1 <= (b[0] + b[2]) / 2 <= x2 and y1 <= (b[1] + b[3]) / 2 <= y2]
    inside.sort(key=lambda tb: (round((tb[1][1] + tb[1][3]) / 2 / 20), tb[1][0]))
    rows, headers, unknown = [], {}, []
    group = None
    for t, _ in inside:
        n = norm(t)
        if h := _HEADER.match(n):
            group = h[1]
            headers[group] = int(h[2])
            continue
        name = match_name(n, names)
        if name is not None:
            rows.append((group, name))
        elif re.search(r"[ぁ-んァ-ン一-龯]", n):
            unknown.append(n)
    return {"rows": rows, "headers": headers, "unknown": unknown}


def merge_rows(screens: list[list]) -> list:
    """多屏行序列拼接：后一屏开头和前面结尾重叠最长的那段只算一次（同名牌可能有几张，所以按序列重叠，不按集合去重）。"""
    out: list = []
    for rows in screens:
        best = 0
        for k in range(min(len(out), len(rows)), 0, -1):
            if out[-k:] == rows[:k]:
                best = k
                break
        out.extend(rows[best:])
    return out


def read_list(*, shoot: Callable, click: Callable, swipe: Callable, ocr_boxes: Callable, view_diff: Callable,
              sleep: Callable = time.sleep, save: Callable | None = None, names=None) -> dict:  # fmt: skip
    """一览已经打开：切到列表显示 → 每屏 OCR、往下滑直到滑不动 → 切回网格。
    返回 {cards: [卡名], groups: {组: [卡名]}, headers, complete, problems, screens}。complete：有分组标题时每组张数对上。"""
    out = {"cards": None, "groups": {}, "headers": {}, "complete": False, "problems": [], "screens": 0}
    before = shoot()
    click(*TOGGLE)
    sleep(0.8)
    img = shoot()
    if before is not None and img is not None and view_diff(before, img) < SAME_DIFF:
        out["problems"].append("点了列表切换按钮，一览没变")
        return out
    imgs, screens, headers, unknown = [img], [], {}, []
    total = None
    for i in range(MAX_SCREENS):
        boxes = ocr_boxes(imgs[-1])
        if total is None:
            total = next((int(m[1]) for t, _, _ in boxes if (m := _TOTAL.search(norm(t)))), None)
        page = parse_screen(boxes, names)
        screens.append(page["rows"])
        headers.update(page["headers"])
        unknown.extend(page["unknown"])
        if i == MAX_SCREENS - 1:
            break
        swipe(*SWIPE, 0.6)
        sleep(0.7)
        nxt = shoot()
        if nxt is None or view_diff(imgs[-1], nxt) < SAME_DIFF:
            break
        imgs.append(nxt)
    if save is not None:
        save(imgs)
    out["screens"] = len(imgs)
    rows = merge_rows(screens)
    out["headers"] = headers
    out["cards"] = [n for _, n in rows]
    for g, n in rows:
        out["groups"].setdefault(g or "", []).append(n)
    if unknown:
        out["problems"].append(f"认不出的文字：{sorted(set(unknown))[:10]}")
    out["total"] = total
    bad = {g: (len(out["groups"].get(g, [])), k) for g, k in headers.items() if len(out["groups"].get(g, [])) != k}
    if bad:
        out["problems"].append(f"分组张数对不上（读到, 标题）：{bad}")
    if total is None:
        out["problems"].append("没读到「スキルカード(N)」总张数，没法核对读全没有")
    elif len(out["cards"]) != total:
        out["problems"].append(f"读到 {len(out['cards'])} 张，总张数 {total}")
    out["complete"] = total is not None and len(out["cards"]) == total and not bad
    click(*TOGGLE)  # 切回网格，不改游戏里的显示设置
    sleep(0.6)
    return out
