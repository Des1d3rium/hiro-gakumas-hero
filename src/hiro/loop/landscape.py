# 来源：digital-hiro hiro/loop/landscape.py（c3fc27f）
"""横屏 LIVE 黑屏的处理（PlayCover 已知问题，PlayCover issue #1496、社区「画面回転でブラックアウトしたらフルスクリーン切替」）。

选拔赛 / 本战结束后的 LIVE 要求横屏，PlayCover 窗口不跟着转，游戏在跑但画面全黑、后台点击无效。
进入全屏（应用菜单「显示 → 进入全屏幕」）会让窗口重新布局，LIVE 就能渲染；LIVE 结束回到竖屏后再退出全屏，否则又是黑屏。
菜单操作需要把游戏短暂切到前台，结束后把原来的前台应用还回去。实机 2026-10-31 第四局验证。

2026-11-07 批量第 1 局 R2 后的 LIVE：窗口拿不到焦点，「set frontmost」和菜单都没生效，全屏切不进去，一直黑屏；
使用者手动点窗口的全屏按钮就能进全屏，之后窗口也能拿到焦点、快捷键可用。所以现在按这个顺序试（switch_fullscreen）：
1. 辅助功能按窗口的全屏按钮（子角色 AXFullScreenButton，等于手点绿色按钮，不需要窗口在前台）；
2. 直接设窗口的 AXFullScreen 属性；
3. 原来的菜单方式，但先激活 + AXRaise，并确认前台应用确实是游戏，不是游戏就不点。
每一步之后核对（默认读 AXFullScreen 属性；调用方给了截图函数就看画面中间是否不再全黑），没生效才换下一步；
屏幕锁住 / 显示器休眠时任何方式都拿不到焦点，直接返回 "locked"，交给使用者。

旋转窗口（HIRO_LIVE_ROTATE=1；batch_hif watch 默认开，run_hif 默认关）：游戏是 PlayCover「可调窗口」模式（resolution=6），PlayTools 把 UIScreen.bounds
接到窗口当前尺寸（PlayScreen.boundsResizable），全屏能救黑屏就是因为窗口变横了。所以不切全屏，直接用辅助功能把窗口
内容区宽高对调成横向（不需要前台、不抢焦点），LIVE 结束再还原成进入前的位置和尺寸。开着时进入只用它，不再退回全屏
（使用者 2026-11-08），没救回画面就停下交给使用者；退出时窗口在全屏里才走全屏方式，否则只用它还原（fn+shift+F 这时会反过来进全屏）。
旋转很便宜（不抢前台、不动动画），所以黑屏 ROTATE_BLACK_S（5s）就转，不像切全屏要等 BLACK_S（20s）；
短暂的加载黑屏可能被误转：第一次转了还黑就还原窗口、重新计时再试一次，第二次还黑才停下。
"""

import logging
import os
import subprocess
import time

from AppKit import NSApplicationActivateAllWindows, NSApplicationActivateIgnoringOtherApps, NSRunningApplication, NSWorkspace

BUNDLE_ID = "jp.co.bandainamcoent.BNEI0421"
MENU_VIEW = "显示"  # 系统语言为中文；英文 / 日文系统按下面的候选找
ENTER = ("进入全屏幕", "Enter Full Screen", "フルスクリーンにする")
EXIT = ("退出全屏幕", "Exit Full Screen", "フルスクリーンを解除")
MENUS = ("显示", "View", "表示")

VERIFY_S = 5.0  # 每种方式之后最多等多久核对
VERIFY_POLL = 0.5
BLACK_MEAN = 3.0
SWITCH_FAILURES = ("locked", "nogame", "failed")

logger = logging.getLogger(__name__)


