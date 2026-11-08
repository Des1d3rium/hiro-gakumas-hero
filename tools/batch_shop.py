"""相談 / 轮间商店买饮料、刷新货架，饮料栏丢饮料：给 batch_hif 的 shop-* / drink-* 命令用。

都复用仓库里按固定像素写好的执行器（hiro.produce.shop / interval / drink_swap）：
买 = 点货架格 → 交換する → 交換確認核对商品 → 交換 → 获得动画按固定位置连点跳过 → 回到主画面且 交換済 / P 减少；
刷新 = リフレッシュ → 确认框（要ジュエル就キャンセル）→ 货架换了；
丢 = 饮料栏第 N 格 → Pドリンク詳細 → 捨てる → 廃棄確認「はい」→ 非空格数减 1（剩下的往左挤）。
"""

from __future__ import annotations

import time

from batch_ledger import norm

HIGH_VALUE = ("ブーストエキス", "センブリソーダ", "初星ホエイプロテイン")
FAST_SKIP = 0.4  # 获得动画的跳过点击间隔（执行器默认 1s）


def high_value(name: str | None) -> bool:
    import batch_rules

    wanted = batch_rules.get("shop.high_value", list(HIGH_VALUE))
    return bool(name) and any(norm(name).startswith(norm(h)) for h in wanted)


def _page(device):
    from hiro.produce.interval import on_interval
    from hiro.produce.ops import ocr_boxes
    from hiro.produce.shop import consult_idle

    img = device.screenshot()
    # 只识别左上角标题这一小块（约 40ms，整帧约 0.5s）
    if on_interval(img, lambda im: [(t, sc, (x1, y1, x2, y2)) for t, sc, (x1, y1, x2, y2) in ocr_boxes(im[:100, :260])]):
        return "interval", img
    if consult_idle(img):
        return "consult", img
    return None, img


def _shelf(kind, img) -> tuple[dict, list[dict]]:
    from hiro.vision.shop import read_consult, read_interval_shelf

    if kind == "interval":
        fields = read_interval_shelf(img)
        drinks = (fields.get("interval") or {}).get("drinks") or []
    else:
        fields = read_consult(img)
        drinks = [s for s in fields["shelf"] if s.get("kind") == "drink" or s.get("drink_id")]
    return fields, drinks


def _view(item: dict) -> dict:
    return {"index": item.get("index"), "name": item.get("name"), "price": item.get("price"),
            "purchased": item.get("purchased"), "high_value": high_value(item.get("name"))}  # fmt: skip


def scan(device) -> dict:
    from hiro.produce.ops import ocr_boxes
    from hiro.produce.shop import read_p

    kind, img = _page(device)
    if kind is None:
        return {"ok": False, "why": "不在相談 / 轮间主画面"}
    fields, drinks = _shelf(kind, img)
    return {"ok": True, "page": kind, "p": read_p(img, ocr_boxes), "drinks": [_view(d) for d in drinks],
            "buy": [d["index"] for d in drinks if high_value(d.get("name")) and not d.get("purchased")],
            "complete": fields.get("complete")}  # fmt: skip


def _executor(kind):
    from hiro.contracts import ActionKind
    from hiro.produce import interval, shop

    shop.SKIP_EVERY = interval.SKIP_EVERY = FAST_SKIP
    if kind == "interval":
        return interval.execute_interval, ActionKind.INTERVAL_BUY
    return shop.execute_shop, ActionKind.SHOP_BUY


def buy(device, index: int) -> dict:
    from hiro.contracts import Action

    kind, img = _page(device)
    if kind is None:
        return {"ok": False, "why": "不在相談 / 轮间主画面"}
    fields, drinks = _shelf(kind, img)
    item = next((d for d in drinks if d.get("index") == index), None)
    if item is None:
        return {"ok": False, "why": f"第 {index} 格不是认出来的饮料，不买"}
    if item.get("purchased"):
        return {"ok": False, "why": f"第 {index} 格已经换过"}
    run, act = _executor(kind)
    res = run(Action(act, {"index": index}), fields, shoot=device.screenshot, click=device.click)
    return {"ok": res.ok, "page": kind, "drink": item.get("name"), "why": res.error, "seconds": res.seconds}


def refresh(device) -> dict:
    from hiro.contracts import Action, ActionKind

    kind, img = _page(device)
    if kind is None:
        return {"ok": False, "why": "不在相談 / 轮间主画面"}
    fields, _ = _shelf(kind, img)
    run, _ = _executor(kind)
    res = run(Action(ActionKind.REROLL, {"scope": kind}), fields, shoot=device.screenshot, click=device.click)
    if not res.ok:
        return {"ok": False, "why": res.error}
    time.sleep(0.3)
    return {**scan(device), "refreshed": True}


def read_bar(device) -> list[dict]:
    from hiro.vision.items import read_drink_slots

    return read_drink_slots(device.screenshot())


def drop(device, slot: int) -> dict:
    """丢饮料栏第 slot 格（0 起），按固定像素：格子 → 詳細 → 捨てる → はい。"""
    from hiro.contracts import Action, ActionKind
    from hiro.produce.drink_swap import discard_owned
    from hiro.produce.ops import Run

    slots = read_bar(device)
    if slots[slot]["state"] == "empty":
        return {"ok": False, "why": f"饮料栏第 {slot + 1} 格是空的"}
    name = slots[slot].get("name")
    r = Run(Action(ActionKind.CONTINUE, {"drink_discard": slot}), None, device.screenshot, device.click, time.monotonic, time.sleep,
            label="丢饮料", calib=True)  # fmt: skip
    why = discard_owned(r, slot, slots)
    return {"ok": why is None, "slot": slot, "drink": name, "why": why}


