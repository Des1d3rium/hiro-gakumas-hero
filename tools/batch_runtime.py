"""watch 自动模式的实机执行。

自动模式不另写流程：在 watch 已确认的停点上，跑和代理一模一样的批量命令（`batch_hif.py --batch <ID> exam …` 等），
子进程方式执行，所以命令自己的锁、记账、trust 自检、停点规则全都照旧。watch 在命令跑完之前不点屏幕。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from batch_driver import DriverBlocked, begin, finish, next_step, recovery_plan

ROOT = Path(__file__).resolve().parents[1]
DANGER = ("ジュエル", "破棄", "リタイア", "別の端末", "他の端末", "通信エラー", "タイトルへ", "有償", "課金")
MONEY = {"interval-recover", "customize", "shop-auto"}  # 动 P / 牌的命令：动手前整屏核对一次
OPENING_PAGES = {"home", "main", "remembered_idol", "start", "opening_confirmation", "ap_shortage",
                 "protected", "photo_error", "timeout", "unsupported_frame"}
COMMAND_TIMEOUT = 20 * 60  # 一条命令最长（考试一轮约 4–5 分钟，留足余量）


class GuardedDevice:
    """自动模式里每次点击前只查暂停旗标（读一个文件）；危险字样在每条命令动手前查一次，不在每下点击前整屏 OCR。"""

    def __init__(self, device):
        self.device = device

    def __getattr__(self, name):
        return getattr(self.device, name)

    def click(self, *xy):
        from hiro.loop import pause_flag

        if pause_flag.ENABLED and pause_flag.reason() is not None:
            raise DriverBlocked("暂停旗标存在")
        return self.device.click(*xy)


def recover(device, rules):
    """轮间回血到 interval.target_hp（默认 10）。回完核对体力和 P 消耗，对不上就算失败（不进商店）。"""
    from hiro.contracts import Action, ActionKind
    from hiro.produce import interval, shop
    from hiro.produce.ops import ocr_boxes
    from hiro.vision.pages import dialog_header
    from hiro.vision.shop import read_interval_shelf

    img = device.screenshot()
    if dialog_header(img) is not None or not interval.on_interval(img, ocr_boxes):
        raise DriverBlocked("回血前未确认轮间主画面")
    hp = interval.read_hp(img, ocr_boxes)
    p = shop.read_p(img, ocr_boxes)
    cfg = rules["interval"]
    target = recovery_plan(*(hp or (None, None)), p, cfg.get("target_hp"), cfg.get("reserve_p", 0),
                           interval.RECOVER_PRICE, interval.RECOVER_HP)
    if not target["count"]:
        return {"ok": True, "hp": hp[0], "p": p, "cost": 0}
    fields = read_interval_shelf(img)
    if not fields.get("complete") or (fields.get("interval", {}).get("recover") or {}).get("available") is not True:
        raise DriverBlocked("回血服务不可确认")
    res = interval.execute_interval(Action(ActionKind.INTERVAL_BUY, {"service": "recover", "target_hp": target["target_hp"]}),
                                    fields, shoot=device.screenshot, click=device.click)
    after = device.screenshot()
    got = interval.read_hp(after, ocr_boxes)
    points = shop.read_p(after, ocr_boxes)
    ok = (res.ok and got is not None and got[0] >= target["target_hp"] and points is not None
          and points >= cfg.get("reserve_p", 0) and p - points == target["cost"])
    return {"ok": ok, "hp": got, "p": points, "cost": None if points is None else p - points,
            "why": None if ok else "回复结果／P 消耗未核验，停止后续购买"}


def argv(batch: str, run: int, action: dict) -> list[str]:
    """一条自动命令 → 代理会敲的同一条命令行。"""
    cmd = action["command"]
    out = [cmd, str(run)]
    if cmd == "exam":
        out += ["--round", str(action["round"])]
    elif cmd == "customize":
        out += ["--card", action["card"], "--item", action["item"], "--guarded"]
        if action.get("need_maxed"):
            out += ["--need-maxed", action["need_maxed"], "--where", "once=0"]
    elif cmd == "shop-auto":
        out += ["--max-refresh", str(action["max_refresh"]), "--reserve-p", str(action.get("reserve_p", 0)), "--guarded"]
    elif cmd == "interval-end":
        out += ["--guarded"]
    elif cmd == "settle":
        out += ["--result", action["result"]]
    elif cmd == "memory-load":
        out += ["--memory", action["memory"]]
    return ["--batch", batch, *out]


def run_command(batch: str, run: int, action: dict, *, runner=subprocess.run) -> dict:
    """子进程跑命令，取最后一行 JSON 当结果；退出码非 0 或 ok=false 都算没成功。"""
    proc = runner([sys.executable, str(ROOT / "tools" / "batch_hif.py"), *argv(batch, run, action)],
                  cwd=ROOT, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, env=os.environ.copy())
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr, flush=True)
    result = None
    for line in reversed((proc.stdout or "").splitlines()):
        try:
            result = json.loads(line)
            break
        except ValueError:
            continue
    if not isinstance(result, dict):
        result = {}
    ok = proc.returncode == 0 and result.get("ok") is not False
    return {**result, "ok": ok, **({} if ok else {"why": result.get("why") or (proc.stderr or "").strip()[-300:]})}


def check_result(state, action, out):
    """命令说成功之后再核一遍：牌组对账、结算是否真的记进批次文件。"""
    cmd = action["command"]
    if cmd == "shop-auto" and (sync := out.get("deck_sync")) and (sync.get("ok") is not True or sync.get("diff")):
        return {**out, "ok": False, "why": "牌组对账未通过"}
    if cmd == "settle" and not any(r["run"] == action["run"] for r in state["completed"]):
        return {**out, "ok": False, "why": "结算没有记进批次文件"}
    return out


def precheck(hold_reason, reason: str, *, shoot_texts=None) -> None:
    """花钱的命令动手前：重读一帧，停点还是同一类、没有危险字样才做（一条命令一次 OCR）。"""
    if shoot_texts is None:
        from hiro.device import connect
        from hiro.produce.ops import ocr_boxes

        device = connect("maatools")
        try:
            texts = [t for t, _, _ in ocr_boxes(device.screenshot())]
        finally:
            device.stop()
    else:
        texts = shoot_texts()
    if any(w in "".join(texts) for w in DANGER):
        raise DriverBlocked("动手前复核出现危险字样")
    now = hold_reason(texts)
    if now is None or now.split("：", 1)[0] != reason.split("：", 1)[0]:
        raise DriverBlocked(f"动手前画面变了（{reason} → {now}）")


def validate(state, action) -> None:
    """不连设备的前置核对：客制化目标在账本里唯一。"""
    if action["command"] != "customize":
        return
    import batch_ledger as L

    ledger = state["active"]["ledger"]
    where = "once=0" if action.get("need_maxed") == "制限削除" else None
    card = L.find_instance(ledger, action["card"], where=L.parse_where(where))
    if card is None or (where and card.get("once") is not False):
        raise DriverBlocked("客制化目标实例或制限削除未核实")
    # 同名几张时界面绑不到唯一一张：不把效果随便记到第一张上
    same = [c for c in L.ensure_deck(ledger) if c["name"] == card["name"] and (not where or c.get("once") is not True)]
    if len(same) != 1:
        raise DriverBlocked("同名多张客制化需先核实目标")


def plan_hold(state, reason, rules, persist, *, hold_reason, shoot_texts=None):
    """在批次锁里调用：决定这条停点要不要自动处理；要的话核对后落盘意图，返回命令（锁外执行）。"""
    action = next_step(state, reason, rules)
    if action is None:
        return None
    if action["command"] == "shop-auto":
        action["reserve_p"] = rules["interval"].get("reserve_p", 0)
    action["run"] = state["active"]["run"]
    try:
        validate(state, action)
        if action["command"] in MONEY:
            precheck(hold_reason, reason, shoot_texts=shoot_texts)
    except DriverBlocked as e:
        driver = state["active"].setdefault("driver", {})
        driver["error"] = {"command": action["command"], "out": {"ok": False, "why": str(e)}}
        persist()
        return {"blocked": str(e), **action}
    begin(state, action, persist)
    return action


def finish_hold(state, action, out, persist) -> bool:
    return finish(state, action, check_result(state, action, out), persist)


def next_run(state, settle_out, rules) -> list[dict]:
    """settle 自动开了下一局之后要补的两条命令（playing、memory-load）。开局停在首页 / 确认页 / 弹窗就不补，交给代理。"""
    if not rules.get("driver", {}).get("enabled") or not isinstance(settle_out, dict):
        return []
    if settle_out.get("page") in OPENING_PAGES or (settle_out.get("settle") or {}).get("page") in OPENING_PAGES:
        return []
    active = state.get("active") or {}
    memory = ((state.get("completed") or [{}])[-1].get("ledger") or {}).get("memory")
    if active.get("phase") != "opening" or not memory:
        return []
    return [{"command": "playing", "run": active["run"]},
            {"command": "memory-load", "run": active["run"], "memory": memory}]


def log_line(run, action, ok, extra=None):
    line = {"t": round(time.time(), 2), "run": run, "action": "auto", "command": action["command"], "ok": ok, **(extra or {})}
    print(json.dumps(line, ensure_ascii=False), flush=True)
