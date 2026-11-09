# 来源：digital-hiro src/hiro/memory_scan.py（1fc4d19）；调用 KAA / kotonebot（kotones-auto-assistant，GPL-3.0）
"""回忆库存扫描（只读）：在实机上跑。先在编成页点开任意一格回忆，停在回忆选择页，再运行：

    python -m hiro.memory_scan explore      # 拍筛选 / 排序对话框、详情第 2 页、翻到底 → reports/live/memory-explore/<时间>/
    python -m hiro.memory_scan scan         # 先滑回顶部，逐格读卡 / 时机 / 属性 / 能力 → reports/live/memory-inventory.json（覆盖，旧文件留 .bak）

两条命令最后都会点「キャンセル」退出选择页，不会点「決定」，不改编成。
"""

import argparse
import time
from pathlib import Path

import hiro  # noqa: F401
from hiro.device import connect
from hiro.produce.memory_picker import SWIPE_DURATION, SWIPE_HOLD, Picker, explore, scan
from kotonebot.backend.context import device, init_context, manual_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["explore", "scan"])
    parser.add_argument("--out", default="reports/live/memory-inventory.json")
    parser.add_argument("--max-pages", type=int, default=400)
    parser.add_argument("--no-calib", action="store_true", help="scan 时不存每页截图")
    args = parser.parse_args()
    dev = connect()
    impl = getattr(dev, "_touch", None)
    if hasattr(impl, "swipe_hold"):
        impl.swipe_hold = SWIPE_HOLD  # 终点停住再抬起：列表不带惯性滚动，扫描靠行重叠核对不漏行
    init_context(target_device=dev)
    manual_context().begin()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    calib = None if args.no_calib else Path(f"reports/live/calib/{stamp}-回忆扫描")
    picker = Picker(device.screenshot, lambda x, y: device.click(x, y),
                    lambda x1, y1, x2, y2: device.swipe(x1, y1, x2, y2, duration=SWIPE_DURATION), calib_dir=calib)  # fmt: skip
    if args.command == "explore":
        out = Path(f"reports/live/memory-explore/{stamp}")
        explore(picker, out)
        print(out)
    else:
        inv = scan(picker, Path(args.out), max_pages=args.max_pages)
        print(f"{len(inv['entries'])} 份回忆 → {args.out}（complete={inv.get('complete')}）")


if __name__ == "__main__":
    main()