def is_black(img) -> bool:
    """画面中间区域全黑（全屏后窗口顶部有白边，只看中间）。"""
    if img is None:
        return False
    h, w = img.shape[:2]
    return float(img[h // 8 : h * 7 // 8, w // 8 : w * 7 // 8].mean()) < BLACK_MEAN


def screen_locked() -> bool:
    """屏幕锁住或主显示器休眠：拿不到焦点，切全屏也没用。"""
    try:
        import Quartz

        info = Quartz.CGSessionCopyCurrentDictionary() or {}
        if info.get("CGSSessionScreenIsLocked"):
            return True
        return bool(Quartz.CGDisplayIsAsleep(Quartz.CGMainDisplayID()))
    except Exception:  # noqa: BLE001  读不到就当没锁，交给后面的核对
        return False


def _osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        logger.info("osascript 失败：%s", r.stderr.strip()[:200])
    return r.stdout.strip()


def _ax_window(pid: int) -> str:
    """游戏进程里的主窗口（标准窗口里面积最大的那个；PlayCover 可能还有别的窗口）。"""
    return f"""
tell application "System Events" to tell (first process whose unix id is {pid})
  set best to missing value
  set bestArea to 0
  repeat with w in windows
    try
      set {{ww, hh}} to size of w
      if ww * hh > bestArea then
        set best to w
        set bestArea to ww * hh
      end if
    end try
  end repeat
  if best is missing value then return "nowindow"
"""


def ax_is_fullscreen(pid: int) -> bool | None:
    out = _osa(_ax_window(pid) + """
  return (value of attribute "AXFullScreen" of best) as text
end tell""")
    return {"true": True, "false": False}.get(out)


def _ax_button(pid: int, want: bool) -> bool:
    """按窗口的全屏按钮（AXFullScreenButton，即绿色按钮）。已经是目标状态就不按。"""
    out = _osa(_ax_window(pid) + f"""
  if ((value of attribute "AXFullScreen" of best) as boolean) is {str(want).lower()} then return "ok"
  set btn to (first button of best whose subrole is "AXFullScreenButton")
  perform action "AXPress" of btn
  return "ok"
end tell""")
    return out == "ok"


def _ax_attribute(pid: int, want: bool) -> bool:
    out = _osa(_ax_window(pid) + f"""
  set value of attribute "AXFullScreen" of best to {str(want).lower()}
  return "ok"
end tell""")
    return out == "ok"


def _focus(pid: int) -> bool:
    """激活游戏 + 把主窗口提到最前，确认前台应用确实是游戏。"""
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False
    app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps | NSApplicationActivateAllWindows)
    _osa(_ax_window(pid) + """
  perform action "AXRaise" of best
  return "ok"
end tell""")
    time.sleep(0.5)
    front = _front_app()
    return front is not None and int(front.processIdentifier()) == pid


def _menu(pid: int, want: bool) -> bool:
    if not _focus(pid):
        logger.warning("菜单切全屏前没能把游戏设为前台：不点菜单")
        return False
    return _click_menu(pid, ENTER if want else EXIT)


KEY_F = 0x03  # kVK_ANSI_F


def _fn_shift_f(post) -> None:
    import Quartz

    flags = Quartz.kCGEventFlagMaskShift | Quartz.kCGEventFlagMaskSecondaryFn
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, KEY_F, down)
        Quartz.CGEventSetFlags(ev, flags)
        post(ev)
        time.sleep(0.05)


def _key_to_pid(pid: int, want: bool) -> bool:
    """fn+shift+F 直接回竖屏（使用者实测 2026-11-07：LIVE「終了する」之后按它）。只用于退出；先投递给游戏进程，不动前台。"""
    if want:
        return False
    import Quartz

    _fn_shift_f(lambda ev: Quartz.CGEventPostToPid(pid, ev))
    return True


def _key_front(pid: int, want: bool) -> bool:
    """同上，投递给进程没生效时：确认游戏在前台后按一次（全局事件，前台不是游戏就不按）。"""
    if want or not _focus(pid):
        return False
    import Quartz

    _fn_shift_f(lambda ev: Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev))
    return True


METHODS = (("fn+shift+F", _key_to_pid), ("fn+shift+F（前台）", _key_front),
           ("全屏按钮", _ax_button), ("AXFullScreen 属性", _ax_attribute), ("菜单", _menu))

