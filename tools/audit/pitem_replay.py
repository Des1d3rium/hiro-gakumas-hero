"""用 hiro.brain.pitems 的通用计数器回放 VEIL trace（夜空を従える花+），每个决策帧和画面道具旁「N回」比对。

用法：PYTHONPATH=src:vendor/kaa .venv/bin/python tools/audit/pitem_replay.py [--all | traces/batch-…-exam1 …]
不给参数只跑账本核对全部对上的批次（ledger_check）；--all 连早期批次一起跑。
客制化取批次账本里的 customs；账本记错客制化（例如漏了 インフルエンサー+ 強気追加）时会整段对不上。"""
import glob, json, os, re, sys
from collections import Counter
import cv2
from hiro.brain import pitems as P
from hiro.brain.veil import flower_count

ITEM = P.load_pitem("pitem_03-3-303-1")


def customs_for(trace):
    m = re.match(r"traces/batch-(.+)-(\d+)-exam\d", trace)
    if not m:
        return {}
    path = f"reports/batches/{m.group(1)}.json"
    if not os.path.exists(path):
        return {}
    b = json.load(open(path))
    run = int(m.group(2))
    led = None
    for r in b.get("completed", []):
        if r.get("run") == run:
            led = r.get("ledger")
    if led is None and (b.get("active") or {}).get("run") == run:
        led = b["active"].get("ledger")
    out = {}
    for c in (led or {}).get("deck", []):
        if c.get("customs"):
            out.setdefault(c["name"], c["customs"])
    return out


def _stance_after(stance, name):
    """连打中间没有决策帧：按这张牌的结构化效果推打出后的姿态（只用来判下一张打出前「有没有指针」）。"""
    from hiro.brain import card_fx
    from hiro.state.catalog import catalog

    r = catalog().db.execute("SELECT id, upgradeCount FROM ProduceCard WHERE name = ? LIMIT 1", (name,)).fetchone()
    fx = card_fx.card_fx(r[0], r[1]) if r else None
    if fx and fx.get("stance") and stance != "全力":  # 全力 下切不了姿态
        return fx["stance"]
    return stance


def replay(trace):
    """决策帧打出的那张按手牌（卡 id / 档位）算；循环连打（VEIL_CYCLE_TAP）一次点出、中间不写决策帧的几张，
    从同一回合下一帧 context 的 played（本回合已打出，实机记账）比上一帧多出的部分补上。
    连打的牌打出前的姿态按前一张的效果推。换回合时 played 清空，只算决策帧那一张（回合末连打不会跨回合）。"""
    ev = glob.glob(trace + "/battle/*/events.jsonl")
    if not ev:
        return None
    base = os.path.dirname(ev[0])
    customs = customs_for(trace)
    ctr = P.play_counter(ITEM)
    rows = [json.loads(l) for l in open(ev[0])]
    stats = Counter(); bad = []
    pending = None  # (profile, stance, name)：上一个决策帧打出的牌
    prev = None  # 上一个决策帧的 (turn, played)
    snapshots = []
    for d in rows:
        k = d["kind"]
        if k in ("exam_stale_frame", "battle_play_not_landed") and pending is not None:
            pending = None
            continue
        if k == "veil_cycle_tap_end" and "没落地" in str(d.get("reason")) and pending is not None:
            pending = None  # 连打第一张就没落地（画面未变化）：这一手没打出，下一帧会重打
            continue
        if k == "veil_play_undone":
            if pending is not None:
                pending = None  # 没落地的那一手还没记
            elif snapshots:
                ctr.played, ctr.unknown = snapshots.pop()
            continue
        if k != "battle_decision":
            continue
        c = d.get("context") or {}
        turn, played = c.get("turn"), c.get("played")
        extra = []
        if prev is not None and prev[0] == turn and played is not None and prev[1] is not None:
            extra = list(played[len(prev[1]) + (pending is not None):])
        if pending is not None:
            prof, stance, name = pending
            snapshots.append((ctr.played, ctr.unknown))
            fired = ctr.play(prof, stance)
            for nm in extra:
                stance = _stance_after(stance, name)
                if fired:
                    out = P.flower_trigger_outcome(stance)
                    stance = out.stance if out and out.stance else stance
                snapshots.append((ctr.played, ctr.unknown))
                fired = ctr.play(P.card_profile_by_name(nm, tuple(customs.get(nm, ()))), stance)
                name = nm
            pending = None
        frame = d.get("frame")
        img = cv2.imread(os.path.join(base, frame)) if frame else None
        seen = flower_count(img)
        want = ctr.remaining
        if seen is not None and want is not None:
            stats["ok" if seen == want else "bad"] += 1
            if seen != want:
                bad.append((d["seq"], turn, want, seen))
        else:
            stats["unread" if seen is None else "unknown"] += 1
        prev = (turn, played)
        a = d["contract"]["action"]
        if a["kind"] == "PlayCard":
            slot = a["args"]["slot"]
            h = next((h for h in c.get("hand", []) if h[0] == slot), None)
            if h:
                name, cid, up = h[1], h[2], h[3] or 0
                prof = P.card_profile(cid, up, tuple(customs.get(name.rstrip("+") + "+", customs.get(name, ())))) if cid else None
                st = c.get("stance") if c.get("turn") != 1 or c.get("stance") else None
                pending = (prof, st, name)
    return stats, bad


def clean_batches() -> set[str]:
    """账本经得起主数据核对（tools/audit/ledger_check.py 全部对上）的批次。早期批次账本客制化 / 一次性记错，
    回放会整段对不上，和计数器本身对错无关（使用者 2026-11-09：「早期账本的问题很大，优先和可以记上的对比」）。"""
    sys.path.insert(0, "tools/audit")
    import ledger_check as LC

    out = set()
    for f in sorted(glob.glob("reports/batches/codex-*.json")):
        try:
            groups = LC.ledger_cards(json.load(open(f)))
        except Exception:  # noqa: BLE001
            continue
        if groups and not any(LC.check_cards(cards) for _, cards in groups):
            out.add(os.path.basename(f)[:-5])
    return out


def main(args):
    if args and args[0] != "--all":
        traces = args
    else:
        traces = sorted(glob.glob("traces/batch-codex-*-exam*"))
        if not args:
            keep = clean_batches()
            traces = [t for t in traces if (m := re.match(r"traces/batch-(.+)-\d+-exam\d", t)) and m.group(1) in keep]
            print("账本核对全部对上的批次：" + "、".join(sorted(keep)) + "（加 --all 连早期批次一起跑）")
    for t in traces:
        r = replay(t)
        if r:
            s, bad = r
            print(os.path.basename(t)[6:], dict(s), bad[:4])


if __name__ == "__main__":
    main(sys.argv[1:])
