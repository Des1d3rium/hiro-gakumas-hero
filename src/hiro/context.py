# 来源：digital-hiro hiro/context.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""kotonebot 运行上下文。KAA 的识别函数依赖全局上下文（image/ocr/color 等转发对象）。"""

from kotonebot.backend.context import get_context, init_context


def ensure_offline_context() -> None:
    """离线识别（只处理传入图片）时，用不截图的 MockDevice 初始化上下文。"""
    if get_context() is not None:
        return
    from kotonebot.debug.mock import MockDevice

    device = MockDevice()
    device.set_screen_size(720, 1280)
    init_context(target_device=device)
