"""watch 自动模式的纯逻辑（不连设备，回放和测试直接调）。

只有一套页面判断：watch 的停点（hold_reason / prep / debug 再挑戦 / result_next）。自动模式不自己认画面，
只在 watch 已确认的常规停点上替代理下命令（考试 → exam，轮间 → 回血 / 客制化 / 商店 / 结束，结算回忆 → settle）。
不认得的停点、危险字样、账本对不上，照旧打印 hold 交给代理。
"""
from __future__ import annotations

from copy import deepcopy

# 重做一次不会多花 P / 券 / 饮料的命令：上次没确认就再做一次（最多 IDEMPOTENT_TRIES 次）。
# exam 从考试记忆续跑；interval-end 只在左上是「インターバル」时点；settle 已记账就跳过；memory-load 有账本就拒绝。
IDEMPOTENT = {"exam", "interval-end", "settle", "playing", "memory-load"}
IDEMPOTENT_TRIES = 2


class DriverBlocked(ValueError):
    pass


def enabled(rules) -> bool:
    return bool(rules.get("driver", {}).get("enabled", False))


def stage_round(stage: str | None) -> int:
    """批次阶段 → 考试轮次。没进过轮间都是 R1（含 R1 再挑戦）；结束轮间之后都是 R2（含 R2 再挑戦）。"""
    return 2 if stage in {"r2", "after_r2"} else 1


def next_step(state, reason: str, rules):
    """watch 已确认的停点（「理由：命中文字」）→ 一条命令；None = 不归自动模式管，照旧交给代理。
    不靠结果页名次推进度（TODO：结果页 0 分时 score / rank 读不出）——轮间页本身就是 R1 晋级的证据。"""
    if not enabled(rules):
        return None
    active = state.get("active") or {}
    if active.get("phase") != "playing" or not active.get("ledger"):
        return None
    driver = active.get("driver") or {}
    if driver.get("error") or active["ledger"].get("pending_drink_replace"):
        return None  # 上次没核验完：交给代理核对后 auto-resolve
    step = _fresh_step(driver, reason, rules)
    if pending := driver.get("pending"):
        # 可重做的命令（考试续跑、轮间结束…）上次没确认、画面还是同一个停点就再来一次；花钱的命令留给代理核对
        same = step is not None and step["command"] == pending["command"] and step.get("round") == pending.get("round")
        return deepcopy(pending) if pending["command"] in IDEMPOTENT and same else None
    return step


def _fresh_step(driver, reason, rules):
    kind = reason.split("：", 1)[0]
    stage = driver.get("stage", "r1")
    if kind == "考试中":
        return {"command": "exam", "round": stage_round(stage), "deck": None}
    if kind == "轮间客制化":
        if stage in {"r2", "after_r2"}:
            return None  # 已经结束过轮间：画面和阶段对不上
        cfg = rules.get("interval", {})
        if cfg.get("recover_enabled", True) and not driver.get("recovered"):
            return {"command": "interval-recover"}
        done = driver.get("customized", [])
        for index, item in enumerate(cfg.get("customize", [])):
            if index not in done:
                return {"command": "customize", "index": index, **item}
        if cfg.get("shop_auto", True) and not driver.get("shopped"):
            return {"command": "shop-auto", "max_refresh": rules.get("driver", {}).get("max_refresh", 3)}
        return {"command": "interval-end"}
    if kind in {"结算回忆", "结算回忆生成"} and stage == "after_r2":
        return {"command": "settle", "result": settle_note(driver)}
    return None


def settle_note(driver) -> str:
    parts = [f"R{r.get('round')} {r.get('round_score')} 第{r.get('rank')}" for r in driver.get("results", [])]
    return "自动模式：" + ("；".join(parts) or "两轮已考完")


def begin(state, action, persist):
    """动手之前把意图落盘：进程中途退出时 pending 留着，重启不会重做花钱的操作。"""
    driver = state["active"].setdefault("driver", {})
    prev = driver.get("pending")
    tries = driver.get("tries", 0) + 1 if prev == action else 1
    driver.update(pending=deepcopy(action), tries=tries)
    persist()


