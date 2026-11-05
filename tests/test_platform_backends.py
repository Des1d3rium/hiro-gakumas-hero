"""设备后端与 OCR 后端：与 KAA 支持的平台对齐（HIRO_DEVICE / HIRO_OCR），以及非 macOS 下能导入。"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from hiro import device
from hiro.vision import ocr

SRC = Path(__file__).resolve().parents[1] / "src"


# ---------------------------------------------------------------- 设备后端
def test_auto_on_mac_prefers_maatools_then_quartz():
    assert device.resolve_backend("auto", platform="darwin", maatools_up=True) == "maatools"
    assert device.resolve_backend("auto", platform="darwin", maatools_up=False) == "quartz"


def test_auto_elsewhere_requires_explicit_device():
    with pytest.raises(RuntimeError, match="HIRO_DEVICE"):
        device.resolve_backend("auto", platform="win32", maatools_up=False)


def test_explicit_backend_passes_through(monkeypatch):
    for name in ("maatools", "quartz", "adb", "mumu", "leidian", "dmm"):
        monkeypatch.setenv("HIRO_DEVICE", name)
        assert device.resolve_backend() == name


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("HIRO_DEVICE", "bluestacks")
    with pytest.raises(ValueError, match="可选"):
        device.requested_backend()


def test_playcover_only_fallbacks(monkeypatch):
    monkeypatch.setattr(device, "BACKEND", None)
    monkeypatch.setenv("HIRO_DEVICE", "mumu")
    assert device.is_playcover() is False
    monkeypatch.setenv("HIRO_DEVICE", "maatools")
    assert device.is_playcover() is True
    monkeypatch.setattr(device, "BACKEND", "adb")  # connect 之后以实际后端为准
    assert device.is_playcover() is False


def test_landscape_guard_inactive_never_toggles_fullscreen():
    from hiro.loop.landscape import LandscapeGuard

    g = LandscapeGuard(active=False)
    black = np.zeros((1280, 720, 3), np.uint8)
    g.black_since = -1e9  # 早已黑屏很久
    assert g.tick(black) is False
    assert g.in_live(black) is True  # 判断 LIVE 照常（调用方据此不按「认不出」停机）


# ---------------------------------------------------------------- KAA 后端配置
kaa_env = pytest.importorskip("hiro.kaa_env")


@pytest.fixture
def kaa_config():
    pytest.importorskip("kaa.config.schema")
    from kaa.config.schema import KaaConfig

    return KaaConfig


def test_playcover_keeps_kaa_default(kaa_config):
    for name in ("auto", "maatools", "quartz"):
        assert kaa_env.kaa_backend(name) is None


@pytest.mark.parametrize(
    ("name", "platform", "kind", "shot"),
    [
        ("adb", "darwin", "custom", "adb"),
        ("leidian", "win32", "leidian", "adb"),
        ("mumu", "win32", "mumu12v5", "nemu_ipc"),
        ("mumu", "darwin", "mumu12v5", "adb"),
        ("dmm", "win32", "dmm", "windows_background"),
    ],
)
def test_kaa_backend_defaults_pass_kaa_validation(kaa_config, name, platform, kind, shot):
    from kaa.config.validation import validate_profile_config

    b = kaa_env.kaa_backend(name, platform=platform)
    assert (b.lifecycle.type, b.screenshot_impl) == (kind, shot)
    cfg = kaa_config()
    cfg.backend = b
    assert [i.message for i in validate_profile_config(cfg) if i.severity == "error"] == []


def test_kaa_backend_adb_address_and_instance(kaa_config):
    b = kaa_env.kaa_backend("adb", adb="127.0.0.1:16384")
    assert (b.connection.type, b.connection.ip, b.connection.port) == ("tcp", "127.0.0.1", 16384)
    m = kaa_env.kaa_backend("mumu", instance="2", platform="win32")
    assert m.connection.type == "auto" and m.lifecycle.instance_id == "2"
    with pytest.raises(ValueError, match="ip:port"):
        kaa_env.kaa_backend("adb", adb="127.0.0.1")


def test_bad_screenshot_combo_is_caught_by_kaa_validation(kaa_config):
    from kaa.config.validation import validate_profile_config

    cfg = kaa_config()
    cfg.backend = kaa_env.kaa_backend("dmm", screenshot="adb")
    assert any(i.severity == "error" for i in validate_profile_config(cfg))


# ---------------------------------------------------------------- OCR 后端
def test_ocr_engine_selection(monkeypatch):
    monkeypatch.setattr(ocr, "_engine", None)
    monkeypatch.setenv("HIRO_OCR", "rapidocr")
    assert ocr.engine() == "rapidocr"
    monkeypatch.setattr(ocr, "_engine", None)
    monkeypatch.setenv("HIRO_OCR", "tesseract")
    with pytest.raises(ValueError, match="可选"):
        ocr.engine()


def test_rapidocr_boxes_scaled_back(monkeypatch):
    """RapidOCR 路径：放大 3 倍识别，框要除回原图坐标；只要英文时用英文模型。"""
    from kotonebot.backend import ocr as kocr
    from kotonebot.primitives.geometry import Rect

    class Res:
        def __init__(self, text, xywh):
            self.text, self.confidence, self.original_rect = text, 0.9, Rect(xywh=xywh)

    calls = []

    class Eng:
        def __init__(self, tag):
            self.tag = tag

        def ocr(self, img, rect=None):
            calls.append((self.tag, img.shape[:2], rect.xywh))
            return [Res("123", (30, 60, 90, 30))]

    monkeypatch.setattr(kocr, "jp", lambda: Eng("jp"))
    monkeypatch.setattr(kocr, "en", lambda: Eng("en"))
    monkeypatch.setattr(ocr, "_engine", "rapidocr")
    img = np.full((40, 100, 3), 255, np.uint8)
    assert ocr.ocr_boxes(img) == [("123", 0.9, (10, 20, 40, 30))]
    assert calls[-1] == ("jp", (120, 300), (0, 0, 300, 120))  # 传整图 rect，避免拿到补白画布坐标
    assert ocr.ocr(img, languages=("en-US",)) == [("123", 0.9)]
    assert calls[-1][0] == "en"


# ---------------------------------------------------------------- 非 macOS 能导入
def test_modules_import_without_macos_frameworks():
    """挡住 AppKit / Quartz / Vision / Foundation / objc，模拟 Windows / Linux：这些模块顶层不能依赖它们。"""
    code = """
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('AppKit', 'Quartz', 'Vision', 'Foundation', 'objc'):
            raise ImportError('blocked: ' + name)
sys.meta_path.insert(0, Block())
import hiro.vision.ocr as o, hiro.loop.landscape, hiro.device as d
o._engine = None
import os; os.environ.pop('HIRO_OCR', None)
assert o.engine() == 'rapidocr', o.engine()
print('ok')
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"PYTHONPATH": str(SRC), "PATH": ""})
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-2000:]
