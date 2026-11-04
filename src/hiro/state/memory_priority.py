# 来源：digital-hiro src/hiro/state/memory_priority.py @ c3fc27f（GPL-3.0-or-later）
"""回忆（プロデュースメモリー）编成的社区优先级表：Campus 広（i_card-shro-3-007，ロジック / やる気）。

用户 2026-11-01：按社区推荐从高到低选，考虑已有哪些回忆、是否定制 / 附魔过（docs/tasks/回忆编成选择系统.md §1）。

来源（社区权重最高，自家实机记录只作状态数据）：
- [いおん14] いおん HIF 攻略 ep14（2026-10-10）：干劲固定 開始時 Wkwk、せのび（踮起脚尖）、仕切り直し；第 4 张 届いて！ 最稳，
  也可 みんな大好き / アイドル宣言 / テレビ出演。Wkwk、届いて 上配 紙吹雪 再动能力，仕切り直し 配眠気删除。
- [いおん15] ep15（干劲 Solo1 S4+ 刷回忆）：回忆带 アイドル魂、せのび、届いて！；HIF 能力优先 紙吹雪 > Wkwk、本番前夜 > 其他。
- [いおん01/03] ep01 / ep03：回忆价值 = 带入的卡 + 词条；词条按「约 1 属性 = 1 分」估：固定值 10/15/20/25 分，
  百分比 = 实际培养增量 × 百分比（扣触顶溢出）；专用回忆推荐「两个主属性百分比 + 副属性固定值」。
- [もなみ] note 斜籐もなみ（strategy/evidence/hif-monami-20261016-v65.json）：やる気型绝对想要 開始時获得的 デイドリーミング、
  せのびの魔法、私がスター；其余带定制过的 SSR；能力尽量主属性レッスンボーナス，拿不准就选固定值。
- [wiki] seesaawiki H.I.F（hif-wiki-20261016-v65.json）：ワクワク 開始時获得 + 第 1 天特別指導定制「開始時手札に入る」；
  開始時 デイドリーミング + 紙吹雪 再动能力有用；ロジック限定 SSR 私がスター 让 R1 也能高分。
- [wiki论坛] 同页通关者（2026-08-15）：带 夢色リップ 定制（用户判断不到必拿，未采用）（能力 ひなたぼっこ 眠気消し）、ワク止ま 定制（能力 リズミカル 再动）、
  届いて 定制、開始時 デイドリ 定制（能力 紙吹雪 再动 2 回）；私がスター 没有本战大概会输。
- [game8] やる気型持ち込みメモリー（决策-07 时整理，knowledge.MEMORY_WANT）：デイドリーミング、紙吹雪、仕切り直し、アイドル魂、
  届いて！、せのび、ワクワク、アイドル宣言。
- [もりし] 定制推荐（同上）。

字段：
- weight：卡本身的优先度（100 分制，决定排序）；同一张卡不同回忆之间由「回忆质量」区分（定制、入手时机、强化、能力、词条）。
- timing：start = 必须开局入手（中间试验后入手扣 timing_penalty）；any = 都可以。
- customs：[(定制键, 加分, 说明)]，定制键匹配 master 的定制 ID / grow effect（hiro.brain.deciders._customize_text）或识别给的定制文字。
"""

from __future__ import annotations

import re

