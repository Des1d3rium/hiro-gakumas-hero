# 来源：digital-hiro hiro/compat.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""让 kotonebot / KAA 在 macOS 上可以导入。

kotonebot 0.20.x 在模块顶层引用了 Windows 专属对象：
- `ctypes.WinDLL` 出现在类方法注解里（nemu_ipc/external_renderer_ipc.py），类定义时就会求值；
- `kotonebot.interop.win._mouse` 无条件 `import mouse`，该库在 Darwin 上直接抛 OSError。
两者在 PlayCover 后端都用不到，这里只做占位，必须在导入 kotonebot 之前执行。
"""

import ctypes
import sys
import types

if sys.platform == "darwin":
    if not hasattr(ctypes, "WinDLL"):
        ctypes.WinDLL = ctypes.CDLL
    sys.modules.setdefault("mouse", types.ModuleType("mouse"))