ROTATE_ENV = "HIRO_LIVE_ROTATE"
ROTATE_NAME = "旋转窗口"
TITLE_BAR = 28  # 标准标题栏高度（PlayCover 设置 hideTitleBar 关着）；宽高按内容区对调，标题栏不算进去
_saved_frames: dict[int, tuple[int, int, int, int]] = {}  # pid → 旋转前的窗口 (x, y, w, h)，退出时原样还原


def rotate_enabled() -> bool:
    return os.environ.get(ROTATE_ENV, "0") == "1"


def ax_window_frame(pid: int) -> tuple[int, int, int, int] | None:
    """主窗口的 (x, y, w, h)，辅助功能坐标（左上原点，单位点）。读不到返回 None。"""
    out = _osa(_ax_window(pid) + """
  set {xx, yy} to position of best
  set {ww, hh} to size of best
  return (xx as text) & "," & (yy as text) & "," & (ww as text) & "," & (hh as text)
end tell""")
    try:
        x, y, w, h = (int(float(v)) for v in out.split(","))
    except ValueError:
        return None
    return x, y, w, h


def _ax_set_frame(pid: int, frame: tuple[int, int, int, int]) -> bool:
    x, y, w, h = frame
    out = _osa(_ax_window(pid) + f"""
  set position of best to {{{x}, {y}}}
  set size of best to {{{w}, {h}}}
  return "ok"
end tell""")
    return out == "ok"


def _visible_area() -> tuple[int, int, int, int] | None:
    """主显示器可用区域（去掉菜单栏 / 程序坞），换成辅助功能坐标 (x, y, w, h)。"""
    try:
        from AppKit import NSScreen

        screens = NSScreen.screens()
        full, vis = screens[0].frame(), screens[0].visibleFrame()
    except Exception:  # noqa: BLE001
        return None
    top = full.size.height - (vis.origin.y + vis.size.height)
    return int(vis.origin.x), int(top), int(vis.size.width), int(vis.size.height)


def rotated_frame(frame, area=None, title_bar: int = TITLE_BAR) -> tuple[int, int, int, int]:
    """窗口内容区宽高对调后的窗口 frame：左上角不动，放不下就往回挪，比可用区域还大就等比缩小。"""
    x, y, w, h = frame
    cw, ch = w, h - title_bar
    nw, nh = ch, cw + title_bar
    if area is not None:
        ax, ay, aw, ah = area
        scale = min(1.0, aw / nw, ah / nh)
        if scale < 1.0:
            nw, nh = int(ch * scale), int(cw * scale) + title_bar
        x = max(ax, min(x, ax + aw - nw))
        y = max(ay, min(y, ay + ah - nh))
    return x, y, nw, nh


def window_landscape(pid: int) -> bool | None:
    frame = ax_window_frame(pid)
    return None if frame is None else frame[2] > frame[3]


def _rotate_window(pid: int, want: bool) -> bool:
    """进入（want=True）：窗口变横；退出：还原成进入前的 frame（没记录就再对调一次）。全屏中不动，交给全屏方式。"""
    if ax_is_fullscreen(pid):
        return False
    frame = ax_window_frame(pid)
    if frame is None:
        return False
    if (frame[2] > frame[3]) == want:
        return True  # 已经是目标方向
    if want:
        _saved_frames[pid] = frame
        target = rotated_frame(frame, _visible_area())
    else:
        target = _saved_frames.pop(pid, None) or rotated_frame(frame, _visible_area())
    logger.warning("旋转窗口：%s → %s", frame, target)
    return _ax_set_frame(pid, target)


def methods_for(want: bool, pid: int, rotate: bool | None = None) -> tuple:
    """这次切换按什么顺序试。开关关着：原来的 METHODS（切全屏）。开着：进入只旋转窗口，不再退回切全屏
    （使用者 2026-11-08：「现在没有必要再去全屏了」）——没救回画面就返回 failed，停下交给使用者；
    退出时窗口在全屏里（以前进的 / 手动进的）才走全屏方式退出，否则只还原窗口，不按 fn+shift+F（会反过来进全屏）。"""
    if not (rotate_enabled() if rotate is None else rotate):
        return METHODS
    rotate = ((ROTATE_NAME, _rotate_window),)
    if want:
        return rotate
    if ax_is_fullscreen(pid):
        return METHODS
    return rotate


