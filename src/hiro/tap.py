# 来源：digital-hiro hiro/tap.py（c3fc27f）
"""手动导航：在 720x1280 逻辑坐标点击，等待后截图保存，便于逐步走页面。

    .venv/bin/python -m hiro.tap 360 1161 [--wait 1.5] [--out reports/live/tap.png]
    .venv/bin/python -m hiro.tap --shot-only
"""

import argparse
import time

import cv2

import hiro  # noqa: F401
from hiro.device import connect
from kotonebot.backend.context import device, init_context, manual_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("x", type=int, nargs="?")
    parser.add_argument("y", type=int, nargs="?")
    parser.add_argument("--wait", type=float, default=1.5)
    parser.add_argument("--out", default="reports/live/tap.png")
    parser.add_argument("--shot-only", action="store_true")
    args = parser.parse_args()
    init_context(target_device=connect())
    manual_context().begin()
    if not args.shot_only:
        device.click(args.x, args.y)
        time.sleep(args.wait)
    cv2.imwrite(args.out, device.screenshot())
    print(args.out)


if __name__ == "__main__":
    main()
