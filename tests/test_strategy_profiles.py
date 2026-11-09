"""回忆档案（hiro.strategies.profiles）：合并顺序、旧 batch-rules.json 兼容、校验、快照绑定、ID ↔ 名字。不连游戏。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from hiro.strategies import names as N
from hiro.strategies import profiles as S
from hiro.strategies import schema

pytestmark = pytest.mark.synthetic
ROOT = Path(__file__).resolve().parents[1]
VEIL = "i_card-skin-hmsz-3-016"


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_code_defaults_match_old_constants():
    """原 batch_rules.DEFAULTS / batch_shop.HIGH_VALUE / batch_prep.DRINK_PRIORITY / batch_memory.PRIORITY 搬进来，值不变。"""
    d = S.code_defaults()
    assert d["drinks"]["high_value"] == ["ブーストエキス", "センブリソーダ", "初星ホエイプロテイン"]
    assert d["drinks"]["priority"][:3] == ["センブリソーダ", "ブーストエキス", "初星ホエイプロテイン"]
    assert d["change"]["out"] == ["立ち回りの基本", "表現の基本", "アピールの基本"]
    assert d["change"]["in"] == {"rule": "once", "column": 3}
    assert [r["card"] for r in d["memory_regen"]] == ["エンターテイナー", "インフルエンサー", "仕切り直し", "巻き返し"]
    assert schema.check(d, "memory-profile") == []


def test_legacy_batch_rules_still_apply(tmp_path, monkeypatch):
    f = _write(tmp_path / "rules.json", {"prep": {"drink_priority": ["初星水"], "change_out": ["ハッピー♪"]},
                                          "interval": {"customize": []}})
    monkeypatch.setenv("HIRO_BATCH_RULES", str(f))
    assert S.get("drinks.priority") == ["初星水"]
    assert S.get("change.out") == ["ハッピー♪"]
    assert S.get("customize") == []
    assert S.get("drinks.high_value") == S.code_defaults()["drinks"]["high_value"]  # 没写的项照代码缺省


def test_memory_profile_overrides_legacy_and_batch_set_overrides_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRO_BATCH_RULES", str(_write(tmp_path / "rules.json", {"prep": {"drink_fallback": "hold"}})))
    _write(tmp_path / "strategies.none" / "memories" / "m1.json",
           {"schema": "memory-profile/1", "memory": "m1", "drinks": {"priority": ["初星湯"]}, "change": {"in": {"column": 2}}})
    r = S.resolve(VEIL, "m1", S.parse_set(["change.in.column=1"]))
    p = r["profile"]
    assert p["drinks"]["priority"] == ["初星湯"] and p["drinks"]["fallback"] == "hold"
    assert p["change"]["in"] == {"rule": "once", "column": 1}
    assert [x["layer"] for x in r["layers"]][0] == "code" and [x["layer"] for x in r["layers"]][-2:] == ["memory", "batch"]


def test_invalid_profile_stops_before_game(tmp_path):
    _write(tmp_path / "strategies.none" / "memories" / "bad.json",
           {"schema": "memory-profile/1", "drinks": {"fallback": "leftmost"}, "typo": 1})
    with pytest.raises(S.ProfileError) as e:
        S.resolve(None, "bad")
    assert "leftmost" in str(e.value) and "typo" in str(e.value)
    with pytest.raises(S.ProfileError):
        S.parse_set(["no-equals-sign"])


def test_snapshot_bind_freezes_until_rebound(tmp_path):
    path = _write(tmp_path / "strategies.none" / "memories" / "m1.json",
                  {"schema": "memory-profile/1", "drinks": {"priority": ["初星湯"]}})
    snap = S.snapshot(VEIL, "m1")
    S.bind(snap)
    _write(path, {"schema": "memory-profile/1", "drinks": {"priority": ["初星水"]}})
    assert S.get("drinks.priority") == ["初星湯"]  # 本局按快照
    assert S.snapshot(VEIL, "m1")["runtime"]["drinks"]["priority"] == ["初星水"]  # 下一局重新快照才变
    assert len(snap["sha256"]) == 16 and snap["memory"] == "m1"
    S.bind(None)
    assert S.get("drinks.priority") == S.code_defaults()["drinks"]["priority"]  # 没绑定、没指定回忆：只有缺省层


def test_batch_hif_snapshots_at_preflight_and_each_run(tmp_path, monkeypatch):
    import batch_hif as B
    from types import SimpleNamespace as NS

    _write(tmp_path / "strategies.none" / "memories" / "m1.json",
           {"schema": "memory-profile/1", "drinks": {"priority": ["初星湯"]}})
    state = {"total": 2, "preflight": None, "active": None, "completed": [], "profile_overrides": {"drinks": {"fallback": "hold"}}}
    B.change(state, NS(command="preflight", ap=100, idol=VEIL, memory="m1"))
    assert state["profile"]["runtime"]["drinks"] == {**S.code_defaults()["drinks"], "priority": ["初星湯"], "fallback": "hold"}
    first = state["profile"]["sha256"]
    _write(tmp_path / "strategies.none" / "memories" / "m1.json",
           {"schema": "memory-profile/1", "drinks": {"priority": ["初星水"]}})
    B.change(state, NS(command="begin"))
    assert state["active"]["profile_sha"] != first and S.get("drinks.priority") == ["初星水"]


def test_memory_evaluate_uses_profile(tmp_path):
    import batch_memory as M

    assert M.evaluate(["エンターテイナー+", "パラメータ+12"])["hit"].startswith("エンターテイナー+")
    _write(tmp_path / "strategies.none" / "memories" / "m1.json",
           {"schema": "memory-profile/1", "memory_regen": [{"name": "只要仕切り直し", "card": "仕切り直し", "need": []}]})
    S.bind(S.snapshot(None, "m1"))
    assert M.evaluate(["エンターテイナー+", "パラメータ+12"])["hit"] is None
    assert M.evaluate(["仕切り直し+"])["hit"] == "只要仕切り直し"


def _has_game_db() -> bool:
    try:
        N._db().execute("SELECT 1 FROM ProduceCard LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _has_game_db(), reason="没有 game.db")
def test_ids_and_names_round_trip():
    assert N.card_ref("インフルエンサー+") == {"id": "p_card-03-men-2_080", "name": "インフルエンサー+", "upgrade": 1}
    assert N.card_name({"id": "p_card-03-men-1_039"}) == "ハッピー♪"
    assert N.drink_name("pdrink_00-2-010") == "センブリソーダ"
    with pytest.raises(N.Unknown):
        N.card_ref("存在しないカード")
    example = json.loads((ROOT / "examples/strategies/memories/veil-senbatsu.example.json").read_text(encoding="utf-8"))
    assert schema.check(example, "memory-profile") == []
    runtime = N.to_names(example)
    assert runtime["change"]["out"] == ["ハッピー♪"]  # 现有名字选中的格子不变
    assert runtime["customize"][0] == {"card": "インフルエンサー+", "item": "強気追加"}
    assert N.to_names(N.to_ids(S.code_defaults())) == S.code_defaults()
