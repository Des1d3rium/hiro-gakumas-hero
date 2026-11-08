"""MaaTools 连续空帧（切屏）：设备层一直重取到 EMPTY_FRAME_WAIT 才抛，不再 4 次 1s 内就抛（验证局 watch / exam 因此退出）。"""

import numpy as np
import pytest

pytest.importorskip("Quartz")
import hiro.device as D  # noqa: E402


def _impl(replies, monkeypatch, wait=5.0):
    t = {"now": 0.0}
    monkeypatch.setattr(D.time, "monotonic", lambda: t["now"])
    monkeypatch.setattr(D.time, "sleep", lambda s: t.update(now=t["now"] + s))
    monkeypatch.setattr(D, "EMPTY_FRAME_WAIT", wait)
    impl = object.__new__(D.MaaToolsImpl)
    it = iter(replies)

    def call(*_a, **_k):
        r = next(it)
        if r is None:
            raise D.MaaToolsEmptyFrame("空帧")
        return r

    impl._call = call
    return impl


def test_empty_frames_wait_then_recover(monkeypatch):
    img = np.zeros((1280, 720, 3), np.uint8)
    impl = _impl([None] * 6 + [img], monkeypatch)
    assert impl.screenshot() is img and impl._size == (720, 1280)


def test_empty_frames_raise_after_wait(monkeypatch):
    impl = _impl([None] * 100, monkeypatch, wait=3.0)
    with pytest.raises(D.MaaToolsEmptyFrame, match="连续"):
        impl.screenshot()
