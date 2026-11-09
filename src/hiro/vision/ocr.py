# 来源：digital-hiro src/hiro/vision/ocr.py（develop c3fc27f）；开源版：加 RapidOCR 后端，非 macOS 也能用
"""固定区域的数字和短文本 OCR。两个后端，接口相同：

- vision：Apple Vision（macOS 系统文字识别）。实测在作者机器上比 KAA 的 RapidOCR 快 2–7 倍且更准（见 README），
  macOS 上默认用它。区域先放大 3 倍再识别，小号描边数字更稳。
- rapidocr：KAA 自带的 RapidOCR（PaddleOCR 日文 / 英文模型，kotonebot.backend.ocr）。非 macOS（模拟器 / DMM）用它；
  **识别率与速度没有在本项目的回归截图上核对过**，换平台后先用回归截图验一遍（见 docs/assets.md）。
- winocr：Windows 自带的文字识别（Windows.Media.Ocr，经 winrt 调用；要装了日文 OCR 语言，Windows 设置里加日语即可）。
  Windows 实测单块区域约 0.05 s。它按「词」返回结果，日文是一字一词：这里按间隙把一行拆成段、段内拼回文字（见 _split_words / _join_words）。

- ppocr5：rapidocr 包里的 PaddleOCR PP-OCRv5（中日英一个模型，onnxruntime），比 KAA 自带的 RapidOCR 模型新；
  Mac / Windows 结果一致。中日混合模型会把部分汉字认成简体（强化、交换済），按 _SIMPLIFIED 换回日文写法。

HIRO_OCR=auto（缺省：macOS 且能导入 Vision → vision，否则 rapidocr）/ vision / rapidocr / winocr / ppocr5。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import cv2
from cv2.typing import MatLike

ENGINES = ("auto", "vision", "rapidocr", "winocr", "ppocr5")
_engine: str | None = None


def _vision_available() -> bool:
    try:
        import Foundation  # noqa: F401
        import Vision  # noqa: F401
    except ImportError:
        return False
    return True


def engine() -> str:
    """当前 OCR 后端（首次调用时按 HIRO_OCR 和平台确定，之后不变）。"""
    global _engine
    if _engine is None:
        want = (os.environ.get("HIRO_OCR") or "auto").strip().lower()
        if want not in ENGINES:
            raise ValueError(f"HIRO_OCR={want!r} 不认识；可选：{', '.join(ENGINES)}")
        if want == "auto":
            want = "vision" if sys.platform == "darwin" and _vision_available() else "rapidocr"
        _engine = want
    return _engine


def _vision_raw(img: MatLike, w: int, h: int, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；框按归一化坐标换回原图尺寸 (w, h)。"""
    import Vision
    from Foundation import NSData

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
        bb = obs.boundingBox()  # 归一化坐标，原点在左下
        x1, y1 = bb.origin.x * w, (1 - bb.origin.y - bb.size.height) * h
        out.append((str(cand.string()), float(cand.confidence()), (round(x1), round(y1), round(x1 + bb.size.width * w), round(y1 + bb.size.height * h))))
    return out


