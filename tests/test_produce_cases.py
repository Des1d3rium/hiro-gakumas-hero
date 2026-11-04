"""培育决策器的合成用例（examples/cases/produce.json，局面手工构造、按社区攻略判对错），只用规则、不调 LLM。

需要主数据（KAA game.db 同结构）：环境变量 HIRO_MASTER_DB 指定路径，或装了 KAA 时用它的 game.db；都没有就跳过。
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from hiro import plugins
from hiro.brain.deciders import ProduceDeciders, action_key
from hiro.brain.state import CAMPUS_HIRO
from hiro.contracts import Decision, GameState

CASES = json.loads((Path(__file__).parents[1] / "examples/cases/produce.json").read_text(encoding="utf-8"))


def _master_path() -> str | None:
    if p := os.environ.get("HIRO_MASTER_DB"):
        return p if Path(p).is_file() else None
    try:
        import kaa.db.sqlite  # noqa: F401  先导入它，避免 kaa.game_data.paths 的循环导入
        from kaa.game_data.paths import game_db_path
    except Exception:  # noqa: BLE001 KAA 没装
        return None
    p = str(game_db_path())
    return p if Path(p).is_file() else None


class _MasterOnly:
    """只给主数据的素材源（其余都返回 None）。"""

    def __init__(self, path):
        self.path = path

    def master_db(self):
        return sqlite3.connect(self.path, check_same_thread=False)

    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture(autouse=True)
def _master(monkeypatch):
    path = _master_path()
    if path is None:
        pytest.skip("没有主数据（设置 HIRO_MASTER_DB 或安装 KAA）")
    monkeypatch.setitem(plugins._registered, "asset_source", lambda: _MasterOnly(path))
    plugins._cache.pop("asset_source", None)
    import hiro.state.catalog as cat
    import hiro.state.context as ctx

    for local in (cat._LOCAL, ctx._LOCAL):
        local.__dict__.clear()
    yield
    plugins._cache.pop("asset_source", None)


NEEDS_SCHEDULE = {"ACTION_SELECT"}  # 选行动要日程（AssetSource.schedule）；只给主数据的素材源没有


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_rules_only(case):
    if case["decider"] in NEEDS_SCHEDULE:
        pytest.skip("需要日程数据（AssetSource.schedule），默认素材源还没有")
    d = ProduceDeciders(CAMPUS_HIRO, None).decide(case["decider"], GameState.from_dict(case["state"]))
    assert isinstance(d, Decision) and not d.problems()
    key = action_key(d.action)
    assert key not in case.get("forbid", []), f"选了禁止项 {key}：{d.reason}"
    if "accept" in case:
        assert key in case["accept"], f"选了 {key}，可接受的是 {case['accept']}：{d.reason}"
