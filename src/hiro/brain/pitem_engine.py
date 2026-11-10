"""账本侧的附魔驱动：持有哪些 P 道具、打出的牌装上了哪些附魔，在账本推演的各个时点交给通用附魔引擎结算。

判触发 / 条件 / 效果 / 次数全在 hiro.brain.play.enchants（推演 sim.TurnState 用的同一套，流派资源登记在
hiro.brain.play.anomaly）；本模块只管：
  · 装哪些（observe）：
    - 画面道具列（hiro.vision.items.read_pitems，asset 形如 img_general_pitem_3-258）→ ProduceItem.assetId 相同的 id。
      同一 asset 常有好几个 id（ゼミパーカー 有 10 个、7 种不同效果）：结构（触发器 + 效果 + 次数）全一样才装；
      不一样就看偶像档案 pitem_assets 有没有写明是哪个 id，没写就不装、记进 unsure。
    - 偶像专属道具（夜空を従える花+）画面道具列里没有：按偶像卡 ProduceItem.originIdolCardId 找强化版（ring_item）。
      它的「每 N 张」计数就是 VEIL 账本的夜空计数（veil_position.flower），由 sim.after_play 结算，这里不重复装它。
    - 打出的牌装上的「以降…」附魔（一番星除外：它由账本 veil_star 记次数、sim.start_turn 结算）。
  · 什么时候发事件（after_play / end_turn / start_turn，接在 veil_exam 的记账时点）；姿态变了再发一层姿态变化事件。
  · 结果怎么落到账本：只落姿态 / 段階 / 下回合姿态 / 追加出牌次数；有判断不了、又可能切姿态的附魔时不动，
    调用方照原来的手写规则走。元気 / 全力値 / 抽牌 / 熱意 / 生成牌等只记日志。
  · 状态存在 memory.strategy_data["veil_pitems"]（JSON，随记忆快照撤回）。

开关 VEIL_ENCHANT_RUNTIME（默认关，和推演用的是同一个开关）。关着时本模块不被调用，账本和原来逐位一致。

实机核对（2026-11-09）：VEIL 带的 ゼミパーカー 是「センスのスキルカード使用後、パラメータ上昇量増加10%」那一版
（research-event-008..010，偶像档案 pitem_assets 写明），不是「温存に変更」那一版（pitem_00-3-258-0）；
Common 牌不算「某计划的牌」见 enchants.COMMON_PLAN_MATCHES。
"""

from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache

from hiro.brain import pitems as P

KEY = "veil_pitems"  # memory.strategy_data 里的状态（JSON，可随记忆快照撤回）
FLAG = "ENCHANT_RUNTIME"


def _eng():
    from hiro.brain.play import anomaly  # noqa: F401  登记アノマリー的条件和效果
    from hiro.brain.play import enchants

    return enchants


def enabled() -> bool:
    from hiro.brain.play.profile import enchant_runtime

    return enchant_runtime()


def _db():
    return P._db()


# ---------------------------------------------------------------- 持有哪些


def _asset_id(asset: str) -> str:
    asset = (asset or "").strip()
    return asset if asset.startswith("img_general_") else f"img_general_{asset}"


@lru_cache(maxsize=None)
def _item_row(item_id: str) -> dict | None:
    return P._row("ProduceItem", item_id)


@lru_cache(maxsize=None)
def signature(item_id: str) -> str:
    """道具在考试里的结构：fireLimit / fireInterval + 每个附魔的（触发器、效果、次数、回合）。同 asset 的几个 id 比这个。"""
    item = P.load_pitem(item_id)
    if item is None:
        return ""
    return json.dumps([item.fire_limit, item.fire_interval,
                       [[e.trigger.id if e.trigger else None, [x.id for x in e.effects], e.limit or 0, e.turns]
                        for e in item.enchants]])


def candidates(asset: str) -> list[str]:
    return [r[0] for r in _db().execute("SELECT id FROM ProduceItem WHERE assetId = ? ORDER BY id", (_asset_id(asset),))]


