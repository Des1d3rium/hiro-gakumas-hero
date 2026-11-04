# 来源：digital-hiro tests/test_action.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""培育行动选择页读取：头部状态、三维与各变体的可选行动。"""

import json
from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision.action import read_action_select

FIX = Path(__file__).with_name("fixtures") / "action"
TRUTH = json.loads((FIX / "truth.json").read_text())


@pytest.mark.parametrize("name", sorted(TRUTH))
def test_action_select(name: str):
    want = TRUTH[name]
    got = read_action_select(cv2.imread(str(FIX / f"{name}.jpg"))).to_dict()
    for key in ("days_left", "stamina", "max_stamina", "p_points", "params", "star"):
        assert got[key] == want[key], key
    assert got["param_caps"] == {"vo": 3000, "da": 3000, "vi": 3000}
    assert got["complete"]
    assert [[o["kind"], o["attr"], o["hp_cost"], o["hp_gain"]] for o in got["options"]] == want["options"]
