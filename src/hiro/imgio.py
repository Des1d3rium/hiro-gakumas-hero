"""存图：OpenCV 的 imwrite 在 Windows 上遇到非 ASCII 路径（「换卡」「强化」等中文目录）会静默失败、什么都不写
（Windows 第三局 reports/live/calib/*-换卡 全是空目录）。先编码再用 Python 写文件，各平台一致。"""

from pathlib import Path

import cv2


def imwrite(path, img, params=None) -> bool:
    ok, buf = cv2.imencode(Path(path).suffix or ".jpg", img, list(params or []))
    if ok:
        Path(path).write_bytes(buf.tobytes())
    return bool(ok)