def switch_fullscreen(want: bool, *, pid: int | None = None, shoot=None, methods=None, rotate: bool | None = None) -> str:
    """进入（want=True）/ 退出全屏，按 METHODS 顺序试，每步之后核对。返回成功的方式名，或 "locked" / "nogame" / "failed"。
    shoot：截图函数。给了就按画面核对（进入：中间不再全黑；退出：竖屏且不全黑），否则读 AXFullScreen 属性。"""
    pid = pid if pid is not None else _game_pid()
    if pid is None:
        return "nogame"
    if screen_locked():
        logger.warning("屏幕锁住 / 显示器休眠：切不了全屏，交给使用者")
        return "locked"

    def done(method):
        if shoot is not None:
            img = shoot()
            if img is not None:
                if want:
                    return not is_black(img)
                return img.shape[0] > img.shape[1] and not is_black(img)
        if method is _rotate_window:
            return window_landscape(pid) is want
        return ax_is_fullscreen(pid) is want

    for name, method in methods or methods_for(want, pid, rotate):
        try:
            ok = method(pid, want)
        except Exception as exc:  # noqa: BLE001  一种方式出错就换下一种
            logger.info("切全屏方式「%s」出错：%s", name, exc)
            ok = False
        if not ok:
            continue
        deadline = time.monotonic() + VERIFY_S
        while time.monotonic() < deadline:
            if done(method):
                logger.warning("%s全屏：用「%s」成功", "进入" if want else "退出", name)
                return name
            time.sleep(VERIFY_POLL)
        logger.warning("%s全屏：「%s」执行了但核对没通过，换下一种", "进入" if want else "退出", name)
        if method is _rotate_window and want and pid in _saved_frames:
            # 旋转没救回画面：把窗口还原成竖向（旋转模式只有这一种方式，接着返回 failed；
            # 手动传 methods 时下一种若是切全屏，也不会在横向窗口上进全屏）
            _ax_set_frame(pid, _saved_frames.pop(pid))
    return "failed"


def _game_pid() -> int | None:
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID)
    return int(apps[0].processIdentifier()) if apps and len(apps) else None


def _front_app():
    return NSWorkspace.sharedWorkspace().frontmostApplication()


def _click_menu(pid: int, items: tuple[str, ...]) -> bool:
    script = f"""
tell application "System Events" to tell (first process whose unix id is {pid})
  set frontmost to true
  delay 0.5
  repeat with mname in {{{", ".join(f'"{m}"' for m in MENUS)}}}
    try
      set theMenu to menu 1 of menu bar item (mname as text) of menu bar 1
      repeat with itm in {{{", ".join(f'"{i}"' for i in items)}}}
        if exists menu item (itm as text) of theMenu then
          click menu item (itm as text) of theMenu
          return "ok"
        end if
      end repeat
    end try
  end repeat
  return "none"
end tell"""
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    return r.stdout.strip() == "ok"


