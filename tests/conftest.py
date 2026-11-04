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

OPTIONAL_LAYERS = ("hiro.loop.controller", "hiro.loop.kaa_patches", "hiro.produce", "hiro.brain.state", "hiro.brain.cards",
                   "hiro.brain.deciders", "hiro.brain.rules", "hiro.brain.replay", "hiro.sim")  # fmt: skip
KAA_USERS = re.compile(r"hiro\.vision|hiro\.perception")


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


def _collect_ignore() -> list[str]:
    """导入时就会失败的测试文件（顶层导入了不在仓库里的层 / 顶层读样例截图），不收集。"""
    out = []
    for f in sorted(TESTS.glob("test_*.py")):
        top = _top_level(f.read_text(encoding="utf-8"))
        if any(re.search(rf"^(from|import) {re.escape(m)}\b", top, re.M) and not _has_module(m) for m in OPTIONAL_LAYERS):
            out.append(f.name)
        elif not FIXTURES.exists() and re.search(r"^[A-Z_]+\s*=.*\b(FIX|fixtures)\b.*read_text|^[A-Z_]+\s*=\s*json\.load", top, re.M):
            out.append(f.name)
        elif _top_import_missing_optional(top):
            out.append(f.name)
    return out


OPTIONAL_DEPS = ("kaa", "kotonebot", "Vision", "Quartz", "AppKit")  # 可选组 play / macOS 系统框架（pyobjc）


def _top_import_missing_optional(top: str) -> bool:
    """顶层导入的 hiro 模块在导入时就要 KAA / macOS 框架，而本机没装：整个文件不收集（CI 不装 play 组）。"""
    import importlib

    for m in re.finditer(r"^(?:from|import) (hiro(?:\.\w+)*)", top, re.M):
        try:
            importlib.import_module(m.group(1))
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


def pytest_collection_modifyitems(config, items):
    kaa_ok = None
    for item in items:
        path = Path(str(item.fspath))
        src = _source(path)
        missing = [m for m in OPTIONAL_LAYERS if m in src and not _has_module(m)]
        if not missing and _needs_exam_obs(item, src) and not _has_module("hiro.brain.cards"):
            missing = ["hiro.brain.cards（考试画面观测打包成 CardView）"]
        if missing:
            item.add_marker(pytest.mark.skip(reason=f"依赖的层不在仓库里：{missing}"))
            continue
        if KAA_USERS.search(src):
            if kaa_ok is None:
                kaa_ok = _kaa_ok()
            if not kaa_ok:
                item.add_marker(pytest.mark.skip(reason="需要 KAA（pip install -e '.[play]'）"))
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
        if not FIXTURES.exists() and _needs_fixtures(item, src):
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
