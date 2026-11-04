"""真实鼠标点击的坐标换算（逻辑 720×1280 → 屏幕；PlayCover 窗口含 28px 标题栏）。不实际点击。"""

from hiro import real_click


def test_to_screen_scales_by_window_width():
    assert real_click.to_screen(0, 0, (100, 50, 540, 988)) == (100, 50 + 28)
    assert real_click.to_screen(720, 1280, (100, 50, 540, 988)) == (100 + 540, 50 + 28 + 960)
    assert real_click.to_screen(360, 640, (0, 0, 720, 1308)) == (360, 668)
