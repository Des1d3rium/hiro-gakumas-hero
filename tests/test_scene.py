# 来源：digital-hiro tests/test_scene.py（develop c3fc27f）。读 tests/fixtures 截图的用例在没有样例目录时跳过（见 conftest.py）
"""场景分类回归：文件名 <帧号>-<期望场景>.jpg，期望场景已人工核对。"""

from pathlib import Path

import cv2
import pytest

import hiro  # noqa: F401
from hiro.vision.offline import load
from hiro.vision.scene import classify_current

FIX = Path(__file__).with_name("fixtures") / "scenes"


@pytest.mark.parametrize("path", sorted(FIX.glob("*.jpg")), ids=lambda p: p.stem)
def test_scene(path: Path):
    load(cv2.imread(str(path)))
    assert classify_current().scene == path.stem.split("-", 1)[1]
