"""tools/audit/replay_common.py：读图缓存的命中 / 作废 / 只读模式，以及多进程结果顺序。"""

import functools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audit"))
import replay_common as RC  # noqa: E402


def _square(x, offset=0):
    return x * x + offset


def test_cache_hit_version_and_readonly(tmp_path, monkeypatch):
    monkeypatch.setattr(RC, "CACHE_DIR", tmp_path / "cache")
    frame = tmp_path / "f.png"
    frame.write_bytes(b"x")
    calls = []
    read = lambda p: calls.append(p) or {"n": 1}  # noqa: E731

    c = RC.FrameCache("t", "v1")
    assert c.get(frame, read) == {"n": 1} and c.get(frame, read) == {"n": 1}
    assert len(calls) == 1 and (c.hits, c.misses) == (1, 1)
    c.save()

    assert RC.FrameCache("t", "v1").get(frame, read) == {"n": 1} and len(calls) == 1  # 读盘命中
    assert RC.FrameCache("t", "v2").get(frame, read) == {"n": 1} and len(calls) == 2  # 版本变了重读
    ro = RC.FrameCache("t", "v3", mode="ro")
    assert ro.get(frame, read) is None and len(calls) == 2  # 只用缓存：不读图
    off = RC.FrameCache("t", "v1", mode="off")
    off.get(frame, read)
    off.save()
    assert len(calls) == 3 and not off.new


def test_cache_key_changes_with_file(tmp_path):
    f = tmp_path / "f.png"
    f.write_bytes(b"x")
    k1 = RC.FrameCache.key(f)
    f.write_bytes(b"xy")
    assert RC.FrameCache.key(f) != k1
    assert RC.FrameCache.key(tmp_path / "missing.png") is None


def test_run_traces_keeps_order():
    fn = functools.partial(_square, offset=1)
    assert RC.run_traces(fn, [3, 1, 2], jobs=1) == [10, 2, 5]
    assert RC.run_traces(fn, [3, 1, 2, 4], jobs=2) == [10, 2, 5, 17]
