# 来源：digital-hiro hiro/produce/memory_picker.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""回忆（メモリー）选择页的点击路径与只读扫描：给总控在实机上跑，产出 reports/live/memory-inventory.json。

画面（编成页点某一格回忆打开的选择页，720x1280；依据 reports/live/memory-formation/cand-*.jpg、memscan2-*.jpg）：
- 顶部：所选回忆的属性 ボーカル / ダンス / ビジュアル / 体力（y≈70）与百分比（y≈103）。
- 详情栏两页（中间「1/2」(361,533)，左右箭头约 (230,530) / (485,530)）：
  1/2 = 獲得可能スキルカード：入手时机标签（「開始時に獲得」/「中間試験/1次オーディション後に獲得」，y≈198）、卡名（y≈240）、效果；
  2/2 = 能力（被动）列表。
- 回忆格：3 列（中心 x 140 / 360 / 580，宽约 210），行高约 118、行距约 137.5；行的上下沿按饱和度空隙找（滚动后位置会变）。
- 底栏 y≈1050：「編成詳細」(135)、筛选图标 (399) + 当前排序名「プロデュースク…」(495)、升降序图标 (653)；
  最底「キャンセル」(211,1162) /「決定」(475,1160)。
只读：选回忆不会改编成，只有「決定」才生效。扫描结束一律点「キャンセル」；筛选 / 排序对话框只用「キャンセル / 閉じる」关闭。
筛选 / 排序 / 关键词搜索的对话框还没有样本：explore() 负责去拍，拍完再补点击路径。
"""

import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from kotonebot import logging

from hiro.produce.ops import find_text, ocr_boxes, roi_diff
from hiro.produce import hif_buttons as BTN
from hiro import imgio

logger = logging.getLogger(__name__)

COL_X = (140, 360, 580)
CELL_W, CELL_H_MIN = 210, 100
GRID_X = (35, 687)
GRID_Y = (560, 1040)  # 回忆格区（上面是详情栏，下面是底栏），用来判断画面变没变
ROW_BOTTOM_MAX = 995  # 完整行的下沿上限：再往下被浮在列表上的「編成詳細 / 筛选 / 排序」按钮（y≈1000–1060）挡住
ROW_H = (108, 128)  # 完整行高（实测 117–120）
GAP_SAT_MAX = 40  # 行间空隙的平均饱和度
DETAIL_ROI = (40, 170, 640, 340)  # 详情栏 (x, y, w, h)：换选中回忆 / 换页时这里变
STATS_ROI = (100, 30, 600, 90)
PAGE_PREV, PAGE_NEXT = (230, 530), (485, 530)
CANCEL_TAP = BTN.DIALOG_CANCEL
FILTER_TAP, SORT_KEY_TAP, SORT_DIR_TAP = (399, 1050), (495, 1050), (653, 1050)
SWIPE_FROM, SWIPE_ROWS = 1000, 2  # 一次上滑 2 行（行距 137.5），可见 3 行 → 与上一页重叠 1 行，用来核对没有跳行
SWIPE_DURATION, SWIPE_HOLD = 1.0, 0.4  # 慢滑 + 终点停住再抬起：不带惯性（2026-11-01 实机：3 行快滑一次滚过 4 行以上，整行漏扫）
CELL_MATCH_MAX = 14.0  # 两张格子缩略图（48x24 灰度）平均差 ≤ 这个算同一格（行带上下差 1–2 像素也能对上）
MAX_BACKTRACK = 3
ROW_PITCH = 137.5
TIMING = {"開始時": "start", "中間試験": "mid", "オーディション後": "mid"}
CLOSE_TEXTS = ("キャンセル", "閉じる", "戻る")
ABILITY_GAP = 22  # 同一张能力卡片里相邻两行的间距 ≤ 这个；卡片之间更大

TAP_TIMEOUT = 3.0
SCROLL_SETTLE = 0.8


def picker_open(boxes) -> bool:
    """在回忆选择页：底部有「キャンセル」「決定」，底栏有「編成詳細」。"""
    # 「編成詳細」RapidOCR 常读歪（半分辨率帧「成詳綱」），认中间的「成詳」也算；キャンセル / 決定 仍须都在
    return all(find_text(boxes, t) is not None for t in ("キャンセル", "決定")) and find_text(boxes, ("編成詳細", "成詳")) is not None


def grid_rows(img: np.ndarray) -> list[tuple[int, int]]:
    """可见的完整回忆行 [(y1, y2)]：按横向饱和度找行间空隙，取高度够的段。"""
    sat = cv2.cvtColor(img[:, GRID_X[0] : GRID_X[1]], cv2.COLOR_BGR2HSV)[:, :, 1].mean(1)
    rows, start = [], None
    for y in range(GRID_Y[0], GRID_Y[1]):
        filled = sat[y] >= GAP_SAT_MAX
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            # 顶部被详情栏截断的半行、底部被浮动按钮挡住的行（2026-11-01 第二次扫描：最后一行被挡、对不上下一页）都不要
            if ROW_H[0] <= y - start <= ROW_H[1] and start > GRID_Y[0] and y <= ROW_BOTTOM_MAX:
                rows.append((start, y))
            start = None
    return rows


def grid_cells(img: np.ndarray) -> list[dict]:
    """可见的完整回忆格：[{row, col, center, rect, hash}]，从左到右、从上到下。hash 用于翻页去重。"""
    out = []
    for r, (y1, y2) in enumerate(grid_rows(img)):
        for c, cx in enumerate(COL_X):
            x1 = cx - CELL_W // 2
            crop = img[y1:y2, x1 : x1 + CELL_W]
            if float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1].mean()) < GAP_SAT_MAX:
                continue  # 最后一行不满 3 格
            # 去掉外沿 8 像素（选中时的橙色框）再算指纹，选中与否不影响
            small = cv2.resize(cv2.cvtColor(crop[8:-8, 8:-8], cv2.COLOR_BGR2GRAY), (48, 24))
            out.append({"row": r, "col": c, "center": [cx, (y1 + y2) // 2], "rect": [x1, y1, CELL_W, y2 - y1],
                        "hash": hashlib.md5((small // 16).tobytes()).hexdigest()[:12]})  # fmt: skip
    return out


def detail_page(boxes) -> int | None:
    for t, _, (x1, y1, x2, y2) in boxes:
        # 斜杠 RapidOCR 常读成 1（「1/2」→「112」）；页码只有 1/2、2/2，三位且中间是 1 / l / I / | 也按斜杠算
        if 480 <= (y1 + y2) // 2 <= 580 and (m := re.fullmatch(r"([12])[/1lI|]2", t.replace(" ", ""))):
            return int(m.group(1))
    return None


def _lines(boxes, y1: int, y2: int) -> list[str]:
    return [t for t, _, b in sorted(boxes, key=lambda b: ((b[2][1] + b[2][3]) // 2, b[2][0])) if y1 <= (b[1] + b[3]) // 2 <= y2]


def read_stats(boxes) -> dict:
    """顶部属性：{Vo, Da, Vi, hp}（「145+15」这类预览差值只取前面的数）。"""
    out = {}
    for key, x in (("Vo", 190), ("Da", 326), ("Vi", 468), ("hp", 630)):
        hit = next((t for t, _, b in boxes if abs((b[0] + b[2]) // 2 - x) <= 45 and 55 <= (b[1] + b[3]) // 2 <= 90), None)
        m = re.match(r"\d+", hit.replace(" ", "")) if hit else None
        out[key] = int(m.group()) if m else None
    return out


OCR_SWAPS = (("ヘ", "へ"), ("宜", "宣"), ("ー", "一"), ("一", "ー"), ("へ", "ヘ"))  # 实机扫描读错的：輝くキミヘ / アイドル宜言 / 精神統ー


def _name_variants(text: str) -> list[str]:
    """卡名原文 + 常见 OCR 混淆逐个替换后的写法（原文优先）。"""
    out = [text]
    for a, b in OCR_SWAPS:
        if a in text:
            out.append(text.replace(a, b))
    return out


def read_card_page(boxes) -> dict:
    """详情 1/2：{timing: start|mid|None, timing_text, card_text（卡名原文）, card（主数据命中 {card_id, name, upgrade}）, effect}。"""
    from hiro.vision.deck import card_from_name

    timing_text = next((t for t in _lines(boxes, 180, 215) if "獲得" in t and "獲得可能" not in t), None)
    timing = next((v for k, v in TIMING.items() if timing_text and k in timing_text), None)
    names = [t for t in _lines(boxes, 222, 260)]
    card = next((c for t in names for v in _name_variants(t) if (c := card_from_name(v))), None)
    return {"timing": timing, "timing_text": timing_text, "card_text": names[0] if names else None, "card": card,
            "effect": _lines(boxes, 270, 510)}  # fmt: skip


def read_ability_page(boxes) -> list[dict]:
    """详情 2/2：能力（被动）卡片，两列排布。按列、按纵向间距把 OCR 框拼成一张张卡片：
    [{text, disabled}]（disabled = 卡片角上有「発動不可」，例如别的偶像专用的能力），左列在前、各列从上到下。"""
    tiles = []
    for lo, hi in ((0, 360), (360, 720)):
        col = sorted(((b[1] + b[3]) // 2, b[1], b[3], t) for t, _, b in boxes
                     if lo <= (b[0] + b[2]) // 2 < hi and 120 <= (b[1] + b[3]) // 2 <= 515 and t != "獲得可能スキルカード")  # fmt: skip
        cur, last_bottom = [], None
        for _, top, bottom, t in col:
            if cur and top - last_bottom > ABILITY_GAP:
                tiles.append(cur)
                cur = []
            cur.append(t)
            last_bottom = bottom
        if cur:
            tiles.append(cur)
    out = []
    for parts in tiles:
        disabled = "発動不可" in parts
        text = "".join(p for p in parts if p != "発動不可").replace(" ", "")
        if text:
            out.append({"text": text, "disabled": disabled})
    return out


class Picker:
    """回忆选择页上的操作。shoot / click / swipe / ocr / clock / sleep 可注入（离线测试）。"""

    def __init__(self, shoot, click, swipe, *, ocr=ocr_boxes, clock=time.monotonic, sleep=time.sleep, calib_dir: Path | None = None):
        self.shoot, self.click, self.swipe, self.ocr, self.clock, self.sleep = shoot, click, swipe, ocr, clock, sleep
        self.calib_dir, self._n = calib_dir, 0

    def snap(self, img, tag: str) -> None:
        if self.calib_dir is None:
            return
        self.calib_dir.mkdir(parents=True, exist_ok=True)
        self._n += 1
        imgio.imwrite(str(self.calib_dir / f"{self._n:04d}-{tag}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])

    def _wait_change(self, before, roi, timeout=TAP_TIMEOUT):
        t0 = self.clock()
        while True:
            img = self.shoot()
            if roi_diff(before, img, roi) > 6:
                self.sleep(0.3)  # 等文字渐入
                return True, self.shoot()
            if self.clock() - t0 >= timeout:
                return False, img
            self.sleep(0.15)

    def select(self, cell: dict) -> np.ndarray:
        """点一格；详情栏变了（或本来就是这格）后返回画面。"""
        before = self.shoot()
        self.click(*cell["center"])
        _, img = self._wait_change(before, DETAIL_ROI)
        return img

    def to_page(self, page: int) -> tuple[np.ndarray, list]:
        """详情栏翻到第 page 页（1 / 2）。"""
        img = self.shoot()
        boxes = self.ocr(img)
        for _ in range(3):
            cur = detail_page(boxes)
            if cur == page:
                return img, boxes
            self.click(*(PAGE_NEXT if (cur or 1) < page else PAGE_PREV))
            _, img = self._wait_change(img, DETAIL_ROI)
            boxes = self.ocr(img)
        return img, boxes

    def read_selected(self) -> dict:
        img, boxes = self.to_page(1)
        entry = {"stats": read_stats(boxes), **read_card_page(boxes)}
        img2, boxes2 = self.to_page(2)
        entry["abilities"] = read_ability_page(boxes2)
        self.to_page(1)
        return entry

    def scroll(self, rows: float = SWIPE_ROWS, *, px: int | None = None) -> np.ndarray:
        """上滑 rows 行（负数下滑；或直接给像素 px），慢滑、终点停住，返回滑后的画面。"""
        dy = int(px if px is not None else rows * ROW_PITCH)
        y1 = SWIPE_FROM if dy > 0 else GRID_Y[0] + 40
        self.swipe(360, y1, 360, y1 - dy)
        self.sleep(SCROLL_SETTLE)
        return self.shoot()

    def cancel(self) -> bool:
        self.click(*CANCEL_TAP)
        self.sleep(1.0)
        return not picker_open(self.ocr(self.shoot()))


def cell_thumb(img: np.ndarray, cell: dict) -> np.ndarray:
    """格子缩略图（去掉外沿 8 像素的选中框），用于跨页对齐。"""
    x, y, w, h = cell["rect"]
    return cv2.resize(cv2.cvtColor(img[y + 8 : y + h - 8, x + 8 : x + w - 8], cv2.COLOR_BGR2GRAY), (48, 24)).astype(np.float32)


def page_rows(img: np.ndarray) -> list[list[dict]]:
    """可见完整行：每行若干格（带 thumb）。"""
    rows: dict[int, list[dict]] = {}
    for c in grid_cells(img):
        rows.setdefault(c["row"], []).append({**c, "thumb": cell_thumb(img, c)})
    return [rows[k] for k in sorted(rows)]


def same_row(a: list[dict], b: list[dict]) -> bool:
    return len(a) == len(b) and all(float(np.abs(x["thumb"] - y["thumb"]).mean()) <= CELL_MATCH_MAX for x, y in zip(a, b))


def row_overlap(prev: list[list[dict]], cur: list[list[dict]]) -> int:
    """cur 开头有几行就是 prev 结尾那几行（最大的 m）；0 = 没重叠（滚过头了，或到了别处）。"""
    for m in range(min(len(prev), len(cur)), 0, -1):
        if all(same_row(prev[len(prev) - m + i], cur[i]) for i in range(m)):
            return m
    return 0


def to_top(picker: "Picker", max_swipes: int = 80) -> np.ndarray:
    """往下滑回列表顶部（画面不再变 = 到顶），返回顶部画面。"""
    img = picker.shoot()
    still = 0
    for _ in range(max_swipes):
        nxt = picker.scroll(-3)
        still = still + 1 if grid_still(img, nxt) else 0
        img = nxt
        if still >= 2:
            break
    return img


def entry_key(e: dict) -> str:
    """同一份回忆的内容键：卡（名 + 时机）+ 属性 + 能力文字（只用于统计重复，不再用来去重）。"""
    s = e.get("stats") or {}
    card = (e.get("card") or {}).get("card_id") or e.get("card_text")
    return json.dumps([card, (e.get("card") or {}).get("upgrade"), e.get("timing"), s.get("Vo"), s.get("Da"), s.get("Vi"), s.get("hp"),
                       e.get("abilities")], ensure_ascii=False)  # fmt: skip


def grid_still(a: np.ndarray, b: np.ndarray) -> bool:
    """回忆格区两帧几乎一样（滑动没让列表动）。"""
    return roi_diff(a, b, (GRID_X[0], GRID_Y[0], GRID_X[1] - GRID_X[0], GRID_Y[1] - GRID_Y[0])) < 2.0


def scan(picker: Picker, out: Path, *, max_pages: int = 600, on_entry: Callable[[dict], None] | None = None) -> dict:
    """从顶部开始逐行读。每次把本页最后一个完整行滑到最上面（按实测行位置算像素，不按固定行数，误差不会累积），
    新页开头应正好是上一页最后一行：按格子缩略图核对，没重叠就往回滑一行再找，还对不上记进 gaps 并继续。
    到底的判定：连续 2 次滑动后回忆格区都完全不变（整页与上一页重叠只说明滑得太少，不算到底）。
    每个格子一条记录（slot = 全局行号 * 3 + 列）；每读一格写一次 out（覆盖，旧文件留 .bak）。结束点「キャンセル」。只读。"""
    img = picker.shoot()
    if not picker_open(picker.ocr(img)):
        raise RuntimeError("不在回忆选择页（没看到 キャンセル / 決定 / 編成詳細）：请先在编成页点开一格回忆")
    if out.exists():
        out.with_suffix(out.suffix + ".bak").write_text(out.read_text())
    img = to_top(picker)
    inv = {"entries": [], "gaps": [], "scanned_at": None, "complete": False}
    pages = new = 0
    row_base, start = 0, 0  # 当前页第 0 行的全局行号；本页从第几行开始读（前面的行上一页读过）
    while pages < max_pages:
        picker.snap(img, f"page{pages:03d}")
        rows = page_rows(img)
        for r in range(start, len(rows)):
            for cell in rows[r]:
                picker.select(cell)
                e = picker.read_selected()
                e.update(page=pages, slot=(row_base + r) * 3 + cell["col"], cell=[row_base + r, cell["col"]])
                inv["entries"].append(e)
                new += 1
                if on_entry:
                    on_entry(e)
                inv["scanned_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                out.write_text(json.dumps(inv, ensure_ascii=False, indent=1))
        prev, prev_base, before = rows, row_base, img
        pages += 1
        if not prev:
            img = picker.scroll(SWIPE_ROWS)
            if grid_still(before, img) and grid_still(img, picker.scroll(SWIPE_ROWS)):
                break
            row_base, start = prev_base, 0
            continue
        dy = prev[-1][0]["rect"][1] - prev[0][0]["rect"][1] if len(prev) > 1 else int(ROW_PITCH)
        img = picker.scroll(px=dy)
        if grid_still(before, img):  # 没动：再滑一次，还不动才算到底
            img = picker.scroll(px=dy)
            if grid_still(before, img):
                inv["complete"] = True
                break
        cur = page_rows(img)
        m = row_overlap(prev, cur)
        back = 0
        while m == 0 and back < MAX_BACKTRACK:  # 滚过头：往回滑一行再找重叠
            back += 1
            img = picker.scroll(-1)
            cur = page_rows(img)
            m = row_overlap(prev, cur)
        if m == 0:
            inv["gaps"].append({"after_row": prev_base + len(prev) - 1, "page": pages})
            logger.warning("回忆扫描：第 %d 页和上一页对不上（往回滑了 %d 次），可能漏了行，继续往下扫", pages, back)
            row_base, start = prev_base + len(prev), 0
        else:
            row_base, start = prev_base + len(prev) - m, m
    out.write_text(json.dumps(inv, ensure_ascii=False, indent=1))
    picker.cancel()
    logger.info("回忆扫描：%d 页，读了 %d 格，漏行 %d 处，complete=%s → %s", pages, new, len(inv["gaps"]), inv["complete"], out)
    return inv


def explore(picker: Picker, out_dir: Path) -> list[dict]:
    """拍下筛选 / 排序对话框与翻到底的画面（含 OCR），给补点击路径用。只用「キャンセル / 閉じる / 戻る」关闭对话框。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    log = []

    def shot(tag: str):
        img = picker.shoot()
        boxes = picker.ocr(img)
        imgio.imwrite(str(out_dir / f"{len(log):02d}-{tag}.png"), img)
        log.append({"tag": tag, "texts": [[t, list(b)] for t, _, b in boxes]})
        (out_dir / "explore.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))
        return img, boxes

    img, boxes = shot("picker")
    if not picker_open(boxes):
        raise RuntimeError("不在回忆选择页")
    for tag, tap in (("filter", FILTER_TAP), ("sort-key", SORT_KEY_TAP)):
        picker.click(*tap)
        picker.sleep(1.2)
        img, boxes = shot(tag)
        if picker_open(boxes):
            continue  # 没弹对话框（可能是排序方向切换）
        close = next((p for t in CLOSE_TEXTS if (p := find_text(boxes, t))), None)
        if close is None:
            raise RuntimeError(f"{tag} 对话框里没找到 キャンセル / 閉じる / 戻る，停下（已拍 {out_dir}）")
        picker.click(*close)
        picker.sleep(1.0)
        _, boxes = shot(f"{tag}-closed")
        if not picker_open(boxes):
            raise RuntimeError(f"关掉 {tag} 对话框后不在回忆选择页了（可能点到了选择页自己的キャンセル），停下（已拍 {out_dir}）")
    picker.to_page(2)
    shot("detail-p2")
    picker.to_page(1)
    img, still = picker.shoot(), 0
    for i in range(200):
        nxt = picker.scroll()
        still = still + 1 if grid_still(img, nxt) else 0
        img = nxt
        if still >= 2:
            break
    shot(f"bottom-after-{i + 1}-scrolls")
    picker.cancel()
    shot("after-cancel")
    return log