class LandscapeGuard:
    """controller 每轮喂一帧：黑屏持续一段时间 → 进入横向（旋转模式：旋转窗口；否则切全屏）；
    横向中又黑屏持续 / 竖屏有画面持续 → 退出（还原窗口或退全屏）并还原前台应用。fullscreen 字段泛指「已进入横向」，两种模式共用。"""

    BLACK_S = 20.0  # 切全屏模式：黑屏这么久才切（切全屏要抢前台，代价大）
    ROTATE_BLACK_S = 5.0  # 旋转模式：黑屏这么久就转（使用者 2026-11-08：缩短）
    EXIT_BLACK_S = 8.0
    EXIT_PORTRAIT_S = 10.0  # 全屏中竖屏且有画面持续这么久：不是 LIVE（误进全屏），退出

    def __init__(self, shoot=None, rotate: bool | None = None):
        self.shoot = shoot  # 截图函数（给了就按画面核对切换结果）
        self.rotate = rotate  # True：先旋转窗口再试全屏（watch 默认）；None：看 HIRO_LIVE_ROTATE
        self.last_result: str | None = None  # 最近一次切换的结果（方式名 / locked / nogame / failed）
        self.last_op: str | None = None  # 最近一次切换是进入（enter）还是退出（exit）全屏：停点提示按它给处置
        self.black_since: float | None = None
        self.fullscreen = False
        self.prev_front: NSRunningApplication | None = None
        self.entered_at = 0.0
        self.portrait_since: float | None = None
        self.rotate_retried = False  # 旋转模式：第一次转了还黑，已经静默重试过一次

    def rotating(self) -> bool:
        return rotate_enabled() if self.rotate is None else self.rotate

    def in_live(self, img) -> bool:
        """LIVE 演出中（横向画面、全屏中或黑屏）：画面本来就认不出，调用方不要按「持续未识别」停下。"""
        if img is None:
            return self.fullscreen
        return self.fullscreen or img.shape[1] > img.shape[0] or is_black(img)

    def tick(self, img, texts: list[str] | None = None) -> bool:
        """返回 True 表示本轮做了窗口操作（调用方跳过本轮分发）。"""
        now = time.monotonic()
        # 只看中间区域：全屏后窗口顶部有一条白边，整帧均值会超过阈值（q8 / q9 LIVE 结束后一直没退出全屏）
        black = is_black(img)
        if not black:
            self.black_since = None
        elif self.black_since is None:
            self.black_since = now
        if not self.fullscreen and img is not None and img.shape[1] > img.shape[0] and not black:
            # 横向且有画面：已经在全屏 LIVE 里（例如人工点了「进入全屏幕」），之后照常负责退出
            self.fullscreen, self.entered_at = True, now
        if not self.fullscreen:
            if black and now - self.black_since >= (self.ROTATE_BLACK_S if self.rotating() else self.BLACK_S):
                return self._enter()
            return False
        # 横向中：LIVE 结束后游戏回竖屏 → 横向窗口又是黑的
        if black and now - self.black_since >= self.EXIT_BLACK_S and now - self.entered_at > 30:
            return self._exit()
        # 横向中却是竖屏且有画面：不是 LIVE（比如长时间加载黑屏被当成 LIVE），过一会儿退出，免得一直当成 LIVE 不点
        portrait = img is not None and img.shape[0] > img.shape[1] and not black
        if not portrait:
            self.portrait_since = None
        elif self.portrait_since is None:
            self.portrait_since = now
        elif now - self.portrait_since >= self.EXIT_PORTRAIT_S and now - self.entered_at > 30:
            self.portrait_since = None
            return self._exit()
        return False

    def _enter(self) -> bool:
        pid = _game_pid()
        if pid is None:
            return False
        self.prev_front = _front_app()
        self.last_op = "enter"
        self.last_result = switch_fullscreen(True, pid=pid, shoot=self.shoot, rotate=self.rotate)
        ok = self.last_result not in SWITCH_FAILURES
        rotating = self.rotating()
        logger.warning("横屏 LIVE 黑屏：%s让窗口重新布局（%s）", "旋转窗口" if rotating else "进入全屏", self.last_result)
        if ok:
            self.fullscreen, self.entered_at, self.black_since = True, time.monotonic(), None
            self.rotate_retried = False
        elif rotating and self.last_result == "failed" and not self.rotate_retried:
            # 可能只是加载黑屏（窗口已由 switch_fullscreen 还原）：不报失败，重新计时再转一次，第二次还黑才停下
            self.rotate_retried = True
            self.last_result, self.black_since = None, time.monotonic()
        return ok

    def exit_now(self) -> bool:
        """调用方确认 LIVE 已结束（例如点了「ライブ終了確認」的「終了する」）：不等黑屏计时，马上回竖屏（还原窗口 / 退全屏）。"""
        return self._exit()

    def failure_cleared(self, img, every: float = 3.0) -> bool:
        """切换失败停下后（locked / failed / nogame），隔 every 秒重新核验一次环境和画面，恢复了就清掉失败标记、返回 True。
        只读屏幕锁状态、AXFullScreen 和传进来的画面，不做任何窗口操作；没恢复返回 False（调用方照旧停着）。
        - locked：屏幕已解锁、读得到 AXFullScreen 就清（按 AX 实际状态同步；还黑着时由 tick 按黑屏计时再试一次，
          那次再失败就是 failed，要使用者手动恢复画面，不会循环切换）；
        - 进入失败：画面已不黑（使用者手动进了全屏，或 LIVE 已结束回竖屏）；
        - 退出失败：画面竖屏且不黑，并且 AXFullScreen 读到 False（使用者手动退出了全屏）。"""
        if self.last_result not in SWITCH_FAILURES:
            return False
        now = time.monotonic()
        if now - getattr(self, "_recheck_at", -1e9) < every:
            return False
        self._recheck_at = now
        if screen_locked():
            return False
        pid = _game_pid()
        if pid is None:
            return False
        ax = ax_is_fullscreen(pid)
        if self.last_result == "locked":
            if ax is None:
                return False
            fullscreen = ax
        else:
            if img is None or is_black(img):
                return False
            height, width = img.shape[:2]
            if height == width:
                return False
            if self.last_op == "exit":
                if height < width or ax is not False:
                    return False
                fullscreen = False
            else:
                fullscreen = ax if ax is not None else width > height
        self.fullscreen = fullscreen
        self.entered_at = 0.0  # 全屏中：tick 的退出条件（黑屏 / 竖屏持续）不再额外等 30s
        self.black_since = self.portrait_since = None
        logger.warning("LIVE 切换失败（%s %s）后环境已恢复：%s全屏，继续", self.last_op, self.last_result, "仍在" if fullscreen else "不在")
        self.last_result = "manual"
        return True

    def _exit(self) -> bool:
        pid = _game_pid()
        self.last_op = "exit"
        self.last_result = switch_fullscreen(False, pid=pid, shoot=self.shoot, rotate=self.rotate) if pid is not None else "nogame"
        ok = self.last_result not in SWITCH_FAILURES
        logger.warning("LIVE 结束：%s（%s）", "还原窗口" if self.rotating() else "退出全屏", self.last_result)
        self.fullscreen, self.black_since = False, None
        if self.prev_front is not None and int(self.prev_front.processIdentifier()) != pid:
            self.prev_front.activateWithOptions_(0)
        self.prev_front = None
        return ok


