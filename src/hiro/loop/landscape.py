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
"""

import logging
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


def switch_fullscreen(want: bool, *, pid: int | None = None, shoot=None, methods=None) -> str:
    """进入（want=True）/ 退出全屏，按 METHODS 顺序试，每步之后核对。返回成功的方式名，或 "locked" / "nogame" / "failed"。
    shoot：截图函数。给了就按画面核对（进入：中间不再全黑；退出：竖屏且不全黑），否则读 AXFullScreen 属性。"""
    pid = pid if pid is not None else _game_pid()
    if pid is None:
        return "nogame"
    if screen_locked():
        logger.warning("屏幕锁住 / 显示器休眠：切不了全屏，交给使用者")
        return "locked"

    def done():
        if shoot is not None:
            img = shoot()
            if img is not None:
                if want:
                    return not is_black(img)
                return img.shape[0] > img.shape[1] and not is_black(img)
        return ax_is_fullscreen(pid) is want

    for name, method in methods or METHODS:
        try:
            ok = method(pid, want)
        except Exception as exc:  # noqa: BLE001  一种方式出错就换下一种
            logger.info("切全屏方式「%s」出错：%s", name, exc)
            ok = False
        if not ok:
            continue
        deadline = time.monotonic() + VERIFY_S
        while time.monotonic() < deadline:
            if done():
                logger.warning("%s全屏：用「%s」成功", "进入" if want else "退出", name)
                return name
            time.sleep(VERIFY_POLL)
        logger.warning("%s全屏：「%s」执行了但核对没通过，换下一种", "进入" if want else "退出", name)
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
    """controller 每轮喂一帧：黑屏持续 BLACK_S 秒 → 进全屏；全屏中 LIVE 计时消失或又黑屏持续 → 退全屏并还原前台应用。"""

    BLACK_S = 20.0
    EXIT_BLACK_S = 8.0
    EXIT_PORTRAIT_S = 10.0  # 全屏中竖屏且有画面持续这么久：不是 LIVE（误进全屏），退出

    def __init__(self, shoot=None):
        self.shoot = shoot  # 截图函数（给了就按画面核对切换结果）
        self.last_result: str | None = None  # 最近一次切换的结果（方式名 / locked / nogame / failed）
        self.last_op: str | None = None  # 最近一次切换是进入（enter）还是退出（exit）全屏：停点提示按它给处置
        self.black_since: float | None = None
        self.fullscreen = False
        self.prev_front: NSRunningApplication | None = None
        self.entered_at = 0.0
        self.portrait_since: float | None = None

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
            if black and now - self.black_since >= self.BLACK_S:
                return self._enter()
            return False
        # 全屏中：LIVE 结束后游戏回竖屏 → 横向全屏窗口又是黑的
        if black and now - self.black_since >= self.EXIT_BLACK_S and now - self.entered_at > 30:
            return self._exit()
        # 全屏中却是竖屏且有画面：不是 LIVE（比如长时间加载黑屏被当成 LIVE），过一会儿退出，免得一直当成 LIVE 不点
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
        self.last_result = switch_fullscreen(True, pid=pid, shoot=self.shoot)
        ok = self.last_result not in SWITCH_FAILURES
        logger.warning("横屏 LIVE 黑屏 %.0fs：进入全屏让窗口重新布局（%s）", self.BLACK_S, self.last_result)
        if ok:
            self.fullscreen, self.entered_at, self.black_since = True, time.monotonic(), None
        return ok

    def exit_now(self) -> bool:
        """调用方确认 LIVE 已结束（例如点了「ライブ終了確認」的「終了する」）：不等黑屏计时，马上退出全屏回竖屏。"""
        return self._exit()

    def failure_cleared(self, img, every: float = 3.0) -> bool:
        """Verify a recovered frame without triggering any window operations."""
        if self.last_result not in SWITCH_FAILURES or img is None or is_black(img):
            return False
        now = time.monotonic()
        if now - getattr(self, "_recheck_at", -1e9) < every:
            return False
        self._recheck_at = now
        if screen_locked():
            return False
        height, width = img.shape[:2]
        if height == width:
            return False
        if self.last_op == "exit":
            pid = _game_pid()
            if height < width or pid is None or ax_is_fullscreen(pid) is not False:
                return False
        self.fullscreen = width > height
        self.entered_at = now if self.fullscreen else 0.0
        self.black_since = self.portrait_since = None
        self.last_result = "manual"
        logger.warning("LIVE 窗口画面已恢复，继续")
        return True

    def _exit(self) -> bool:
        pid = _game_pid()
        self.last_op = "exit"
        self.last_result = switch_fullscreen(False, pid=pid, shoot=self.shoot) if pid is not None else "nogame"
        ok = self.last_result not in SWITCH_FAILURES
        logger.warning("LIVE 结束：退出全屏（%s）", self.last_result)
        self.fullscreen, self.black_since = False, None
        if self.prev_front is not None and int(self.prev_front.processIdentifier()) != pid:
            self.prev_front.activateWithOptions_(0)
        self.prev_front = None
        return ok
