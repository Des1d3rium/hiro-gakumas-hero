"""测试分层：没有截图样例 / 没装实机依赖 / 依赖的层还没在仓库里时自动跳过，CI 只跑不读截图的部分。

- 识别层很多用例读 tests/fixtures/ 下的实机截图。截图属于游戏画面，仓库不带；把自己的截图按同样的相对路径放进
  tests/fixtures/（或设 HIRO_FIXTURES 指向一个同结构目录，会软链过来）就能跑。没有样例目录时这些用例跳过。
- 识别层依赖 KAA（可选组 play：pip install -e '.[play]'，子模块 vendor/kaa）。导入不了 kotonebot / kaa 时跳过。
- 个别用例引到操作层 / 策略层（hiro.loop.controller、hiro.produce、hiro.brain.*）或不开源的模拟器（hiro.sim）；
  这些模块不在时，整个文件跳过。
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
if not FIXTURES.exists() and os.environ.get("HIRO_FIXTURES") and Path(os.environ["HIRO_FIXTURES"]).is_dir():
    FIXTURES.symlink_to(Path(os.environ["HIRO_FIXTURES"]).resolve(), target_is_directory=True)
HAVE_FIXTURES = FIXTURES.is_dir() and any(FIXTURES.iterdir())  # 空目录 / 断链都算没有样例

# 按钮注册表（hiro.ui.buttons）在导入时取值：测试不读使用者本机的 conf/ui/buttons.json
os.environ.setdefault("HIRO_BUTTONS", str(Path(__file__).resolve().parent / ".no-user-buttons.json"))
if os.environ.get("HIRO_KAA_GAME_DATA"):
    try:  # 先把 KAA 的数据目录指过去（hiro.vision 导入时做）：别的测试模块先导入 KAA 会在当前目录建空的 resources/game_data
        import hiro.vision  # noqa: F401
    except Exception:  # noqa: BLE001 没装识别依赖：照常收集
        pass

OPTIONAL_LAYERS = ("hiro.loop.controller", "hiro.loop.kaa_patches", "hiro.produce", "hiro.brain.state", "hiro.brain.cards",
                   "hiro.brain.deciders", "hiro.brain.rules", "hiro.brain.replay", "hiro.sim")  # fmt: skip
KAA_USERS = re.compile(r"hiro\.(?:vision|perception)(?:\.(\w+))?")
KAA_FREE = {"assets", "layout", "items", "region_cache"}  # 导入时不碰 KAA 的识别层模块（items 只在读饮料时才导入 KAA）


def _needs_kaa(src: str) -> bool:
    """测试源码引用了要 KAA 的识别层模块（只引用 KAA_FREE 里的模块不算）。"""
    return any(m.group(1) not in KAA_FREE for m in KAA_USERS.finditer(src))


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _kaa_ok() -> bool:
    try:
        import kotonebot  # noqa: F401
        import kaa  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 没装 play 组，或 macOS 上缺 hiro.compat 的补丁
        return False


def _top_level(src: str) -> str:
    """模块顶层语句（不缩进的行），导入时就会执行的部分。"""
    return "\n".join(line for line in src.splitlines() if line and not line[0].isspace() and not line.startswith(("#", '"', "'")))


_TOP_PATH = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(Path\(__file__\)\S.*?)\s*(?:#.*)?$", re.M)
_TOP_READ = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\.read_text\(")


def _missing_top_files(f: Path, top: str) -> list[Path]:
    """模块顶层就要读的文件（`X = Path(__file__)…` 常量拼出来的 `(…).read_text()`）哪些不在。
    按具体文件判断：tests/fixtures 是空目录、或只放了部分样例时，缺 action/truth.json 的 test_action.py 不收集，别的照常。
    只对路径表达式求值（不给 builtins），算不出来的读法不管（交给 pytest 正常报错）。"""
    ns: dict = {"Path": Path, "__file__": str(f)}
    for m in _TOP_PATH.finditer(top):
        try:
            ns[m.group(1)] = eval(m.group(2), {"__builtins__": {}}, ns)  # noqa: S307 只有测试文件里的路径常量
        except Exception:  # noqa: BLE001
            continue
    missing = []
    for m in _TOP_READ.finditer(top):
        try:
            path = eval(m.group(1), {"__builtins__": {}}, ns)  # noqa: S307
        except Exception:  # noqa: BLE001
            continue
        if isinstance(path, Path) and not path.is_file():
            missing.append(path)
    return missing


def _collect_ignore() -> list[str]:
    """导入时就会失败的测试文件（顶层导入了不在仓库里的层 / 顶层读样例截图），不收集。"""
    out = []
    for f in sorted(TESTS.glob("test_*.py")):
        top = _top_level(f.read_text(encoding="utf-8"))
        if any(re.search(rf"^(from|import) {re.escape(m)}\b", top, re.M) and not _has_module(m) for m in OPTIONAL_LAYERS):
            out.append(f.name)
        elif _missing_top_files(f, top):
            out.append(f.name)
        elif _top_import_missing_optional(top):
            out.append(f.name)
    return out


OPTIONAL_DEPS = ("kaa", "kotonebot", "Vision", "Quartz", "AppKit")  # 可选组 play / macOS 系统框架（pyobjc）


def _body_needs_optional(item) -> bool:
    """用例函数体里导入的 hiro 模块（操作层大多在导入时就要 KAA）本机导入不了，且缺的是 KAA / macOS 框架。"""
    import inspect

    try:
        body = inspect.getsource(item.function)
    except (OSError, TypeError):
        return False
    lines = "\n".join(line.strip() for line in body.splitlines())
    return _top_import_missing_optional(lines)


def _top_import_missing_optional(top: str) -> bool:
    """顶层导入的 hiro 模块在导入时就要 KAA / macOS 框架，而本机没装：整个文件不收集（CI 不装 play 组）。"""
    import importlib

    names = []
    for m in re.finditer(r"^(?:from|import) (hiro(?:\.\w+)*)(?: import \(?([\w ,]+))?", top, re.M):
        names.append(m.group(1))
        # from hiro.produce import ops, shop：被导入的名字可能是子模块，也要试
        names += [f"{m.group(1)}.{n.strip().split(' as ')[0]}" for n in (m.group(2) or "").split(",") if n.strip()]
    for name in names:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as e:
            if (e.name or "").split(".")[0] in OPTIONAL_DEPS:
                return True
        except Exception:  # noqa: BLE001 其他导入错误交给 pytest 正常报出来
            pass
    return False


collect_ignore = _collect_ignore()


_SRC: dict[Path, str] = {}


def _source(path: Path) -> str:
    if path not in _SRC:
        _SRC[path] = path.read_text(encoding="utf-8")
    return _SRC[path]


def pytest_configure(config):
    config.addinivalue_line("markers", "user_assets(*kinds): 需要自建素材（HIRO_USER_ASSETS）；kinds 见 hiro.vision.assets.USER_ASSETS")
    config.addinivalue_line("markers", "synthetic: 只用合成图（源码里的 .png 是自己画的），不按「读样例截图」跳过")
    config.addinivalue_line("markers", "fixtures: 要读 tests/fixtures 的截图样例（经类 / 多层辅助函数间接读，自动判断看不出来时显式标）")
    config.addinivalue_line("markers", "master: 要主数据 game.db（经决策器 / 识别层间接查，自动判断看不出来时显式标）")


def _missing_user_assets(kinds) -> list[str]:
    try:
        from hiro.vision import assets
    except Exception:  # noqa: BLE001
        return list(kinds)
    return [k for k in kinds if not assets.available(k)]


def pytest_collection_modifyitems(config, items):
    kaa_ok = None
    for item in items:
        marker = item.get_closest_marker("user_assets")
        if marker is not None and (missing := _missing_user_assets(marker.args)):
            item.add_marker(pytest.mark.skip(reason=f"需要自建素材（HIRO_USER_ASSETS）：{', '.join(missing)}"))
            continue
        path = Path(str(item.fspath))
        src = _source(path)
        missing = [m for m in OPTIONAL_LAYERS if m in src and not _has_module(m)]
        if not missing and _needs_exam_obs(item, src) and not _has_module("hiro.brain.cards"):
            missing = ["hiro.brain.cards（考试画面观测打包成 CardView）"]
        if missing:
            item.add_marker(pytest.mark.skip(reason=f"依赖的层不在仓库里：{missing}"))
            continue
        if _needs_kaa(src) or _body_needs_optional(item):
            if kaa_ok is None:
                kaa_ok = _kaa_ok()
            if not kaa_ok:
                item.add_marker(pytest.mark.skip(reason="需要 KAA（pip install -e '.[play]'）"))
                continue
        if item.get_closest_marker("master") is not None and not _master_ok():
            item.add_marker(pytest.mark.skip(reason="需要主数据 game.db（KAA 公开数据，HIRO_KAA_GAME_DATA）"))
            continue
        if item.get_closest_marker("fixtures") is not None and not HAVE_FIXTURES:
            item.add_marker(pytest.mark.skip(reason="没有截图样例 tests/fixtures（游戏画面不随仓库发布）"))
            continue
        if MASTER_HINT.search(src) and not _master_ok():
            try:
                import inspect

                body = inspect.getsource(item.function)
            except (OSError, TypeError):
                body = ""
            if MASTER_HINT.search(body):
                item.add_marker(pytest.mark.skip(reason="需要主数据 game.db（KAA 公开数据，HIRO_KAA_GAME_DATA）"))
                continue
        if not HAVE_FIXTURES and not item.get_closest_marker("synthetic") and _needs_fixtures(item, src):
            item.add_marker(pytest.mark.skip(reason="没有截图样例 tests/fixtures（游戏画面不随仓库发布）"))


_HELPERS: dict[Path, set[str]] = {}
FIXTURE_HINT = re.compile(r"\bFIX\b|fixtures|\.jpg|\.png")


def _fixture_helpers(path: Path, src: str) -> set[str]:
    """模块里读样例的辅助函数名（函数体引用了 FIX / fixtures / 图片文件）。"""
    if path not in _HELPERS:
        names = set()
        for m in re.finditer(r"^def (\w+)\(.*?(?=^def |^@|^class |\Z)", src, re.M | re.S):
            if FIXTURE_HINT.search(m.group(0)):
                names.add(m.group(1))
        for m in re.finditer(r"^([A-Z_][A-Z0-9_]*)\s*=.*?(?=^\S|\Z)", src, re.M | re.S):  # 顶层常量（可跨行）引用了样例
            if FIXTURE_HINT.search(m.group(0)) and m.group(1) != "FIX":
                names.add(m.group(1))
        _HELPERS[path] = names
    return _HELPERS[path]


def _needs_fixtures(item, src: str) -> bool:
    """按用例判断：函数体、参数、或它调用的本模块辅助函数用到了样例截图。纯逻辑用例照跑。"""
    import inspect

    try:
        body = inspect.getsource(item.function)
    except (OSError, TypeError):
        return True
    params = str(getattr(getattr(item, "callspec", None), "params", ""))
    if FIXTURE_HINT.search(body) or FIXTURE_HINT.search(params):
        return True
    helpers = _fixture_helpers(Path(str(item.fspath)), src)
    return any(re.search(rf"\b{h}\b", body) for h in helpers)


EXAM_OBS = re.compile(r'observation\(.*"HIF_EXAM"|extract\(|read_hif_exam\(|battle_observation\(')


def _needs_exam_obs(item, src: str) -> bool:
    """用例（或它调用的本模块辅助函数）把考试画面打包成观测：要策略层的 CardView。"""
    import inspect

    try:
        body = inspect.getsource(item.function)
    except (OSError, TypeError):
        return False
    if EXAM_OBS.search(body):
        return '"HIF_EXAM"' in src or "battle_observation" in body or "read_hif_exam" in body
    helpers = [m.group(1) for m in re.finditer(r"^def (\w+)\(.*?(?=^def |^@|^class |\Z)", src, re.M | re.S) if EXAM_OBS.search(m.group(0))]
    return any(re.search(rf"\b{h}\(", body) for h in helpers) and '"HIF_EXAM"' in src


MASTER_HINT = re.compile(r"\b(master_offers|card_from_name|split_card_names|card_version|card_catalog|drink_catalog|pitem_catalog)\(")


def _master_ok() -> bool:
    """主数据（KAA game.db）在不在：PublicAssetSource 的 master_db()。"""
    try:
        from hiro.defaults.assets import PublicAssetSource

        return PublicAssetSource().master_db() is not None
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(autouse=True)
def _isolate_batch_rules(tmp_path, monkeypatch):
    """周回模式规则（tools/batch_rules.py）：测试不读使用者本机的 conf/batch-rules.json，一律用 DEFAULTS。
    要测覆盖的用例自己再设 HIRO_BATCH_RULES。"""
    if "HIRO_BATCH_RULES" not in os.environ:
        monkeypatch.setenv("HIRO_BATCH_RULES", str(tmp_path / "batch-rules.none.json"))
    # 策略档案（hiro.strategies）：不读使用者本机的 conf/strategies，也不让上一个用例绑定的批次快照漏过来
    if "HIRO_STRATEGIES" not in os.environ:
        monkeypatch.setenv("HIRO_STRATEGIES", str(tmp_path / "strategies.none"))
    try:
        from hiro.strategies import profiles

        profiles.bind(None)
        yield
        profiles.bind(None)
    except ImportError:
        yield


@pytest.fixture(autouse=True)
def _no_live_deck_list_frames(monkeypatch):
    """测试里的一览读法不往实机留档目录（reports/live/deck-list）存帧：被测代码会把 deck_review.SAVE_DIR 设成那里，
    之后的假黑帧都存了进去（1-f 审计以为实机一览截到黑屏）。要测存帧的用例自己再 patch。"""
    try:
        import hiro.produce.deck_review as DR
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(DR, "SAVE_DIR", None)
    monkeypatch.setattr(DR, "_save", lambda imgs: None)


def _under_fixtures(path) -> Path | None:
    """path 在 tests/fixtures 下（含 HIRO_FIXTURES 软链）就返回相对路径，否则 None。"""
    try:
        p = Path(os.fspath(path))
    except TypeError:
        return None
    for root in {FIXTURES, FIXTURES.resolve()}:
        try:
            return p.resolve().relative_to(root.resolve()) if p.is_absolute() else Path(os.path.abspath(p)).relative_to(root)
        except (ValueError, OSError):
            continue
    return None


def _patch_imread() -> None:
    """只放了部分截图样例时（例如 tools/fixtures/build_local.py 从自己的截图 / trace 生成的那几十张），
    缺哪张就只跳过读它的那条用例（模块 / 夹具级读图时跳过整组），别的照常跑；
    原来只要 tests/fixtures 非空就全部开跑，缺图的几百条都会报错。在 conftest 导入时就换掉 cv2.imread，模块级夹具也生效。"""
    if not HAVE_FIXTURES:
        return
    try:
        import cv2
    except ImportError:
        return
    real = cv2.imread
    if getattr(real, "_hiro_fixture_guard", False):
        return

    def imread(path, *args, **kwargs):
        rel = _under_fixtures(path)
        if rel is not None:
            path = os.path.normpath(os.fspath(path))  # finals/../trace/x.jpg：finals 目录不在时也要找得到
        if rel is not None and not Path(path).is_file():
            pytest.skip(f"缺截图样例 tests/fixtures/{rel.as_posix()}", allow_module_level=True)
        return real(path, *args, **kwargs)

    imread._hiro_fixture_guard = True
    cv2.imread = imread


_patch_imread()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """读 tests/fixtures 里不存在的文件（json 真值、npz…）抛 FileNotFoundError：同样按「缺样例」跳过。"""
    outcome = yield
    report = outcome.get_result()
    if not HAVE_FIXTURES or call.excinfo is None:
        return
    why = None
    if call.excinfo.errisinstance(FileNotFoundError):
        rel = _under_fixtures(getattr(call.excinfo.value, "filename", None) or "")
        why = rel and f"tests/fixtures/{rel.as_posix()}"
    elif call.excinfo.errisinstance(StopIteration):
        # next(FLOW.glob("*-idle.jpg")) 这类按通配找样例：目录 / 文件不在时抛 StopIteration
        lines = [str(e.statement) for e in call.excinfo.traceback if "tests" in str(e.path)]
        if any(".glob(" in line for line in lines):
            why = "按通配找的样例文件不在"
    if why:
        report.outcome = "skipped"
        report.longrepr = (str(item.fspath), item.location[1] or 0, f"Skipped: 缺截图样例 {why}")
