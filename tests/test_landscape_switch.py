"""横屏 LIVE 黑屏切全屏（hiro.loop.landscape）：按全屏按钮 → AXFullScreen 属性 → 菜单，每步核对；锁屏 / 失败交给使用者。
2026-11-07 批量第 1 局 R2 后：窗口拿不到焦点，菜单方式切不进全屏，一直黑屏；手动点全屏按钮就好。"""

import numpy as np
import pytest

pytest.importorskip("AppKit")

import hiro.loop.landscape as L  # noqa: E402

BLACK = np.zeros((1280, 720, 3), np.uint8)
LIVE = np.full((720, 1280, 3), 120, np.uint8)
PORTRAIT = np.full((1280, 720, 3), 120, np.uint8)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(L, "VERIFY_S", 0.05)
    monkeypatch.setattr(L, "VERIFY_POLL", 0.01)
    monkeypatch.setattr(L, "screen_locked", lambda: False)


def test_first_method_that_verifies_wins():
    calls = []
    frames = iter([BLACK] * 50)

    def button(pid, want):
        calls.append("button")
        return True  # 按了但画面还是黑的 → 换下一种

    def attribute(pid, want):
        nonlocal frames
        calls.append("attr")
        frames = iter([LIVE] * 50)
        return True

    got = L.switch_fullscreen(True, pid=1, shoot=lambda: next(frames),
                              methods=(("按钮", button), ("属性", attribute), ("菜单", lambda p, w: calls.append("menu"))))
    assert got == "属性" and calls == ["button", "attr"]


def test_failed_and_errors_fall_through():
    def boom(pid, want):
        raise RuntimeError("osascript")

    got = L.switch_fullscreen(True, pid=1, shoot=lambda: BLACK, methods=(("a", boom), ("b", lambda p, w: False)))
    assert got == "failed"


def test_locked_screen_is_not_tried(monkeypatch):
    monkeypatch.setattr(L, "screen_locked", lambda: True)
    tried = []
    assert L.switch_fullscreen(True, pid=1, methods=(("a", lambda p, w: tried.append(1)),)) == "locked" and not tried


def test_exit_verifies_portrait_with_picture():
    frames = iter([LIVE, BLACK, PORTRAIT] + [PORTRAIT] * 10)
    assert L.switch_fullscreen(False, pid=1, shoot=lambda: next(frames), methods=(("a", lambda p, w: True),)) == "a"


def test_menu_needs_focus(monkeypatch):
    monkeypatch.setattr(L, "_focus", lambda pid: False)
    monkeypatch.setattr(L, "_click_menu", lambda pid, items: pytest.fail("没拿到焦点不该点菜单"))
    assert L._menu(1, True) is False


def test_guard_exits_fullscreen_on_lasting_portrait_picture(monkeypatch):
    """误进全屏（长时间加载黑屏被当成 LIVE）：全屏中竖屏有画面持续 EXIT_PORTRAIT_S → 退出，不再一直当成 LIVE。"""
    t = [0.0]
    monkeypatch.setattr(L.time, "monotonic", lambda: t[0])
    exits = []
    g = L.LandscapeGuard()
    g.fullscreen, g.entered_at = True, 0.0
    g._exit = lambda: exits.append(t[0]) or True
    for t[0] in (31.0, 35.0):
        assert not g.tick(PORTRAIT)
    t[0] = 41.5
    assert g.tick(PORTRAIT) and exits == [41.5]


def test_guard_enter_records_result(monkeypatch):
    monkeypatch.setattr(L, "_game_pid", lambda: 7)
    monkeypatch.setattr(L, "_front_app", lambda: None)
    monkeypatch.setattr(L, "switch_fullscreen", lambda want, pid, shoot: "failed")
    g = L.LandscapeGuard()
    assert g._enter() is False and g.last_result == "failed" and not g.fullscreen


def test_fn_shift_f_only_for_exit_and_tried_first():
    # 使用者 2026-11-07：LIVE「終了する」之后按 fn+shift+F 直接回竖屏
    assert L.METHODS[0][1] is L._key_to_pid and L.METHODS[1][1] is L._key_front
    assert L._key_to_pid(1, True) is False and L._key_front(1, True) is False  # 进入全屏不按


