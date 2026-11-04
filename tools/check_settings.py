# 来源：digital-hiro tools/check_settings.py（c3fc27f）
"""开局前检查游戏设置（用户 2026-11-02：重新登录后这些设置会被重置；先只报警，不自动改）。

检查「メニュー → 設定 → プロデュース」里的 5 项是否 ON：
  未読コミュでも早送りする、サポートイベントのコミュスキップ、各プロデュース開始時の説明コミュスキップ、
  定期公演『初』の「気になる様子」コミュスキップ、親愛度コミュ内の映像スキップ。
只读：从首页打开菜单 → 設定 → プロデュース 页签，读开关，存截图，回首页。不点任何开关。

    python tools/check_settings.py    # 打印每项状态；有 OFF / 读不到时退出码 1
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENU, SETTINGS, PRODUCE_TAB, HOME = (653, 1213), (605, 895), (580, 1076), (63, 1213)
WANT = {
    "未読早送り": ("未読コミュでも早送り",),
    "支援事件剧情跳过": ("サポートイベントの", "コミュスキップ"),
    "开始说明剧情跳过": ("説明コミュスキップ",),
    "定期公演剧情跳过": ("様子」コミュスキップ", "気になる"),
    "親愛度映像跳过": ("映像スキップ",),
}


def read_toggles(boxes) -> dict[str, str | None]:
    """boxes = [(text, conf, (x1, y1, x2, y2))]。每项按标题行找同一高度（±45px）右侧的 ON / OFF。"""
    switches = [(t, (b[1] + b[3]) // 2) for t, _, b in boxes if t.strip() in ("ON", "OFF") and b[0] > 480]
    out: dict[str, str | None] = {}
    for key, needles in WANT.items():
        ys = [(b[1] + b[3]) // 2 for t, _, b in boxes if any(n in t.replace(" ", "") for n in needles)]
        state = None
        for y in ys:
            near = [s for s, sy in switches if abs(sy - y) <= 45]
            if near:
                state = near[0]
                break
        out[key] = state
    return out


def check(save_dir: Path | None = None) -> dict[str, str | None]:
    sys.path.insert(0, str(ROOT / "src"))
    import hiro  # noqa: F401
    import cv2

    from hiro.device import connect
    from hiro.produce.ops import ocr_boxes

    d = connect()
    d.click(*MENU)
    time.sleep(2.5)
    d.click(*SETTINGS)
    time.sleep(3.0)
    d.click(*PRODUCE_TAB)
    time.sleep(2.5)
    found: dict[str, str | None] = {k: None for k in WANT}
    for i in range(2):
        img = d.screenshot()
        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(save_dir / f"check-{i}.png"), img)
        for k, v in read_toggles(ocr_boxes(img)).items():
            found[k] = found[k] or v
        d.swipe(360, 900, 360, 450, 0.8)
        time.sleep(1.5)
    d.click(*HOME)
    time.sleep(3.0)
    return found


if __name__ == "__main__":
    res = check(ROOT / "reports/live/settings")
    bad = {k: v for k, v in res.items() if v != "ON"}
    for k, v in res.items():
        print(f"{k}: {v}")
    if bad:
        print(f"⚠ 设置被重置或读不到：{bad}（只报警，不自动改）")
    sys.exit(1 if bad else 0)
