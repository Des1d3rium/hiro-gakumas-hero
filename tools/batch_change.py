"""授業换卡（セレクトチェンジ）的一次扫描 + 按行列点选，给 batch_hif 的 change-scan / change-pick 用。

阶段 1（选换入的新卡）：一帧读完 4 张候选（卡面模板认名字、强化、レッスン中1回），不用逐张点开看详情。
阶段 2（选换出的原卡）：从上往下滑一遍拼出整副牌组的行列表（hiro.loop.kaa_patches.scan_dialog_deck），滑回顶部；
点选时滑到目标所在屏，用当前屏认出的卡与行列表对齐行号，按 (行, 列) 点，再读选中框核对。
"""

from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path
from hiro.produce import hif_buttons as BTN

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIR = ROOT / "reports" / "live" / "deck-list"
STAGE1_WORDS = ("獲得するスキルカードを選",)
STAGE2_WORDS = ("チェンジするスキルカード", "スキルカードが選択されていません")
NEXT_TAP = BTN.BOTTOM_LOW  # 阶段 1「次へ」
CHANGE_TAP = BTN.DIALOG_OK  # 阶段 2「チェンジ」
SETTLE = 0.6
STAGE_WAIT = 6.0


# ------------------------------------------------------------------ 纯函数（可离线测）
def stage_of(texts: list[str]) -> int | None:
    flat = "".join(t.replace(" ", "") for t in texts)
    if any(w in flat for w in STAGE1_WORDS):
        return 1
    if any(w in flat for w in STAGE2_WORDS):
        return 2
    return None


def deck_rows(cards: list[dict]) -> list[list[dict]]:
    """扫描结果（每张有全局 row / col）→ 按行分组，行内按列排。"""
    rows: dict[int, list[dict]] = {}
    for c in cards:
        rows.setdefault(c["row"], []).append(c)
    return [sorted(rows[r], key=lambda c: c["col"]) for r in sorted(rows)]


def screen_offset(visible: list[dict], rows: list[list[dict]]) -> int | None:
    """当前屏（read_card_grid 的 row 为屏内行号）对齐到整副牌组的第几行。
    认出的格必须和行列表一致、每个屏内行至少一格认出；没认出的格当通配。对齐方式不唯一或没有时返回 None（不猜）。"""
    by_row: dict[int, dict[int, dict]] = {}
    for c in visible:
        by_row.setdefault(c["row"], {})[c["col"]] = c
    if not by_row:
        return None
    hits = []
    for offset in range(len(rows) - len(by_row) + 1):
        ok = True
        for r, cells in by_row.items():
            deck = {c["col"]: c for c in rows[offset + r]} if offset + r < len(rows) else {}
            known = [(col, c) for col, c in cells.items() if c.get("card_id") and deck.get(col, {}).get("card_id")]
            if not known or any(c["card_id"] != deck[col]["card_id"] for col, c in known) or set(cells) - set(deck):
                ok = False
                break
        if ok:
            hits.append(offset)
    return hits[0] if len(hits) == 1 else None


def summary(scan: dict) -> list[str]:
    """给代理看的简表：每行「r<行>: 名字+强化(1回) | …」，认不出写 ?。"""
    out = []
    for row in deck_rows(scan["cards"]):
        cells = []
        for c in row:
            name = c.get("name") or "?"
            up = "+" * (c.get("upgrade") or 0)
            cells.append(f"c{c['col']} {name}{up}{'（1回）' if c.get('once') else ''}")
        out.append(f"r{row[0]['row']}: " + " | ".join(cells))
    return out


def card_full(c: dict) -> str | None:
    return (c.get("name") or "").replace(" ", "") + "+" * (c.get("upgrade") or 0) if c.get("name") else None


def cells_for(cards: list[dict], name: str, customized: bool | None = None) -> dict:
    """按卡名（带「+」）和客制化标记在扫描表里找要换出的格（使用者 2026-11-07：代理逐格点开看是哪张太慢）。
    返回 {"cells": [匹配的格], "unknown": [认不出的格]}。匹配的格都同名同客制化标记 = 同一种卡，点哪格都行。"""
    want = unicodedata.normalize("NFKC", name).replace(" ", "")
    hits = [c for c in cards if card_full(c) == want and (customized is None or bool(c.get("customized")) == customized)]
    return {"cells": hits, "unknown": [c for c in cards if not c.get("card_id")]}