def _rapid_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；框除以 scale 换回原图坐标。只要英文时用英文模型（数字），否则日文模型。"""
    from kotonebot.backend import ocr as kocr
    from kotonebot.primitives.geometry import Rect

    eng = kocr.en() if tuple(languages) == ("en-US",) else kocr.jp()
    h, w = img.shape[:2]
    # 传整图 rect：kotonebot 不传 rect 时返回的是补白后画布上的坐标，传了才会减掉补白偏移（original_rect）
    res = eng.ocr(img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), rect=Rect(xywh=(0, 0, w, h)))
    out = []
    for r in res:
        x, y, rw, rh = r.original_rect.xywh
        out.append((r.text, float(r.confidence), (round(x / scale), round(y / scale), round((x + rw) / scale), round((y + rh) / scale))))
    return out


_win: dict = {}


def _win_engine(languages):
    """按语言缓存 OcrEngine（只要英文时用 en-US，否则 ja）；事件循环也缓存，recognize_async 在里面同步跑完。"""
    import asyncio

    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    tag = "en-US" if tuple(languages) == ("en-US",) else "ja"
    if tag not in _win:
        eng = OcrEngine.try_create_from_language(Language(tag))
        if eng is None:
            raise RuntimeError(f"Windows OCR 没有 {tag} 语言：在 Windows 设置 → 时间和语言 → 语言里添加日语（含 OCR）")
        _win[tag] = eng
    if "loop" not in _win:
        _win["loop"] = asyncio.new_event_loop()
    return _win[tag], _win["loop"]


def _is_ascii_word(t: str) -> bool:
    return bool(t) and t.isascii() and any(c.isalnum() for c in t)


def _split_words(words: list[tuple[str, tuple[float, float, float, float]]]) -> list[list[tuple]]:
    """Windows OCR 会把同一水平线上相隔很远的几段字并成一行（货架一排价格「70 49 105 35」），Apple Vision 是分开的。
    间隙大于字高的 1.2 倍就断成新的一段，各自出框。"""
    segs: list[list[tuple]] = []
    for wd in words:
        x, _, _, h = wd[1]
        if segs:
            px, _, pw, ph = segs[-1][-1][1]
            if x - (px + pw) <= 1.2 * max(h, ph):
                segs[-1].append(wd)
                continue
        segs.append([wd])
    return segs


def _join_words(words: list[tuple[str, tuple[float, float, float, float]]]) -> str:
    """一段里的词拼成文字：日文一字一词、字间不留空格；只有两边都是英文 / 数字且确实隔开（间隙 ≥ 字高 0.35 倍）才留一个空格。"""
    out = ""
    prev = None
    for text, (x, _, w, h) in words:
        if prev is not None:
            ptext, px, pw, ph = prev
            gap = x - (px + pw)
            if _is_ascii_word(ptext) and _is_ascii_word(text[:1]) and gap >= 0.35 * max(h, ph):
                out += " "
        out += text
        prev = (text, x, w, h)
    return out


def _win_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；框除以 scale 换回原图坐标。Windows OCR 不给置信度，统一记 1.0。"""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    eng, loop = _win_engine(languages)
    bgra = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA if img.ndim == 2 else cv2.COLOR_BGR2BGRA)
    h, w = bgra.shape[:2]
    if min(h, w) < 40:   # 太小的图 Windows OCR 读不出，向右下补到 40（不影响原坐标）
        bgra = cv2.copyMakeBorder(bgra, 0, max(0, 40 - h), 0, max(0, 40 - w), cv2.BORDER_REPLICATE)
        h, w = bgra.shape[:2]
    dw = DataWriter()
    dw.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    res = loop.run_until_complete(eng.recognize_async(bmp))
    out = []
    for line in res.lines:
        ws = [(wd.text, (wd.bounding_rect.x, wd.bounding_rect.y, wd.bounding_rect.width, wd.bounding_rect.height)) for wd in line.words]
        if not ws:
            continue
        ws.sort(key=lambda t: t[1][0])
        for seg in _split_words(ws):
            x1 = min(b[0] for _, b in seg)
            y1 = min(b[1] for _, b in seg)
            x2 = max(b[0] + b[2] for _, b in seg)
            y2 = max(b[1] + b[3] for _, b in seg)
            out.append((_join_words(seg), 1.0, (round(x1 / scale), round(y1 / scale), round(x2 / scale), round(y2 / scale))))
    return out


