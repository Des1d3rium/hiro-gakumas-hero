# 来源：digital-hiro src/hiro/vision/ocr.py（develop c3fc27f）
"""Apple Vision OCR（macOS 系统文字识别），用于读固定区域的数字和短文本。

实测在本机比 KAA 的 RapidOCR 快 2–7 倍且更准（见 README）。区域先放大 3 倍再识别，小号描边数字更稳。
"""

import re

import cv2
import Vision
from cv2.typing import MatLike
from Foundation import NSData


def ocr(img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[tuple[str, float]]:
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".png", img)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(buf.tobytes(), len(buf)), None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)  # accurate
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(False)
    handler.performRequests_error_([request], None)
    out = []
    for obs in request.results() or []:
        cand = obs.topCandidates_(1)[0]
        out.append((str(cand.string()), float(cand.confidence())))
    return out


def ocr_boxes(img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """同 ocr()，另给出每段文字在 img 中的外接框 (x1, y1, x2, y2)。"""
    h, w = img.shape[:2]
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".png", img)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(buf.tobytes(), len(buf)), None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(False)
    handler.performRequests_error_([request], None)
    out = []
    for obs in request.results() or []:
        cand = obs.topCandidates_(1)[0]
        bb = obs.boundingBox()  # 归一化坐标，原点在左下
        x1, y1 = bb.origin.x * w, (1 - bb.origin.y - bb.size.height) * h
        out.append((str(cand.string()), float(cand.confidence()), (round(x1), round(y1), round(x1 + bb.size.width * w), round(y1 + bb.size.height * h))))
    return out


def digits_image(img: MatLike, *, white_min: int = 200) -> MatLike:
    """白色描边数字 → 白底黑字：取接近白色的像素作为字形，加边框，便于 Vision 识别小号数字。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    _, mask = cv2.threshold(gray, white_min, 255, cv2.THRESH_BINARY)
    inv = 255 - mask
    return cv2.copyMakeBorder(inv, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)


def ocr_digits(img: MatLike, *, white_min: int = 200, scale: float = 3.0) -> int | None:
    pre = digits_image(img, white_min=white_min)
    text = " ".join(t for t, _ in ocr(cv2.cvtColor(pre, cv2.COLOR_GRAY2BGR), scale=scale, languages=("en-US",)))
    text = text.replace(",", "").replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def ocr_text(img: MatLike, **kw) -> str:
    return " ".join(t for t, _ in ocr(img, **kw))


def ocr_int(img: MatLike, **kw) -> int | None:
    text = ocr_text(img, **kw).replace(",", "").replace("O", "0").replace("o", "0")
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def ocr_batch(crops: list[MatLike], *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[str]:
    """多块区域各自 OCR，返回每块按 Vision 结果顺序拼接的文字。

    试过把多块竖向拼成一张图只调一次 Vision：快约一半，但精度明显下降（突破线「64」读成「9A」、卡名的「+」
    读成「-十」，换放大倍数和间隔都不稳），线程并发也不会更快（Vision 内部串行）。所以仍逐块调用，
    省时间靠调用方的区域缓存（hiro.vision.region_cache）跳过没变的区域。"""
    return ["".join(t for t, _ in ocr(c, scale=scale, languages=languages)) for c in crops]