def panel_info(boxes: list, name: str | None, top: int) -> dict:
    """阶段 2 选中后上方详情栏（卡格区上面）的 OCR：卡名对不对、有没有「レッスン中1回」。
    卡名按去掉「+」的名字包含比（OCR 常把「+」读丢）。详情栏模板认不出时（观察流：Detail empty）靠它核对。"""
    texts = [t for t, _, (x1, y1, x2, y2) in boxes if (y1 + y2) // 2 < top]
    flat = unicodedata.normalize("NFKC", "".join(texts)).replace(" ", "")
    base = unicodedata.normalize("NFKC", name or "").replace(" ", "").rstrip("+")
    return {"text": flat[:120], "name_ok": bool(base) and base in flat, "once": "レッスン中1回" in flat if flat else None}


# ------------------------------------------------------------------ 实机
def _tools():
    from hiro.loop.kaa_patches import CARD_OP_SWIPE_DURATION, CHANGE2_CARD_AREA, _dialog_swipe, scan_dialog_deck
    from hiro.produce.ops import find_text, ocr_boxes
    from hiro.state.knowledge import is_once
    from hiro.vision.deck import dialog_scroll, read_card_dialog, read_card_grid, read_change_candidates

    return locals()


def _stage(device, t) -> tuple[int | None, object, list]:
    img = device.screenshot()
    boxes = t["ocr_boxes"](img)
    return stage_of([b[0] for b in boxes]), img, boxes


def scan(device, batch: str, run: int) -> dict:
    t = _tools()
    stage, img, _ = _stage(device, t)
    if stage == 1:
        r = t["read_change_candidates"](img)
        cands = [{"col": c["index"], "card_id": c["card_id"], "name": c["name"], "upgrade": c["upgrade"],
                  "once": t["is_once"](c["card_id"]), "center": c["center"]} for c in r["candidates"]]  # fmt: skip
        return {"stage": 1, "candidates": cands, "unknown": [c["col"] for c in cands if not c["card_id"]]}
    if stage != 2:
        return {"stage": None, "ok": False, "why": "不在换卡阶段 1 / 2 画面"}
    swipe = lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=t["CARD_OP_SWIPE_DURATION"])  # noqa: E731
    # 先滑到顶部：scan_dialog_deck 从当前位置往下拼，开始时不在顶部，行号整体错位、上面几行漏掉
    #（2026-11-07 验证局 1-i：第一次扫描从第 2 行开始，r1c0 实际是第 3 行的卡，按它点会点到别的卡）
    if not to_top(device, t, swipe):
        return {"stage": 2, "ok": False, "why": "换卡阶段 2 列表滑不回顶部，没扫描"}
    frames: list = []

    def shoot():
        img = device.screenshot()
        frames.append(img)
        return img

    res = t["scan_dialog_deck"](t["CHANGE2_CARD_AREA"], shoot=shoot, swipe=swipe)
    cards = [{k: c.get(k) for k in ("deck_index", "row", "col", "card_id", "name", "upgrade", "customized", "screen")}
             | {"once": t["is_once"](c.get("card_id"))} for c in res["cards"]]  # fmt: skip
    out = {"stage": 2, "complete": res["complete"], "screens": res["screens"], "problems": res["problems"], "cards": cards,
           "unknown": [[c["row"], c["col"]] for c in cards if not c["card_id"]]}  # fmt: skip
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    path = SCAN_DIR / f"change-{batch}-{run}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    if out["unknown"]:
        # 认不出的格留证据（2026-11-08 批次 1-e：客制化过的巻き返し+ 在换卡框里两次都认不出，当时没存帧、查不了）
        import cv2

        for i, img in enumerate(frames):
            cv2.imwrite(str(path.with_suffix(f".{i:02d}.jpg")), img)
        out["frames"] = len(frames)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**out, "file": str(path), "table": summary(out)}


def changed_text(texts: list[str]) -> bool:
    """换卡完成提示「AをBにチェンジしました」。OCR 常拆成「…にチェンジ」「しました」两个框（验证局 1-i 实机），拼起来找。"""
    return "チェンジしました" in "".join(t.replace(" ", "") for t in texts)


CELL_TOL = 30  # 选中框所在格与目标格的中心差（像素）


