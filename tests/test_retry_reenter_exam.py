# 来源：digital-hiro tests/test_retry_reenter_exam.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""第十八局本战 R2：点「再挑戦」后画面从结果页直接回到同一场考试，KAA 记的上一页还是 EXAM，
_dispatch 只在「上一页不是 EXAM」时进 on_exam_entered → 主循环空转（两次各卡 18 / 7 分钟）。
策略置 exam_restarted 后，下一次看到考试页要重新进入 on_exam_entered，且只进一次。"""

import hiro  # noqa: F401  兼容层（先于 kaa）
from kaa.tasks.produce.new.consts import Scene, SceneType

from hiro.loop.controller import HiroController


class Strategy:
    exam_restarted = False

    def __init__(self):
        self.entered = 0

    def on_exam_entered(self, ctx):
        self.entered += 1


def _ctrl():
    c = HiroController.__new__(HiroController)
    c.strategy, c.page, c._last_scene = Strategy(), None, None
    return c


def _tick(c, scene):
    c._rearm_exam(scene)
    c._dispatch(scene, c._last_scene)
    c._last_scene = scene


def test_retry_reenters_same_exam_once():
    c, exam = _ctrl(), Scene(SceneType.EXAM)
    _tick(c, exam)
    assert c.strategy.entered == 1
    c.strategy.exam_restarted = True  # on_exam_entered 里点了再挑戦
    _tick(c, exam)
    assert c.strategy.entered == 2 and c.strategy.exam_restarted is False
    _tick(c, exam)  # 同一场考试后续帧：不重复进入
    assert c.strategy.entered == 2


def test_without_flag_kaa_does_not_reenter():
    """旧行为（复现 bug）：没有标志时连续 EXAM 只进一次。"""
    c, exam = _ctrl(), Scene(SceneType.EXAM)
    _tick(c, exam)
    _tick(c, exam)
    assert c.strategy.entered == 1


def test_flag_waits_for_exam_page():
    """再挑戦后先过一帧别的页面（转场）：标志保留到真正看到考试页。"""
    c = _ctrl()
    c.strategy.exam_restarted = True
    c._last_scene = Scene(SceneType.EXAM)
    c._rearm_exam(Scene(SceneType.UNKNOWN))
    assert c.strategy.exam_restarted is True


def test_strategy_sets_flag_on_finals_retry(monkeypatch):
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.pages as pages

    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st.s = NS(note=lambda *a: None)
    st.rec = NS(log=lambda *a, **k: None)
    monkeypatch.setattr(pages, "classify_page", lambda img: ("HIF_FINALS_RANKING", {"kind": "final", "rank": 2, "outcome": "敗退"}))
    btn = NS(text="再挑戦", rect=NS(center=NS(y=1100)))
    monkeypatch.setattr(hif, "ocr", NS(ocr=lambda: [btn]))
    st._click_retry = lambda label, b, texts, **kw: "free"
    assert st._finals_retry(None, "r2") is True and st.exam_restarted is True


def test_declined_retry_not_reopened_on_same_page(monkeypatch):
    """Windows 第七局：止损（RETRY_STOP_LOSS）取消再挑戦后，收尾循环每轮又点开确认框、再取消，一直没点「次へ」，空转看门狗停机。
    同一张结算页决定不再挑戦后，不再点开确认框；换了一张结算页（分数不同）照常判断。"""
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif
    import hiro.vision.pages as pages

    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st.s = NS(note=lambda *a: None)
    st.rec = NS(log=lambda *a, **k: None)
    page = {"kind": "final", "rank": 3, "outcome": "敗退", "score": 756342, "round_score": 158038}
    monkeypatch.setattr(pages, "classify_page", lambda img: ("HIF_FINALS_RANKING", page))
    btn = NS(text="再挑戦", rect=NS(center=NS(y=1100)))
    monkeypatch.setattr(hif, "ocr", NS(ocr=lambda: [btn]))
    calls = []
    st._click_retry = lambda label, b, texts, **kw: calls.append(kw) or None
    assert st._finals_retry(None, "r2") is False and st._finals_retry(None, "r2") is False and len(calls) == 1
    page.update(score=900000, round_score=300000)
    assert st._finals_retry(None, "r2") is False and len(calls) == 2


def test_retries_left_from_screen_overrides_trace_count():
    """「あと N回」以画面为准：通信エラー期间 trace 里多记的「再挑戦」不该把剩余次数吃掉。"""
    from types import SimpleNamespace as NS

    from hiro.produce.hif import screen_retries_left

    assert screen_retries_left([NS(text="あと3回"), NS(text="再挑戦")]) == 3
    assert screen_retries_left([NS(text="あと 2 回")]) == 2
    assert screen_retries_left([NS(text="次へ")]) is None
