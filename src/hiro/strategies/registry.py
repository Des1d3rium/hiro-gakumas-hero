"""偶像策略注册表（S3）：偶像 skin id → 偶像档案（考试引擎 + 牌名 / 顺序 / 阈值 / 开关默认值 + 能力位）。

档案来源（同一偶像两处都有时逐项合并，使用者的覆盖随仓库的）：
  1. 随仓库：hiro/strategies/data/idols/*.json（VEIL 是第一份）
  2. 使用者：conf/strategies/idols/<偶像>.json（HIRO_STRATEGIES 可换目录）

调用方不要再写 `idol == VEIL_IDOL`，改问能力位：
  engine(idol) == "cycle"      清牌 → 过渡 → 循环的 VEIL 式引擎（hiro.brain.veil）、批次 exam / watch 快点入口
  has(idol, "needs_ledger")    要整副牌组账本（deck-base / memory-save 校验牌组）
  has(idol, "fp_balance")      换卡 / 差し入れ 按全力値来源总量调优先级
  has(idol, "burst_auto_route")  burst 命令自带停点路由
没有档案的偶像：engine = "default"（通用规则链 + LLM），能力位全 False。
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from hiro.strategies import schema

DATA = Path(__file__).resolve().parent / "data" / "idols"


def _user_dir() -> Path:
    from hiro.strategies.profiles import strategies_dir

    return strategies_dir() / "idols"


def _merge(base: dict, over: dict) -> dict:
    from hiro.strategies.profiles import merge

    return merge(base, over)


def _read(path: Path) -> dict:
    from hiro.strategies.profiles import ProfileError

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProfileError(f"偶像档案读不了：{path}：{e}") from e
    return data


def _files() -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for d in (DATA, _user_dir()):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            if p.name.endswith(".example.json"):
                continue
            idol = _read(p).get("idol") or p.stem
            out.setdefault(idol, []).append(p)
    return out


def idol_profile(idol: str | None) -> dict | None:
    """合并后的偶像档案（带 _sources：[(路径, sha256)]）；没有档案返回 None。不合法抛 ProfileError。"""
    from hiro.strategies.profiles import ProfileError

    if not idol:
        return None
    paths = _files().get(idol)
    if not paths:
        return None
    data: dict = {}
    for p in paths:
        data = _merge(data, _read(p))
    problems = schema.check(data, "idol-profile")
    if problems:
        raise ProfileError(f"偶像档案不合法（{', '.join(map(str, paths))}）：\n  " + "\n  ".join(problems[:20]))
    data["_sources"] = [(str(p), hashlib.sha256(p.read_bytes()).hexdigest()[:16]) for p in paths]
    return data


def public(profile: dict | None) -> dict | None:
    """去掉内部字段（_sources）的副本，写进批次快照 / profile show。"""
    if profile is None:
        return None
    return {k: copy.deepcopy(v) for k, v in profile.items() if not k.startswith("_")}


def idols() -> list[str]:
    return sorted(_files())


def engine(idol: str | None) -> str:
    p = idol_profile(idol)
    return (p or {}).get("engine", "default")


def has(idol: str | None, capability: str) -> bool:
    p = idol_profile(idol)
    return bool(((p or {}).get("capabilities") or {}).get(capability))


def label(idol: str | None) -> str:
    p = idol_profile(idol)
    return (p or {}).get("label") or str(idol)


def require_engine(idol: str | None, name: str, what: str) -> None:
    """批次入口核对：偶像档案的引擎不是 name 时抛 ValueError（原「仅适用于 [VEIL] 秦谷美玲」）。"""
    if engine(idol) != name:
        have = [i for i in idols() if engine(i) == name]
        raise ValueError(f"{what}只适用于偶像档案引擎为 {name!r} 的偶像（现有：{', '.join(label(i) for i in have) or '无'}）；"
                         f"首检记录的偶像 {idol!r} 是 {engine(idol)!r}")