def resolve(asset: str, hints: dict | None = None) -> tuple[str | None, dict]:
    """画面 asset → ProduceItem id。返回 (id 或 None, 说明)。几个 id 结构不同又没有提示时 None（不装）。"""
    ids = candidates(asset)
    info = {"asset": _asset_id(asset), "candidates": len(ids)}
    if not ids:
        return None, {**info, "why": "主数据里没有这个 asset"}
    hint = (hints or {}).get(_asset_id(asset)) or (hints or {}).get(asset)
    if hint:
        if hint in ids:
            return hint, {**info, "by": "hint"}
        return None, {**info, "why": f"偶像档案写的 {hint} 不是这个 asset 的道具"}
    sigs = {signature(i) for i in ids}
    if len(sigs) == 1:
        return ids[0], {**info, "by": "same"}
    return None, {**info, "signatures": len(sigs), "why": "同一图标有几种效果不同的道具，认不出是哪个"}


@lru_cache(maxsize=None)
def ring_item(idol: str) -> str | None:
    """偶像专属 P 道具（强化版）里带「每 N 张」计数的那个：VEIL 的夜空を従える花+。idol 可以是 skin id 或偶像卡 id。"""
    card = idol
    r = _db().execute("SELECT idolCardId FROM IdolCardSkin WHERE id = ?", (idol,)).fetchone()
    if r is not None and r[0]:
        card = r[0]
    rows = _db().execute("SELECT id FROM ProduceItem WHERE originIdolCardId = ? ORDER BY isUpgraded DESC, id", (card,)).fetchall()
    for (item_id,) in rows:
        item = P.load_pitem(item_id)
        if item is not None and item.play_counters:
            return item_id
    return None


@lru_cache(maxsize=None)
def _idol_items(ring: str) -> frozenset[str]:
    r = _item_row(ring) or {}
    rows = _db().execute("SELECT id FROM ProduceItem WHERE originIdolCardId = ?", (r.get("originIdolCardId") or "",)).fetchall()
    return frozenset(x[0] for x in rows)


def _new_state(idol: str | None) -> dict:
    return {"items": [], "cards": [], "ring": None, "unsure": [], "unsupported": [], "assets": [], "insts": {},
            "names": {}, "boundary": None, "ended": None, "next_stance": None, "next_stance_turn": None, "log": [],
            "idol": idol}


def _install(st: dict, item_id: str, source: str) -> None:
    if any(e["item"] == item_id for e in st["items"]):
        return
    item = P.load_pitem(item_id)
    if item is None:
        st["unsure"].append({"item": item_id, "why": "主数据查不到"})
        return
    st["items"].append({"item": item_id, "name": item.name, "source": source})
    eng = _eng()
    for spec in eng.pitem_specs(item_id):
        st["names"][spec.key] = item.name
        if bad := eng.unsupported_phases(spec):
            st["unsupported"].append({"key": spec.key, "name": item.name, "why": f"触发时机不支持：{bad}"})
    for i, en in enumerate(item.enchants):
        if en.trigger is None:
            st["unsupported"].append({"key": f"{item_id}#{i}", "name": item.name, "why": "没有触发器"})


def observe(data: dict, pitems_raw: list | None, idol: str, hints: dict | None = None) -> dict:
    """每帧调用：第一次装偶像专属道具（ring）+ 画面道具列；之后画面多读到的道具补装（记 late）。返回状态。"""
    st = data.get(KEY)
    if st is None:
        st = data[KEY] = _new_state(idol)
        try:
            st["ring"] = ring_item(idol)
        except Exception:  # noqa: BLE001
            st["ring"] = None
    seen = set(st["assets"])
    for p in pitems_raw or []:
        asset = _asset_id(p.get("asset") or "")
        if not p.get("asset") or asset in seen:
            continue
        seen.add(asset)
        st["assets"].append(asset)
        try:
            item_id, info = resolve(asset, hints)
        except Exception as e:  # noqa: BLE001
            item_id, info = None, {"asset": asset, "why": f"主数据不可用：{e}"}
        if item_id is not None and st["ring"] and signature(item_id) == signature(st["ring"]):
            continue  # 偶像专属道具也出现在道具列：已经作为 ring 处理
        if item_id is None:
            if st["ring"] and all(i in _idol_items(st["ring"]) for i in candidates(asset)):
                continue  # 偶像专属道具的未强化 / 强化两版：ring 已管
            st["unsure"].append({**info, "name": p.get("name")})
            continue
        before = len(st["items"])
        _install(st, item_id, "screen")
        if len(st["items"]) > before and st.get("boundary") is not None:
            st["items"][-1]["late"] = True  # 考试中途才读到：之前的发动没记
    return st