def main(argv=None) -> int:
    """手动实测：python -m hiro.loop.landscape rotate|restore|show（只动窗口尺寸，不点游戏）。"""
    import argparse

    p = argparse.ArgumentParser(description="旋转 / 还原游戏窗口（HIRO_LIVE_ROTATE 的实测入口）")
    p.add_argument("action", choices=("rotate", "restore", "show"))
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    pid = _game_pid()
    if pid is None:
        print("游戏没在运行")
        return 1
    print("窗口", ax_window_frame(pid), "全屏" if ax_is_fullscreen(pid) else "非全屏")
    if a.action == "show":
        return 0
    want = a.action == "rotate"
    ok = _rotate_window(pid, want)  # 新进程没有旋转前的记录：restore 按内容区再对调一次还原
    time.sleep(1.0)
    print("结果", ok, "现在", ax_window_frame(pid))
    if want and window_landscape(pid) is False and pid in _saved_frames:
        # 被窗口比例锁（PlayCover「宽高比」自定义 9:16）压回竖窗：马上还原
        _ax_set_frame(pid, _saved_frames.pop(pid))
        print("没转成横向（窗口比例被锁住？），已还原", ax_window_frame(pid))
        return 2
    return 0 if ok and window_landscape(pid) is want else 2


if __name__ == "__main__":
    raise SystemExit(main())
