"""按钮注册表（S5）：固定点击坐标集中在 hiro/ui/buttons.json，使用者覆盖 conf/ui/buttons.json。

每个按钮：page（哪一页）、kind（point / x / y / xs / ys / points / dy / ratio）、value、space（portrait = 720x1280 逻辑坐标，
默认；landscape = LIVE 横屏的比例坐标）、note、danger（花钱 / 不可撤销：调用方必须先核对文字）、blind_ok（直接点也无害的理由）、
alias（同一位置只存一次，指向另一个按钮）。

原则（docs/strategies.md、OCR 定位操作规程）：能用 OCR / 模板定位的按钮先定位，注册表坐标只作退路。
旧常量（hiro.produce.hif_buttons、tools/batch_* 里的点击点）改成从这里取值的别名，同一个按钮只有一个值。

覆盖文件只能改已有按钮的 value（和 note），不能加新 id、不能改 kind / danger：
  {"buttons": {"CORNER": {"value": [16, 16]}}}
HIRO_BUTTONS 可换覆盖文件路径（测试用）。
"""

from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "buttons.json"
ROOT = Path(__file__).resolve().parents[3]
KINDS = ("point", "x", "y", "xs", "ys", "points", "dy", "ratio")


class ButtonError(ValueError):
    pass


def user_path() -> Path:
    return Path(os.environ.get("HIRO_BUTTONS") or ROOT / "conf" / "ui" / "buttons.json")


def _tuple(kind: str, value):
    if kind in ("point", "ratio", "xs", "ys"):
        return tuple(value)
    if kind == "points":
        return {k: tuple(v) for k, v in value.items()}
    return value


@lru_cache(maxsize=4)
def _table(user: str) -> dict:
    from hiro.strategies import schema

    base = json.loads(DEFAULT.read_text(encoding="utf-8"))
    problems = schema.check(base, "buttons")
    if problems:
        raise ButtonError("buttons.json 不合法：\n  " + "\n  ".join(problems[:20]))
    table = copy.deepcopy(base["buttons"])
    p = Path(user)
    if p.is_file():
        over = json.loads(p.read_text(encoding="utf-8"))
        for bid, e in (over.get("buttons") or {}).items():
            if bid not in table or "alias" in table[bid]:
                raise ButtonError(f"{p}：不能覆盖 {bid!r}（没有这个按钮，或它是别名）")
            extra = set(e) - {"value", "note"}
            if extra:
                raise ButtonError(f"{p}：{bid} 只能改 value / note，不能改 {sorted(extra)}")
            probe = {"schema": "buttons/1", "buttons": {bid: {**table[bid], **e}}}
            problems = schema.check(probe, "buttons")
            if problems:
                raise ButtonError(f"{p}：{bid} 不合法：" + "；".join(problems))
            table[bid].update(e)
            table[bid]["overridden"] = True
    return table


def table() -> dict:
    return _table(str(user_path()))


def entry(bid: str) -> dict:
    t = table()
    seen = []
    while bid in t and "alias" in t[bid]:
        seen.append(bid)
        bid = t[bid]["alias"]
        if bid in seen:
            raise ButtonError(f"别名成环：{seen}")
    if bid not in t:
        raise ButtonError(f"没有按钮 {bid!r}")
    return t[bid]


def get(bid: str):
    """按钮的值（point → (x, y)，xs → (x1, x2, …)，points → {名: (x, y)}，y / dy → int，ratio → (u, v)）。"""
    e = entry(bid)
    return _tuple(e["kind"], e["value"])


def point(bid: str) -> tuple[int, int]:
    e = entry(bid)
    if e["kind"] != "point":
        raise ButtonError(f"{bid} 是 {e['kind']}，不是点")
    return tuple(e["value"])


def is_danger(bid: str) -> bool:
    return bool(entry(bid).get("danger"))


def reload() -> None:
    _table.cache_clear()
