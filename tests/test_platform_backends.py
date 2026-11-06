"""设备后端与 OCR 后端：与 KAA 支持的平台对齐（HIRO_DEVICE / HIRO_OCR），以及非 macOS 下能导入。"""

import os
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
    d = kaa_env.kaa_backend("mumu", platform="win32")
    assert d.lifecycle.instance_id == "0"  # 单开缺省 0（KAA 不设会报错）
    assert d.lifecycle.mumu_background_mode is True  # MuMu 6 不开后台模式，nemu_ipc 截到的是桌面
    assert kaa_env.kaa_backend("mumu", platform="win32", mumu_background=False).lifecycle.mumu_background_mode is False
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
    env = {**os.environ, "PYTHONPATH": str(SRC)}  # 保留原环境：Windows 上的 Python 缺 SYSTEMROOT 无法初始化
    env.pop("HIRO_OCR", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-2000:]


# ---------------------------------------------------------------- RapidOCR 读歪时的选行动页（2026-11-06 Windows 实测）
def test_lesson_text_tolerates_rapidocr_misreads():
    from hiro.vision import action

    def kind(text):
        return next((k for p, k in action.ACTIONS if p.search(text)), None)

    assert kind("V0公開レッズン") == "open_lesson"  # Vo.→V0、ス→ズ
    assert kind("W公開レッスン") == "open_lesson"  # Vi.→W
    assert kind("Da.公開レッスン") == "open_lesson"


@pytest.mark.parametrize(("bgr", "want"), [((180, 80, 240), "vo"), ((230, 140, 30), "da"), ((40, 150, 250), "vi")])
def test_attr_by_band_color(bgr, want):
    """文字条底色：粉=Vo、蓝=Da、橙=Vi（中间留一条白字，模拟按钮文字）。"""
    from hiro.vision import action

    img = np.full((1280, 720, 3), 255, np.uint8)
    img[1090:1120, 100:260] = bgr
    img[1100:1110, 150:210] = 255
    assert action._attr_by_band(img, 180, 1105) == want


# ---------------------------------------------------------------- 字形归一后的安全拦截（RapidOCR：「タイトルへ」→「タイトルヘ」）
def test_textnorm_folds_lookalike_kana():
    from hiro.vision.textnorm import contains, fold

    assert fold("タイトルヘ") == fold("タイトルへ")
    assert contains("メタイトルヘ", "タイトルへ")
    assert contains("通信工ラー", "エラー")
    assert not contains("リトライ", "タイトルへ")


def test_rescue_blocks_title_button_read_as_katakana_he():
    """LLM 兜底不能点「タイトルへ」：读成片假名「ヘ」也要拦住。"""
    from hiro.loop.rescue import check

    boxes = [("通信中にエラーが発生しました", 0.99, (60, 1042, 396, 1068)), ("タイトルヘ", 0.96, (112, 1142, 313, 1178)),
             ("リトライ", 1.0, (470, 1146, 578, 1175))]  # fmt: skip
    act, why = check({"action": "tap_box", "box": 1}, boxes)
    assert act["action"] == "stop" and "危险词" in why


def test_comm_error_never_picks_merged_title_box():
    """两个按钮被读成一个框「タイトルヘリトライ」时，不能把它当成リトライ去点（中心落在两个按钮之间）。"""
    from hiro.loop.comm_error import comm_error

    boxes = [("通信エラー", 0.99, (59, 957, 216, 989)), ("タイトルヘリトライ", 0.9, (112, 1142, 578, 1178))]
    assert comm_error(boxes)["retry"] is None


def test_advance_blocks_title_read_as_katakana_he():
    from hiro.loop.advance import blocked

    assert blocked(["タイトルヘ"]) is not None


# ---------------------------------------------------------------- Codex CLI 作为 LLM（Windows 上用 ChatGPT 账号）
def test_codex_adapter_spec_and_missing_binary(monkeypatch):
    from hiro.brain import llm

    monkeypatch.setenv("HIRO_CODEX_BIN", "")
    monkeypatch.setattr("shutil.which", lambda _: None)
    a = llm.from_spec("codex:gpt-6-astra")
    assert isinstance(a, llm.CodexCLI) and a.model == "gpt-6-astra"
    a.binary = None
    r = a.ask_json("sys", "user")
    assert r.data is None and "找不到 codex" in r.error


def test_codex_adapter_passes_utf8_prompt_and_reads_reply(monkeypatch, tmp_path):
    """系统提示 + 用户提示拼成 UTF-8 从标准输入传；回复从 -o 文件读；在临时目录里跑（不读仓库 AGENTS.md）。"""
    import subprocess as sp
    from pathlib import Path

    from hiro.brain import llm

    seen = {}

    def fake_run(cmd, input=None, capture_output=None, timeout=None, cwd=None):
        seen.update(cmd=cmd, prompt=input.decode("utf-8"), cwd=cwd)
        Path(cmd[cmd.index("-o") + 1]).write_text('{"action": "play", "card": 1}', encoding="utf-8")
        return sp.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    a = llm.CodexCLI(binary="codex.exe")
    r = a.ask_json("系统：只输出 JSON", "用户：出哪张牌")
    assert r.error is None and r.data == {"action": "play", "card": 1}
    assert "系统：只输出 JSON" in seen["prompt"] and "用户：出哪张牌" in seen["prompt"]
    assert seen["cmd"][-1] == "-" and "read-only" in seen["cmd"] and "--ephemeral" in seen["cmd"]
    assert seen["cwd"] and "hiro-codex-" in seen["cwd"]
    r = a.ask_json_image("s", "u", b"\xff\xd8jpeg")
    assert "-i" in seen["cmd"] and r.data == {"action": "play", "card": 1}


# ---------------------------------------------------------------- 本战选课：缺倍率换算数据时按 R1 回合数近似（R1_TURN_PROXY）
def test_r1_turn_weighted_gain_prefers_heavier_r1_attr():
    from types import SimpleNamespace

    from hiro.state import context as C
    from hiro.state.produce import FINALS

    st = SimpleNamespace(mode=FINALS, params={"Vo": 1710, "Da": 2268, "Vi": 913}, param_cap=3200, idol_card_id="i_card-shro-3-007")
    vo = C.r1_turn_weighted_gain(st, {"Vo": 180})
    vi = C.r1_turn_weighted_gain(st, {"Vi": 180})
    da = C.r1_turn_weighted_gain(st, {"Da": 180})
    assert vo > da > vi  # 篠澤広 均衡型 R1 回合 Vo 4 / Da 3 / Vi 2
    capped = SimpleNamespace(**{**st.__dict__, "params": {"Vo": 3150, "Da": 2268, "Vi": 913}})
    assert C.r1_turn_weighted_gain(capped, {"Vo": 180}) == 4 * 50  # 到上限为止
    assert C.r1_turn_weighted_gain(SimpleNamespace(**{**st.__dict__, "mode": "produce-007"}), {"Vo": 180}) is None  # 选拔不算
