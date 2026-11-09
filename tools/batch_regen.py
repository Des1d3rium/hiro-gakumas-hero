"""本战 LIVE 之后的回忆生成 / 再生成 / 挑选（固定坐标 + 局部 OCR）。

实机（codex-20261107-6 第 1 局，2026-11-07）流程：
1. LIVE 后一路快进（点左上角）到「MEMORY」生成页，点中间「生成」(360,975)；背景一直在动，像素不会稳定，按按钮那一小块 OCR 认；
2. 再快进到回忆页：左下角是「あとN回可能」+「再生成」(110,1162)。局部 OCR 左下角认出「再生成」就停下快进；
3. 还有次数就点再生成 → 「メモリー再生成確認」框：消耗「メモリー再生成チケット」（所持 332→331），残り再生成回数 2/2；
   「再生成する」在标题条下方固定偏移 (498, 标题顶 + 621)。框里出现ジュエル / 有償 / 購入就不点，停下；
4. 回到第 1 步（又是生成页）。次数用完后在挑选页「獲得するメモリーを選択してください」用 < (220,1027) / > (498,1027)
   逐个看每个版本（中间标签「再生成N回目」），按回忆档案 memory_regen（batch_memory.priority()）挑命中的一份，翻到它再点「決定」(360,1160)。
   都没命中就停下，把每个版本的评估交给代理。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path[:0] = [p for p in (str(Path(__file__).resolve().parents[1] / "src"),) if p not in sys.path]

import batch_memory  # noqa: E402
from hiro.produce import hif_buttons as BTN  # noqa: E402
from hiro.vision.stable import BOTTOM_BAR, wait_stable  # noqa: E402

CORNER = BTN.CORNER
GEN_CROP = (280, 935, 440, 1015)  # x1, y1, x2, y2：生成页中间按钮
GEN_TAP = BTN.MEMORY_GENERATE
REGEN_CROP = (0, 1085, 240, 1195)  # 回忆页左下：「あとN回可能」+「再生成」
REGEN_TAP = BTN.MEMORY_REGEN
CONFIRM_X, CONFIRM_DY = 498, BTN.REGEN_CONFIRM_DY  # 再生成确认框「再生成する」：标题条顶 + 621
TITLE_CROP = (0, 45, 720, 115)  # 挑选页标题
DOT_Y, DOT_X1, DOT_X2 = BTN.MEMORY_DOTS_Y, 300, 420  # 挑选页分页圆点
CARD_CROP = (0, 220, 720, 780)  # 挑选页卡名 + 效果 + 回忆能力（评估只读这一块）
PREV_TAP, NEXT_TAP = BTN.MEMORY_PREV, BTN.MEMORY_NEXT
DECIDE_TAP = BTN.BOTTOM_HIGH
NEXT_TAP_PAGE = BTN.SETTLE  # 回忆页「次へ」（与之后结算页 × / 次へ 重合处同一点）
FINAL_X, FINAL_DY = 485, BTN.FINAL_CONFIRM_DY  # 「メモリー確定確認」框「生成する」：标题条顶 + 292
TICK = 0.15
DANGER = ("ジュエル", "有償", "購入", "課金")
CONFIRM_RETRY = 4.0  # 点了「再生成する」后框还在：这么久之后才补点（补点不计次数）
# 挑选页每个版本存一帧到这里（traces/ 已被 .gitignore 挡住），方便事后对帧核对版本识别（审计清单 18 / N11）
FRAMES_ROOT = Path(__file__).resolve().parents[1] / "traces"


def crop_texts(ocr, img, box):
    x1, y1, x2, y2 = box
    return [batch_memory.norm(t) for t, _, _ in ocr(img[y1:y2, x1:x2])]


def regen_left(texts):
    """左下角文字 → (有再生成按钮, 剩余次数 | None)。"""
    has = any("再生成" in t for t in texts)
    m = next((re.search(r"あと([0-9]+)回", t) for t in texts if re.search(r"あと([0-9]+)回", t)), None)
    return has, int(m.group(1)) if m else None


def rank(ev):
    rules = [r for r, *_ in batch_memory.priority()]
    return rules.index(ev["hit"]) if ev.get("hit") in rules else len(rules)


STALE_WAIT = 8.0  # 点过再生成后，同一个剩余次数读数这么久内不再点（等画面刷新）


def run(shoot, click, ocr, header, *, max_regen=None, timeout=240.0, log=print, frames="auto"):
    """跑完再生成并挑选。返回 {ok, page, regens, versions, pick, why}（versions 每项带 name / texts / frame）；ok=False 时停在当前页交代理。
    frames：挑选页每个版本的存帧目录，"auto" = traces/regen-<时间>/，None = 不存。"""
    t0 = time.monotonic()
    regens = 0
    unread = 0
    # 只有点过左下「再生成」（或进程刚起步、可能正停在确认框上）之后看到的确认框才算一次新的再生成。
    # 实机 codex-20261107-1：再生成次数上限 2（2 次后 3 个版本），日志却打了「再生成第 3 次」——
    # 框淡出时标题条有一帧认不出、下一帧又认出来，等框关掉的循环提前退出，同一个框被再点一次、多记一次。
    await_confirm = True
    last_confirm = None
    stale = {"left": None, "at": 0.0}  # 上一次点再生成时读到的剩余次数
    while time.monotonic() - t0 < timeout:
        img = shoot()
        if img is None or img.shape[:2] != (1280, 720):
            time.sleep(TICK)
            continue
        top = header(img)
        if top is not None:
            texts = [batch_memory.norm(t) for t, _, _ in ocr(img)]
            flat = "".join(texts)
            if "再生成確認" not in flat:
                return {"ok": False, "page": "dialog", "texts": texts, "regens": regens, "why": "未知弹窗"}
            if any(w in flat for w in DANGER) or not ("チケット" in flat or "回数" in flat):
                return {"ok": False, "page": "regen_confirm", "texts": texts, "regens": regens, "why": "确认框没写明用券 / 有付费字样"}
            if not await_confirm:
                # 上次点「再生成する」之后没再点过左下「再生成」：还是同一个框（在淡出，或上一下没点上）
                if last_confirm is not None and time.monotonic() - last_confirm < CONFIRM_RETRY:
                    time.sleep(TICK)
                    continue
                click(CONFIRM_X, top[0] + CONFIRM_DY)
                last_confirm = time.monotonic()
                log(f"再生成确认框 {CONFIRM_RETRY:.0f}s 没关，补点「再生成する」（不计次数）")
                continue
            click(CONFIRM_X, top[0] + CONFIRM_DY)
            regens += 1
            await_confirm, last_confirm = False, time.monotonic()
            log(f"再生成第 {regens} 次")
            # 等确认框关掉再继续：框淡出时还认得出标题，旧代码会再点一次、把一次再生成数成两次
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and header(shoot()) is not None:
                time.sleep(TICK)
            continue
        has, left = regen_left(crop_texts(ocr, img, REGEN_CROP))
        if has:
            if left is None:
                unread += 1
                if unread < 4:
                    time.sleep(TICK)
                    continue
            unread = 0
            if left and stale.get("left") == left and time.monotonic() - stale["at"] < STALE_WAIT:
                # 再生成后左下次数还没刷新（验证局 codex-20261107-1-b：第 2 次后仍读到「あと1回」，又点了一下再生成）：等它变
                time.sleep(TICK)
                continue
            if left and (max_regen is None or regens < max_regen):
                stale.update(left=left, at=time.monotonic())
                log(f"左下「あと{left}回可能」，点再生成")
                click(*REGEN_TAP)
                await_confirm = True
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and header(shoot()) is None:
                    time.sleep(TICK)
                continue
            return choose(shoot, click, ocr, header, regens=regens, log=log, frames=frames)
        if any(t == "生成" for t in crop_texts(ocr, img, GEN_CROP)):
            click(*GEN_TAP)
            time.sleep(0.4)
            continue
        if "選択" in "".join(crop_texts(ocr, img, TITLE_CROP)):
            # 次数用完后挑选页左下角没有「再生成」按钮（实机：再生成2回目后整个按钮消失）：看标题直接进挑选
            return choose(shoot, click, ocr, header, regens=regens, log=log, frames=frames)
        click(*CORNER)
        time.sleep(TICK)
    return {"ok": False, "page": "timeout", "regens": regens, "why": f"{timeout:.0f}s 内没走到回忆页"}


def dots(img):
    """挑选页分页圆点（y=1050，x 300–420，以 360 为中心、间距约 26）→ (版本数, 当前序号)；没有圆点返回 (0, None)。
    当前版本是橙点（BGR 0,118,255），其余灰点（203,203,201）。实机 2 / 3 个版本的帧量过。"""
    row = img[DOT_Y, DOT_X1:DOT_X2].astype(int)
    on = (255 * 3 - row.sum(axis=1)) > 60  # 不是白底
    runs, start = [], None
    for x, v in enumerate(list(on) + [False]):
        if v and start is None:
            start = x
        elif not v and start is not None:
            runs.append((start, x - 1))
            start = None
    current = None
    for i, (x1, x2) in enumerate(runs):
        b, g, r = row[(x1 + x2) // 2]
        if r > 200 and b < 80:
            current = i
    return len(runs), current


def _wait_dot(shoot, want, wait=2.0):
    deadline = time.monotonic() + wait
    while True:
        img = shoot()
        n, cur = dots(img)
        if cur == want or time.monotonic() >= deadline:
            return img, n, cur
        time.sleep(0.08)


def version_label(i):
    return "再生成前" if i == 0 else f"再生成{i}回目"


def _frames_dir(frames):
    if frames == "auto":
        return FRAMES_ROOT / time.strftime("regen-%Y%m%d-%H%M%S")
    return Path(frames) if frames else None


def _save(folder, name, img):
    """存一帧；存不了只记日志，不影响挑选。返回路径字符串或 None。"""
    if folder is None:
        return None
    try:
        import cv2

        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        return str(path) if cv2.imwrite(str(path), img) else None
    except Exception:  # noqa: BLE001
        return None


def choose(shoot, click, ocr, header, *, regens=0, log=print, frames=None):
    folder = _frames_dir(frames)
    img = shoot()
    n, cur = dots(img)
    if n == 0 or cur is None:
        title = "".join(crop_texts(ocr, img, TITLE_CROP))
        if "選択" not in title:
            # 没再生成过（或游戏没给挑选页）：只有一份，交给后面的「次へ」
            return {"ok": True, "page": "memory_single", "regens": regens, "versions": [], "pick": None, "why": "没有挑选页"}
        return {"ok": False, "page": "memory_choice", "regens": regens, "versions": [], "pick": None, "why": "挑选页没认出分页圆点"}
    # 翻到最左（第 0 个），每翻一下按圆点核对
    while cur:
        click(*PREV_TAP)
        img, n, now = _wait_dot(shoot, cur - 1)
        if now != cur - 1:
            return {"ok": False, "page": "memory_choice", "regens": regens, "versions": [], "pick": None, "why": "往左翻页后圆点没变"}
        cur = now
    versions = []
    for i in range(n):
        if i:
            click(*NEXT_TAP)
            img, _, now = _wait_dot(shoot, i)
            if now != i:
                return {"ok": False, "page": "memory_choice", "regens": regens, "versions": versions, "pick": None,
                        "why": f"翻到第 {i} 个版本时圆点没变"}
            time.sleep(0.2)  # 卡面淡入
            img = shoot()
        texts = crop_texts(ocr, img, CARD_CROP)
        ev = batch_memory.evaluate(texts)
        # card 只报优先表里的强化版卡名；name 是实际读到的卡名，texts 是原始 OCR，frame 是这一版的整帧（对帧核对用）
        rec = {"label": version_label(i), **ev, "name": batch_memory.card_name(texts), "texts": texts,
               "frame": _save(folder, f"v{i}.png", img)}
        versions.append(rec)
        log(f"版本 {version_label(i)}：hit={ev['hit']} card={ev['card']} name={rec['name']} frame={rec['frame']} texts={texts}")
    if n != regens + 1:
        # 版本数（分页圆点）是游戏给的真值；点击计数对不上只记日志，返回值按圆点改正
        log(f"再生成计数 {regens} 与版本数 {n} 对不上，按圆点记为再生成 {n - 1} 次")
        regens = n - 1
    best = min(range(n), key=lambda i: (rank(versions[i]), i))
    if versions[best]["hit"] is None:
        import batch_rules

        if batch_rules.get("memory.no_hit", "first") != "first":
            return {"ok": False, "page": "memory_choice", "regens": regens, "versions": versions, "pick": None,
                    "why": "所有版本都没命中优先项，交代理挑"}
        best = 0  # 使用者 2026-11-07：都没有优先项就选第一个（再生成前），不唤起代理
    for i in range(n - 2, best - 1, -1):  # 现在在最右：往左翻到目标
        click(*PREV_TAP)
        img, _, now = _wait_dot(shoot, i)
        if now != i:
            return {"ok": False, "page": "memory_choice", "regens": regens, "versions": versions, "pick": best,
                    "why": f"往回翻到第 {best} 个版本时圆点不对"}
    return decide(shoot, click, ocr, header, versions, best, regens=regens)


def pick(shoot, click, ocr, header, index, *, regens=0):
    """代理指定版本序号（0 = 再生成前）：翻过去后走同一个确认流程。"""
    img = shoot()
    n, cur = dots(img)
    if cur is None or not 0 <= index < n:
        return {"ok": False, "page": "memory_choice", "why": f"圆点 {n} 个、当前 {cur}，选不了第 {index} 个"}
    while cur != index:
        step = -1 if cur > index else 1
        click(*(PREV_TAP if step < 0 else NEXT_TAP))
        img, _, now = _wait_dot(shoot, cur + step)
        if now != cur + step:
            return {"ok": False, "page": "memory_choice", "why": "翻页后圆点没变"}
        cur = now
    versions = [{"label": version_label(i), "hit": None} for i in range(n)]
    return decide(shoot, click, ocr, header, versions, index, regens=regens)


def decide(shoot, click, ocr, header, versions, best, *, regens=0):
    """当前页就是要选的版本（已按分页圆点核对）：点「決定」→「メモリー確定確認」框（像素标题条）→「生成する」
    （标题条顶 + 292，实机 (485,1161)）→ 框关掉后点回忆页「次へ」(360,1160)。三步都不做 OCR。"""
    label = versions[best]["label"]
    click(*DECIDE_TAP)
    deadline = time.monotonic() + 4
    top = None
    while time.monotonic() < deadline and (top := header(shoot())) is None:
        time.sleep(0.08)
    if top is None:
        return {"ok": False, "page": "memory_choice", "regens": regens, "versions": versions, "pick": best, "why": "点「決定」后没出确认框"}
    # 框还在滑入时点会落空（实机第 2 局：框出来了「生成する」没点上）：标题条位置连续两帧不变再点；4s 内框没关就补点
    for _ in range(10):
        time.sleep(0.08)
        again = header(shoot())
        if again is not None and again[0] == top[0]:
            break
        top = again or top
    for attempt in range(2):
        click(FINAL_X, top[0] + FINAL_DY)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and header(shoot()) is not None:
            time.sleep(0.08)
        if header(shoot()) is None:
            break
    wait_stable(shoot, BOTTOM_BAR, timeout=2.0)  # 框关掉后底部按钮栏停稳再点「次へ」（代替固定等 0.5s）
    click(*NEXT_TAP_PAGE)
    return {"ok": True, "page": "memory_decided", "regens": regens, "versions": versions, "pick": best,
            "why": f"选 {label}：{versions[best]['hit']}"}


def main(argv=None):
    """单独跑：python tools/batch_regen.py [--max-regen N]（从 LIVE 后任意一步开始，跑到挑选完成或停点）。"""
    import argparse
    import json
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "src"), str(root / "vendor" / "kaa")]
    from hiro.device import connect
    from hiro.produce.ops import ocr_boxes
    from hiro.vision.pages import dialog_header

    p = argparse.ArgumentParser(description=main.__doc__)
    p.add_argument("--max-regen", type=int, default=None)
    p.add_argument("--pick", type=int, default=None, help="在挑选页直接选第 N 个版本（0 = 再生成前），代理决定时用")
    args = p.parse_args(argv)
    device = connect("maatools")
    try:
        if args.pick is not None:
            out = pick(device.screenshot, device.click, ocr_boxes, dialog_header, args.pick)
        else:
            out = run(device.screenshot, device.click, ocr_boxes, dialog_header, max_regen=args.max_regen,
                      log=lambda m: print(m, file=sys.stderr, flush=True))
    finally:
        device.stop()
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
