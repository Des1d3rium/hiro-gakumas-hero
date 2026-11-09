"""回忆档案（S1）：一份回忆一套「1 客制化、2 换卡、3 饮料、4 结算回忆优先级」。

合并顺序（后面的覆盖前面的；字典逐项合并，列表整个替换）：
  1. 代码缺省      hiro/strategies/data/memory-defaults.json（原 batch_rules.DEFAULTS 的 prep / shop / interval.customize
                   和 batch_memory.PRIORITY）
  2. 旧规则文件    conf/batch-rules.json 里还写着的这几项（兼容期：没迁移的使用者照旧生效，见 LEGACY）
  3. 偶像档案      hiro/strategies/data/idols/<偶像>.json 或 conf/strategies/idols/<偶像>.json 的 memory_defaults
  4. 回忆档案      conf/strategies/memories/<回忆ID>.json
  5. 批次覆盖      batch_hif init --set change.in.column=2

批次在 preflight 时快照一份（snapshot），每局 begin 时重新快照：前端 / 使用者改档案，下一局生效，正在打的这局不变。
批次文件里的快照带各层文件的 sha256，审计时按「哪副牌组、哪套策略」分组比较。

运行时模块用 get("drinks.priority")：绑定了批次快照（bind）就读快照，否则现场合并（没有偶像 / 回忆 = 只有 1、2 层）。
档案里的卡 / 饮料可以写 ID（hiro.strategies.names），运行时统一换成显示名。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from pathlib import Path

from hiro.strategies import schema
from hiro.strategies.names import to_names

ROOT = Path(__file__).resolve().parents[3]
DATA = Path(__file__).resolve().parent / "data"
SCHEMA = "memory-profile/1"

# conf/batch-rules.json 旧位置 → 回忆档案位置（兼容期两边都认，档案优先）
LEGACY = (
    ("interval.customize", "customize"),
    ("prep.change_in", "change.in.rule"),
    ("prep.change_column", "change.in.column"),
    ("prep.change_out", "change.out"),
    ("prep.change_out_swapped", "change.out_swapped"),
    ("prep.change_out_duplicate", "change.out_duplicate"),
    ("prep.veil_fp_balance", "change.fp_balance"),
    ("prep.card_prefer", "change.gift_card.prefer"),
    ("prep.card_avoid", "change.gift_card.avoid"),
    ("prep.card_fallback", "change.gift_card.fallback"),
    ("prep.card_column", "change.gift_card.column"),
    ("shop.high_value", "drinks.high_value"),
    ("prep.drink_priority", "drinks.priority"),
    ("prep.drink_fallback", "drinks.fallback"),
)


class ProfileError(ValueError):
    """档案不合法 / 找不到：在碰游戏前退出（码 2）。"""


def strategies_dir() -> Path:
    return Path(os.environ.get("HIRO_STRATEGIES") or ROOT / "conf" / "strategies")


def legacy_rules_path() -> Path:
    return Path(os.environ.get("HIRO_BATCH_RULES") or ROOT / "conf" / "batch-rules.json")


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _dig(node, dotted: str):
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            raise KeyError(dotted)
        node = node[key]
    return node


def _put(node: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def code_defaults() -> dict:
    return json.loads((DATA / "memory-defaults.json").read_text(encoding="utf-8"))


def legacy_layer(path: Path | None = None) -> tuple[dict, Path | None]:
    """旧 conf/batch-rules.json 里还写着的回忆相关项 → 档案格式（只取文件里真有的键，不带 batch_rules.DEFAULTS）。"""
    p = path or legacy_rules_path()
    if not p.is_file():
        return {}, None
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: dict = {}
    for old, new in LEGACY:
        try:
            _put(out, new, copy.deepcopy(_dig(raw, old)))
        except KeyError:
            pass
    return out, p


def memory_path(memory: str) -> Path:
    return strategies_dir() / "memories" / f"{memory}.json"


def read_json(path: Path, kind: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProfileError(f"{kind}读不了：{path}：{e}") from e
    return data


def load_memory_profile(memory: str | None) -> tuple[dict, Path | None]:
    if not memory:
        return {}, None
    path = memory_path(memory)
    if not path.is_file():
        return {}, None
    data = read_json(path, "回忆档案")
    problems = schema.check(data, "memory-profile")
    if problems:
        raise ProfileError(f"回忆档案不合法：{path}\n  " + "\n  ".join(problems[:20]))
    if data.get("memory") not in (None, memory):
        raise ProfileError(f"回忆档案 {path} 写的是回忆 {data['memory']!r}，不是 {memory!r}")
    return data, path


def parse_set(items: list[str] | None) -> dict:
    """["change.in.column=2", "drinks.fallback=hold", 'drinks.priority=["初星湯"]'] → 覆盖字典。值先按 JSON 解，解不了当字符串。"""
    out: dict = {}
    for item in items or []:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise ProfileError(f"--set 要写成 键=值：{item!r}")
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        _put(out, key.strip(), value)
    return out


def resolve(idol: str | None = None, memory: str | None = None, overrides: dict | None = None) -> dict:
    """合并 → {"profile": 档案（原样，可能含 ID）, "layers": [{layer, path, sha256}]}。不合法抛 ProfileError。"""
    from hiro.strategies import registry

    layers = [{"layer": "code", "path": str(DATA / "memory-defaults.json"), "sha256": _sha(DATA / "memory-defaults.json")}]
    profile = code_defaults()
    legacy, lpath = legacy_layer()
    if legacy:
        profile = merge(profile, legacy)
        layers.append({"layer": "batch-rules", "path": str(lpath), "sha256": _sha(lpath)})
    idol_profile = registry.idol_profile(idol) if idol else None
    if idol_profile and idol_profile.get("memory_defaults"):
        profile = merge(profile, idol_profile["memory_defaults"])
        layers += [{"layer": "idol", "path": p, "sha256": s} for p, s in idol_profile.get("_sources", [])]
    mem, mpath = load_memory_profile(memory)
    if mem:
        profile = merge(profile, {k: v for k, v in mem.items() if k not in ("schema", "memory", "idol", "deck", "note")})
        layers.append({"layer": "memory", "path": str(mpath), "sha256": _sha(mpath)})
    if overrides:
        profile = merge(profile, overrides)
        layers.append({"layer": "batch", "set": copy.deepcopy(overrides)})
    profile["schema"] = SCHEMA
    problems = schema.check(profile, "memory-profile")
    if problems:
        raise ProfileError("合并后的回忆档案不合法：\n  " + "\n  ".join(problems[:20]))
    return {"profile": profile, "layers": layers}


def snapshot(idol: str | None, memory: str | None, overrides: dict | None = None) -> dict:
    """批次快照：合并结果 + 各层哈希 + 偶像档案（引擎 / 能力位）+ 运行时名字版本（ID 已换成名字）。"""
    from hiro.strategies import registry

    merged = resolve(idol, memory, overrides)
    runtime = to_names(merged["profile"])
    body = json.dumps(runtime, ensure_ascii=False, sort_keys=True)
    ip = registry.idol_profile(idol) if idol else None
    return {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"), "idol": idol, "memory": memory,
        "profile": merged["profile"], "runtime": runtime, "layers": merged["layers"],
        "idol_profile": registry.public(ip) if ip else None,
        "sha256": hashlib.sha256(body.encode()).hexdigest()[:16],
    }


# ---------------------------------------------------------------- 运行时读取
_BOUND: dict | None = None
_LIVE_CACHE: dict = {}


def bind(snap: dict | None) -> None:
    """绑定批次快照（batch_hif 读到批次文件后调用）；None = 解绑，回到现场合并。"""
    global _BOUND
    _BOUND = copy.deepcopy(snap) if snap else None


def bound() -> dict | None:
    return _BOUND


def current() -> dict:
    """运行时档案（卡 / 饮料都是显示名）。"""
    if _BOUND is not None:
        return _BOUND["runtime"]
    return to_names(resolve()["profile"])


def get(dotted: str, default=None):
    """profiles.get("drinks.priority")。没绑定快照时每次现场合并（改了文件马上生效，和旧 batch_rules.get 一样）。"""
    try:
        return copy.deepcopy(_dig(current(), dotted))
    except KeyError:
        return default


def from_legacy(memory: str, idol: str | None = None, *, ids: bool = False) -> dict:
    """把现在生效的回忆相关规则（代码缺省 + 旧 batch-rules.json）写成一份回忆档案（迁移用）。
    ids=True：卡 / 饮料名字经 game.db 换成 ID（名字不唯一就报错，不猜）。"""
    from hiro.strategies.names import to_ids

    legacy, _ = legacy_layer()
    body = merge(code_defaults(), legacy)
    body.pop("schema", None)
    out = {"schema": SCHEMA, "memory": memory, **({"idol": idol} if idol else {}), **body}
    deck = ROOT / "reports" / "memories" / f"{memory}.json"
    if deck.is_file():
        out["deck"] = str(deck.relative_to(ROOT))
    return to_ids(out) if ids else out
