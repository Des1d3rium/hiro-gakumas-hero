# 来源：digital-hiro src/hiro/vision/offline.py（develop c3fc27f）
# 部分逻辑基于 KAA（kotones-auto-assistant，GPL-3.0）的识别接口、主数据与界面模板
"""离线识别环境：用 MockDevice 喂图，让 KAA 的识别函数作用在指定截图上。"""

import cv2
from cv2.typing import MatLike
from kaa.config.schema import KaaConfig
from kaa.kaa_context import init as kaa_init
from kotonebot.backend.context import device, get_context, init_context, manual_context
from kotonebot.debug.mock import MockDevice

_mock: MockDevice | None = None


def setup_offline() -> None:
    global _mock
    if _mock is not None:
        return
    kaa_init(KaaConfig(), "offline")
    _mock = MockDevice()
    _mock.set_screen_size(720, 1280)
    init_context(target_device=_mock, force=get_context() is not None)
    manual_context().begin()


def load(img: MatLike) -> MatLike:
    """把截图设为当前画面（缩放到 720x1280），返回实际使用的图。"""
    setup_offline()
    if img.shape[:2] != (1280, 720):
        img = cv2.resize(img, (720, 1280))
    _mock.load_image(img)
    return device.screenshot()
