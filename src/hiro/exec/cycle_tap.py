"""循环回合连打（VEIL_CYCLE_TAP，默认关；使用者 2026-11-08）。

原来五卡循环每打一张牌都走一遍整套：等稳两帧 → 完整 Frame（KAA 逐张卡图匹配）→ 识别层读手牌（卡面 + 卡名 OCR）→ 局面来源记账核对
→ 决策 → 按画面定位点击。循环回合里手牌是已知的几张核心牌，打出顺序也定了，这些重读只是在反复确认同一件事。

现在：回合里第一张循环牌照常整帧读取（确保读到所有牌的位置和内容）、决策、点击、记账。之后：
1. 局面来源（burst_begin）按首读的手牌顺序删掉打出的那张、槽位重排，得到「n 张手牌」的局面；
2. 每张之前只看轻量签名（剩余回合 + 卡名首字母角标，约 25ms）：张数是 n、位置合 n 张布局（hiro.vision.hif.HAND_LAYOUT）、
   连续两次一致、剩余回合没变，再按账本结算上一手（burst_settle），发灰按角标颜色；
3. 决策照旧（决策只读局面，不读画面），仍是循环出牌就按 n 张布局里的第几格算出点击点（卡名首字母框中心往左上偏到卡面中心），直接打；
4. 落地核对与整帧路径相同（SELECT 确认 / 画面变化 / 回合没自动结束 / 卡没停在选中态），落地了照常记账、写 trace。

张数或位置对不上（打出后多抽了牌、回合结束）、发灰、弹窗、决策不是循环出牌、没落地、出牌次数用完，都退出连打，回到整帧读取，
那一帧由 read_position 核对最后一手。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from kotonebot import device, logging, sleep

from hiro.vision.hif import HAND_LAYOUT, hand_layout

logger = logging.getLogger(__name__)

CARD_W, CARD_H = 57, 148  # KAA play_cards.ui.CARD_OFFSET：卡名首字母框（中心 x、上缘 y）到卡面左上角的偏移
GREY = "#7a7d7d"  # 首字母角标发灰色（KAA locate_cards 判 available 用的同一个颜色）
SETTLE_S = 4.0  # 等手牌摆到 n 张布局的上限（同 exam.SETTLE_FAST_MAX）
SETTLE_GAP = 0.15
MARK = "五卡循环"  # VEIL 循环出牌决策理由的前缀（hiro.brain.veil：五卡循环 / 五卡循环收尾）


def tap_point(n: int, index: int, letter_top: int) -> tuple[int, int]:
    """n 张手牌里第 index 格（从左数，0 起）的点击点：布局里卡名首字母框中心 x，往左半张卡、往上半张卡（= KAA 卡面框中心）。"""
    x = HAND_LAYOUT[n][index]
    return int(x - CARD_W / 2), int(letter_top - CARD_H / 2)


def read_letters(img) -> list[tuple[int, int, bool]]:
    """卡名首字母角标 [(中心 x, 上缘 y, 发灰)]，从左到右。只做模板匹配（同 exam._lite_sig），不查卡图。"""
    from kaa.tasks.produce.new.play_cards.ui import _locate_letters
    from kotonebot.backend import color

    rects = sorted(_locate_letters(img), key=lambda r: r.x1)
    return [(int(r.center.x), int(r.y1), color.find(img, GREY, rect=r) is not None) for r in rects]


def wait_hand(n: int, remaining: int | None, *, timeout: float = SETTLE_S, clock=time.monotonic):
    """等手牌稳定在 n 张布局：返回 (截图, 角标) 或 (None, 原因)。原因：dialog / remaining（剩余回合变了）/
    count:<张数>（稳定在别的张数）/ timeout。动画中照整帧路径的规矩点舞台空白推进。"""
    from hiro.exec import exam as ex

    start, prev = clock(), None
    while clock() - start < timeout:
        img = device.screenshot()
        if ex._dialog_top(img) is not None:
            return None, "dialog"
        sig = ex._lite_sig(img)
        if sig is None:
            prev = None
        else:
            if remaining is not None and sig[0][0] is not None and sig[0][0] != remaining:
                return None, "remaining"
            same = prev is not None and ex._lite_same(prev, sig)
            prev = sig
            if same:
                letters = read_letters(img)
                xs = [x for x, _, _ in letters]
                if len(xs) == n and hand_layout(xs) == "ok":
                    if ex.card_in_flight(img):  # 上一张还浮着结算：手牌已摆好也先等它落下（1-i R2 OD 后 教えて 被吞）
                        sleep(SETTLE_GAP)
                        continue
                    return (img, letters), None
                if hand_layout(xs) == "ok":
                    return None, f"count:{len(xs)}"
        ex._anim_tap(img)
        sleep(SETTLE_GAP)
    return None, "timeout"


def _wait_change(before, *, clock=time.monotonic) -> bool:
    """点完等画面变（轻量签名：剩余回合 / 角标位置），期间照常处理弹窗；同 exam._wait_change，但不读完整 Frame。"""
    from hiro.exec import exam as ex

    start = clock()
    while clock() - start < ex.CHANGE_TIMEOUT:
        img = device.screenshot()
        sig = ex._lite_sig(img)
        if sig is None or before is None or not ex._lite_same(before, sig):
            return True
        if ex._handle_dialogs(img, select=clock() - start >= ex.SELECT_GRACE):
            return True
        sleep(ex.CHANGE_POLL)
    return False


def run_burst(state, memory, decision, *, brain, source, advisor, exam: str, rec=None, session=None) -> int:
    """整帧路径刚打出并记完账一张循环牌（state / decision 是那一步的）之后调用：按布局连打本回合剩下的循环牌。
    返回连打打出的张数；退出后主循环照常整帧读取。"""
    from hiro.contracts import Action, ExecutionResult
    from hiro.exec import exam as ex
    from hiro.loop.errors import ResumeBlocked

    if not hasattr(source, "burst_begin"):
        return 0
    st = source.burst_begin(state, memory, decision)
    if st is None:
        return 0
    remaining = state.remaining_turns
    done, why = 0, None
    while st is not None:
        n = len(st.hand)
        if n not in HAND_LAYOUT:
            why = f"手牌 {n} 张不在布局表里"
            break
        t0 = time.monotonic()
        got, why = wait_hand(n, remaining)
        if got is None:
            break
        img, letters = got
        if not source.burst_settle(st, memory, [grey for _, _, grey in letters]):
            why = "账本结算不了上一手"
            break
        legal = [Action.from_legal(a) for a in st.legal_actions()]
        try:
            contract = advisor.decide(st, memory, None, legal)
        except ResumeBlocked as e:  # 推出来的局面让决策停机：交回整帧读取，用画面再判一次
            why = f"决策停机（{e}）"
            break
        d = contract.to_battle()
        if d.kind != "play" or MARK not in contract.reason or d.slot is None or d.slot >= n:
            why = f"决策不是循环出牌：{d.action} {contract.reason}"
            break
        card = st.hand[d.slot]
        x, y = tap_point(n, d.slot, letters[d.slot][1])
        context = source.trace_context(st, memory) if hasattr(source, "trace_context") else None
        if rec is not None:
            rec.log("battle_decision", image=img, exam=exam, source="cycle_tap", contract=contract.to_dict(),
                    **({"context": context} if context else {}))
        logger.info("[%s T%s] 连打 %s（%d 张中第 %d 格 → (%d,%d)）：%s", exam, memory.turn, d.action, n, d.slot + 1, x, y,
                    contract.reason)
        before = ex._lite_sig(img)
        t_tap = time.monotonic()
        taps = ex._play_card(x, y, img, card.name)
        changed = _wait_change(before)
        missed = None
        if changed:
            frame = SimpleNamespace(img=img)
            if ex._play_missed(frame, ex.PLAY_RESULT["confirmed"]):
                missed = "回合自动结束了，牌没打出去"
            elif taps:
                missed = ex._play_not_landed(frame, taps[-1][0], card.name)
        t_done = time.monotonic()
        ex._LAST_EXEC["t"] = t_done
        ok = changed and missed is None
        result = ExecutionResult(action=Action.from_legal(d.action), ok=ok, taps=taps, waited_s=round(t_done - t_tap, 3),
                                 page_before="HIF_EXAM", page_after="HIF_EXAM", expected_transition=True, retries=0,
                                 error=missed if missed is not None else None if changed else "画面未变化",
                                 seconds=round(t_done - t_tap, 3))  # fmt: skip
        if ok:
            brain.commit(st, memory, d)
            source.executed(Action.from_legal(d.action), result)
            done += 1
        if session is not None:
            if not ok:
                session.recover("tap_no_effect", f"连打 {d.action} {result.error}，交回整帧读取")
            session.note(f"cycle_tap {n} 张第 {d.slot + 1} 格 ({x},{y}) {card.name}")
            session.record(None, decision=contract, previews=[], execution=result,
                           timings_ms={"cycle_tap": 1, "settle": (t_tap - t0) * 1000, "wait_change": (t_done - t_tap) * 1000})
            if ok:
                ex.save_exam_memory(session.trace.dir, memory)
        if not ok:
            why = f"没落地：{result.error}"
            break
        if memory.actions_left <= 0:
            why = "出牌次数用完"
            break
        st = source.burst_begin(st, memory, d)
        why = None if st is not None else "连打条件不再满足"
    if rec is not None:
        rec.log("veil_cycle_tap_end", exam=exam, played=done, reason=why)
    logger.info("[%s T%s] 连打结束：打出 %d 张，%s", exam, memory.turn, done, why or "—")
    return done