def test_fn_shift_f_posts_shift_fn_f_to_game(monkeypatch):
    import Quartz

    sent = []
    monkeypatch.setattr(Quartz, "CGEventPostToPid", lambda pid, ev: sent.append(
        (pid, Quartz.CGEventGetIntegerValueField(ev, Quartz.kCGKeyboardEventKeycode), Quartz.CGEventGetFlags(ev))))
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    assert L._key_to_pid(42, False)
    flags = Quartz.kCGEventFlagMaskShift | Quartz.kCGEventFlagMaskSecondaryFn
    assert [(p, k) for p, k, _ in sent] == [(42, L.KEY_F)] * 2 and all(f & flags == flags for _, _, f in sent)


class Env:
    """模拟窗口环境：屏幕锁、AXFullScreen、切换结果都由测试控制；记下每次窗口操作。"""

    def __init__(self, monkeypatch, *, locked=False, ax=False, result="failed"):
        self.locked, self.ax, self.result, self.switches = locked, ax, result, []
        monkeypatch.setattr(L, "_game_pid", lambda: 42)
        monkeypatch.setattr(L, "_front_app", lambda: None)
        monkeypatch.setattr(L, "screen_locked", lambda: self.locked)
        monkeypatch.setattr(L, "ax_is_fullscreen", lambda pid: self.ax)
        monkeypatch.setattr(L, "switch_fullscreen", self.switch)

    def switch(self, want, **kwargs):
        self.switches.append(want)
        return self.result


def held(g, img):
    """batch_hif.live_step 的停点判断：失败标记还在且没核验恢复 → 停。"""
    return g.last_result in L.SWITCH_FAILURES and not g.failure_cleared(img, every=0)


@pytest.mark.parametrize("operation, failure, ax_after, recovered, fullscreen_after", [
    ("enter", "failed", True, LIVE, True),       # 进入失败 → 使用者手动进了全屏
    ("enter", "failed", False, PORTRAIT, False),  # 进入失败 → LIVE 已结束、画面回竖屏
    ("exit", "failed", False, PORTRAIT, False),   # 退出失败 → 使用者手动退出全屏
    ("exit", "nogame", False, PORTRAIT, False),
])
def test_guard_hold_clears_after_manual_recovery(monkeypatch, operation, failure, ax_after, recovered, fullscreen_after):
    env = Env(monkeypatch, ax=operation == "exit", result=failure)
    g = L.LandscapeGuard()
    assert not (g._enter() if operation == "enter" else g.exit_now())
    assert env.switches == [operation == "enter"]
    # 没恢复：多次核验都还停着，也不做窗口操作
    for img in (None, BLACK, BLACK, LIVE if operation == "exit" else BLACK):
        assert held(g, img)
    env.locked = True
    assert held(g, recovered)  # 锁着屏时即使画面像恢复了也不放行
    env.locked, env.ax = False, ax_after
    assert not held(g, recovered)
    assert g.last_result == "manual" and g.fullscreen == fullscreen_after
    assert not g.tick(recovered)
    assert env.switches == [operation == "enter"]


def test_exit_failure_needs_ax_confirmation(monkeypatch):
    env = Env(monkeypatch, ax=True)
    g = L.LandscapeGuard()
    g.exit_now()
    assert held(g, PORTRAIT)  # 画面竖屏但 AX 还说全屏 / 读不到：不放行
    env.ax = None
    assert held(g, PORTRAIT)
    env.ax = False
    assert not held(g, PORTRAIT)


@pytest.mark.parametrize("operation", ["enter", "exit"])
def test_locked_hold_clears_after_unlock_and_retries_once(monkeypatch, operation):
    env = Env(monkeypatch, locked=True, ax=operation == "exit", result="locked")
    g = L.LandscapeGuard()
    assert not (g._enter() if operation == "enter" else g.exit_now())
    for img in (None, BLACK, LIVE, PORTRAIT):
        assert held(g, img)  # 还锁着：一直停，不切窗口
    assert env.switches == [operation == "enter"]
    env.locked = False
    assert not held(g, BLACK)  # 解锁就重新核验：按 AX 同步状态，清掉停点
    assert g.fullscreen == (operation == "exit")
    # 画面还黑：由 tick 按黑屏计时再试一次；这次失败是 failed，要手动恢复画面，不再循环切换
    clock = [1000.0]
    monkeypatch.setattr(L.time, "monotonic", lambda: clock[0])
    env.result = "failed"
    g._recheck_at = -1e9
    assert not g.tick(BLACK)
    clock[0] += max(g.BLACK_S, g.EXIT_BLACK_S) + 1
    g.tick(BLACK)  # 再试的这一次（tick 返回的是切换是否成功）
    assert env.switches == [operation == "enter"] * 2 and g.last_result == "failed"
    for _ in range(5):
        clock[0] += 60
        assert held(g, BLACK)
    assert env.switches == [operation == "enter"] * 2