def finish(state, action, out, persist):
    """命令返回明确成功才清 pending 并推进阶段；否则记 error（花钱的）或留 pending 等下一次（可重做的）。"""
    active = state["active"]
    if active is None:
        # settle 记完最后一局，批次完成、active 已清空（codex-20261108-1：这里原来 AttributeError，watch 退出）
        persist()
        return bool(out and out.get("ok") is True)
    driver = active.setdefault("driver", {})
    if not out or out.get("ok") is not True or out.get("ledger_todo"):
        if action["command"] not in IDEMPOTENT or driver.get("tries", 0) >= IDEMPOTENT_TRIES:
            driver["error"] = {"command": action["command"], "out": _brief(out)}
        persist()
        return False
    apply(active, action, out)
    driver.pop("pending", None)
    driver.pop("tries", None)
    driver.pop("error", None)
    persist()
    return True


def apply(active, action, out=None):
    """一条命令成功后的进度记账（auto-resolve applied 也走这里）。"""
    driver = active.setdefault("driver", {})
    command = action["command"]
    if command == "exam":
        rnd = action["round"]
        driver["stage"] = "r1" if rnd == 1 else "after_r2"
        driver.setdefault("results", []).append(
            {"round": rnd, "round_score": (out or {}).get("round_score"), "rank": (out or {}).get("rank"),
             "trace": (out or {}).get("trace"), "trusted": ((out or {}).get("trust") or {}).get("trusted")})
    elif command == "interval-recover":
        driver["recovered"] = True
        driver["stage"] = "interval"
    elif command == "customize":
        driver.setdefault("customized", []).append(action["index"])
        driver["stage"] = "interval"
    elif command == "shop-auto":
        driver["shopped"] = True
    elif command == "interval-end":
        driver["stage"] = "r2"


def resolve(active, applied: bool, note: str):
    """代理核对实机和账本后：applied = 那一步实际生效了（按成功记进度）；否则当没发生。两种都清 pending / error 并留记录。"""
    driver = active.get("driver") or {}
    action = driver.get("pending")  # 失败时 pending 不清，error 只是附带原因
    if action is None and not driver.get("error"):
        raise DriverBlocked("没有待核验的自动操作")
    if applied and action is not None:
        apply(active, action)
    driver.setdefault("resolved", []).append({"action": action, "applied": applied, "note": note,
                                              "error": driver.get("error")})
    for key in ("pending", "tries", "error"):
        driver.pop(key, None)
    active["driver"] = driver
    return {"ok": True, "action": action, "applied": applied}


def _brief(out):
    if not isinstance(out, dict):
        return out
    return {k: out[k] for k in ("ok", "why", "stop", "ledger_todo", "page", "hold") if k in out}


def recovery_plan(hp, maximum, points, target, reserve, price=10, amount=2):
    """回到 VEIL 的目标体力（interval.target_hp，默认 10）要花多少 P；P 不够就停，不进 R2。"""
    values = (hp, maximum, points, target, reserve, price, amount)
    if any(type(v) is not int for v in values) or not (0 <= hp <= maximum and maximum > 0
            and points >= 0 and target > 0 and reserve >= 0 and price > 0 and amount > 0):
        raise DriverBlocked("回血目标、体力、P 或保留预算不明")
    target = min(target, maximum)
    count = max(0, (target - hp + amount - 1) // amount)
    cost = count * price
    if cost + reserve > points and count:
        raise DriverBlocked("P 不足以回血到目标并保留预算，不自动进入 R2")
    return {"target_hp": target, "cost": cost, "count": count}


def replacement(held, incoming, priority, high_value, norm=lambda s: s):
    """栏满时换饮料：只换掉一瓶认得、严格更差的；名字两边先按 norm 统一写法（OCR 和账本写法不同不算对不上）。"""
    held = [norm(n) if n else None for n in held]
    incoming = [norm(n) if n else None for n in incoming]
    priority = [norm(n) for n in priority]
    high = {norm(n) for n in high_value}
    if len(held) != 4 or any(not n for n in held) or not incoming or any(not n for n in incoming):
        return None
    rank = lambda n: priority.index(n) if n in priority else len(priority)  # noqa: E731
    candidates = [n for n in incoming if n in high]
    if not candidates:
        return None
    best = min(candidates, key=rank)
    worst = max(held, key=rank)
    if rank(best) >= rank(worst):
        return None
    # 同名几瓶等价；名次相同的不同饮料不随便挑第一瓶
    if len({n for n in held if rank(n) == rank(worst)}) != 1:
        return None
    return {"pick": incoming.index(best), "discard": held.index(worst), "incoming": best, "held": worst}