MEMORY_PRIORITY: list[dict] = [
    dict(card_id="p_card-02-men-2_004", name="ワクワクが止まらない", weight=100, role="开局 / 核心（やる気引擎，选拔①就要用）",
         timing="start", timing_penalty=25,
         customs=[("initial_add", 8, "開始時手札に入る [wiki][もりし]"), ("aggressive_add", 6, "やる気+ 更强 [いおん14]")],
         alts=["ゆめみごこち（[wiki] 有它可代替 Wkwk）"], conflicts=[],
         sources=["いおん14", "wiki", "wiki论坛", "もりし", "game8"]),
    dict(card_id="p_card-02-act-3_186", name="せのびの魔法", weight=96, role="兑现（延迟：剩 5–6 回合打，之后按元气结算）",
         timing="any", timing_penalty=0, timing_bonus_start=3,
         customs=[("exam_block", 8, "元気 定制 [もりし]"), ("exam_card_play_aggressive", 4, "やる気 定制")],
         alts=[], conflicts=[], sources=["いおん14", "いおん15", "もなみ", "もりし", "game8"]),
    dict(card_id="p_card-02-act-1_037", name="デイドリーミング", weight=93, role="核心（持续：元気≥30 时装上，之后每回合按元气得分）",
         timing="start", timing_penalty=15,
         customs=[("enc_", 8, "持续效果强化 [game8]"), ("play_trigger_change", 6, "使用条件 元気30→15")],
         alts=[], conflicts=["仕切り直し：开局在手、元気不够打不出时会被换手牌洗进弃牌（第六局选拔③，协调 4a）"],
         sources=["もなみ", "wiki", "wiki论坛", "game8", "もりし"]),
    dict(card_id="p_card-02-act-3_039", name="届いて！", weight=90, role="兑现（终结：末回合按元气 400%+ 得分）",
         timing="any", timing_penalty=0,
         customs=[("lesson_depend_block_add", 8, "パラメータ+（元気倍率）定制 [もりし][wiki论坛]")],
         alts=[], conflicts=[], sources=["いおん14", "いおん15", "wiki论坛", "もりし", "game8"]),
    dict(card_id="p_card-02-men-3_002", name="私がスター", weight=85, role="兑现辅助（回合追加 +1，延长兑现窗口）",
         timing="any", timing_penalty=0, timing_bonus_start=3,
         customs=[("exam_card_upgrade", 4, "手札强化"), ("cost_review_reduce", 3, "好印象消費 -1")],
         alts=[], conflicts=[], sources=["もなみ", "wiki", "wiki论坛"]),
    dict(card_id="p_card-00-men-3_005", name="仕切り直し", weight=84, role="压缩 / 过牌（SSR 共通，选拔里很难拿到）",
         timing="any", timing_penalty=0, timing_bonus_start=3,
         customs=[("cost_reduce", 6, "コスト -2"), ("stamina_consumption_down", 3, "消費体力減少")],
         alts=["アイドル宣言", "テレビ出演"], conflicts=["デイドリーミング（开局入手时）"], sources=["いおん14", "game8"]),
    dict(card_id="p_card-02-act-2_062", name="きらきら紙吹雪", weight=72, role="循环 / 兑现（定制后是循环核心）",
         timing="any", timing_penalty=0,
         customs=[("lesson_depend_block_add", 8, "元気依存パラメータ+ 定制 [game8][wiki论坛]")],
         alts=[], conflicts=[], sources=["game8", "いおん15", "wiki论坛"], note="选拔里大多拾得到、再定制 [wiki论坛]，所以带入优先度低于上面几张"),
    dict(card_id="p_card-00-men-3_003", name="アイドル魂", weight=68, role="压缩（使用数 +1，下回合手牌强化）",
         timing="any", timing_penalty=0,
         customs=[("cost_reduce", 6, "コスト -2 [game8][いおん01]"), ("block_add", 3, "元気+")],
         alts=["アイドル宣言"], conflicts=[], sources=["いおん15", "game8", "いおん01"]),
    dict(card_id="p_card-02-men-2_057", name="ゆめみごこち", weight=55, role="开局 / やる気（Wkwk 的替代）",
         timing="start", timing_penalty=10,
         customs=[("exam_lesson_depend_block", 6, "元気依存 定制 [wiki论坛]")],
         alts=["ワクワクが止まらない"], conflicts=[], sources=["wiki", "wiki论坛"]),
    dict(card_id="p_card-00-men-2_012", name="アイドル宣言", weight=52, role="压缩 / 过牌（抽 2、使用数 +1）",
         timing="any", timing_penalty=0,
         customs=[("exam_block", 3, "元気 定制"), ("stamina_consumption_down", 2, "消費体力減少")],
         alts=["仕切り直し"], conflicts=[], sources=["いおん14", "もりし", "game8"]),
    dict(card_id="p_card-00-men-3_012", name="テレビ出演", weight=50, role="压缩 / 再动",
         timing="any", timing_penalty=0,
         customs=[("exam_playable_value_add", 6, "再动 定制（[いおん09] 电视演出须有再动定制）")],
         alts=["仕切り直し"], conflicts=[], sources=["いおん14", "いおん09"]),
]
PRIORITY_BY_ID = {e["card_id"]: e for e in MEMORY_PRIORITY}

