"""牌组一览「列表显示」里读客制化（使用者 2026-11-08）。

列表显示每张卡一块：左边卡图，右边卡名 + 效果文字。客制化过的卡有两个标记：
- 卡图下方中间一个绿底白字的圆角标，数字 = 这张卡做过几项客制化；
- 效果文字里客制化改动的部分是绿字（加的效果整行绿：インフルエンサー+「強気に変更」；改数值只有数字绿：
  エンターテイナー+「パラメータ+24」的 24、仕切り直し+「消費体力減少6ターン」的 6）。
「レッスン中1回の制限削除」不出绿字，表现为这张卡本来是一次性、块里却没有「レッスン中1回」。

读法：按块找角标数字和绿字行，再对主数据 ProduceCardCustomize 里这张卡的客制化项（effectType）逐项认：
- EffectAdd：绿字行包含这一项（这一级）追加的效果文字 → 菜单名（description，如「強気追加」「成長追加」/「成長+」）；
- PlayMovePositionTypeChange：主数据一次性、块里没有「レッスン中1回」→ 菜单名（「レッスン中1回の制限削除」）；
- LessonAdd / CostReduce / StaminaConsumptionDownTurnAdd / CostFullPowerPointReduce：绿字行里有对应的关键词（パラメータ /
  体力消費 / 消費体力減少 / 全力値消費）→ 记成「パラメータ+12」这类可读名字（主数据没有菜单名；名字里不带「強気」，
  veil_exam.keyword_texts 不会给它补牌面文字）。
认出的项数要等于角标数字才算这张卡读准（角标读不出时，认出 ≥1 项且没有对不上的绿字行也算）。读不准的卡不记客制化，列进 problems。
"""

from __future__ import annotations

import json
import re
import unicodedata

import cv2
import numpy as np

GREEN_H = (58, 72)  # 客制化绿字的色相（OpenCV 0–180）；心形 / 元気等图标是渐变色，各色相都有
GREEN_MIN_PX = 40  # 一行里至少这么多绿字像素
GREEN_COMPONENT = 0.85  # 一个连通块里绿色像素的比例下限：字的笔画整块是绿的，渐变图标不是
SAT_MIN, VAL_MIN = 90, 80
CARD_X = (37, 180)  # 卡图左右（720 宽逻辑画面）
BADGE_DX, BADGE_DY, BADGE_R = 107, 107, 20  # 角标中心：x 绝对、y 相对卡名框上缘；半径
BADGE_GREEN = 0.25  # 角标圆里绿色像素比例下限

NUMERIC = {  # effectType → (绿字行关键词, 记账名模板)
    "ProduceCardGrowEffectType_LessonAdd": (("パラメータ",), "パラメータ+{v}"),
    "ProduceCardGrowEffectType_CostReduce": (("体力消費", "コスト"), "コスト減少-{v}"),
    "ProduceCardGrowEffectType_StaminaConsumptionDownTurnAdd": (("消費体力減少",), "消費体力減少ターン+{v}"),
    "ProduceCardGrowEffectType_CostFullPowerPointReduce": (("全力値消費",), "全力値消費-{v}"),
}
LIMIT = "ProduceCardGrowEffectType_PlayMovePositionTypeChange"
EFFECT_ADD = "ProduceCardGrowEffectType_EffectAdd"


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split()).replace("\\n", "")


def green_line(hsv, box) -> bool:
    """这一行有客制化绿字：彩色像素按 8 连通分块，整块（≥ GREEN_COMPONENT）都是绿色的块才算字的笔画；
    渐变色图标（心形コスト：黄→绿）整块剔除。2026-11-08 实机 memory-save --scan：巻き返し+「♥コスト値減少」
    的 OCR 框把心形框进去，按整行比例算误判成绿字（存成 jpg 后框的范围变了，离线没复现）。"""
    x1, y1, x2, y2 = box
    h = hsv[y1:y2, x1:x2]
    if h.size == 0:
        return False
    sat = (h[:, :, 1] >= SAT_MIN) & (h[:, :, 2] >= VAL_MIN)
    green = sat & (h[:, :, 0] >= GREEN_H[0]) & (h[:, :, 0] <= GREEN_H[1])
    n, labels = cv2.connectedComponents(sat.astype(np.uint8), connectivity=8)
    if n <= 1:
        return False
    total = np.bincount(labels.ravel(), minlength=n)
    greens = np.bincount(labels.ravel(), weights=green.ravel(), minlength=n)
    keep = sum(int(greens[k]) for k in range(1, n) if total[k] and greens[k] / total[k] >= GREEN_COMPONENT)
    return keep >= GREEN_MIN_PX