def to_top(device, t, swipe, tries: int = 6) -> bool:
    """阶段 2 列表滑到顶部（滚动条滑块贴顶，或画面不再变化）。"""
    area = t["CHANGE2_CARD_AREA"]
    before = None
    for _ in range(tries):
        img = device.screenshot()
        scroll = t["dialog_scroll"](img, area)
        if not scroll["scrollable"] or scroll["at_top"]:
            return True
        if before is not None and scroll["thumb"] == before:
            return False
        before = scroll["thumb"]
        t["_dialog_swipe"](area, False, swipe)
        time.sleep(0.8)
    return False


def latest_scan(batch: str, run: int) -> Path | None:
    files = sorted(SCAN_DIR.glob(f"change-{batch}-{run}-*.json"))
    return files[-1] if files else None


def pick(device, batch: str, run: int, *, col: int | None = None, row: int | None = None, commit: bool = False,
         scan_file: Path | None = None, name: str | None = None, customized: bool | None = None) -> dict:
    """阶段 1 按 --col 选候选；阶段 2 按 --row/--col，或按 --name [--customized 0|1] 在扫描表里自己找格。
    阶段 2 点选后读上方详情栏：卡名对得上（扫描表认错 / 详情栏模板认不出时也能核对）、有没有「レッスン中1回」，
    结果里带 customized / once，记账时用来挑同名的哪一张。"""
    t = _tools()
    stage, img, boxes = _stage(device, t)
    if stage == 1:
        if row is not None or col is None:
            return {"ok": False, "why": "阶段 1 只按列选（--col），不带 --row"}
        cands = t["read_change_candidates"](img)["candidates"]
        target = next((c for c in cands if c["index"] == col), None)
        if target is None:
            return {"ok": False, "why": f"阶段 1 没找到第 {col} 列候选", "found": len(cands)}
        device.click(*target["center"])
        time.sleep(SETTLE)
        device.click(*(t["find_text"](t["ocr_boxes"](device.screenshot()), "次へ", region=(200, 1040, 520, 1140)) or NEXT_TAP))
        deadline = time.monotonic() + STAGE_WAIT
        while time.monotonic() < deadline:
            time.sleep(SETTLE)
            if _stage(device, t)[0] == 2:
                return {"ok": True, "stage": 1, "picked": {"col": col, "name": target["name"], "upgrade": target.get("upgrade"),
                                                           "once": t["is_once"](target["card_id"])}}  # fmt: skip
        return {"ok": False, "stage": 1, "why": "点了候选和「次へ」，没进入阶段 2", "picked": target["name"]}
    if stage != 2:
        return {"ok": False, "why": "不在换卡阶段 1 / 2 画面"}
    path = scan_file or latest_scan(batch, run)
    if path is None:
        return {"ok": False, "why": "没有本局的 change-scan 结果，先扫描"}
    scanned = json.loads(path.read_text(encoding="utf-8"))["cards"]
    rows = deck_rows(scanned)
    if row is None and name:
        found = cells_for(scanned, name, customized)
        cells = found["cells"]
        if not cells:
            unknown = [f"r{c['row']}c{c['col']}" for c in found["unknown"]]
            label = {None: "", True: "客制化过的", False: "未客制化的"}[customized]
            return {"ok": False, "why": f"扫描表里没有{label}「{name}」"
                    + (f"；认不出的格 {unknown} 可能是它，逐格 --row/--col 点开看（不加 --commit）" if unknown else ""), "scan": str(path)}
        row, col = cells[0]["row"], cells[0]["col"]
    if row is None or col is None:
        return {"ok": False, "why": "阶段 2 要给 --row 和 --col，或 --name [--customized 0|1]（先 change-scan）"}
    if not (0 <= row < len(rows)) or col not in {c["col"] for c in rows[row]}:
        return {"ok": False, "why": f"扫描表里没有 r{row} c{col}", "scan": str(path)}
    target = next(c for c in rows[row] if c["col"] == col)
    area = t["CHANGE2_CARD_AREA"]
    swipe = lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=t["CARD_OP_SWIPE_DURATION"])  # noqa: E731
    cell = None
    for _ in range(12):
        visible = t["read_card_grid"](img, area, "dialog", icons=True)  # 与 change-scan 同一套认卡（HIRO_CARD_ICON），否则补认的格对不上
        offset = screen_offset(visible, rows)
        if offset is None:
            return {"ok": False, "why": "当前屏对不上扫描表（牌组变了？重新 change-scan）", "scan": str(path)}
        shown = {c["row"] for c in visible}
        if row - offset in shown:
            cell = next((c for c in visible if c["row"] == row - offset and c["col"] == col), None)
            break
        t["_dialog_swipe"](area, row > offset, swipe)
        time.sleep(0.8)
        img = device.screenshot()
    if cell is None:
        return {"ok": False, "why": f"滑动后没找到 r{row} c{col} 的卡格"}
    device.click(*cell["center"])
    time.sleep(SETTLE)
    shot = device.screenshot()
    sel = t["read_card_dialog"](shot, area, icons=True)
    chosen = sel.get("selected_card") or {}
    picked = sel["cards"][sel["selected"]] if sel.get("selected") is not None else None
    # 按格子中心比，不按屏内行号：点选前后两帧顶上那行算不算整格可见可能不同，行号会差一（验证局 1-i 实机）
    near = picked is not None and all(abs(p - q) <= CELL_TOL for p, q in zip(picked["center"], cell["center"]))
    same_cell = near or (picked is not None and picked.get("card_id") is not None and picked.get("card_id") == cell.get("card_id")
                         and picked.get("upgrade") == cell.get("upgrade") and picked["col"] == col)
    # 要换出的卡名：--name 给了就以它为准（扫描表会认错，观察流 r3c1 巻き返し+ 认成アイドル宣言+），否则用扫描表
    expect = name or card_full(target)
    panel = panel_info(t["ocr_boxes"](shot), expect, area[1])
    if name:
        same_card = panel["name_ok"] or chosen.get("card_id") is not None and card_full(chosen) == expect
    else:
        same_card = not target.get("card_id") or chosen.get("card_id") in (None, target["card_id"])
    customized_seen = picked.get("customized") if picked is not None and picked.get("customized") is not None else target.get("customized")
    info = {"row": row, "col": col, "name": name or target.get("name"), "upgrade": target.get("upgrade") if not name else None,
            "customized": customized_seen, "once": panel["once"], "selected_detail": chosen.get("name"), "panel": panel["text"]}  # fmt: skip
    if name:  # 记账用：卡名按 --name（带「+」）拆成名字 + 强化
        info["name"], info["upgrade"] = name.rstrip("+"), len(name) - len(name.rstrip("+"))
    if not (same_cell and same_card):
        return {"ok": False, "stage": 2, "why": "选中框和目标对不上，没点「チェンジ」", "target": info}
    verified = bool(chosen.get("card_id")) or panel["name_ok"]
    if commit and (not (target.get("card_id") or name) or customized_seen or not verified):
        # 认不出的卡 / 客制化过的卡不换（AGENTS：认不出就キャンセル，不猜）；扫描表认不出、也没给 --name 时选中框读到什么都不算核对
        #（2026-11-07 验证局 1-i：扫描表 r1c0 是「?」，原来 same_card 直接放行，差点把客制化过的巻き返し+换掉）
        why = ("目标卡认不出（给 --name 并让详情栏核对）" if not (target.get("card_id") or name) else
               "目标卡客制化过" if customized_seen else "选中后详情栏模板和 OCR 都没认出卡名")
        return {"ok": False, "stage": 2, "why": f"{why}，没点「チェンジ」", "target": info}
    if not commit:
        return {"ok": True, "stage": 2, "selected": info, "next": "核对后加 --commit，或手动点「チェンジ」→「決定」"}
    device.click(*(t["find_text"](t["ocr_boxes"](device.screenshot()), "チェンジ", region=(400, 1100, 720, 1220), exact=True) or CHANGE_TAP))
    deadline = time.monotonic() + STAGE_WAIT
    confirmed = False
    while time.monotonic() < deadline:
        time.sleep(SETTLE)
        img = device.screenshot()
        boxes = t["ocr_boxes"](img)
        if changed_text([b[0] for b in boxes]):  # 游戏没弹确认框、直接换好时的完成提示
            return {"ok": True, "stage": 2, "changed": info, "confirmed": confirmed}
        if not confirmed and t["find_text"](boxes, "チェンジ確認"):
            device.click(*(t["find_text"](boxes, ("決定", "はい", "OK"), region=(300, 1080, 720, 1240)) or CHANGE_TAP))
            confirmed = True
            continue
        if confirmed and stage_of([b[0] for b in boxes]) is None and not t["find_text"](boxes, "チェンジ確認"):
            return {"ok": True, "stage": 2, "changed": info}
    return {"ok": False, "stage": 2, "why": "点了「チェンジ」，确认框没出现或没关闭", "confirmed": confirmed, "target": info}
