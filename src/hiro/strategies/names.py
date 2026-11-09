"""档案里的卡 / 饮料引用 ↔ 显示名（S2：卡和饮料存 ID，不存名字）。

档案两种写法都认：
- 卡：「インフルエンサー+」这样的名字，或 {"id": "p_card-03-men-2_080", "upgrade": 1}（upgrade 省略 = 不分强化，按基名匹配）。
- 饮料：「センブリソーダ」，或 "pdrink_00-2-010"，或 {"id": "pdrink_00-2-010"}。

运行时各模块仍按名字匹配画面（OCR / 账本存的都是名字），所以这里把 ID 换成名字给它们用；名字原样放行，不碰 game.db
（测试和没有主数据的环境也能跑）。反方向（名字 → ID，迁移 / 前端存档用）按 game.db 精确匹配，不唯一时抛 Ambiguous，不猜。
"""

from __future__ import annotations

import unicodedata


class Ambiguous(ValueError):
    pass


class Unknown(ValueError):
    pass


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split()).replace("＋", "+")


def _db():
    from hiro.state.catalog import catalog

    return catalog().db


def card_name(ref) -> str:
    """卡引用 → 显示名。{"id", "upgrade"} 查 game.db（upgrade 省略按 0 = 基名）；字符串原样返回。"""
    if isinstance(ref, str):
        return ref
    if not isinstance(ref, dict) or not ref.get("id"):
        raise Unknown(f"认不出的卡引用：{ref!r}")
    row = _db().execute("SELECT name FROM ProduceCard WHERE id = ? AND upgradeCount = ?",
                        (ref["id"], int(ref.get("upgrade") or 0))).fetchone()
    if row is None:
        raise Unknown(f"game.db 里没有 {ref['id']} 强化 {ref.get('upgrade') or 0}")
    return row[0]


def drink_name(ref) -> str:
    key = ref.get("id") if isinstance(ref, dict) else ref
    if not isinstance(key, str) or not key:
        raise Unknown(f"认不出的饮料引用：{ref!r}")
    if not key.startswith("pdrink_"):
        return key
    row = _db().execute("SELECT name FROM ProduceDrink WHERE id = ?", (key,)).fetchone()
    if row is None:
        raise Unknown(f"game.db 里没有饮料 {key}")
    return row[0]


def card_ref(name: str, *, keep_upgrade: bool = True) -> dict:
    """名字 → {"id", "upgrade", "name"}。名字带「+」按强化次数精确找；同名对应多个 ID 时抛 Ambiguous。
    keep_upgrade=False：只存 id（不分强化，换出 / 回忆优先级这类按基名匹配的项用）。"""
    n = norm(name)
    rows = _db().execute("SELECT DISTINCT id, upgradeCount FROM ProduceCard WHERE name = ?", (n,)).fetchall()
    ids = sorted({r[0] for r in rows})
    if not ids:
        raise Unknown(f"game.db 里没有叫「{n}」的技能卡")
    if len(ids) > 1:
        raise Ambiguous(f"「{n}」对应 {len(ids)} 张卡（{', '.join(ids)}），请在档案里写 id")
    up = min(r[1] for r in rows if r[0] == ids[0])
    out = {"id": ids[0], "name": n}
    if keep_upgrade:
        out["upgrade"] = up
    return out


def drink_ref(name: str) -> dict:
    n = norm(name)
    rows = _db().execute("SELECT DISTINCT id FROM ProduceDrink WHERE name = ?", (n,)).fetchall()
    if not rows:
        raise Unknown(f"game.db 里没有叫「{n}」的饮料")
    if len(rows) > 1:
        raise Ambiguous(f"「{n}」对应 {len(rows)} 种饮料，请在档案里写 id")
    return {"id": rows[0][0], "name": n}


# 档案里哪些位置是卡 / 饮料引用（memory-profile/1）。值是「点分路径」，[] 表示列表每一项，card 是项里的键。
CARD_PATHS = ("customize[].card", "change.out[]", "change.gift_card.prefer[]", "change.gift_card.avoid[]",
              "memory_regen[].card")
DRINK_PATHS = ("drinks.high_value[]", "drinks.priority[]")
BASE_ONLY = {"change.out[]", "change.gift_card.prefer[]", "change.gift_card.avoid[]", "memory_regen[].card"}


def _walk(node, parts: list[str], fn):
    if not parts or node is None:
        return fn(node)
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        key = head[:-2]
        items = node.get(key) if key else node
        if isinstance(items, list):
            node[key] = [_walk(x, rest, fn) for x in items]
        return node
    if isinstance(node, dict) and head in node:
        node[head] = _walk(node[head], rest, fn) if rest else fn(node[head])
    return node


def _paths(path: str) -> list[str]:
    # "customize[].card" → ["customize[]", "card"]
    return path.split(".")


def to_names(profile: dict) -> dict:
    """档案（可能含 ID 引用）→ 全部换成显示名的副本（运行时用）。只有遇到 ID 引用才查 game.db。"""
    import copy

    out = copy.deepcopy(profile)
    for p in CARD_PATHS:
        _walk(out, _paths(p), lambda v: card_name(v) if isinstance(v, dict) else v)
    for p in DRINK_PATHS:
        _walk(out, _paths(p), lambda v: drink_name(v) if (isinstance(v, dict) or str(v).startswith("pdrink_")) else v)
    return out


def to_ids(profile: dict) -> dict:
    """档案里的名字 → ID 引用（迁移 / 前端存档用）。名字不唯一或查不到时抛错，不猜。
    按基名匹配的位置（换出、回忆优先级…）只存 id；客制化存 id + 强化次数。"""
    import copy

    out = copy.deepcopy(profile)
    for p in CARD_PATHS:
        base_only = p in BASE_ONLY

        def conv(v, base_only=base_only):
            if not isinstance(v, str):
                return v
            if base_only:
                return card_ref(norm(v).rstrip("+"), keep_upgrade=False)
            return card_ref(v)

        _walk(out, _paths(p), conv)
    for p in DRINK_PATHS:
        _walk(out, _paths(p), lambda v: drink_ref(v) if isinstance(v, str) and not v.startswith("pdrink_") else v)
    return out
