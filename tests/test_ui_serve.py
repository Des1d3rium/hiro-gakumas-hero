"""给前端的本机服务（hiro.ui.serve）：路由、保存前校验、坏档案不落盘。不开端口、不碰游戏。"""
import json

import pytest

from hiro.ui.serve import handle

pytestmark = pytest.mark.synthetic
VEIL = "i_card-skin-hmsz-3-016"


def test_schema_and_unknown_route():
    status, body = handle("GET", "/schemas/memory-profile")
    assert status == 200 and body["$id"] == "memory-profile/1"
    assert handle("GET", "/nope")[0] == 404
    assert handle("GET", "/profiles/memory/..%2Fetc")[0] == 400


def test_put_memory_profile_validates_then_writes(tmp_path):
    bad = json.dumps({"drinks": {"fallback": "leftmost"}}).encode()
    status, body = handle("PUT", "/profiles/memory/m1", bad)
    assert status == 400 and "leftmost" in body["error"]
    assert not (tmp_path / "strategies.none" / "memories" / "m1.json").exists()
    good = json.dumps({"drinks": {"priority": ["初星湯"]}}).encode()
    assert handle("PUT", "/profiles/memory/m1", good)[0] == 200
    status, body = handle("GET", "/profiles/memory/m1")
    assert status == 200 and body["merged"]["drinks"]["priority"] == ["初星湯"] and body["raw"]["memory"] == "m1"


def test_put_idol_override_rolls_back_when_merged_invalid(tmp_path):
    assert handle("PUT", f"/profiles/idol/{VEIL}", json.dumps({"limits": {"r1": 800000}}).encode())[0] == 200
    assert handle("GET", f"/profiles/idol/{VEIL}")[1]["merged"]["limits"]["r1"] == 800000
    status, _ = handle("PUT", f"/profiles/idol/{VEIL}", json.dumps({"engine": "magic"}).encode())
    assert status == 400
    saved = json.loads((tmp_path / "strategies.none" / "idols" / f"{VEIL}.json").read_text(encoding="utf-8"))
    assert saved["limits"] == {"r1": 800000} and "engine" not in saved  # 坏的没落盘，留着上一版
    assert handle("PUT", "/profiles/idol/i_card-skin-new", json.dumps({"limits": {"r1": 1}}).encode())[0] == 400


def test_idols_lists_profiles():
    status, body = handle("GET", "/idols")
    assert status == 200 and any(r["id"] == VEIL and r["engine"] == "cycle" for r in body)


def _has_game_db() -> bool:
    return handle("GET", "/drinks")[0] == 200


@pytest.mark.skipif(not _has_game_db(), reason="没有 game.db")
def test_catalog_endpoints():
    drinks = handle("GET", "/drinks")[1]
    assert {"id": "pdrink_00-2-010", "name": "センブリソーダ"}.items() <= next(d for d in drinks if d["id"] == "pdrink_00-2-010").items()
    cards = handle("GET", "/cards?q=インフルエンサー")[1]
    assert any(c["id"] == "p_card-03-men-2_080" and c["upgrade"] == 1 for c in cards)
    menu = handle("GET", "/customize/p_card-03-men-2_080?upgrade=1")[1]
    assert len(menu) == 2 and menu[0]["levels"]