# ---------------------------------------------------------------- 实例（JSON ↔ enchants.Inst）


def _specs(st: dict) -> list:
    eng = _eng()
    out = []
    for entry in st["items"]:
        out += eng.pitem_specs(entry["item"])
    for c in st.get("cards") or []:
        en = P.enchant(c["enchant"])
        if en is not None and en.trigger is not None:
            out.append(eng.Spec(c["key"], "card", en.trigger, en.effects, c.get("limit"), None, en.text))
    return out


def _insts(st: dict) -> tuple:
    eng = _eng()
    saved = st["insts"]
    out = []
    for spec in _specs(st):
        rec = saved.get(spec.key)
        out.append(eng.install(spec) if rec is None else
                   eng.Inst(spec, rec.get("left"), rec.get("count"), rec.get("until"), rec.get("fired", 0), rec.get("last")))
    return tuple(out)


def _save(st: dict, insts) -> None:
    st["insts"] = {i.spec.key: {"left": i.left, "count": i.count, "until": i.until, "fired": i.fired, "last": i.last}
                   for i in insts}


def _log(st: dict, turn, phase, key, what, **extra) -> None:
    st["log"].append({"turn": turn, "phase": phase.rsplit("_", 1)[-1], "key": key, "name": st["names"].get(key, key),
                      "what": what, **extra})
    del st["log"][:-200]


def _event(phase, turn, stance, level=None, *, current=None, card=None, state=None, fields=None):
    eng = _eng()
    return eng.Event(phase, turn=turn or 0, stance=stance, current=stance if current is None else current, card=card,
                     remaining_turns=getattr(state, "remaining_turns", None), fields=fields or {}, level=level)


def _fields(state, *, resources: bool, fp=None) -> dict:
    """判条件用的资源。resources=False（出牌后 / 回合交界）：元気 / 体力 / 全力値没有准确读数，不给。"""
    out = {"hp_max": getattr(state, "max_hp", None)}
    if resources:
        out.update(genki=getattr(state, "genki", None), hp=getattr(state, "hp", None), fp=fp)
    return {k: v for k, v in out.items() if v is not None}


def _dispatch(st: dict, events) -> list:
    """依次发事件，存回实例，返回 [(实例, Outcome | None)]。"""
    eng = _eng()
    insts = _insts(st)
    fired = []
    for ev in events:
        insts, got = eng.dispatch(insts, ev)
        for inst, out in got:
            key = inst.spec.key
            if out is None:
                _log(st, ev.turn, ev.phase, key, "判断不了")
            else:
                _log(st, ev.turn, ev.phase, key, "发动", stance=out.stance, next_stance=out.next_stance,
                     actions=out.delta.get("actions") or None, score=out.score or None, ledger=out.unknown or None,
                     left=inst.left)
        fired += got
    _save(st, insts)
    return fired


# ---------------------------------------------------------------- 落到账本


def _stance_level(before, before_level, dest, explicit=None):
    """同 veil.level_after（不 import veil，免得循环）。"""
    base = lambda s: "温存" if s and s.startswith("温存") else s  # noqa: E731
    if base(dest) not in ("強気", "温存"):
        return None
    if explicit in _eng().LEVELS:
        return explicit
    if dest.endswith("段階目"):
        return int(dest[-4])
    return 2 if base(before) == base(dest) else 1


def apply_stance(current, level, fired) -> tuple[str | None, int | None, bool]:
    """把发动结果里的姿态落到 (current, level)。同一时点几个附魔要切的姿态不同 → 姿态不明（None）。
    有判断不了、又可能切姿态的附魔 → 不动（调用方照原来的规则），第三项 changed=False。"""
    if any(out is None and inst.spec.may_change_stance for inst, out in fired):
        return current, level, False
    dests = [out for _, out in fired if out is not None and out.stance is not None]
    if not dests:
        return current, level, False
    if len({(o.stance, o.level) for o in dests}) > 1:
        return None, None, True
    o = dests[0]
    if current == "のんびり" and o.stance.startswith("温存"):
        return current, level, False  # のんびり 覆盖温存（veil.keeps_nonbiri）
    if current == "全力" and o.stance != "全力":
        return current, level, False
    return o.stance, _stance_level(current, level, o.stance, o.level), True


