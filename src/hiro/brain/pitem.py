# 来源：digital-hiro src/hiro/brain/pitem.py @ c3fc27f（GPL-3.0-or-later）
"""H.I.F カスタムPアイテム（P 道具背包）三选一（SELECT_PITEM）。

流程（seesaawiki H.I.F「カスタムPアイテム」，master ProduceCustomizeItem / ProduceCustomizeItemRelationship 核对）：
- 选拔①通过后取得背包：ポーチ（赤 / 緑 / 黄）；
- 选拔②后第一段定制（ぬいぐるみ）：黄 = ロボ / もじゃ / インコ / うさぎ（4 种随机出 3 个）；
- 选拔③后第二段定制（デコレーション）：花 / リボン / メダル / 羽（ロボ固定 3 选：花 / リボン / 羽）。

ID 规则：customize_pitem-<计划>-04-3-00<颜色>[_00-04-2-00<ぬいぐるみ>_01-01-1-001[_00-04-4-00<装饰>]]。

社区建议（strategy/evidence/hif-guides-20261030.json）：
- 背包选黄（饮料）[seesaa][もなみ][もりし「ポーチ（黄）一択」]；赤（回体力）最差：「赤を選ぶくらいなら授業で眠気取得…おでかけで体力をキープ」[seesaa]；
- 第一段：ロボ（每次课程多拿饮料）> もじゃ（相談的饮料 / 强化折扣）[もなみ][もりし]；インコ、うさぎ 社区没有排序，只作兜底；
- 第二段：花（ロボ：选饮料获得；もじゃ：相談饮料 5 折）> リボン（もじゃ：强化 5 折）> メダル > 羽（「完全に邪魔」）[もなみ]。
"""

import re

COLOR = {"001": "赤", "002": "緑", "003": "黄"}
PLUSH = {"001": "くま", "002": "人形", "003": "ロボ", "004": "もじゃ", "005": "インコ", "006": "うさぎ"}
DECO = {"001": "花", "002": "リボン", "003": "メダル", "004": "羽"}
COLOR_KEY = {"red": "赤", "green": "緑", "yellow": "黄", "赤": "赤", "緑": "緑", "黄": "黄"}  # 识别层的 color 字段

POUCH_RANK = {"黄": 3, "緑": 2, "赤": 1}
PLUSH_RANK = {"ロボ": 4, "もじゃ": 3, "インコ": 2, "うさぎ": 1, "くま": 1, "人形": 1}
DECO_RANK = {"花": 4, "リボン": 3, "メダル": 2, "羽": 1}
WHY = {
    "黄": "背包选黄（饮料）：社区一致推荐 [seesaa][もなみ][もりし]",
    "緑": "没有黄时选緑（P 点）",
    "赤": "赤（回体力）社区评价最低 [seesaa]",
    "ロボ": "ロボ：每次课程后多拿一瓶饮料，第二段必能选花 [もなみ][もりし]",
    "もじゃ": "もじゃ：相談时拿饮料与折扣，ロボ不在时的首选；选了要记得最后一天去相談 [もなみ][もりし]",
    "花": "花：ロボ = 选饮料获得，もじゃ = 相談饮料 5 折 [もなみ]",
    "リボン": "リボン：没有花时的次选（もじゃ = 相談强化 5 折）[もなみ]",
    "メダル": "メダル：社区未推荐",
    "羽": "羽：社区认为不需要（追加卡会让牌组变大）[もなみ]",
}

_ID = re.compile(r"customize_pitem-\d+-04-3-(\d{3})(?:_00-04-2-(\d{3})_01-01-1-001(?:_00-04-4-(\d{3}))?)?$")
_NAME = re.compile(r"^(花|リボン|メダル|羽)?(ポーチ|くま|人形|ロボ|もじゃ|インコ|うさぎ)（(赤|緑|黄)）$")


def parse(item: dict) -> dict | None:
    """识别结果的一项（{item_id?, name?, color?}）→ {stage, color, plush, deco}；stage ∈ pouch / plush / deco。认不出返回 None。
    优先级 item_id > name > color；只有 color（red / yellow / green）时视为取得背包阶段。"""
    if m := _ID.match(item.get("item_id") or item.get("id") or ""):
        color, plush, deco = COLOR.get(m.group(1)), PLUSH.get(m.group(2) or ""), DECO.get(m.group(3) or "")
    elif m := _NAME.match((item.get("name") or "").strip()):
        deco, base, color = m.group(1), m.group(2), m.group(3)
        plush = None if base == "ポーチ" else base
    elif (color := COLOR_KEY.get(str(item.get("color") or "").strip().lower())) is not None:
        plush, deco = None, None  # 识别只给颜色：取得背包阶段（ポーチ）
    else:
        return None
    stage = "deco" if deco else "plush" if plush else "pouch"
    return {"stage": stage, "color": color, "plush": plush, "deco": deco}


def score(p: dict) -> int:
    if p["stage"] == "pouch":
        return POUCH_RANK.get(p["color"], 0)
    if p["stage"] == "plush":
        return PLUSH_RANK.get(p["plush"], 0)
    return DECO_RANK.get(p["deco"], 0)


def reason(p: dict) -> str:
    key = {"pouch": p["color"], "plush": p["plush"], "deco": p["deco"]}[p["stage"]]
    return WHY.get(key, "社区未提及")
