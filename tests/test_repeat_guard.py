"""通用防空转（hiro.loop.repeat_guard）：同一局面同一动作连续 3 次 → 禁用 / 停机；画面和局面 3 分钟没进展 → 停机报警。"""

import numpy as np
import pytest

from hiro.contracts import Action, ActionKind, Decision, ExecutionResult, TraceWriter
from hiro.loop import repeat_guard as rg
from hiro.loop.session import Session

import hiro.loop.session as _sess

NEED_GUARD = pytest.mark.skipif("guard" not in _sess.Session.__init__.__code__.co_names,
                                reason="开源 hiro.loop.session 还没接防空转（Session.guard），等核心同步")

def _session(tmp_path, t):
    tw = TraceWriter(root=tmp_path, name="t", meta={"test": True})
    return Session(tw, shoot=lambda: None, classify=lambda: ("X", "produce"), extract=lambda s, i: ({}, 0.0), sleep=lambda s: None,
                   clock=lambda: t[0])  # fmt: skip


def _step(s, page, fields, action, ok=True):
    obs = s.observe(scene=page, fields=fields, save_frame=False)
    d = Decision(action, "测试", "rule")
    return s.record(obs, decision=d, execution=ExecutionResult(action=action, ok=ok))


def test_state_sig_ignores_volatile():
    a = {"hand": [{"name": "x", "badge_score": 9015, "center": [1, 2]}], "score": 10}
    b = {"hand": [{"name": "x", "badge_score": 13032, "center": [3, 4]}], "score": 10}
    assert rg.state_sig("HIF_EXAM", a) == rg.state_sig("HIF_EXAM", b)
    assert rg.state_sig("HIF_EXAM", a) != rg.state_sig("HIF_EXAM", {**a, "score": 11})


@NEED_GUARD
def test_non_exam_repeat_stops_on_third(tmp_path):
    t = [0.0]
    s = _session(tmp_path, t)
    buy = Action(ActionKind.INTERVAL_BUY, {"index": 6})
    fields = {"produce_point": 465}
    _step(s, "HIF_ROUND_INTERVAL", fields, buy, ok=False)
    _step(s, "HIF_ROUND_INTERVAL", fields, buy, ok=False)
    with pytest.raises(rg.RepeatedNoEffect):
        _step(s, "HIF_ROUND_INTERVAL", fields, buy, ok=False)
    assert s.recoveries["tap_no_effect"] == 1


@NEED_GUARD
def test_changed_state_or_action_resets(tmp_path):
    t = [0.0]
    s = _session(tmp_path, t)
    buy6, buy2 = Action(ActionKind.INTERVAL_BUY, {"index": 6}), Action(ActionKind.INTERVAL_BUY, {"index": 2})
    for p, a in ((465, buy6), (465, buy6), (415, buy6), (415, buy2), (415, buy6)):
        _step(s, "HIF_ROUND_INTERVAL", {"produce_point": p}, a)
    assert s.repeat_streak == 1


@NEED_GUARD
def test_exam_repeat_does_not_raise_but_flags(tmp_path):
    t = [0.0]
    s = _session(tmp_path, t)
    drink = Action.use_drink(0)
    for _ in range(3):
        _step(s, "HIF_EXAM", {}, drink)
    assert s.repeat_streak == 3 and s.recoveries["tap_no_effect"] == 1


def test_screen_progress_needs_stable_change():
    t = [0.0]
    g = rg.Guard(clock=lambda: t[0])
    base, flash, other = (np.full((1280, 720, 3), v, np.uint8) for v in (40, 200, 120))
    g.screen(base)
    t[0] = 100
    g.screen(flash)  # 一闪（弹窗 / 动画）
    g.screen(base)
    assert g.stalled() == 100
    g.screen(other)
    g.screen(other)  # 连续两次都是新画面 = 稳定地变了
    assert g.stalled() == 0


def test_watchdog_raises_after_stall():
    from types import SimpleNamespace as NS

    t = [0.0]
    notes = []
    sess = NS(guard=rg.Guard(clock=lambda: t[0]), state=NS(page=NS(name="HIF_EXAM")), recover=lambda k, d: notes.append(k),
              note=notes.append, observe=lambda **kw: None, record=lambda: None)  # fmt: skip
    cb = rg.watchdog(sess, clock=lambda: t[0])
    loop = NS(screenshot=np.full((1280, 720, 3), 40, np.uint8))
    for sec in range(0, int(rg.STALL_S), 5):
        t[0] = sec
        assert cb(loop) is False
    t[0] = rg.STALL_S + 1
    with pytest.raises(rg.Stalled):
        cb(loop)
    assert "unhandled" in notes


@NEED_GUARD
def test_page_change_between_attempts_resets(tmp_path):
    """第十六局选拔：连续拿到几瓶饮料，饮料满确认框连着出现、每次都处理成功（中间回到过 PDRINK_MAX）——不算重复无效。"""
    t = [0.0]
    s = _session(tmp_path, t)
    ok = Action(ActionKind.DIALOG, {"kind": "reward_skip", "confirm": True})
    for _ in range(4):
        s.observe(scene="PDRINK_MAX", fields={}, save_frame=False)
        _step(s, "PDRINK_MAX_CONFIRM", {}, ok)
    assert s.repeat_streak == 1


def test_landscape_live_counts_as_progress():
    """第三十一局：夺冠后横屏 LIVE 约 2 分 45 秒，画面一直在动、从不「稳定地变」，看门狗在 LIVE 结束 2s 后按 181s 没进展停机。
    横屏帧（宽 > 高）算进展。"""
    t = [0.0]
    g = rg.Guard(clock=lambda: t[0])
    g.screen(np.full((1280, 720, 3), 40, np.uint8))
    for sec in range(5, 200, 5):
        t[0] = sec
        g.screen(np.random.default_rng(sec).integers(0, 255, (720, 1280, 3), dtype=np.uint8))  # LIVE：每帧都不同
    assert g.stalled() == 0
    t[0] = 205
    g.screen(np.full((1280, 720, 3), 40, np.uint8))  # 回到竖屏：重新开始计
    assert g.stalled() == 10 and g._base is not None  # 最后一帧横屏在 195s；竖屏这帧只重设基准
