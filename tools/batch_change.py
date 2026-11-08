"""授業换卡（セレクトチェンジ）的一次扫描 + 按行列点选，给 batch_hif 的 change-scan / change-pick 用。

阶段 1（选换入的新卡）：一帧读完 4 张候选（卡面模板认名字、强化、レッスン中1回），不用逐张点开看详情。
阶段 2（选换出的原卡）：从上往下滑一遍拼出整副牌组的行列表（hiro.loop.kaa_patches.scan_dialog_deck），滑回顶部；
点选时滑到目标所在屏，用当前屏认出的卡与行列表对齐行号，按 (行, 列) 点，再读选中框核对。
"""

from __future__ import annotations

import json
import time
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
    res = t["scan_dialog_deck"](t["CHANGE2_CARD_AREA"], shoot=device.screenshot, swipe=swipe)
    cards = [{k: c.get(k) for k in ("deck_index", "row", "col", "card_id", "name", "upgrade", "customized", "screen")}
             | {"once": t["is_once"](c.get("card_id"))} for c in res["cards"]]  # fmt: skip
    out = {"stage": 2, "complete": res["complete"], "screens": res["screens"], "problems": res["problems"], "cards": cards,
           "unknown": [[c["row"], c["col"]] for c in cards if not c["card_id"]]}  # fmt: skip
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    path = SCAN_DIR / f"change-{batch}-{run}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**out, "file": str(path), "table": summary(out)}


def latest_scan(batch: str, run: int) -> Path | None:
    files = sorted(SCAN_DIR.glob(f"change-{batch}-{run}-*.json"))
    return files[-1] if files else None


def pick(device, batch: str, run: int, *, col: int, row: int | None = None, commit: bool = False, scan_file: Path | None = None) -> dict:
    t = _tools()
    stage, img, boxes = _stage(device, t)
    if stage == 1:
        if row is not None:
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
    if row is None:
        return {"ok": False, "why": "阶段 2 要同时给 --row 和 --col（先 change-scan）"}
    path = scan_file or latest_scan(batch, run)
    if path is None:
        return {"ok": False, "why": "没有本局的 change-scan 结果，先扫描"}
    rows = deck_rows(json.loads(path.read_text(encoding="utf-8"))["cards"])
    if not (0 <= row < len(rows)) or col not in {c["col"] for c in rows[row]}:
        return {"ok": False, "why": f"扫描表里没有 r{row} c{col}", "scan": str(path)}
    target = next(c for c in rows[row] if c["col"] == col)
    area = t["CHANGE2_CARD_AREA"]
    swipe = lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=t["CARD_OP_SWIPE_DURATION"])  # noqa: E731
    cell = None
    for _ in range(12):
        visible = t["read_card_grid"](img, area, "dialog")
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
    sel = t["read_card_dialog"](device.screenshot(), area)
    chosen = sel.get("selected_card") or {}
    picked = sel["cards"][sel["selected"]] if sel.get("selected") is not None else None
    same_cell = picked is not None and picked["row"] == cell["row"] and picked["col"] == col
    same_card = not target.get("card_id") or chosen.get("card_id") in (None, target["card_id"])
    info = {"row": row, "col": col, "name": target.get("name"), "upgrade": target.get("upgrade"), "once": target.get("once"),
            "selected_detail": chosen.get("name")}  # fmt: skip
    if not (same_cell and same_card):
        return {"ok": False, "stage": 2, "why": "选中框和目标对不上，没点「チェンジ」", "target": info}
    if not commit:
        return {"ok": True, "stage": 2, "selected": info, "next": "核对后加 --commit，或手动点「チェンジ」→「決定」"}
    device.click(*(t["find_text"](t["ocr_boxes"](device.screenshot()), "チェンジ", region=(400, 1100, 720, 1220), exact=True) or CHANGE_TAP))
    deadline = time.monotonic() + STAGE_WAIT
    confirmed = False
    while time.monotonic() < deadline:
        time.sleep(SETTLE)
        img = device.screenshot()
        boxes = t["ocr_boxes"](img)
        if any("チェンジしました" in b[0] for b in boxes):  # 游戏没弹确认框、直接换好时的完成提示
            return {"ok": True, "stage": 2, "changed": info, "confirmed": confirmed}
        if not confirmed and t["find_text"](boxes, "チェンジ確認"):
            device.click(*(t["find_text"](boxes, ("決定", "はい", "OK"), region=(300, 1080, 720, 1240)) or CHANGE_TAP))
            confirmed = True
            continue
        if confirmed and stage_of([b[0] for b in boxes]) is None and not t["find_text"](boxes, "チェンジ確認"):
            return {"ok": True, "stage": 2, "changed": info}
    return {"ok": False, "stage": 2, "why": "点了「チェンジ」，确认框没出现或没关闭", "confirmed": confirmed, "target": info}
