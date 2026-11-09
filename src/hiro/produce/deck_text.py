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
MAX_SCREENS = 32  # 21 张卡约 7000px，8 屏只滚到一半（1-h）；考试中一览带 5 个分组标题，22 张 20 屏还没到底（codex-20261109-1-m R2 T4 读到 21/22）。到底就提前停
SAME_DIFF = 2.0
GROUPS = ("保留", "手札", "山札", "捨札", "除外")
_TOTAL = re.compile(r"スキルカード[(（](\d+)[)）]")
# 分组标题前面有折叠图标，OCR 成「+」「▼」等一个符号（codex-20261109-1-e 实机：「+手札（3）」「+山札(19)」），原来一个都没认出
_HEADER = re.compile(r"^[^ぁ-んァ-ン一-龯\w]?(保留|手札|山札|捨札|除外)[(（]?(\d+)(?:/\d+)?[)）]?$")
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


def is_list_view(boxes) -> bool:
    """一览是列表显示：卡图下方有「強化確認」「カスタマイズ確認」按钮（网格显示没有）。"""
    return any(norm(t) in ("強化確認", "カスタマイズ確認") for t, _, _ in boxes)


def merge_rows(screens: list[list]) -> list:
    """多屏行序列拼接：后一屏开头和前面结尾重叠的那段只算一次（同名牌可能有几张，所以按序列对齐，不按集合去重）。
    只比卡名：每屏开头在本屏第一个分组标题之前的行组是 None（标题滚出去了）。对齐容许一张漏读（某一屏 OCR 漏了一行）：
    codex-20261109-1-h 实机一屏漏读一行，重叠对不上，整屏又接了一遍，读到 23 / 22。组为 None 的行最后沿用上一行的组。"""
    out: list = []
    for rows in screens:
        merged = None
        for j in range(max(0, len(out) - MERGE_WINDOW), len(out)):  # 从早往晚试：重叠越长越优先
            merged = _align(out, rows, j)
            if merged is not None:
                break
        out = merged if merged is not None else out + list(rows)
    group, filled = None, []
    for g, n in out:
        group = g if g is not None else group
        filled.append((group, n))
    return filled


MERGE_WINDOW = 8  # 一屏最多 4–5 行，重叠只会出现在已拼结果的最后这几行里


def _align(out: list, rows: list, j: int) -> list | None:
    """rows 的开头对齐到 out[j:]，一直对到 out 结尾就返回拼好的新序列；容许一处漏读（out 或 rows 少一行），对不上返回 None。"""
    p, i, skips, res = j, 0, 0, list(out)
    while p < len(res) and i < len(rows):
        name = rows[i][1]
        if res[p][1] == name:
            p, i = p + 1, i + 1
        elif skips == 0 and i + 1 < len(rows) and rows[i + 1][1] == res[p][1]:
            res.insert(p, rows[i])  # 之前那屏漏读了这一行
            p, i, skips = p + 1, i + 1, 1
        elif skips == 0 and (p + 1 < len(res) and res[p + 1][1] == name or i > 0 and p + 1 == len(res)):
            p, skips = p + 1, 1  # 这一屏漏读了 res[p]（已对上至少一行后，out 最后一行没出现在这屏也只能是漏读）
        else:
            return None
    if p < len(res) or i == 0:
        return None
    return res + list(rows[i:])


def read_list(*, shoot: Callable, click: Callable, swipe: Callable, ocr_boxes: Callable, view_diff: Callable,
              sleep: Callable = time.sleep, save: Callable | None = None, names=None) -> dict:  # fmt: skip
    """一览已经打开：切到列表显示 → 每屏 OCR、往下滑直到滑不动 → 切回网格。
    返回 {cards: [卡名], groups: {组: [卡名]}, headers, complete, problems, screens}。complete：有分组标题时每组张数对上。"""
    out = {"cards": None, "groups": {}, "headers": {}, "complete": False, "problems": [], "screens": 0}
    before = shoot()
    # 游戏记住上次的显示方式：一览打开时已经是列表显示就不切（2026-11-08 使用者手动切成列表后，再点切换会切回网格），读完也不切
    toggled = not (before is not None and is_list_view(ocr_boxes(before)))
    if toggled:
        click(*TOGGLE)
        sleep(0.8)
        img = shoot()
        if before is not None and img is not None and view_diff(before, img) < SAME_DIFF:
            out["problems"].append("点了列表切换按钮，一览没变")
            return out
    else:
        img = before
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
    if toggled:
        click(*TOGGLE)  # 切回网格，不改游戏里的显示设置
        sleep(0.6)
    out["imgs"] = imgs
    return out