def _stance_phases(before: str | None, after: str | None) -> set[str] | None:
    """姿态从 before 变成 after 时的事件；判断不了（姿态不明、のんびり 算不算温存没核对）None。"""
    eng = _eng()
    if before == after:
        return set()
    if before is None or after is None:
        return None
    out = set()
    if after == "強気":
        out.add(eng.TO_STRONG)
    if after == "全力":
        out.add(eng.TO_FULL)
    if after in ("温存", "温存1段階目", "温存2段階目"):
        out.add(eng.TO_WARM)
    if after == "のんびり":
        return None
    if before == "全力":
        out.add(eng.FROM_FULL)
    return out


def _watches(st: dict, phases) -> bool:
    return any(p in phases for spec in _specs(st) for p, _ in spec.trigger.phases)


def stance_changed(st, state, before, after, level, turn) -> tuple[str | None, int | None]:
    """姿态变化类附魔（变成強気 / 全力 / 温存、离开全力）。只结算一层，不再连锁。"""
    eng = _eng()
    phases = _stance_phases(before, after)
    if phases is None:
        if _watches(st, eng.STANCE_EVENTS):
            _log(st, turn, "StanceChange", None, "姿态变化判断不了", before=before, after=after)
        return after, level
    if not phases:
        return after, level
    fields = _fields(state, resources=False)
    fired = _dispatch(st, [_event(p, turn, after, level, state=state, fields=fields) for p in sorted(phases)])
    new, new_level, changed = apply_stance(after, level, fired)
    return (new, new_level) if changed else (after, level)


def _card_enchants(st: dict, card, turn) -> None:
    """打出的牌装上的附魔（「以降…」）进账本；一番星由 veil_star 记，不装。"""
    from hiro.brain.play.profile import STAR

    try:
        found = P.card_enchants(card)
    except Exception:  # noqa: BLE001  没有主数据
        return
    name = getattr(card, "name", "") or ""
    if STAR and name.startswith(STAR):
        return
    eng = _eng()
    for en, count in found:
        if en.trigger is None:
            continue
        key = f"{name}@T{turn}:{en.id}#{len(st['cards'])}"  # 同名牌重复打出各装一份
        st["cards"].append({"key": key, "enchant": en.id, "limit": count, "turn": turn})
        st["names"][key] = name
        st["insts"][key] = {"left": count, "count": 0, "until": None, "fired": 0, "last": None}
        spec = eng.Spec(key, "card", en.trigger, en.effects, count)
        if bad := eng.unsupported_phases(spec):
            st["unsupported"].append({"key": key, "name": name, "why": f"触发时机不支持：{bad}"})


def after_play(data: dict, memory, state, card, before: str | None, after: str | None, level: int | None,
               *, card_dest: str | None = None) -> tuple[str | None, int | None]:
    """出牌记账钩子（veil_exam._commit_ledger）：before = 打出前姿态，after / level = 牌（和夜空）结算后的姿态。
    依次结算：出牌时（牌的效果之前；牌自己切姿态的话以牌为准）→ 出牌后 → 姿态变化，再装上这张牌自己的附魔。
    追加出牌次数加到 memory.actions_left。返回最终 (姿态, 段階)；什么都没触发时原样返回。"""
    st = data.get(KEY)
    if st is None:
        return after, level
    eng = _eng()
    turn = memory.turn
    pre = _dispatch(st, [_event(eng.CARD_PLAY, turn, before, card=card, state=state,
                                fields=_fields(state, resources=True, fp=data.get("veil_fp")))])
    if card_dest is None:
        new, new_level, changed = apply_stance(after, level, pre)
        if changed:
            after, level = new, new_level
    post = _dispatch(st, [_event(eng.CARD_PLAY_AFTER, turn, after, level, card=card, state=state,
                                 fields=_fields(state, resources=False))])
    final, final_level, changed = apply_stance(after, level, post)
    if changed:
        after, level = final, final_level
    after, level = stance_changed(st, state, before, after, level, turn)
    fired = [out for _, out in pre + post if out is not None]
    actions = sum(o.delta.get("actions", 0) for o in fired)
    if actions:
        memory.actions_left += actions
    nxt = [o.next_stance for o in fired if o.next_stance]
    if nxt:
        st["next_stance"] = nxt[-1] if len(set(nxt)) == 1 else None
        st["next_stance_turn"] = turn + 1
    _card_enchants(st, card, turn)
    return after, level