# 不带入回忆（权重 0）：用户 2026-10-31 判断 夢色リップ 不到必拿（[wiki论坛] 曾带它的定制回忆）；协调 4a 第八局本战分析（用户确认）
# 列为本战要删的冲突牌：気まぐれハート、トキメキ、手書きのメッセージ、夢色リップ、止められない想い、ノートの端の決意
MEMORY_EXCLUDE = {
    "p_card-02-men-3_040": "夢色リップ（用户：不到必拿；4a：本战冲突牌）",
    "p_card-02-men-2_100": "気まぐれハート（4a：本战冲突牌）",
    "p_card-02-men-3_043": "トキメキ（4a：本战冲突牌）",
    "p_card-02-men-3_044": "手書きのメッセージ（4a：本战冲突牌）",
    "p_card-02-men-2_056": "止められない想い（4a：本战冲突牌）",
    "p_card-02-men-3_041": "ノートの端の決意（4a：本战冲突牌）",
}

# 识别只给定制文字（没有定制 ID）时，文字关键词 → 定制键
CUSTOM_TEXT_KEYS = {
    "initial_add": ("開始時手札", "レッスン開始時手札に入る"),
    "aggressive_add": ("やる気+", "やる気＋"),
    "exam_block": ("元気+", "元気＋"),
    "lesson_depend_block_add": ("パラメータ上昇", "元気の"),
    "cost_reduce": ("コスト", "コスト削減"),
    "play_trigger_change": ("元気が15以上",),
}

# ---- HIF 能力（回忆被动）----
# 「〇〇使用時、スキルカード使用数追加+1」（再动）：值取决于触发卡在我们的牌组里打得多不多 [いおん15][いおん14][wiki][wiki论坛]
REACT_TRIGGERS = {
    "きらきら紙吹雪": 10,  # [いおん15] 优先级第一；[wiki] 開始時デイドリ + 紙吹雪 再动有用；[wiki论坛] 紙吹雪 再动 2 回
    "ワクワクが止まらない": 8,  # [いおん15]
    "本番前夜": 6,  # [いおん15]
    "リズミカル": 5,  # [wiki论坛]
    "届いて！": 4,
}
SLEEP_TRIGGERS = {"ひなたぼっこ": 5}  # 「〇〇使用時、眠気を削除」[wiki论坛]；其余默认 3


def card_entry(card_id: str | None) -> dict | None:
    return PRIORITY_BY_ID.get(card_id or "")


def fallback_weight(card_id: str | None) -> int:
    """不在优先级表里的卡：やる気 卡按评级给低分；冲突 / 偏离路线的卡 0。"""
    from hiro.state.knowledge import CONFLICT_CARDS, OFF_PLAN_CARDS, YARUKI_CARDS

    if not card_id or card_id in CONFLICT_CARDS or card_id in OFF_PLAN_CARDS or card_id in MEMORY_EXCLUDE:
        return 0
    tier = (YARUKI_CARDS.get(card_id) or {}).get("tier")
    return {"S": 45, "A": 40, "B": 30}.get(tier, 20 if "-02-" in card_id or "-00-" in card_id else 0)


def _quoted(text: str) -> str | None:
    """能力文字里的触发卡：「〇〇」使用時（旧格式）或「試験ごとに、以降N回まで、〇〇使用後…」（回忆页实读，2026-11-01 库存）。"""
    m = re.search(r"[「『](.+?)[」』]", text) or re.search(r"まで、(.+?)使用", text)
    return m.group(1).rstrip("+＋") if m else None


