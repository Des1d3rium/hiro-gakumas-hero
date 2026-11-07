"""Screen adapter and single-round runner for the VEIL batch route."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from hiro.brain.battle import BattleBrain
from hiro.defaults.exam_state import ScreenExamStateSource
from hiro.loop.errors import ResumeBlocked
from hiro.brain.veil import (
    CORE, DRIVE, IDOL, REBOUND, SODA, STANCES, TEACH, WARM, VeilAdvisor,
    after_play, destination, flower_count, next_stance, norm, stance,
)


SLEEP_GENERATORS = {DRIVE, "フルスロットル", "フルスロットル+"}


def validate_deck(data):
    if not isinstance(data, dict) or not isinstance(data.get("evidence"), str) or not data["evidence"].strip():
        raise ValueError("牌组清单须记录证据路径")
    if not isinstance(data.get("cards"), list) or not all(isinstance(c, str) and c.strip() for c in data["cards"]):
        raise ValueError("牌组清单须列出完整卡名及强化，每张单列")
    names = Counter(norm(c) for c in data["cards"])
    if any(names[c] != 1 for c in CORE) or not isinstance(data.get("rebound_repeatable"), bool):
        raise ValueError("须记录四张核心及巻き返し+的一次性限制状态")
    once = data.get("once", [])
    if not isinstance(once, list) or not all(isinstance(c, str) and norm(c) in names for c in once):
        raise ValueError("once 只可列出清单中已核对的一次性卡")
    known_once = {norm(c) for c in once}
    if data["rebound_repeatable"]:
        known_once.discard(REBOUND)
    else:
        known_once.add(REBOUND)
    return dict(names), known_once


def read_deck(source):
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    return validate_deck(data)


def apply_deck_change(base, change):
    cards = [norm(c) for c in base["cards"]]
    once = {norm(c) for c in base.get("once", [])}
    if not isinstance(change, dict) or not isinstance(change.get("evidence"), str) or not change["evidence"].strip():
        raise ValueError("本局变卡记录须提供证据")
    removed, added = change.get("remove"), change.get("add")
    if removed is not None or added is not None:
        if not all(isinstance(c, str) and c.strip() for c in (removed, added)):
            raise ValueError("变卡须同时记录换出和换入")
        removed, added = norm(removed), norm(added)
        if removed not in cards or not isinstance(change.get("add_once"), bool):
            raise ValueError("换出牌须在基准牌组中，并须记录换入牌是否一次性")
        cards.remove(removed)
        if removed not in cards:
            once.discard(removed)
        if added in cards and (added in once) != change["add_once"]:
            raise ValueError("同名卡的一次性状态有歧义，请提交该轮完整清单")
        cards.append(added)
        if change["add_once"]:
            once.add(added)
    deck = {**base, "cards": cards, "once": sorted(once),
            "evidence": f"{base['evidence']} | {change['evidence']}"}
    if "rebound_repeatable" in change:
        deck["rebound_repeatable"] = change["rebound_repeatable"]
    validate_deck(deck)
    return deck


def track_piles(data, turn, slots, outside):
    """Reconcile known piles against the observed hand without reading pile UI."""
    previous = data.get("veil_piles")
    deck, grave, unknown = Counter(), Counter(), Counter()
    if previous is None:
        (deck if turn == 1 and not data.get("veil_legacy_piles") else unknown).update(outside)
    else:
        deck, grave, unknown = (Counter(previous[k]) for k in ("deck", "grave", "unlocated"))
        old_hand = Counter(c for c in previous["hand"] if c)
        hand = Counter(c for c in slots if c)
        if turn != previous["turn"]:
            grave.update(old_hand)
            drawn = hand
        else:
            grave.update(old_hand - hand)
            drawn = hand - old_hand
        if drawn - deck and not unknown and deck.total() < drawn.total():
            deck.update(grave)
            grave.clear()
        if drawn - deck or deck - drawn + grave + unknown != outside:
            deck.clear()
            grave.clear()
            unknown = outside.copy()
        else:
            deck -= drawn
    data["veil_piles"] = {"turn": turn, "hand": slots, "deck": dict(deck), "grave": dict(grave), "unlocated": dict(unknown)}
    return {"deck": list(deck.elements()), "grave": list(grave.elements()), "unlocated": list(unknown.elements())}


def read_position(state, memory, hand, read_flower, *, calibrate=False):
    """Use the last verified play until a turn boundary or resync."""
    data = memory.strategy_data
    position = data.get("veil_position", {})
    pending = data.get("veil_pending_play")
    new_turn = position.get("turn") != memory.turn
    observed = stance(state)
    current = position.get("stance")
    flower = position.get("flower")
    delayed = position.get("next_stance")

    if pending:
        if pending["turn"] == memory.turn:
            if hand[pending["card"]] >= pending["count"]:
                return "veil_play_unverified"
            current, flower = pending["stance"], pending["flower"]
            delayed = pending["next_stance"] or delayed
        else:
            # The next hand may redraw the card. Verify the digit if this
            # unverified play could have changed it.
            if pending["flower"] != flower:
                flower = None
        # Keep the pending record until any required resync also succeeds.
    predicted_flower = flower
    if calibrate or flower is None:
        flower = read_flower()
        if flower is None:
            return "veil_flower_unreadable"
    if new_turn:
        current = observed or (delayed if not pending or not pending["next_stance"] else None)
    elif calibrate:
        # On a resumed cycle a verified hand plus the expected flower digit
        # can corroborate the saved position even if the stance icon is hidden.
        current = observed or (current if flower == predicted_flower else None)
    elif current is None:
        current = observed
    if new_turn or calibrate:
        if current is None and position:
            return "veil_stance_unreadable"
        if new_turn:
            delayed = None

    data["veil_position"] = {"turn": memory.turn, "stance": current, "flower": flower, "next_stance": delayed}
    data.pop("veil_pending_play", None)
    state.pitem_charges = flower
    state.statuses = [s for s in state.statuses or [] if norm(s.get("name") or "") not in STANCES]
    if current is not None:
        state.statuses.append({"name": current})
    if delayed:
        state.statuses.append({"name": f"次のターン開始時、{delayed}に変更"})
    return None


def init_abilities(data, img, recorded, started=False):
    """一场考试开局：状态栏读回忆能力（最多 4 项）；读不全时用回忆栏记下的。结果放进考试记忆 veil_abilities（每项 left 次数）。
    started：这一轮已经打过牌（续跑旧记忆）。用过的能力图标会从状态栏消失，这时以画面为准，不按回忆栏把用过的补回来。"""
    from hiro.brain.memory_ability import fresh

    screen = []
    try:
        from hiro.vision.status import read_memory_abilities

        screen = read_memory_abilities(img) if img is not None else []
    except Exception:  # noqa: BLE001  识别失败不挡考试，退回回忆栏
        screen = []
    names = lambda items: sorted(norm(a.get("card") or "") for a in items)  # noqa: E731
    chosen = screen if started or len(screen) >= len(recorded or []) else recorded
    if screen and recorded and names(screen) != names(recorded):
        data["veil_abilities_mismatch"] = {"screen": names(screen), "recorded": names(recorded)}
    data["veil_abilities"] = fresh([{k: a.get(k) for k in ("card_id", "card", "kind", "count")} for a in chosen or []])
    data["veil_abilities_source"] = "screen" if chosen is screen and screen else "memory"


def apply_ability(data, memory, played):
    """打出 played 后：有对应回忆能力就触发一次（再动 +1 / 抽一张 / 从山札或捨札删一张眠気），次数减一。返回触发的能力。"""
    from hiro.brain.memory_ability import trigger

    hit = trigger(data.get("veil_abilities") or [], played)
    if hit is None:
        return None
    if hit["kind"] in ("draw", "play"):
        memory.actions_left += 1  # 抽到的牌由下一帧手牌与牌堆账（track_piles 同回合 drawn）记
    elif hit["kind"] == "sleep":
        deck = data.get("veil_deck") or {}
        if deck.get("眠気", 0) > 0:
            deck["眠気"] -= 1
            piles = data.get("veil_piles")
            if piles is not None:
                where = [k for k in ("deck", "grave") if piles[k].get("眠気", 0) > 0]
                if len(where) == 1:
                    piles[where[0]]["眠気"] -= 1
                elif len(where) == 2:  # 山札、捨札都有：随机删一张，不知道哪边 → 两边的眠気都记成位置未知
                    total = piles["deck"].pop("眠気") + piles["grave"].pop("眠気") - 1
                    if total:
                        piles["unlocated"]["眠気"] = piles["unlocated"].get("眠気", 0) + total
                elif piles["unlocated"].get("眠気", 0) > 0:
                    piles["unlocated"]["眠気"] -= 1
    data.setdefault("veil_ability_log", []).append({"turn": memory.turn, "card": hit["card"], "kind": hit["kind"], "left": hit["left"]})
    return hit


def _drink_by_name(name):
    from kaa.db.drink import Drink

    want = norm(name)
    return next((d for d in Drink.all() if norm(d.name) == want), None)


class VeilScreen(ScreenExamStateSource):
    def __init__(self, deck, once, ledger_drinks=None, abilities=None):
        super().__init__()
        self.deck = deck
        self.once = once
        # 批量账本里的饮料栏（1–4 格，开考前）：某格画面读不清（unknown）时按账本补，不因识别失手漏掉饮料
        self.ledger_drinks = list(ledger_drinks or [])
        # 回忆栏记下的回忆能力（开局状态栏读不全时用）；每场考试开局优先按画面读
        self.abilities = list(abilities or [])
        self.data = None
        self.calibrate = True
        self.position_retries = 0
        self.names_by_base = defaultdict(list)
        for name in deck:
            self.names_by_base[name.rstrip("+")].append(name)

    def read(self, frame, memory):
        from hiro.vision.hif import read_hif_exam

        if getattr(frame, "raw", None) is None:
            frame.raw = read_hif_exam(frame.img, batch_names=True)
        state, why = super().read(frame, memory)
        if state is None:
            return state, why
        if self.last_raw.get("finals_round") not in (None, int(state.exam[-1])):
            raise ResumeBlocked("VEIL 画面轮次已变化，停止考试操作")
        from hiro.brain.cards import drink_view
        from kaa.db.drink import Drink

        data = memory.strategy_data
        self.data = data
        if "veil_abilities" not in data:
            init_abilities(data, frame.img, self.abilities,
                           started=bool(memory.history or memory.this_turn or memory.turn > 1))  # fmt: skip
        physical = (self.last_raw or {}).get("drink_slots")
        if physical is not None:
            frame.fixed_drink_slots = True
            state.drinks = []
            previous = data.get("veil_drinks") or [None] * 4
            recorded = [None] * 4
            for slot in physical:
                i = slot["slot"]
                if not 0 <= i < 4:
                    continue
                recorded[i] = {"state": slot["state"], "name": slot.get("name"), "drink_id": slot.get("drink_id")}
                if slot["state"] == "drink" and (drink := Drink.from_asset_id(slot["asset"])):
                    state.drinks.append(drink_view(i, drink))
                elif slot["state"] == "unknown" and (previous[i] or {}).get("state") != "consumed":
                    # 读不清：沿用本场上一次的读数，没有就按账本；喝过的格不补
                    name = (previous[i] or {}).get("name") if (previous[i] or {}).get("state") == "drink" else None
                    name = name or (self.ledger_drinks[i] if i < len(self.ledger_drinks) else None)
                    if name and (drink := _drink_by_name(name)):
                        recorded[i] = {"state": "drink", "name": drink.name, "drink_id": drink.id, "from": "ledger"}
                        state.drinks.append(drink_view(i, drink))
                elif (previous[i] or {}).get("state") == "consumed" and slot["state"] != "drink":
                    recorded[i] = previous[i]
            data["veil_drinks"] = recorded
        if "veil_deck" not in data:
            if memory.this_turn or memory.history or memory.used_once:
                raise ResumeBlocked("无法从旧考试记忆建立 VEIL 牌库，请核对后交回代理")
            data["veil_deck"] = dict(self.deck)
            data["veil_initial"] = dict(self.deck)
            data["veil_initial_once"] = sorted(self.once)
        if self.deck != data.get("veil_initial") or sorted(self.once) != data.get("veil_initial_once"):
            raise ResumeBlocked("本轮续跑的牌组清单与开场清单不一致")
        if "veil_piles" not in data and memory.history:
            data["veil_legacy_piles"] = True
        deck = Counter(data["veil_deck"])
        # Temporary upgrades and OCR of the + badge can change the display
        # name. Match it to the reviewed deck only for unambiguous base names.
        for i, card in enumerate(state.hand):
            name = norm(card.name)
            variants = self.names_by_base.get(name.rstrip("+"), ())
            if len(variants) == 1 and name != variants[0]:
                state.hand[i] = replace(card, name=variants[0])
        hand = Counter(norm(c.name) for c in state.hand if c.card_id)
        if len(state.hand) != hand.total():
            raise ResumeBlocked("VEIL 手牌未读全，暂停以免错牌")
        problem = read_position(state, memory, hand, lambda: flower_count(frame.img), calibrate=self.calibrate)
        if problem:
            self.position_retries += 1
            if self.position_retries >= 3:
                raise ResumeBlocked(f"VEIL 无法确认姿态、夜空或上一次出牌：{problem}")
            return None, problem
        self.calibrate = False
        self.position_retries = 0
        outside = deck - hand
        if data.get("veil_uncertain") or hand - deck:
            if CORE <= hand.keys() or data.get("veil_cycle"):
                raise ResumeBlocked("VEIL 牌库记账与当前画面无法互证，暂停核对")
            return state, why
        played = {norm(a[2:]) for a in memory.this_turn if a.startswith("打 ")}
        next_draw = 3 + 2 * (TEACH in played) + int(memory.turn < data.get("veil_soda_until", 0))
        # Only the combined multiset is known, so don't expose a guessed draw order.
        known_once = set(data.get("veil_known_once", [])) | self.once
        known_once.update(norm(c.name) for c in state.hand if c.tags.once and norm(c.name) != REBOUND)
        data["veil_known_once"] = sorted(known_once)
        slots = [None] * 5
        for card in state.hand:
            if not 0 <= card.slot < 5 or slots[card.slot] is not None:
                raise ResumeBlocked("VEIL 手牌槽位无法核对")
            slots[card.slot] = norm(card.name)
        state.piles = {**track_piles(data, memory.turn, slots, outside),
                       "next_draw": next_draw, "once": data["veil_known_once"]}
        return state, why


class VeilBrain(BattleBrain):
    def commit(self, state, memory, decision):
        super().commit(state, memory, decision)
        data = memory.strategy_data
        if decision.kind == "drink":
            drink = next(d for d in state.drinks if d.slot == decision.slot)
            if "veil_drinks" in data and 0 <= decision.slot < 4:
                data["veil_drinks"][decision.slot] = {"state": "consumed", "name": drink.name, "drink_id": drink.drink_id}
            if drink.name == SODA:
                data["veil_soda_until"] = memory.turn + 5
            return
        if decision.kind != "play":
            return
        card = next(c for c in state.hand if c.slot == decision.slot)
        name = norm(card.name)
        current, flower = after_play(stance(state), state.pitem_charges, card)
        data["veil_pending_play"] = {
            "turn": memory.turn, "card": name, "count": sum(norm(c.name) == name for c in state.hand),
            "stance": current, "flower": flower, "next_stance": next_stance(card),
        }
        if destination(card) == "強気" and stance(state) in WARM:
            memory.actions_left += 1
        deck = data["veil_deck"]
        lost = (name in data.get("veil_initial_once", ()) if name == REBOUND else
                card.tags.once or name == "眠気" or name in data.get("veil_known_once", ()))
        if lost:
            if deck.get(name, 0) == 0:
                data["veil_uncertain"] = True
            else:
                deck[name] -= 1
        piles = data.get("veil_piles")
        if piles is not None:
            piles["hand"][decision.slot] = None
            if not lost:
                piles["grave"][name] = piles["grave"].get(name, 0) + 1
        if name in SLEEP_GENERATORS:
            deck["眠気"] = deck.get("眠気", 0) + 1
            if piles is not None:
                piles["deck"]["眠気"] = piles["deck"].get("眠気", 0) + 1
        elif card.tags.makes_sleep or "生成" in card.text or "変換" in card.text:
            data["veil_uncertain"] = True
        # 回忆能力在牌的效果之后结算（「〜使用後」）：再动 / 抽牌 / 删眠気，每项按次数触发后取否
        apply_ability(data, memory, name)


def run_round(round_no, deck_path, trace_name, drinks=None, abilities=None):
    from kaa.config.const import HifScenario
    from kotonebot import device
    from kotonebot.backend.context import init_context, manual_context

    from hiro.contracts import TraceWriter
    from hiro.device import connect
    from hiro.exec.exam import load_exam_memory, run_exam
    from hiro.kaa_env import init_kaa
    from hiro.loop import pause_flag
    from hiro.loop.repeat_guard import watchdog
    from hiro.loop.session import Session
    from hiro.recorder import Recorder
    from hiro.vision.hif import is_hif_exam, read_hif_exam
    from hiro.vision.pages import classify_page, dialog_header
    from kotonebot.config.config import conf

    deck, once = read_deck(deck_path)
    init_kaa(HifScenario.MAIN, idol=IDOL, require_sets=False)
    pause_flag.wait_until_recording()
    connection = connect("maatools")
    try:
        init_context(target_device=connection)
        manual_context().begin()
        from kotonebot.backend.context import vars as kvars

        img = device.screenshot()
        if (img is None or img.shape[:2] != (1280, 720) or dialog_header(img) is not None or not is_hif_exam(img)):
            raise ResumeBlocked(f"实机画面未确认是本战 R{round_no} 出牌页")
        observed = read_hif_exam(img)
        if not observed.get("finals") or observed.get("finals_round") != round_no:
            raise ResumeBlocked(f"实机轮次未确认是本战 R{round_no}")
        exam = f"r{round_no}"
        directory = Path("traces") / trace_name
        restored = load_exam_memory(directory, exam)
        if restored is None and observed.get("remaining_turns") != {1: 9, 2: 12}[round_no]:
            raise ResumeBlocked("缺少同一轮的考试记忆，请从首回合接管")
        result = None

        def ended():
            nonlocal result
            frame = device.screenshot()
            if frame is None or frame.shape[:2] != (1280, 720) or is_hif_exam(frame):
                return False
            page = classify_page(frame)
            if not page or page[0] != "HIF_FINALS_RANKING":
                return False
            fields = page[1]
            if not ((fields.get("kind") == "round_result" and fields.get("finals_round") == round_no)
                    or (fields.get("kind") == "final" and round_no == 2)):
                return False
            result = fields
            session.record(session.observe(scene="HIF_FINALS_RANKING", img=frame, fields=fields, save_frame=True))
            return True

        trace = TraceWriter(name=trace_name, meta={"mode": "main", "idol": IDOL, "advisor": "veil", "round": round_no})
        session = Session(trace)
        watch = pause_flag.Watcher(kvars.flow, session=session).start()
        guard = watchdog(session)
        callbacks = conf().loop.loop_callbacks
        callbacks.append(guard)
        rec = Recorder(trace.dir / "battle")
        source = VeilScreen(deck, once, drinks, abilities)
        try:
            run_exam(VeilBrain(None, None), exam, rec, ended, session=session, source=source,
                     advisor=VeilAdvisor(), round_no=round_no, strict=True)
        finally:
            callbacks.remove(guard)
            watch.stop()
            trace.close()
        if result is None:
            raise ResumeBlocked("VEIL 考试结束页未确认，请查看实机与 trace")
        return {"ok": True, "exam": exam, "trace": str(trace.dir),
                "round_score": result.get("round_score"), "rank": result.get("rank"),
                "drinks": (source.data or {}).get("veil_drinks"),
                "abilities": (source.data or {}).get("veil_abilities"),
                "abilities_source": (source.data or {}).get("veil_abilities_source"),
                "abilities_mismatch": (source.data or {}).get("veil_abilities_mismatch")}
    finally:
        connection.stop()