def end_turn(data: dict, memory, state, ended_turn: int | None, current: str | None, level: int | None):
    """回合结束（read_position 新回合分支，上回合的姿态 current）：回合结束 / 每 N 回合结束。"""
    st = data.get(KEY)
    if st is None or ended_turn is None:
        return current, level
    done = st.get("ended")
    if done is not None and done.get("turn") == ended_turn:
        # read_position 早退后重读：同一个回合结束只结算一次
        return (done["stance"], done["level"]) if done.get("input") == [current, level] else (current, level)
    eng = _eng()
    fired = _dispatch(st, [_event(eng.END_TURN, ended_turn, current, level, state=state, fields=_fields(state, resources=False))])
    new, new_level, changed = apply_stance(current, level, fired)
    if changed:
        new, new_level = stance_changed(st, state, current, new, new_level, ended_turn)
    else:
        new, new_level = current, level
    st["ended"] = {"turn": ended_turn, "input": [current, level], "stance": new, "level": new_level}
    return new, new_level


def start_turn(data: dict, memory, state, predicted: str | None, level: int | None):
    """回合开始（预约姿态、一番星之后）：第 1 回合先结算考试开始；再回合开始（抽牌前 / 后）、每 N 回合。
    返回 (姿态, 段階, changed)。同一回合重复调用（read_position 早退后重读）返回第一次的结果，不重复发动。"""
    st = data.get(KEY)
    turn = memory.turn
    if st is None:
        return predicted, level, False
    b = st.get("boundary")
    if b is not None and b.get("turn") == turn:
        return (b["stance"], b["level"], b["changed"]) if b.get("input") == [predicted, level] else (predicted, level, False)
    eng = _eng()
    changed = False
    cur, cur_level = predicted, level
    if st.get("next_stance") and st.get("next_stance_turn") == turn and cur is not None:
        if not (cur == "のんびり" and st["next_stance"].startswith("温存")) and cur != "全力":
            cur, cur_level, changed = st["next_stance"], _stance_level(cur, cur_level, st["next_stance"]), True
    st["next_stance"] = None
    _save(st, eng.new_turn(_insts(st)))  # 回合内计数清零
    phases = []
    if b is None and turn == 1:
        phases += [eng.START_EXAM, eng.START_EXAM_PLAY]  # 考试中途才装上（续跑）时不补发考试开始
    phases += [eng.START_TURN, eng.START_PLAY]
    fields = _fields(state, resources=False)
    fired = _dispatch(st, [_event(p, turn, cur, cur_level, state=state, fields=fields) for p in phases])
    new, new_level, ch = apply_stance(cur, cur_level, fired)
    if ch:
        new, new_level = stance_changed(st, state, cur, new, new_level, turn)
        cur, cur_level, changed = new, new_level, True
    st["boundary"] = {"turn": turn, "input": [predicted, level], "stance": cur, "level": cur_level, "changed": changed}
    return cur, cur_level, changed


def summary(data: dict) -> dict | None:
    """trace 用：装了什么、没装上什么、各附魔发动次数。"""
    st = data.get(KEY)
    if st is None:
        return None
    fired = {k: v["fired"] for k, v in st["insts"].items() if v.get("fired")}
    return {"items": [e["name"] for e in st["items"]], "ring": st.get("ring"),
            "cards": [c["key"] for c in st.get("cards") or []] or None,
            "unsure": [u.get("name") or u.get("asset") for u in st["unsure"]] or None,
            "unsupported": len(st["unsupported"]) or None, "fired": fired or None}


def profile_hints() -> dict:
    """偶像档案 pitem_assets：画面 asset → 实际持有的 ProduceItem id（同图标多版本时写明）。"""
    try:
        from hiro.brain.veil import PROFILE

        return dict(PROFILE.get("pitem_assets") or {})
    except Exception:  # noqa: BLE001
        return {}