# 只在简体中文里出现、日文不用的字 → 日文写法（PP-OCRv5 中日混合模型的读法；两边写法相同的字不收）
_SIMPLIFIED = str.maketrans(dict(zip(
    "强换查选择认识确战试验费结气动习课讲获领张级时间开关闭门问题说话读买卖价钱饮剧场进还对应实际现发处务员东车运转轮输边过达连补态优胜败负记录设计让训练绩续继绝经给线红约纪细终组织编缘网罗义书页顶项顺须预频颜风飞饰马驱鱼鸟乐兴举产亲亿传伤众华单卫历压变叶听启园围图圣坏块报备头夺奋宽导层岁岛带广库废异弹归忆怀总恶惊惯戏户扩扫护损据挥摄敌无显晓暂术机杂权极构标样树桥检欢测济满灵烧热爱猎环电疗盘盖监码础种积稳竞笔简类紧纯纸绘统维综绿缓缩罚职联脑艺节范药营虑见观规视览觉议许论访证评诉词译诗该详语误请诸谁调谈谢谱贝贡财责贤货质贩贯贵贴贸资赏赖赞赠跃践轻载较辅辈迁远违适递遗邮邻释钟钢银锁锐错键镜长闪闻阳阶阵陆险隐难额馆骑鲜鸣",
    "強換査選択認識確戦試験費結気動習課講獲領張級時間開関閉門問題説話読買売価銭飲劇場進還対応実際現発処務員東車運転輪輸辺過達連補態優勝敗負記録設計譲訓練績続継絶経給線紅約紀細終組織編縁網羅義書頁頂項順須預頻顔風飛飾馬駆魚鳥楽興挙産親億伝傷衆華単衛歴圧変葉聴啓園囲図聖壊塊報備頭奪奮寛導層歳島帯広庫廃異弾帰憶懐総悪驚慣戯戸拡掃護損拠揮摂敵無顕暁暫術機雑権極構標様樹橋検歓測済満霊焼熱愛猟環電療盤蓋監碼礎種積穏競筆簡類緊純紙絵統維総緑緩縮罰職聯脳芸節範薬営慮見観規視覧覚議許論訪証評訴詞訳詩該詳語誤請諸誰調談謝譜貝貢財責賢貨質販貫貴貼貿資賞頼賛贈躍践軽載較輔輩遷遠違適逓遺郵隣釈鐘鋼銀鎖鋭錯鍵鏡長閃聞陽階陣陸険隠難額館騎鮮鳴",
    strict=True,
)))
_pp: dict = {}


PP_DIR_DEFAULT = Path(__file__).resolve().parents[3] / "cache" / "ppocr5"


def _pp_engine(languages):
    """PP-OCRv5 mobile（检测 + 中日英识别）。模型从 HIRO_PPOCR_DIR（缺省 cache/ppocr5）加载：KAA 把 onnxruntime 固定在 1.14
    （Windows DLL 问题），只认 IR ≤ 8，rapidocr 自带的 v5 模型是 IR 10，要先用 tools/assets/ppocr_models.py 转一份。
    数字也用同一个模型（英文模型读本项目的小号描边数字没有更好），languages 不再区分。"""
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    if "eng" not in _pp:
        d = Path(os.environ.get("HIRO_PPOCR_DIR") or PP_DIR_DEFAULT)
        det, rec = d / "ch_PP-OCRv5_det_mobile.onnx", d / "ch_PP-OCRv5_rec_mobile.onnx"
        if not det.exists() or not rec.exists():
            raise RuntimeError(f"HIRO_OCR=ppocr5 要的模型不在 {d}：先跑 python tools/assets/ppocr_models.py")
        _pp["eng"] = RapidOCR(params={
            "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE, "Det.model_path": str(det),
            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE, "Rec.model_path": str(rec),
            "Rec.lang_type": LangRec.CH, "Global.log_level": "error",
        })  # fmt: skip
    return _pp["eng"]


def _pp_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；四点框取外接矩形、除以 scale 换回原图坐标；简体字换回日文写法。"""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    res = _pp_engine(languages)(img)
    if res.boxes is None or res.txts is None:
        return []
    out = []
    for box, text, conf in zip(res.boxes, res.txts, res.scores, strict=False):
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append((str(text).translate(_SIMPLIFIED), float(conf),
                    (round(min(xs) / scale), round(min(ys) / scale), round(max(xs) / scale), round(max(ys) / scale))))
    return out


def ocr_boxes(img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """每段文字、置信度和它在 img 中的外接框 (x1, y1, x2, y2)。"""
    h, w = img.shape[:2]
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    eng = engine()
    if eng == "vision":
        return _vision_raw(img, w, h, languages)
    if eng == "winocr":
        return _win_raw(img, scale, languages)
    if eng == "ppocr5":
        return _pp_raw(img, scale, languages)
    return _rapid_raw(img, scale, languages)


def ocr(img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[tuple[str, float]]:
    return [(t, c) for t, c, _ in ocr_boxes(img, scale=scale, languages=languages)]


def digits_image(img: MatLike, *, white_min: int = 200) -> MatLike:
    """白色描边数字 → 白底黑字：取接近白色的像素作为字形，加边框，便于识别小号数字。"""
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