def _trigger_value(trigger: str | None, table: dict, plan_default: float) -> float:
    """触发卡的价值：表里的（部分匹配，OCR 可能截断）；やる気 牌组常见的卡 plan_default；别的プラン的卡（意地、ハイタッチ…）几乎没用。"""
    if not trigger:
        return 1.0
    for name, v in table.items():
        if name in trigger or trigger in name:
            return v
    from hiro.state.knowledge import YARUKI_CARDS

    if any(trigger in (k.get("name") or "") or (k.get("name") or "") in trigger for k in YARUKI_CARDS.values()):
        return plan_default
    return 0.5


def ability_value(text: str) -> tuple[float, str | None]:
    """一条 HIF 能力 / 被动 → (分值, 去重键)。去重键相同的能力在一套编成里第二条只算一半（[いおん14] 同类重复不能按次数累加）。
    属性百分比 / 固定值词条不在这里算（见 param_points）。"""
    t = text.replace("＋", "+")
    trigger = _quoted(t)
    times = 1.5 if re.search(r"[2２]回", t) else 1.0
    if "使用数追加" in t:
        return _trigger_value(trigger, REACT_TRIGGERS, 3) * times, f"react:{trigger}"
    if "眠気" in t and any(k in t for k in ("削除", "除外", "消")):
        return _trigger_value(trigger, SLEEP_TRIGGERS, 3) * times, f"sleep:{trigger}"
    if ("ボーナス" in t and "%" in t) or "上昇+" in t:
        return 0.0, None  # 属性词条：按 param_points
    return 0.0, None  # 其他（OCR 碎片「H.I.F専用重複発動不可」、体力回复概率等）不计分


# ---- 属性词条估价 [いおん01][いおん03] ----
# 1 点属性 ≈ 1 分；百分比 × 实际培养增量（选拔里各属性的增量），扣掉触顶溢出。
# 默认值取 main8（2026-11-01，Campus 広 Da 主属性）选拔结束：Da 3220（上限 3200，已触顶）、Vo 2171、Vi 1173。
PARAM_CAP = 3200
PARAM_GAINS = {"Da": 2900, "Vo": 1900, "Vi": 950}  # 选拔中的属性增量（百分比的作用基数，约 = 结束值 − 开局值）
PARAM_FINALS = {"Da": 3150, "Vo": 2170, "Vi": 1170}  # 不带这份回忆时的预计结束值（离上限多远）
ATTR_WORDS = {"Vo": ("ボーカル", "Vo"), "Da": ("ダンス", "Da"), "Vi": ("ビジュアル", "Vi")}


def param_points(params: dict | None, gains: dict | None = None, finals: dict | None = None, cap: int = PARAM_CAP) -> float:
    """params：{Vo: {fixed, pct}, ...}。固定值 + 百分比 × 增量，按「离上限还有多少」封顶后相加。"""
    gains, finals = gains or PARAM_GAINS, finals or PARAM_FINALS
    total = 0.0
    for attr, v in (params or {}).items():
        if attr not in gains:
            continue
        fixed, pct = (v.get("fixed") or 0, v.get("pct") or 0) if isinstance(v, dict) else (v or 0, 0)
        raw = fixed + pct / 100 * gains[attr]
        room = max(0, cap - finals[attr]) if attr in finals else raw  # main8 Da 已触顶：Da 词条大多溢出
        total += min(raw, room)
    return total


def parse_param_texts(texts: list[str]) -> dict:
    """「ダンスパラメータボーナス +2.8%」「ボーカル +25」之类的文字 → {Da: {pct: 2.8}, Vo: {fixed: 25}}。"""
    out: dict = {}
    for t in texts or []:
        t = t.replace("＋", "+")
        if any(k in t for k in ("確率", "回復", "終了時", "使用")):
            continue  # 「ビジュアルレッスン終了時、45%の確率で体力回復4」、再动能力之类不是属性词条
        attr = next((a for a, words in ATTR_WORDS.items() if any(w in t for w in words)), None)
        m = re.search(r"\+\s*([0-9.]+)\s*(%?)", t)
        if attr is None or m is None:
            continue
        slot = out.setdefault(attr, {"fixed": 0, "pct": 0.0})
        if m.group(2):
            slot["pct"] += float(m.group(1))
        else:
            slot["fixed"] += int(float(m.group(1)))
    return out