def badge(img, name_top: int, ocr_boxes=None) -> int | None:
    """卡名框上缘 → 角标数字。没有绿圆 → 0；有绿圆但数字读不出 → None。"""
    cx, cy, r = BADGE_DX, name_top + BADGE_DY, BADGE_R
    if cy + r > img.shape[0] or cy - r < 0:
        return None
    crop = img[cy - r : cy + r, cx - r : cx + r]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    g = (hsv[:, :, 0] >= GREEN_H[0]) & (hsv[:, :, 0] <= GREEN_H[1] + 6) & (hsv[:, :, 1] >= SAT_MIN) & (hsv[:, :, 2] >= VAL_MIN)
    if g.mean() < BADGE_GREEN:
        return 0
    if ocr_boxes is None:
        return None
    # 小图直接 OCR 读不出：放大 3 倍、四周补白边（2026-11-08 エンターテイナー+ 的「1」原样读不出，补边后读出）；
    # 再不行用白字二值化
    white = cv2.cvtColor(255 - (((hsv[:, :, 1] < 60) & (hsv[:, :, 2] > 200)).astype(np.uint8) * 255), cv2.COLOR_GRAY2BGR)
    for im in (crop, white):
        big = cv2.resize(im, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        big = cv2.copyMakeBorder(big, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        digits = [norm(t) for t, _, _ in ocr_boxes(big) if norm(t).isdigit()]
        if digits:
            return int(digits[0])
    return None


def master_items(card_name: str) -> list[dict]:
    """主数据：这张卡的客制化项，每项一组按级的 {level, type, desc, value, texts}。"""
    from hiro.brain.cards import description_text
    from hiro.state.catalog import catalog

    db = catalog().db
    row = db.execute("SELECT produceCardCustomizeIds FROM ProduceCard WHERE name = ? LIMIT 1", (card_name,)).fetchone()
    out = []
    for cid in json.loads((row[0] if row else None) or "[]"):
        levels = []
        for count, desc, grow_ids in db.execute("SELECT customizeCount, description, produceCardGrowEffectIds FROM ProduceCardCustomize "
                                                "WHERE id = ? ORDER BY customizeCount", (cid,)).fetchall():  # fmt: skip
            kinds, value, texts = [], None, []
            for gid in json.loads(grow_ids or "[]"):
                g = db.execute("SELECT effectType, value, playProduceExamEffectId, targetPlayProduceExamEffectIds "
                               "FROM ProduceCardGrowEffect WHERE id = ?", (gid,)).fetchone()  # fmt: skip
                if g is None:
                    continue
                kinds.append(g[0])
                value = g[1] if g[1] else value
                if g[0] == EFFECT_ADD:
                    for eid in [g[2], *json.loads(g[3] or "[]")]:
                        e = db.execute("SELECT produceDescriptions FROM ProduceExamEffect WHERE id = ?", (eid,)).fetchone() if eid else None
                        if e is not None and (t := norm(description_text(e[0]))):
                            texts.append(t)
            levels.append({"level": count, "type": kinds[0] if kinds else None, "desc": norm(desc), "value": value, "texts": texts})
        out.append(levels)
    return out


def is_once_name(card_name: str) -> bool | None:
    from hiro.state.catalog import catalog

    row = catalog().db.execute("SELECT playMovePositionType FROM ProduceCard WHERE name = ? LIMIT 1", (card_name,)).fetchone()
    return None if row is None else "Lost" in str(row[0])


def identify(card_name: str, green: list[str], has_once_mark: bool, count: int | None) -> dict:
    """一块卡的读数 → {items: [记账名], ok, why}。green：绿字行文字。"""
    items, used = [], set()
    for levels in master_items(card_name):
        kind = levels[0]["type"]
        if kind == LIMIT:
            if is_once_name(card_name) and not has_once_mark:
                items.append(levels[0]["desc"] or "レッスン中1回の制限削除")
            continue
        if kind == EFFECT_ADD:
            # 最高的一级先比（成長+ 第 3 级的文字包含第 1 级的数字前缀时，先认高的）
            hit = next(((lv, i) for lv in reversed(levels) for i, g in enumerate(green)
                        if i not in used and any(t and (t in g or g in t) for t in lv["texts"])), None)
            if hit is not None:
                lv, i = hit
                used.add(i)
                items.append(lv["desc"] if lv["level"] == 1 else f"{levels[0]['desc']}→{lv['level']}级")
            continue
        if kind in NUMERIC:
            words, label = NUMERIC[kind]
            i = next((i for i, g in enumerate(green) if i not in used and any(w in g for w in words)), None)
            if i is not None:
                used.add(i)
                total = sum(lv["value"] or 0 for lv in levels[:1])
                items.append(label.format(v=total))
    leftover = [g for i, g in enumerate(green) if i not in used]
    if count is not None:
        ok = len(items) == count and not leftover
        why = None if ok else f"角标 {count} 项，认出 {items}，没对上的绿字 {leftover}"
    else:
        ok = bool(items) and not leftover
        why = None if ok else f"角标读不出，认出 {items}，没对上的绿字 {leftover}"
    return {"items": items, "ok": ok, "why": why}


def read_screen(img, boxes, names=None, ocr_boxes=None, *, last: bool = False, heads_out: list | None = None) -> list[dict]:
    """一屏列表显示 → 每个完整的卡块 {name, top, badge, green, once_mark}。
    只取完整的块：下面紧接着下一张卡的卡名（块下沿在屏内）；或 last=True（滑到底的最后一屏）的最后一块。
    屏底被截断的块不取：「レッスン中1回」那行落在屏外会被当成「制限削除」。"""
    from hiro.produce.deck_text import VIEW, match_name

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    x1, y1, x2, y2 = VIEW
    items = [(norm(t).replace("＋", "+"), b) for t, _, b in boxes if x1 <= (b[0] + b[2]) / 2 <= x2 and y1 <= (b[1] + b[3]) / 2 <= y2]
    heads = sorted(((b[1], match_name(t, names), b) for t, b in items if 185 <= b[0] <= 205), key=lambda h: h[0])
    heads = [h for h in heads if h[1]]
    out = []
    for k, (top, name, _) in enumerate(heads):
        if k + 1 < len(heads):
            bottom = heads[k + 1][0] - 20
        elif last:
            bottom = y2
        else:
            continue
        lines = [(t, b) for t, b in items if top + 30 < (b[1] + b[3]) / 2 < bottom and b[0] >= 185]
        green = [t for t, b in lines if green_line(hsv, b)]
        once_mark = any("レッスン中1回" in t for t, _ in lines)
        out.append({"name": name, "top": top, "idx": k, "badge": badge(img, top, ocr_boxes), "green": green, "once_mark": once_mark})
    if heads_out is not None:
        heads_out.extend(name for _, name, _ in heads)
    return out


def customized(b: dict) -> bool:
    """有角标、有绿字，或主数据一次性却没写「レッスン中1回」（制限削除）。"""
    return bool(b["badge"]) or bool(b["green"]) or (not b["once_mark"] and bool(is_once_name(b["name"])))


def _offsets(heads: list[list[str]]) -> list[int]:
    """每屏卡名序列（含屏底截断那块的卡名）按重叠拼接，返回每屏第 0 个卡名在整副里的位置。
    用整屏卡名对齐，不只用完整块：同名两张分在相邻两屏时（ハッピー♪ ×2），只看完整块会把两张对成一张（2026-11-08 实机）。"""
    out: list[str] = []
    starts = []
    for names in heads:
        best = 0
        for k in range(min(len(out), len(names)), 0, -1):
            if out[-k:] == names[:k]:
                best = k
                break
        starts.append(len(out) - best)
        out.extend(names[best:])
    return starts


def read_customs(imgs, ocr_boxes, names=None) -> dict:
    """列表显示多屏截图（从上往下、最后一屏已滑到底）→ {blocks, custom: [{card, item[, copy]}], problems}。
    各屏的完整块按卡名拼成整副（列表顺序 = 牌组顺序），每张单独认客制化。同名几张客制化不同时照记
    （使用者 2026-11-08：「不报问题，就记成两种」）：custom 记录带 copy（同名里的第几张，0 起，按列表顺序）；
    只有一张的卡不带 copy。考试里分不清手里是哪一张时用点按读效果文字（HIRO_VEIL_SIGNATURE）。
    认不准的卡（角标项数和绿字对不上）不记客制化，列进 problems。"""
    screens, heads = [], []
    for i, img in enumerate(imgs):
        names_here: list[str] = []
        blocks = read_screen(img, ocr_boxes(img), names, ocr_boxes, last=i == len(imgs) - 1, heads_out=names_here)
        for b in blocks:
            if customized(b):
                b["identified"] = identify(b["name"], b["green"], b["once_mark"], b["badge"])
                b["got"] = tuple(b["identified"]["items"]) if b["identified"]["ok"] else None
            else:
                b["got"] = ()
        screens.append(blocks)
        heads.append(names_here)
    by_pos: dict[int, dict] = {}
    for start, blocks in zip(_offsets(heads), screens):
        for b in blocks:
            pos = start + b["idx"]
            old = by_pos.get(pos)
            if old is None or (old["got"] is None and b["got"] is not None):
                by_pos[pos] = b
    merged = [by_pos[k] for k in sorted(by_pos)]
    counts: dict[str, int] = {}
    for b in merged:
        counts[b["name"]] = counts.get(b["name"], 0) + 1
    seen: dict[str, int] = {}
    custom, problems = [], []
    for b in merged:
        copy = seen.get(b["name"], 0)
        seen[b["name"]] = copy + 1
        if b["got"] is None:
            problems.append(f"{b['name']}（第 {copy + 1} 张）：{b['identified']['why']}")
            continue
        for it in b["got"]:
            custom.append({"card": b["name"], "item": it, **({"copy": copy} if counts[b["name"]] > 1 else {})})
    return {"blocks": merged, "custom": custom, "problems": problems, "names": [b["name"] for b in merged]}
