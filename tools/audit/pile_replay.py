"""用 hiro.brain.piles（VEIL_PILE_ENGINE）回放 VEIL trace 的牌堆记账，和 trace 里原记账（track_piles）比：
每帧位置不明的张数、新记账的事件（暗抽 / 倒推山札已空 / 出局 / 对不上）。

用法：PYTHONPATH=src:vendor/kaa .venv/bin/python tools/audit/pile_replay.py [traces/batch-…-exam2 …]
不给参数跑账本核对对上的批次（同 pitem_replay）。回放不含回忆能力的抽牌（trace 里没有逐帧记），只按牌和饮料的效果登记应抽张数。"""
import glob, json, os, sys
from collections import Counter

sys.path.insert(0, "tools/audit")
from hiro.brain import card_fx, piles as P  # noqa: E402


def replay(trace):
    ev = glob.glob(trace + "/battle/*/events.jsonl")
    if not ev:
        return None
    rows = [json.loads(l) for l in open(ev[0])]
    data = {}
    old_unloc = new_unloc = frames = 0
    pending = None
    pending_lost = None
    for d in rows:
        if d["kind"] in ("exam_stale_frame", "battle_play_not_landed") or (
                d["kind"] == "veil_cycle_tap_end" and "没落地" in str(d.get("reason"))):
            pending = None
            continue
        if d["kind"] != "battle_decision":
            continue
        c = d["context"]
        book = {k: v for k, v in (c.get("deck") or {}).items()}
        if not book:
            continue
        for k, n in (data.get("veil_gone") or {}).items():  # 原 trace 的账本没拿掉新记账认定出局的牌
            book[k] = max(0, book.get(k, 0) - n)
        if pending is not None and data.get("veil_piles") is not None:
            kind, slot, name, fx_draw, lost = pending
            piles = data["veil_piles"]
            if kind == "play" and 0 <= slot < 5 and piles["hand"][slot] == name:
                piles["hand"][slot] = None
                pending_lost = name if lost else None
                if not lost:
                    piles["grave"][name] = piles["grave"].get(name, 0) + 1
                P.expect(piles, P.Moves(draw=fx_draw))
            elif kind == "drink":
                P.expect(piles, P.moves_of_drink(name))
        pending = None
        if data.get("veil_piles") is not None and "veil_deck" in data:
            # 牌组账本在两帧之间的增减（不是这一手打出的一次性牌）：眠気生成（加进山札）、回忆能力删眠気（先捨札后山札）。
            # 实机里由 _commit / apply_ability 直接改牌堆，回放按账本差补上
            piles = data["veil_piles"]
            prev_book = Counter({k: v for k, v in data["veil_deck"].items() if v > 0})
            now_book = Counter({k: v for k, v in book.items() if v > 0})
            lost_play = Counter([pending_lost]) if pending_lost else Counter()
            for name, n in (now_book - prev_book).items():
                piles["deck"][name] = piles["deck"].get(name, 0) + n
            for name, n in ((prev_book - now_book) - lost_play).items():
                for k in ("grave", "deck", "unlocated"):
                    take = min(n, piles[k].get(name, 0))
                    piles[k][name] = piles[k].get(name, 0) - take
                    n -= take
        pending_lost = None
        data["veil_deck"] = {k: v for k, v in book.items()}
        slots = [None] * 5
        for h in c.get("hand", []):
            if 0 <= h[0] < 5:
                slots[h[0]] = h[1]
        hand = Counter(s for s in slots if s)
        outside = Counter({k: v for k, v in data["veil_deck"].items() if v > 0}) - hand
        v = P.observe(data, c["turn"], slots, outside)
        frames += 1
        old_unloc += bool((c.get("piles") or {}).get("unlocated"))
        new_unloc += bool(v["unlocated"])
        a = d["contract"]["action"]
        if a["kind"] == "PlayCard":
            h = next((h for h in c.get("hand", []) if h[0] == a["args"]["slot"]), None)
            if h:
                fx = card_fx.card_fx(h[2], h[3] or 0) if h[2] else None
                lost = bool(fx and "Lost" in str(fx.get("move"))) or h[1] == "眠気"
                if h[1].rstrip("+") == "巻き返し":
                    lost = False
                pending = ("play", a["args"]["slot"], h[1], int((fx or {}).get("draws") or 0), lost)
        elif a["kind"] == "UseDrink":
            dr = c.get("drinks") or []
            if 0 <= a["args"]["slot"] < len(dr):
                pending = ("drink", None, dr[a["args"]["slot"]], 0, False)
    events = Counter(e["event"] for e in data.get("veil_pile_log", []))
    return frames, old_unloc, new_unloc, dict(events), data.get("veil_gone")


def main(args):
    if args:
        traces = args
    else:
        import pitem_replay as R  # noqa: F401  复用「账本对上的批次」

        keep = R.clean_batches()
        traces = [t for t in sorted(glob.glob("traces/batch-codex-*-exam*"))
                  if any(os.path.basename(t).startswith(f"batch-{b}-") for b in keep)]
    for t in traces:
        r = replay(t)
        if r:
            frames, old, new, events, gone = r
            print(f"{os.path.basename(t)[6:]}: {frames} 帧，位置不明 原 {old} 帧 → 新 {new} 帧；事件 {events}；出局 {gone}")


if __name__ == "__main__":
    main(sys.argv[1:])
