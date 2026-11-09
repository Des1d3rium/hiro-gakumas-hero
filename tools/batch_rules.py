"""周回模式（tools/batch_*）的流程开关：列在 conf/batch-rules.json，使用者随时可改，改完下一次读取就生效。

挑选规则（客制化、换卡、差し入れ技能卡、饮料、结算回忆优先级）已搬到回忆档案（hiro.strategies.profiles，
conf/strategies/memories/<回忆>.json，见 docs/strategies.md）。这里旧位置（prep.drink_priority、interval.customize…）
文件里还写着时照样生效（兼容期，档案优先），但 DEFAULTS 里不再有它们。

文件不存在或缺某一项时用这里的 DEFAULTS。路径可用 HIRO_BATCH_RULES 换。环境变量（HIRO_WATCH_TAP_GAP 等）仍可临时覆盖。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "conf" / "batch-rules.json"

DEFAULTS: dict = {
    "driver": {  # watch 自动模式（watch --auto 也能临时打开）：常规停点（考试 / 轮间 / 结算回忆）自己下命令，不叫代理
        "enabled": False,
        "max_refresh": 3,  # 轮间 shop-auto 最多刷新几次
    },
    "watch": {
        "tap_gap": 0.2,  # 连点间隔（秒）；实测 0.2 → 实际约 0.39s/下，0.4 → 约 0.59s/下
        "stay": True,  # 停点不退出，画面变了自动继续
        "tap_through_holds": ["领取选择", "考试中", "差し入れ 选饮料", "差し入れ 选技能卡"],  # 停点确认期间仍点角落
        "result_next_first": True,  # 考试结果页先点一次「次へ」，只有弹「再挑戦確認」才停
        "action_hold_s": 10.0,  # 行动页停点持续这么久才报给代理（首日淡入帧会误停，7.7s 后自己认出）
    },
    "open": {
        "auto_start": "repeat",  # 开局确认页：repeat = 每批第一局由代理核对后手点，之后的重复局没有弹窗 / 危险字样时直接点
        #                         「プロデュース開始」；true = 每局都自动；false = 每局都由代理核对
    },
    "prep": {  # 前六周（R1 之前）固定流程
        # 选饮料页栏满、候选里有高价值：丢持有里最差的一瓶再收（实读饮料栏和账本一致才做，做完实读核对）。
        # 使用者 2026-11-08：「把这个打开」
        "auto_drink_replace": True,
        "enabled": True,
        "action": "差し入れ",  # 行动页选这个
        "last_day": "相談",  # 只剩这个时（最后一天）选它
        "consult_shop": True,  # 相談里先 shop-auto 再「終了」
        # 饮料优先表、差し入れ技能卡、换卡换入 / 换出：回忆档案的 drinks / change（docs/strategies.md）
        "exam_briefing": True,  # 考前说明页（優勝条件）点 (360,300) 进入考试
    },
    "interval": {  # 轮间（代理按这里执行）
        "recover_enabled": True,  # 只有自动模式会用：轮间先回血再客制化 / 购物（使用者 2026-11-08）
        "target_hp": 10,  # VEIL 回到这么多体力（使用者 2026-11-08 定 10，和考试体力闸保底线 HIRO_VEIL_HP_FLOOR 一致）
        "reserve_p": 0,  # 回血后至少留下的 P；先回血再购物，不让刷新吃掉回血钱
        # 客制化清单：回忆档案的 customize（watch 自动模式把它填进 interval.customize 交给 batch_driver）
        "shop_auto": True,
    },
    "shop": {},  # shop-auto 买哪些：回忆档案的 drinks.high_value
    "memory": {
        "use_all_regens": True,
        "no_hit": "first",  # 所有版本都没命中优先项：first = 选第一个（再生成前）；hold = 停下
    },
    "retry": {
        # 敗退「再挑戦確認」：auto = 自动模式下自己点再挑戦（每轮最多 3 次）；hold = 停下交给代理。
        # 使用者 2026-11-08：「把再挑战选择做成默认选项，不再返回到agent。只有再挑战变灰：不可选的时候再选择终了。」
        "on_defeat": "auto",
        "tickets": True,  # auto 时也用再挑戦チケット（没有「無料」字样）；要花ジュエル的照样不点、停下
        "end_when_exhausted": True,  # 再挑戦变灰（「あと0回」）或本轮已用满 3 次：点「プロデュース終了」接着结算；False = 停下交给代理
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: str | Path | None = None) -> dict:
    p = Path(path or os.environ.get("HIRO_BATCH_RULES") or DEFAULT_PATH)
    if not p.is_file():
        return copy.deepcopy(DEFAULTS)
    return _merge(DEFAULTS, json.loads(p.read_text(encoding="utf-8")))


def get(dotted: str, default=None):
    """rules.get("prep.drink_priority")：每次都重读文件，改了马上生效。"""
    node = load()
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def write_defaults(path: Path = DEFAULT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def with_profile(rules: dict) -> dict:
    """规则副本里补上回忆档案的客制化清单（interval.customize），给 batch_driver 用（它只看 rules 字典，不读文件）。"""
    from hiro.strategies import profiles

    out = copy.deepcopy(rules)
    out.setdefault("interval", {})["customize"] = profiles.get("customize", [])
    return out
