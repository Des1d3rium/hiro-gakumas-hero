# 来源：digital-hiro tools/daily/run_daily.py（c3fc27f）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""用 KAA 的日常任务跑一遍不花钱的项（用户 2026-11-02 经协调定）。

    python tools/daily/run_daily.py [--only contest,presents]

只开：activity_funds / presents / assignment（领取并重新派出）/ contest / mission_reward / club_reward。
不开：purchase / upgrade_support_card / capsule_toys / produce。
contest 的 when_no_set 固定为 skip：没有编成就跳过并报告，不让 KAA 自动编成。
每项前后截图到 reports/live/daily/<时间>/，结果写 summary.json。
兜底（hiro.daily_guard）：每项执行期间后台看守弹窗（说明框点「閉じる」、含ジュエル的确认框只点「キャンセル」），
单项超过 --timeout 秒（默认 300）就打断、截图记 timeout，继续下一项，不再空转。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import hiro  # noqa: E402,F401  兼容层
from kotonebot.backend.context import init_context, manual_context  # noqa: E402

from hiro.device import connect  # noqa: E402

TASKS = ("activity_funds", "presents", "assignment", "contest", "mission_reward", "club_reward")


def _config():
    from kaa.config.schema import KaaConfig
    from kaa.kaa_context import init as kaa_init

    c = KaaConfig()
    t = c.tasks
    t.activity_funds.enabled = True
    t.presents.enabled = True
    t.assignment.enabled = True
    t.assignment.mini_live_reassign_enabled = True
    t.assignment.online_live_reassign_enabled = True
    t.contest.enabled = True
    t.contest.when_no_set = "skip"
    t.mission_reward.enabled = True
    t.club_reward.enabled = True
    for off in ("purchase", "upgrade_support_card", "capsule_toys", "produce"):
        getattr(t, off).enabled = False
    kaa_init(c, "hiro-daily")
    return c


def _funcs():
    from kaa.tasks.daily.acquire_activity_funds import acquire_activity_funds
    from kaa.tasks.daily.acquire_presents import acquire_presents
    from kaa.tasks.daily.assignment import assignment
    from kaa.tasks.daily.club_reward import club_reward
    from kaa.tasks.daily.contest import contest
    from kaa.tasks.daily.mission_reward import mission_reward

    return {"activity_funds": acquire_activity_funds, "presents": acquire_presents, "assignment": assignment,
            "contest": contest, "mission_reward": mission_reward, "club_reward": club_reward}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔，只跑这些项")
    ap.add_argument("--timeout", type=float, default=300.0, help="单项超时（秒），超时打断并截图")
    args = ap.parse_args()
    names = [n for n in (args.only.split(",") if args.only else TASKS) if n]
    out = ROOT / "reports/live/daily" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    import cv2
    from kotonebot import device

    from kotonebot.backend.context import vars as kvars

    from hiro.daily_guard import PopupGuard, run_with_timeout
    from hiro.produce.ops import ocr_boxes

    _config()
    dev = connect()
    init_context(target_device=dev)
    manual_context().begin()
    funcs = _funcs()
    flow = kvars.flow  # 在本线程取出真正的 FlowController，交给超时计时器线程

    def shot(tag: str) -> str:
        p = out / f"{tag}.png"
        cv2.imwrite(str(p), device.screenshot())
        return str(p)

    summary = []
    for n in names:
        rec = {"task": n, "before": shot(f"{n}-before")}
        t0 = time.monotonic()
        popups = []

        def on_popup(kind, img, n=n):
            p = out / f"{n}-popup{len(popups) + 1}-{kind}.png"
            cv2.imwrite(str(p), img)
            popups.append({"kind": kind, "shot": str(p)})

        guard = PopupGuard(dev.screenshot, lambda x, y: dev.click(x, y), ocr_boxes, on_action=on_popup)
        with guard:
            timed_out, err = run_with_timeout(funcs[n], args.timeout, flow)
        rec["ok"] = not timed_out and err is None
        if timed_out:
            rec["error"] = f"超时 {args.timeout:.0f}s，已打断"
            rec["timeout_shot"] = shot(f"{n}-timeout")
        elif err is not None:
            rec["error"] = f"{type(err).__name__}: {err}"
            rec["traceback"] = "".join(traceback.format_exception(type(err), err, err.__traceback__))[-1500:]
        if popups:
            rec["popups"] = popups
        rec["seconds"] = round(time.monotonic() - t0, 1)
        rec["after"] = shot(f"{n}-after")
        summary.append(rec)
        print(json.dumps({k: v for k, v in rec.items() if k != "traceback"}, ensure_ascii=False), flush=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")
    return 0 if all(r["ok"] for r in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