def overflow(device, discard: int) -> dict:
    """饮料溢出页（所持上限）：丢已有的第 discard 瓶，收下新的。返回新饮料名供记账。"""
    from hiro.produce.drink_swap import resolve_overflow
    from hiro.vision.items import read_pdrink_max

    page = read_pdrink_max(device.screenshot())
    if page is None:
        return {"ok": False, "why": "不在饮料溢出页"}
    incoming = [d.get("name") for d in (page.get("incomings") or ([page["incoming"]] if page.get("incoming") else []))]
    held = page.get("held") or []
    gone = next((h.get("name") for h in held if h.get("index") == discard), None)
    res = resolve_overflow(discard, held=held, incoming=page.get("incoming"), shoot=device.screenshot, click=device.click)
    return {"ok": res.ok, "dropped": gone, "incoming": incoming, "why": res.error}


REFRESH_PRICES = (10, 10, 20, 30, 40, 50)  # 刷新价格按次数固定上涨，50 封顶（使用者确认）


def next_refresh_price(paid: int, tens: int) -> int:
    """刚花 paid 刷新了一次之后，下一次的价格。tens：到目前为止已经付过几次 10（10 出现两次，要靠它区分）。"""
    if paid == 10:
        return 10 if tens < 2 else 20
    return min(50, paid + 10)


def _refresh_price(fields: dict) -> int | None:
    ref = ((fields.get("interval") or {}).get("refresh") if "interval" in fields else fields.get("refresh")) or {}
    return ref.get("price") if isinstance(ref, dict) else None


def auto(device, *, free_slots, on_buy, max_refresh: int = 10, make_room=None, reserve_p: int = 0) -> dict:
    """一条命令做完补给：读货架 → 买没买过的高价值饮料 → P 够就刷新 → 再读，直到饮料栏满 / P 不够刷新 / 刷新到上限。
    同一进程里循环：识别图库只在第一次加载（约 2s），也省掉代理每步之间的往返。
    free_slots()：账本还剩几个空格；on_buy(name)：买成功后记账。任何一步失败就停下，带上已做的步骤。
    make_room()：栏满、货架上又有要买的高价值饮料时调用，丢一瓶低价值的腾格子；返回丢掉的名字，丢不了返回 None。"""
    from hiro.produce.ops import ocr_boxes
    from hiro.produce.shop import read_p

    if type(reserve_p) is not int or reserve_p < 0:
        raise ValueError("reserve_p 必须是非负整数")
    steps, refreshes, expect, predicted, tens = [], 0, None, None, 0  # expect：按价格记账算出的 P（核对画面读数用）
    while True:
        kind, img = _page(device)
        if kind is None:
            return {"ok": False, "why": "不在相談 / 轮间主画面", "steps": steps}
        fields, drinks = _shelf(kind, img)
        seen = read_p(img, ocr_boxes)  # 数字读取器读左上一小块，约 30ms
        if expect is not None and seen is not None and seen != expect:
            steps.append({"op": "p_mismatch", "expected": expect, "screen": seen})  # 以画面为准（价格变了 / 读错）
        p = seen if seen is not None else expect
        want = [d for d in drinks if high_value(d.get("name")) and d.get("purchased") is False
                and d.get("available", True) is True and d.get("index") is not None]
        budget = min(seen, expect) if seen is not None and expect is not None else seen
        item = next((d for d in want if d.get("price") is not None and budget is not None and 0 <= d["price"] <= budget - reserve_p), None)
        if item is not None:
            if free_slots() <= 0 and make_room is not None:
                dropped = make_room()
                steps.append({"op": "drop_low", "drink": dropped, "for": item.get("name")})
                if dropped:
                    time.sleep(0.3)
            if free_slots() <= 0:
                return {"ok": True, "stop": "饮料栏已满", "page": kind, "steps": steps, "p": p}
            res = buy(device, item["index"])
            steps.append({"op": "buy", "index": item["index"], "drink": item.get("name"), "ok": res["ok"], "why": res.get("why")})
            if not res["ok"]:
                return {"ok": False, "why": f"买第 {item['index']} 格失败：{res.get('why')}", "steps": steps}
            on_buy(res["drink"] or item.get("name"))
            expect = budget - item["price"]
            time.sleep(0.3)
            continue  # 购买前重新核对货架和余额
        if want:
            return {"ok": True, "stop": "无法确认目标饮料买得起", "page": kind, "steps": steps, "p": p}
        read = _refresh_price(fields)
        if predicted is not None and read is not None and read != predicted:
            steps.append({"op": "refresh_price_mismatch", "expected": predicted, "screen": read})
        price = read if read is not None else predicted  # 读不到就用价目表推出来的
        if free_slots() <= 0:
            return {"ok": True, "stop": "饮料栏已满", "page": kind, "steps": steps, "p": p}
        if price is None or p is None or p - reserve_p < price:
            return {"ok": True, "stop": f"P 不够刷新（P {p}，刷新 {price}）", "page": kind, "steps": steps, "p": p}
        if refreshes >= max_refresh:
            return {"ok": True, "stop": f"已刷新 {refreshes} 次（上限）", "page": kind, "steps": steps, "p": p}
        run, _ = _executor(kind)
        from hiro.contracts import Action, ActionKind

        res = run(Action(ActionKind.REROLL, {"scope": kind}), fields, shoot=device.screenshot, click=device.click)
        refreshes += 1
        steps.append({"op": "refresh", "price": price, "p_before": p, "ok": res.ok, "why": res.error})
        expect = p - price
        tens += price == 10
        predicted = next_refresh_price(price, tens)
        if not res.ok:
            return {"ok": False, "why": f"刷新失败：{res.error}", "steps": steps}
        time.sleep(0.3)
