"""H.I.F 本战「五卡循环」引擎（engine = "cycle"）。第一份档案是 [VEIL] 秦谷美玲。

牌名、核心牌、出牌顺序、停手阈值、手牌上限、考试饮料、全力値阈值、姿态限定牌、开关默认值都来自偶像档案
（hiro/strategies/data/idols/<偶像>.json，使用者覆盖 conf/strategies/idols/<偶像>.json，见 docs/strategies.md）。
导入时读一次：批次绑定了档案快照（hiro.strategies.profiles.bind）就用快照里的偶像档案，本局不变。
"""

from __future__ import annotations

import os
import sys
import re
import unicodedata
from collections import Counter

from hiro.contracts import Decision
from hiro.loop.errors import ResumeBlocked

IDOL = "i_card-skin-hmsz-3-016"  # 这个引擎缺省用的偶像档案


def _load_profile(idol: str) -> dict:
    from hiro.strategies import profiles, registry

    snap = profiles.bound()
    if snap and snap.get("idol") == idol and snap.get("idol_profile"):
        return snap["idol_profile"]
    profile = registry.idol_profile(idol)
    if profile is None or profile.get("engine") != "cycle":
        raise RuntimeError(f"没有偶像 {idol} 的 cycle 引擎档案（hiro/strategies/data/idols/）")
    return profile


PROFILE = _load_profile(IDOL)
_CARDS = PROFILE["cards"]
ENTERTAINER, DRIVE, TEACH, REBOUND = (_CARDS[k] for k in ("entertainer", "drive", "teach", "rebound"))
CORE = frozenset(_CARDS[k] for k in PROFILE["core"])
BOOST, SODA, WHEY = (PROFILE["drinks"][k] for k in ("boost", "soda", "whey"))
EXTRA_TURN = PROFILE["drinks"]["extra_turn"]  # 初星湯：ターン追加+1体力消費2消費体力増加1ターン
WARM = {"温存", "温存1段階目", "温存2段階目", "のんびり"}  # 游戏机制，不进档案
STANCES = ("温存", "温存1段階目", "温存2段階目", "のんびり", "強気", "全力", "指針解除")
ORDERS = {int(k): tuple(_CARDS[r] for r in v) for k, v in PROFILE["orders"].items()}  # 夜空剩余次数 → 出牌顺序
LIMITS = dict(PROFILE["limits"])  # 各轮停手阈值
HAND_MAX = PROFILE["hand_max"]  # 手牌上限：抽牌超出的作废
STANCE_LIMITED = dict(PROFILE.get("stance_limited") or {})  # 姿态限定牌（基名）→ {stance, unlock_from}
PLAY_LATER = frozenset(PROFILE.get("play_later") or ())  # 有别的牌可打时留后的牌（基名）


def in_stance(current, spec: str) -> bool:
    """档案里的姿态写法：warm = 温存类；!warm = 非温存类；其余按姿态名比较（!名 = 不是它）。"""
    if spec.startswith("!"):
        return not in_stance(current, spec[1:])
    return current in WARM if spec == "warm" else current == spec


# 默认开的策略开关（使用者 2026-11-07，22 个 trace 回放后定）：设 VEIL_<name>=0 关掉。其余默认关，=1 打开
# 2026-11-08 trusted trace 复盘后：DRAW_ALL / LAST_TURN / CYCLE_SODA / STUCK_SODA 也默认开（使用者确认）；HP_NET 默认关
# 2026-11-08 验证局 1-c R1 倒数第三回合后：FP_RESERVE / CLEAR_FIRST / MINOR_HP 默认开（使用者：「按方案改」）
# 2026-11-08 验证局 1-d R2 剩 12：HP_NET 默认开（使用者：「做方案A」）——体力 12 时 みずたまりスキップ+（元気+10、再动）被保底线拦
# 默认开的一组现在在偶像档案 flags.default_on（以上记录照旧作来历说明）。
DEFAULT_ON = frozenset(PROFILE["flags"]["default_on"])
STRATEGY_FLAGS = tuple(PROFILE["flags"]["known"])


def _flag(name: str) -> bool:
    """VEIL 出牌策略开关：环境变量 VEIL_<name>=1 打开、=0 关掉，没设时按 DEFAULT_ON。旧名 HIRO_VEIL_<name> 仍认，
    两个都设时以新名为准（使用者 2026-11-07：策略开关去掉 HIRO_ 前缀）。"""
    value = os.environ.get(f"VEIL_{name}")
    value = value if value is not None else os.environ.get(f"HIRO_VEIL_{name}")
    if value in ("0", "1"):
        return value == "1"
    return name in DEFAULT_ON


def keep_warm():
    """VEIL_KEEP_WARM=1：清牌阶段保留温存 / 能留到下回合的再动（默认关，回放验证后再开）。"""
    return _flag("KEEP_WARM")


def hp_guard():
    """VEIL_HP_GUARD=1：清牌阶段按姿态和消耗加成算出牌实际掉的体力，跌破保底线的不打；強気下不改姿态的掉血牌留到温存；
    強気下优先打切回温存的牌
    （使用者 2026-11-07：脚本在強気、一心不乱加成后、元気不足时照打高消耗牌，trace 里一张牌 25→4）。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("HP_GUARD")


MINOR_LOSS = PROFILE["minor_loss"]  # 強気下实际掉血 < 8、打完仍在保底线上的牌不留到温存（使用者 2026-11-08：「当前会消耗小于8的都可以打」）


def minor_hp():
    """VEIL_MINOR_HP=1（默认开）：体力闸「強気下不改姿态的掉血牌留到温存」只拦实际掉血 ≥ 8（> MINOR_LOSS）的牌；
    实际掉血按 hp_loss 算（強気 ×2 / 2 段階直接扣、温存减半、消費体力減少 / 増加 / 削減 / 追加、一心不乱等コスト加成、先扣元気）。
    验证局 1-c R1 剩 3：強気、体力 44，ひと呼吸+ 只掉 1 点也被拦，候选只剩 エンターテイナー+ / 巻き返し+，
    打了 巻き返し+ 花掉全力値，下回合 アイドルになります+ 打不出（使用者 2026-11-08：应先清一次性牌）。"""
    return _flag("MINOR_HP")


def release_gain():
    """VEIL_RELEASE_GAIN=1：温存 / のんびり 下打「強気に変更」的牌，按「先结算牌、再结算夜空」算温存解除的 +1 出牌次数——
    夜空只剩 1 次、最终变成のんびり时也算（原来算 0，于是手里有 エンターテイナー+、巻き返し+ 也去喝ホエイ）。
    实机 codex-20261107-1 第 1 局 R2 剩 8 回合：喝了ホエイ再打这两张，打完同回合还剩 1 次（trace 第 23 步）。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("RELEASE_GAIN")


def fp_reserve():
    """VEIL_FP_RESERVE=1：清牌阶段花全力値的牌（アンダンテ+、清牌用的 巻き返し+…）只在花完还剩 ≥ FP_RESERVE_AT 时打，
    给五卡循环留住 アイドルになります+（全力値消費3）先打的全力値。账本不知道（None）、アイドルになります+ 已不在牌组、
    最后一回合时不限制。实机 codex-20261107-1 第 1 局 R2：清牌把 成果+ 的 +4 花在 アンダンテ+ / 巻き返し+ ×2 上，
    剩 5 回合进循环时全力値 0，アイドルになります+ 灰着打不出。默认开（=0 关；使用者 2026-11-08，验证局 1-c R1 剩 3 同样的错）。"""
    return _flag("FP_RESERVE")


def draw_last():
    """VEIL_DRAW_LAST=1：清牌时好几张都能加出牌次数，先打不抽牌、也不改姿态的那张（みずたまりスキップ 等），
    带抽牌的（インフルエンサー+、フルスロットル+、仕切り直し+）留后：手牌少了，抽进来的牌不会因手牌满而作废
    （使用者 2026-11-07，验证局 R1 剩 7 回合：满手先打了 インフルエンサー+ / フルスロットル+，みずたまりスキップ 留到最后）。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("DRAW_LAST")


def clear_first():
    """VEIL_CLEAR_FIRST=1：清牌阶段一次性牌（打出就离开牌库）排在复用牌前面，哪怕复用牌在温存下切強気能补一次出牌次数；
    ひと呼吸 只让给别的一次性牌，不再让给复用牌。使用者 2026-11-07（验证局 1-f R1 剩 5–2 回合）：温存下总是先打
    教えてあげる+ / エンターテイナー+（切強気 +1 次），到強気又被体力闸压后，頂点へ+ 从剩 5 拖到剩 2 才打。
    默认开（=0 关；使用者 2026-11-08，验证局 1-c R1 剩 3：应先清一次性牌 ひと呼吸+）。"""
    return _flag("CLEAR_FIRST")


def drive_fp():
    """VEIL_DRIVE_FP=1：清牌阶段手里 アイドルになります+ / 巻き返し+ 因全力値不够发灰、又没有别的可打的加全力値牌时，
    打 オーバードライブ+（全力値+2、自带再动）攒全力値。原来清牌一律不打 オーバードライブ，它又是唯一的全力値来源，
    全力値永远 0、两张一直灰着（使用者 2026-11-07，验证局 1-f R2 剩 7–1 回合：死循环）。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("DRIVE_FP")


def fp_cap():
    """VEIL_FP_CAP=1：清牌阶段别把全力値加到 FULL_POWER_AT（满了下回合自动进全力、全力値清零）。有别的牌可打时，
    不打会让全力値到顶的加全力値牌。验证局 codex-20261107-1-c R2：プライド +3、成果+ +4、涙の思い出+ +4 在 T3 加到 11，
    T4 自动全力打的全是清牌牌，之后 T6–T10 全力値 0，巻き返し+ / アイドルになります+ 打不出。默认开（=0 关；22 个 trace 回放后打开，使用者 2026-11-07）。"""
    return _flag("FP_CAP")


def draw_all():
    """VEIL_DRAW_ALL=1：过渡阶段「要不要打 教えてあげる+ 预约下回合多抽 2 张」按牌库张数判断：下回合抽的张数少于牌库
    （核心 4 张 + 第五张）就预约。原来只看下回合抽 4 / 5 张，センブリソーダ 让下回合抽 4 张时就当成够了。
    trusted trace codex-20261108-3-c 第 2 局 R2 T5–T8：牌库 5 张、每回合抽 4 张，次次缺一张核心，连空 4 回合（使用者 2026-11-08 确认）。默认开（=0 关）。"""
    return _flag("DRAW_ALL")


def last_turn_play():
    """VEIL_LAST_TURN=1：最后一回合（剩 1）不进过渡、不「等待下回合」，按清牌把能打的都打掉，オーバードライブ+ 也算。
    trusted trace 3 第 2 局 R1 attempt2 T9、3-c 第 1 局 R2 T12：剩 1 回合手里有 エンターテイナー+ 等、还有出牌次数却跳过（使用者 2026-11-08 确认）。默认开（=0 关）。"""
    return _flag("LAST_TURN")


def cycle_soda():
    """VEIL_CYCLE_SODA=1：五卡循环开头手牌放不下 2 张（满 5 / 4 张）时不喝センブリソーダ（当场抽的 2 张作废），
    最后一回合照喝（只要 +10%）。trusted trace 里 10 次满手进循环喝ソーダ（使用者 2026-11-08 确认）。默认开（=0 关）。"""
    return _flag("CYCLE_SODA")


def stuck_soda():
    """VEIL_STUCK_SODA=1：清牌阶段还有出牌次数、手里没有一张允许打的牌（不是被体力闸压住）时，手牌放得下就喝センブリソーダ 抽 2 继续清。
    trusted trace 3-3 第 1 局 R1 attempt2 T3：手牌空、1 次出牌、有ソーダ 却跳过（使用者 2026-11-08：不 optimal）。默认开（=0 关）。"""
    return _flag("STUCK_SODA")


def hp_net():
    """VEIL_HP_NET=1：体力闸按「可用体力 = 体力 + 元気」看：打出后加的元気不少于这张实际花掉的（元気 + 体力），
    可用体力不减，不拦（みずたまりスキップ+：体力消費1、元気+8）；核心打分牌（エンターテイナー+ 等）也不拦。
    打完体力至少剩 1。使用者 2026-11-08：mutation test 说体力闸没错但太保守。
    默认开（=0 关；使用者 2026-11-08「做方案A」：验证局 1-d R2 剩 12，体力 12 时 みずたまりスキップ+ 被保底线拦）。"""
    return _flag("HP_NET")


# 2026-11-08 trusted trace 决策审查（以进入循环的目标回合为准，32 条 trace）后新增的 8 个开关：默认关，实机验证后再定默认值。
# 执行层的「没点上先重试」另在 hiro.exec.exam（HIRO_LANDING_RETRY）。
def idol_clear():
    """VEIL_IDOL_CLEAR=1：清牌 / 过渡里，手里别的牌都清不了时，アイドルになります+ 不受「三选二」限制（手里至少两张
    エンターテイナー+／オーバードライブ+／教えてあげる+）。画面上它不发灰，牌面只要求全力値消費3；三选二是为了它的
    「手札のパラメータ上昇回数増加」，但清牌期留着它会推迟进循环。例：3-1 第 1 局 R1 attempt1 剩 6，手里只剩 OD 和アイドル，
    结果 Skip，进循环晚一回合。"""
    return _flag("IDOL_CLEAR")


def stance_screen():
    """VEIL_STANCE_SCREEN=1：画面姿态图标和账本姿态不一致时，体力估算取两者里掉血多的那个，决策仍以账本为准。
    例：3-c 第 1 局 R2 attempt1 T1，账本是のんびり、画面是強気1，按 0 消耗打出頂点へ+，体力 8→1，整局被体力闸卡住，没进循环。"""
    return _flag("STANCE_SCREEN")


def cycle_plan():
    """VEIL_CYCLE_PLAN=1：牌库快压缩完时（手牌之外最多还剩 1 张一次性牌 / 眠気），枚举本回合的出牌顺序（含ホエイ），
    按姿态、夜空、全力値、体力推演，找出能让下回合开局就进循环的顺序照着打：压缩完、温存、夜空 1/2、下回合抽满。
    过渡里推了夜空却切不回温存时就不推。审查中 3-c-3 R1 attempt2 剩 4、3-3 R2 剩 5、1107 R1 attempt2 剩 3 都因此晚了 1–2 回合。"""
    return _flag("CYCLE_PLAN")


def strong_teach():
    """VEIL_STRONG_TEACH=1：清牌里「可打的只剩复用牌 → 不打、温存留到下回合」只在温存 / のんびり 下才成立。
    不在温存时本来就没有温存可留，夜空为 2 / 3 时改打教えてあげる+：推夜空，并预约下回合多抽 2。
    例：1-c 第 1 局 R2 attempt1 剩 8、7 连跳两回合。"""
    return _flag("STRONG_TEACH")


def cycle_whey():
    """VEIL_CYCLE_WHEY=1：五卡循环的回合，手牌不到 5 张（第五张不在手里）时不喝初星ホエイプロテイン，因为多出的出牌次数没有牌可打；
    最后一回合照喝。例：3-c 第 1 局 R1 attempt2 剩 4 白喝了一瓶，下回合过渡差一次出牌，掉出循环两回合。"""
    return _flag("CYCLE_WHEY")


def drive_check():
    """VEIL_DRIVE_CHECK=1：DRIVE_FP 为攒全力値打オーバードライブ之前，先确认打完有用：
    加完的全力値够让发灰的那张牌打出；アイドルになります+ 在打完 OD 后仍满足打出条件；全力状态下不为巻き返し+ 攒（全力下切不了姿态）。
    例：1-c R2 剩 5、3-2 R1 attempt1、3-c-3 R1 attempt1 / R2 attempt1，OD 都白打了，还多出一张眠気。"""
    return _flag("DRIVE_CHECK")


def stance_later():
    """VEIL_STANCE_LATER=1：只剩 1 次出牌、姿态限定牌（一心不乱+ / ガタゴトすやすや+）本身又不加次数时，
    先打一张不改变当前姿态的一次性再动牌，或先喝ホエイ，再打姿态限定牌。
    例：3-1 第 1 局 R1 剩 6，一心不乱 用掉了最后一次出牌，3 瓶ホエイ一瓶没喝。"""
    return _flag("STANCE_LATER")


def hp_zero():
    """VEIL_HP_ZERO=1：状态栏有「消費体力変化」时（全身全霊+：次に使用したスキルカードの消費体力を0にする），下一张牌不掉体力。
    原来体力闸当它照常掉血。例：3-3 第 1 局 R1 attempt2 剩 9，インフルエンサー++ 因此被留到温存。"""
    return _flag("HP_ZERO")


FULL_POWER_AT = PROFILE["full_power"]["at"]  # 全力値到 10 自动进全力（实机）
IDOL_CARD = _CARDS["idol_card"]  # 去「+」的基名
FP_RESERVE_AT = PROFILE["full_power"]["reserve"]  # アイドルになります+：全力値消費3（主数据）

# 全力値：牌面「全力値+N」「全力値消費N」「全力値増加量増加+X%（Nターン）」。只看打出时立即结算的部分：
# 「以降」「次のターン」「ターン開始時」「成長：」之后是延迟 / 常驻 / 成长效果（如 待ち望んだ瞬間 的「直接効果で全力値が増加後…全力値+1」），不在打出时记。
_FP_GAIN = re.compile(r"全力値\+(\d+)")
_FP_COST = re.compile(r"全力値消費(\d+)")
_FP_BOOST = re.compile(r"全力値増加量増加\+(\d+)[%％][（(](\d+)ターン[）)]")  # norm 后全角括号变半角
_FP_LATER = re.compile(r"以降|次のターン|ターン開始時|成長：")
# 姿态条件的全力値：「強気の場合、全力値+4」。牌面前面先「温存に変更」的，结算到这里时姿态已是温存，条件不成立
# （涙の思い出+「温存に変更 元気+4 強気の場合、全力値+4」：codex-20261108-3-b 第 1 局 R1 被无条件记 +4，
# 账本 7、画面 3，T8 按账本解灰打 アイドルになります+ 打不出，撤销后又解灰，循环卡死）
_FP_COND = re.compile(r"(非全力状態|強気|温存|のんびり|全力)の場合、全力値\+\d+")
_STANCE_SET = re.compile(r"(強気|温存|のんびり|全力)(?:\d段階目)?に変更")


def _fp_drop_false_conditions(text: str) -> str:
    """去掉牌面里「先变姿态、后接与新姿态不符的姿态条件」的全力値段；前面没有变姿态的条件取决于打出时的姿态，照旧算。"""
    out, last = [], 0
    for m in _FP_COND.finditer(text):
        changes = _STANCE_SET.findall(text, 0, m.start())
        cond = m[1]
        if changes and not (cond == changes[-1] or (cond == "非全力状態" and changes[-1] != "全力")):
            out.append(text[last:m.start()])
            last = m.end()
    return "".join(out) + text[last:]


def fp_effect(card) -> dict:
    """牌面文字 → {gain, cost, boost: (百分比, 回合数) | None}。主数据原文：オーバードライブ+ 全力値+2、巻き返し+ 全力値消費1、
    アイドルになります+ 全力値消費3、リスキーチャンス+ 全力値+3 / 全力値増加量増加+50%（4ターン）。"""
    text = _fp_drop_false_conditions(_FP_LATER.split(norm(getattr(card, "text", card) or ""), maxsplit=1)[0])
    boost = _FP_BOOST.search(text)
    return {"gain": sum(map(int, _FP_GAIN.findall(text))), "cost": sum(map(int, _FP_COST.findall(text))),
            "boost": (int(boost[1]), int(boost[2])) if boost else None}


def fp_delta(card) -> tuple[int, int]:
    """牌面文字里的（全力値+N, 全力値消費N）。"""
    eff = fp_effect(card)
    return eff["gain"], eff["cost"]


def hp_floor():
    """体力闸的保底线：打完这张至少还剩这么多体力（HIRO_VEIL_HP_FLOOR，默认 10）。"""
    try:
        return int(os.environ.get("HIRO_VEIL_HP_FLOOR", "10"))
    except ValueError:
        return 10


STRONG2_DIRECT = 1  # 強気 2 段階：每张牌另外直接扣 1（使用者 2026-11-07 确认就是 1）
ISSHIN_COST_ADD = 7  # 一心不乱+：強気効果のスキルカードの…コスト値増加+7（主数据，重複不可，本场一直有效）
MORIAGE_COST_ADD = 1  # 盛り上げ上手：アクティブスキルカードの…コスト値増加+1（主数据）
# 打出后本场一直有效的消耗增减：「（メンタル|アクティブ|強気効果の|すべての）スキルカードの…コスト値増加+N / 減少-N」。
# 使用者 2026-11-07：一心不乱、盛り上げ上手 增加，巻き返し 减少；1-e R2 T1 インフルエンサー+（メンタル +1）没算，
# モチベ+ 估 4 实际 7（(2+1)×2+1）、ひと呼吸+ (7+1)×2+1 = 17
_COST_MOD = re.compile(r"(メンタル|アクティブ|強気効果の|すべての)スキルカードの[^。]{0,24}?コスト値(?:増加|減少)([+-]?\d+)")


def cost_mods(plays, text_of) -> list[tuple[str, int]] | None:
    """本场打过的牌（[(回合, 打/喝, 名字)]）→ [(范围, 增减)]；范围 mental / active / strong / all。没有出牌记录 None（退回按状态名估）。"""
    if not plays:
        return None
    out = []
    for _, kind, name in plays:
        if kind != "打":
            continue
        for scope, n in _COST_MOD.findall(norm(text_of(name) or "")):
            out.append(({"メンタル": "mental", "アクティブ": "active", "強気効果の": "strong", "すべての": "all"}[scope], int(n)))
    return out


def _master_text(name):
    try:
        from hiro.brain.cards import description_text, select

        row = select("SELECT produceDescriptions FROM ProduceCard WHERE name = ?;", name)
    except Exception:  # noqa: BLE001
        return None
    return description_text(row[0]) if row else None


def _status_value(status) -> int:
    v = (status or {}).get("value")
    return int(v) if isinstance(v, (int, float)) and v > 0 else 0


SCREEN_STANCE = "_screen_stance"  # veil_exam 放进 statuses：画面姿态和账本不一致时的画面读数（VEIL_STANCE_SCREEN）


def hp_loss(state, card, current, mods=None, *, spent=False):
    """打出这张牌实际掉的体力（估计，宁高勿低）。VEIL_STANCE_SCREEN：current 就是账本姿态、画面读到别的姿态时，取掉血多的。"""
    loss = _hp_loss_at(state, card, current, mods, spent=spent)
    if stance_screen() and current is not None and current == stance(state):
        seen = next((s for s in state.statuses or [] if s.get("name") == SCREEN_STANCE), None)
        if seen and seen.get("value") and seen["value"] != current:
            level = seen.get("level") if seen.get("level") in (1, 2) else None
            loss = max(loss, _hp_loss_at(state, card, seen["value"], mods, spent=spent, level=level, use_level=True))
    return loss


def _hp_loss_at(state, card, current, mods=None, *, spent=False, level=None, use_level=False):
    """打出这张牌实际掉的体力（估计，宁高勿低）。

    主数据（2026-11-07 核对）：コスト值先加一心不乱（強気効果的牌 +7）、盛り上げ上手（アクティブ +1）；
    姿态倍率 強気 ×2（2 段階另外每张直接扣 1）、温存 1 段階 ×0.5、2 段階 ×0.25、のんびり ×0；先扣元気。
    未在主数据文本核实、按保守处理：消費体力増加 ×2、減少 ×0.5，消費体力追加 / 削減 按状态数值；体力消費（直接扣）也乘姿态倍率、不被元気挡。
    spent=True：算花掉的元気 + 体力（不先扣元気），给 usable_net 用。"""
    import math

    names = {norm(s.get("name") or ""): s for s in state.statuses or []}
    cost = card.cost or 0
    if mods is not None:
        # 按本场出牌记录里的牌面效果算（一心不乱、盛り上げ上手、インフルエンサー、巻き返し…）
        for scope, n in mods:
            if (scope == "all" or scope == card.category or (scope == "strong" and destination(card) == "強気")):
                cost += n
        cost = max(0, cost)
    else:
        if "一心不乱" in names and destination(card) == "強気":
            cost += ISSHIN_COST_ADD
        if "盛り上げ上手" in names and card.category == "active":
            cost += MORIAGE_COST_ADD
    mult, per_card = 1.0, 0
    if current == "強気":
        mult = 2.0
        # 強気 2 段階：每张另外直接扣 STRONG2_DIRECT。实测 1-e R2 T1：第一印象++（cost 4，強気 1）33→25 = 4×2；
        # モチベ+（cost 2，強気 2）25→18 = 2×2+3；ひと呼吸+（cost 7，強気 2）18→1 = 7×2+3（原来按 +1 估，低估 2）
        # 段階不明时按二阶段估计，避免低估扣血。
        per_card = STRONG2_DIRECT if (level if use_level else stance_level(state, current)) != 1 else 0
    elif current == "のんびり":
        mult = 0.0
    elif current in WARM:
        mult = 0.25 if (level if use_level and current == "温存" else stance_level(state, current)) == 2 else 0.5
    if "消費体力増加" in names:
        mult *= 2.0  # 原按 1.5；游戏里应是翻倍（未在主数据核实），按高估
    if "消費体力減少" in names:
        mult *= 0.5
    # 固定值：消費体力追加 N 在倍率前加（高估），消費体力削減 N 在倍率后减（少减，高估）。
    # 体力消費軽減 / 消費体力変化 效果未核实、状态栏也读不到数值：不减（宁高勿低）
    add_fix = _status_value(names.get("消費体力追加"))
    cut_fix = _status_value(names.get("消費体力削減"))
    scaled = max(0, math.ceil((cost + add_fix) * mult) - cut_fix)
    direct = math.ceil((card.hp_cost or 0) * max(mult, 1.0))  # 体力消費：只往高估，温存 / のんびり 不减
    if hp_zero() and _status_value(names.get("消費体力変化")) > 0:
        # 全身全霊+「次に使用したスキルカードの消費体力を0にする（2回）」：状态栏「消費体力変化 N」还有次数，这一张不掉体力
        # （元気也不花）。強気 2 段階的直接扣 1 没核实，照旧算（宁高勿低）
        scaled = direct = 0
    paid = max(0, scaled - (state.genki or 0))
    if spent:
        return scaled + direct + per_card
    return paid + direct + per_card


_GENKI_GAIN = re.compile(r"元気\+(\d+)")


def genki_gain(card) -> int:
    """牌面「元気+N」（打出时立刻加的元気；「元気値増加+N」这类加成不算）。"""
    return sum(int(n) for n in _GENKI_GAIN.findall(norm(card.text or "")))


def usable_net(state, card, current, mods=None) -> int:
    """打出这张后可用体力（体力 + 元気）的变化：加的元気 − 花掉的（元気 + 体力）。≥ 0 表示可用体力不减。"""
    return genki_gain(card) - hp_loss(state, card, current, mods, spent=True)


def norm(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def stance(state):
    names = {norm(s.get("name") or "") for s in state.statuses or []}
    return next((name for name in STANCES if name in names), None)


def stance_level(state, current):
    if current is None:
        return None
    if current in ("温存1段階目", "温存2段階目"):
        return 2 if current == "温存2段階目" else 1
    return next((s["value"] for s in state.statuses or []
                 if norm(s.get("name") or "") == current and s.get("value") in (1, 2)), None)


# 「強気2段階目に変更」（心・技・体）也是切強気：原来认不出，当成不改姿态（验证局 R2 剩 11 回合：没算它的温存解除 +1）
_STANCE_CHANGE = re.compile(r"(強気|温存(?:[12]段階目)?|のんびり|全力)(?:[12]段階目)?に変更|指針を解除")
_DELAYED = re.compile(r"次のターン|ターン開始|以降")


def destination(card):
    immediate = _DELAYED.split(norm(card.text), maxsplit=1)[0]
    match = _STANCE_CHANGE.search(immediate)
    return (match[1] or "指針解除") if match else None


def stance_change_level(card):
    immediate = _DELAYED.split(norm(card.text), maxsplit=1)[0]
    match = _STANCE_CHANGE.search(immediate)
    if match:
        for level in (1, 2):
            if f"{level}段階目" in match[0]:
                return level
    return None


def level_after(before, before_level, dest, explicit=None):
    """切到 dest 之后的段階（強気 / 温存 才有）：牌面写明「N段階目」用 N；已经在同一姿态再切一次 → 2 段階（封顶）；
    从别的姿态切进来 → 1 段階。のんびり / 全力 / 指針解除 没有段階（None）。
    使用者 2026-11-08：「把强气阶段几，温存阶段几也都计入」——原来普通的「強気に変更」记成段階不明，体力闸按 2 段階多估 1 点。"""
    base = lambda s: "温存" if s and s.startswith("温存") else s  # noqa: E731
    if base(dest) not in ("強気", "温存"):
        return None
    if explicit in (1, 2):
        return explicit
    if m := re.search(r"([12])段階目", dest):
        return int(m.group(1))
    return 2 if base(before) == base(dest) else 1


def next_stance_change(card):
    match = re.search(r"(?:次のターン(?:開始時)?|ターン開始時)[、,に：:]*(強気|温存|のんびり|全力)(?:([12])段階目)?に変更",
                      norm(card.text))
    if match is None:
        return None, None
    name, stage = match.groups()
    if name == "温存" and stage:
        name = f"温存{stage}段階目"
    return name, int(stage) if stage else None


def next_stance(card):
    return next_stance_change(card)[0]


NEUTRAL = "指針なし"  # 考试第 1 回合还没切过姿态（画面没有姿态图标）：切強気照样算夜空，但没有温存解除
STAR = _CARDS["star"]  # 以降3回まで、ターン開始時、非温存状態の場合、のんびりに変更


def start_turn(current, star):
    """回合开始：一番星还有次数且不在温存 / のんびり 时切のんびり，返回（姿态，剩余次数）。姿态不明时不动。"""
    if current is not None and current not in WARM and (star or 0) > 0:
        return "のんびり", star - 1
    return current, star


def warm_next_turn(state, memory=None) -> bool:
    """下回合开局会不会自己进温存 / のんびり：预约「次のターン…温存 / のんびりに変更」，或一番星还有次数。
    几种来源同时存在也只进一次温存（使用者 2026-11-07），所以只回答有没有。"""
    for s in state.statuses or []:
        label = norm(s.get("name") or "")
        if ("次のターン" in label or "ターン開始" in label) and ("温存" in label or "のんびり" in label):
            return True
        if label.startswith(STAR) and (s.get("value") or 0) > 0:
            return True
    return bool(memory is not None and (memory.strategy_data.get("veil_star") or 0) > 0)


def keeps_nonbiri(current, dest):
    """のんびり 覆盖温存：温存 → のんびり 可以，のんびり 收到「温存に変更」（任一段階）仍是のんびり（使用者 2026-11-07，
    1-g R2 再挑戦3 剩 9：のんびり 下打 アイドルになります+，画面仍のんびり、账本记成温存）。"""
    return current == "のんびり" and dest is not None and dest.startswith("温存")


def after_play(current, flower, card):
    """Resolve the card first, then the flower's overriding stance effect."""
    dest = destination(card)
    if keeps_nonbiri(current, dest):
        return current, flower
    if current == "全力":
        # 全力 下切不了姿态，但「強気」效果照样推夜空计数，到 1 时切不了のんびり、计数回到 3（使用者 2026-11-07）
        if dest == "強気" and flower in (1, 2, 3):
            return current, 3 if flower == 1 else flower - 1
        return current, flower
    if dest != "強気" or current == "指針解除":
        return dest or current, flower
    if current is None or flower not in (1, 2, 3):
        return None, None
    return ("のんびり", 3) if flower == 1 else (dest, flower - 1)


def flower_count(img):
    """Read the digit beside VEIL's flower ring, not the generic P-item badge."""
    from hiro.vision.digits import read_number

    if img is None or img.shape[:2] != (1280, 720):
        return None
    a = read_number(img, (596, 263, 617, 291))
    b = read_number(img, (599, 266, 615, 289))
    return a if a in (1, 2, 3) and a == b else None


FP_BOXES = ((126, 248, 162, 278), (130, 251, 158, 276))  # 左上「★ N」药丸（右边粉色圈是到 10 的进度）


def full_power_count(img):
    """画面左上姿态图标右边「★ N」里的全力値（0–10，满 10 进全力、下回合开始归零）。两个框读数一致才算，读不清 None。
    验证局 codex-20261107-1-c：账本没算「满 10 进全力归零」和条件获得（涙の思い出+「強気の場合」），记成 9、画面 0，
    按账本解灰去打 アイドルになります+ 连续没打出去停机 4 次。"""
    from hiro.vision.digits import read_number

    if img is None or img.shape[:2] != (1280, 720):
        return None
    a, b = (read_number(img, box) for box in FP_BOXES)
    return a if a is not None and a == b and 0 <= a <= 20 else None


def small_deck(state):
    if state.piles is None or not all(isinstance(state.piles.get(k), list) for k in ("deck", "grave")):
        return False
    hand = [norm(c.name) for c in state.hand if c.card_id]
    if len(hand) != len(state.hand):
        return False
    names = Counter(hand + [norm(c) for k in ("deck", "grave", "unlocated") for c in state.piles.get(k, [])])
    if REBOUND in state.piles.get("once", []):
        return False
    if any(names[c] != 1 for c in CORE) or names.total() not in (4, 5):
        return False
    extra = names - Counter(CORE)
    once = {norm(c) for c in state.piles.get("once", [])} | {norm(c.name) for c in state.hand if c.tags.once}
    return not extra or next(iter(extra)) in once | {"眠気"}


PLAN_MAX = PROFILE["plan_max"]  # 进循环规划最多推演的出牌数（手牌 5 + ホエイ 1）


def plan_cycle(state, memory, hand_cards, actions, drinks, current, flower, count, played):
    """VEIL_CYCLE_PLAN：枚举本回合出牌顺序（手牌 + 最多一瓶ホエイ），找「打完后下回合开局就能进五卡循环」的顺序。
    返回 (第一步, 整个顺序的名字)，第一步是 CardView / WHEY / None（什么都不用打）；找不到返回 None（交给原来的规则）。

    推演：出牌次数（再动、温存→強気 的解除）、姿态 / 夜空（after_play）、全力値（到 10 进全力 → 不要）、体力（体力闸）。
    条件：剩下的牌库是核心 4 张（+ 至多 1 张一次性牌 / 眠気，OD 生成的眠気算进去）；回合末在温存 / のんびり，或下回合开局会自动进温存；
    夜空 1 / 2；下回合抽的张数不少于牌库。打法依据：prompts/游戏接手提示词.md「过渡」「循环」。
    只在牌库快压缩完（手牌外最多 1 张非核心牌）时推演；抽到的新牌不算（拿不准是什么）。"""
    from itertools import permutations

    rem = state.remaining_turns
    if rem is None or rem < 2 or current in (None, "全力") or flower not in (1, 2, 3):
        return None
    piles = state.piles
    if piles is None or not all(isinstance(piles.get(k), list) for k in ("deck", "grave")):
        return None
    if len(hand_cards) != len(state.hand):
        return None
    once_pile = {norm(c) for c in piles.get("once") or []}
    if REBOUND in once_pile:
        return None
    hand_names = Counter(norm(c.name) for c in hand_cards)
    allc = hand_names + Counter(norm(c) for k in ("deck", "grave", "unlocated") for c in piles.get(k) or [])
    if any(allc[c] != 1 for c in CORE):
        return None
    once_known = once_pile | {norm(c.name) for c in hand_cards if c.tags.once} | set(memory.strategy_data.get("veil_known_once") or ())
    outside = (allc - Counter(CORE)) - hand_names
    if outside.total() > 1 or any(n not in once_known | {"眠気"} for n in outside):
        return None
    fp0 = memory.strategy_data.get("veil_fp")
    mods = cost_mods(memory.strategy_data.get("veil_log"), _master_text)
    warm0 = warm_next_turn(state, memory)
    draw_extra = (piles.get("next_draw") or 3) - 3 - 2 * (TEACH in played)
    floor = hp_floor()

    def usable(c, cur, fpv):
        t = norm(c.text)
        if "温存の場合、使用可" in t and cur not in WARM:
            return False
        if "強気の場合、使用可" in t and cur != "強気":
            return False
        need = fp_delta(c)[1]
        if need and (fpv is None or fpv < need):
            return False
        if f"play:{c.slot}" in actions:
            return True
        # 现在打不了：只认「现在的姿态 / 全力値不满足牌面条件」这种本回合会变的原因；条件现在已满足还打不了的（体力、读错…）不算
        if c.tags.genki_min is not None:
            return False
        return bool(("温存の場合、使用可" in t and current not in WARM) or ("強気の場合、使用可" in t and current != "強気")
                    or (need and (fp0 is None or fp0 < need)))

    def run(seq):
        acts, cur, fl, fpv = count, current, flower, fp0
        spent = gain = 0
        cleared, sleeps, teach, warm, bad_next, swapped = Counter(), 0, TEACH in played, warm0, False, False
        for item in seq:
            if acts < 1 or swapped:
                return None
            if item == WHEY:
                acts += 1
                continue
            c = item
            if not usable(c, cur, fpv):
                return None
            spent += hp_loss(state, c, cur, mods, spent=True)
            gain += genki_gain(c)
            to_strong = destination(c) == "強気" if release_gain() else after_play(cur, fl, c)[0] == "強気"
            acts += c.tags.extra_actions + int(to_strong and cur in WARM) - 1
            if fpv is not None:
                g, cost = fp_delta(c)
                fpv = fpv - cost + g
                if fpv >= FULL_POWER_AT:
                    return None
            cur, fl = after_play(cur, fl, c)
            if cur is None:
                return None
            name = norm(c.name)
            nxt = next_stance(c)
            if nxt in WARM or name.startswith(STAR):
                warm = True
            elif nxt is not None:
                bad_next = True
            if c.tags.once or c.tags.is_sleep or name in once_known:
                cleared[name] += 1
            sleeps += int(c.tags.makes_sleep)
            teach = teach or name == TEACH
            swapped = "手札をすべて入れ替える" in norm(c.text)
        if fl not in ORDERS or bad_next or not (cur in WARM or warm):
            return None
        left = allc - cleared + Counter({"眠気": sleeps})
        total = left.total()
        extra = left - Counter(CORE)
        if any(left[c] != 1 for c in CORE) or total not in (4, 5) or any(n not in once_known | {"眠気"} for n in extra):
            return None
        if 3 + draw_extra + 2 * teach < total:
            return None
        hp_after = None
        if state.hp is not None:
            hp_after = state.hp - max(0, spent - (state.genki or 0) - gain)
            if hp_guard() and hp_after < floor and not (hp_net() and hp_after >= 1):
                return None
        idol_left = any(n.rstrip("+") == IDOL_CARD for n in extra)
        return (total == 4, not idol_left or (fpv or 0) >= FP_RESERVE_AT, -len(seq), hp_after or 0)

    items = list(hand_cards) + ([WHEY] if WHEY in drinks else [])
    best = None
    for k in range(min(len(items), PLAN_MAX) + 1):
        for seq in permutations(items, k):
            score = run(seq)
            if score is not None and (best is None or score > best[0]):
                best = (score, seq)
    if best is None:
        return None
    seq = best[1]
    names = [x if x == WHEY else norm(x.name) for x in seq]
    return (seq[0] if seq else None), names


def drive_useful(drive, starved, fp, current, hand) -> bool:
    """VEIL_DRIVE_CHECK：打 オーバードライブ 攒全力値之后，发灰的牌里至少有一张真能打出：全力値够；
    巻き返し+ 不在全力下（全力切不了姿态，攒了也没用）；アイドルになります+ 在 OD 离手后仍满足三选二（或开了 IDOL_CLEAR）。"""
    if not drive_check():
        return True
    if fp is None:
        return False
    after = fp + fp_delta(drive)[0]
    for c in starved:
        name = norm(c.name).rstrip("+")
        need = fp_delta(c)[1] or (FP_RESERVE_AT if name == IDOL_CARD else 1)
        if after < need:
            continue
        if name == REBOUND.rstrip("+") and current == "全力":
            continue
        if name == IDOL_CARD and not idol_clear() and len({ENTERTAINER, TEACH} & set(hand)) < 2:
            continue
        return True
    return False


class VeilAdvisor:
    def on_round_start(self, round_no, context):
        pass

    def on_round_end(self, result):
        pass

    def decide(self, state, memory, previews, legal):
        if state.exam not in LIMITS:
            raise ResumeBlocked("VEIL 策略仅用于 H.I.F 本战 R1/R2")
        actions = {a.legal_key(): a for a in legal}

        def act(key, reason):
            if key not in actions:
                raise ResumeBlocked(f"VEIL 所需动作不可用：{reason}")
            return Decision(actions[key], reason, "rule", decider="veil")

        def play(card, reason):
            return act(f"play:{card.slot}", f"{reason}：{card.name}")

        def skip(reason):
            return act("skip", reason)

        score = state.score if state.score is not None else memory.last_score
        if score is not None and score > LIMITS[state.exam]:
            return skip("超过本轮停手阈值")

        hand_cards = [c for c in state.hand if c.card_id]
        slot_keys = memory.strategy_data.get("veil_slot_keys") or {}
        if any(slot_keys.get(str(c.slot), norm(c.name)) != norm(c.name) for c in hand_cards):
            # 分版本记账（HIRO_VEIL_SIGNATURE=1）：同名两张（一次性的巻き返し+ 和常驻的核心）都在手里时，
            # 按名字取常驻的那张（循环要打的是核心）。只在手里有分了版本的牌时重排，其余情况顺序不变
            hand_cards.sort(key=lambda c: not c.tags.once)
        observed = {norm(c.name): c for c in hand_cards}
        by_name = {name: c for name, c in observed.items() if f"play:{c.slot}" in actions}
        hand = observed.keys()
        drinks = {d.name: f"drink:{d.slot}" for d in state.drinks if f"drink:{d.slot}" in actions}
        current = stance(state)
        flower = state.pitem_charges
        count = max(1, state.actions_left or 1)
        played = [norm(a[2:]) for a in memory.this_turn if a.startswith("打 ")]
        compact = small_deck(state)
        cycle = memory.strategy_data.get("veil_cycle")
        if (not cycle or cycle["turn"] != state.turn) and not played and compact and current in WARM and CORE <= hand:
            if flower in ORDERS and len(state.hand) in (4, 5):
                cycle = {"turn": state.turn, "flower": flower, "fifth": next(iter(hand - CORE), None)}
                memory.strategy_data["veil_cycle"] = cycle
        if (clear_first() and not played and not (cycle and cycle["turn"] == state.turn) and compact and current in WARM
                and flower in ORDERS and SODA in drinks and 1 <= len(CORE - hand) <= 2 and len(state.hand) + 2 <= HAND_MAX):
            # 开局只差 1–2 张核心：先喝ソーダ 抽 2 补齐再进循环（过渡里留着没喝的那瓶）
            return act(drinks[SODA], f"循环前：喝 {SODA} 抽 2 补齐核心（缺 {'、'.join(sorted(CORE - hand))}）")
        # 初星湯（ターン追加+1）：原来不在候选里，1-g R2 三次再挑戦都没喝（使用者 2026-11-07）。进循环时和别的饮料一起喝；
        # 到最后一回合还在就照喝。体力消費2：开体力闸时喝完不跌破保底线
        extra_turn_ok = clear_first() and EXTRA_TURN in drinks and (
            not hp_guard() or state.hp is None or state.hp - 2 >= hp_floor())
        if extra_turn_ok and state.remaining_turns == 1:
            return act(drinks[EXTRA_TURN], f"最后一回合：使用 {EXTRA_TURN}（回合+1）")
        if clear_first() and current == "全力" and compact and state.remaining_turns not in (None, 1):
            # 使用者 2026-11-07（1-h R2 剩 2）：循环里全力値满 10 进全力，切不了姿态 → 多过渡一回合再回循环。
            # 夜空 2：教えてあげる+ → 随便一张 OD 以外的牌（推荐眠気）→ 结束；夜空 1：OD → 教えてあげる+ → 眠気 → 结束。
            # 打完夜空 2→1、1→2，下回合按新的计数回到循环
            full = memory.strategy_data.get("veil_full")
            if not full or full.get("turn") != state.turn:
                full = memory.strategy_data["veil_full"] = {"turn": state.turn, "flower": flower}
            if full["flower"] in (1, 2):
                plan = ([DRIVE] if full["flower"] == 1 else []) + [TEACH, None]
                if len(played) < len(plan):
                    want = plan[len(played)]
                    if want is None:
                        filler = by_name.get("眠気") or next(
                            (c for n, c in by_name.items() if n != DRIVE and n not in CORE), None) or next(
                            (c for n, c in by_name.items() if n != DRIVE), None)
                        if filler is not None:
                            return play(filler, f"全力过渡（夜空 {full['flower']}）：打一张 OD 以外的牌收尾")
                    elif want in by_name:
                        return play(by_name[want], f"全力过渡（夜空 {full['flower']}）")
                    else:
                        return skip(f"全力过渡（夜空 {full['flower']}）：{want} 不在手里或打不了，结束回合")
                return skip(f"全力过渡（夜空 {full['flower']}）：打完，结束回合，下回合回到循环")
        if cycle and cycle["turn"] == state.turn:
            order = ORDERS[cycle["flower"]]
            for drink in (WHEY, BOOST, SODA) + ((EXTRA_TURN,) if extra_turn_ok else ()):
                if drink == SODA and cycle_soda() and len(state.hand) + 2 > HAND_MAX \
                        and (state.remaining_turns is None or state.remaining_turns > 1):
                    continue  # 满手喝ソーダ 抽的 2 张作废（VEIL_CYCLE_SODA）
                if drink == WHEY and cycle_whey() and len(state.hand) < HAND_MAX \
                        and (state.remaining_turns is None or state.remaining_turns > 1):
                    continue  # 第五张不在手：多的次数没牌打，留给下回合过渡（VEIL_CYCLE_WHEY）
                if drink in drinks:
                    return act(drinks[drink], f"循环：使用 {drink}")
            # 第五张是アイドルになります+：它要求手里至少两张 エンターテイナー+／オーバードライブ+／教えてあげる+，
            # 放到最后时三张都已打出、永远打不了。改为循环第一张（温存に変更、+1 出牌次数，不动夜空计数，循环条件不变）。
            idol = next((n for n in (cycle.get("fifth"),) if n and n.rstrip("+") == IDOL_CARD), None)
            if idol and not played and idol in by_name and len({ENTERTAINER, DRIVE, TEACH} & hand) >= 2:
                cycle["idol_first"] = True
                return play(by_name[idol], "五卡循环：アイドルになります+ 先打（打出条件要核心牌在手）")
            if cycle.get("idol_first") and played and played[0] == idol:
                played = played[1:]
            if played[:len(order)] != list(order[:len(played)]):
                raise ResumeBlocked("VEIL 循环出牌记录和预定顺序不一致")
            if played and played[-1] in hand:
                raise ResumeBlocked(f"VEIL 循环上一张牌仍在手中，无法确认已打出：{played[-1]}")
            if len(played) < len(order):
                name = order[len(played)]
                if name not in by_name and name in observed:
                    # 在手里但发灰（多半是全力値不够：巻き返し+ 消費1）：跳过本回合，不停机（验证局 1-c R2 剩 3 回合停在这里）
                    return skip(f"循环：下一张 {name} 在手里但打不出，本回合跳过")
                if name not in by_name:
                    raise ResumeBlocked(f"VEIL 循环下一张牌不可用：{name}")
                # 1-g R2 再挑戦 剩 7：本回合已先打 アイドルになります+，巻き返し+ 进温存后又打最后一张复用牌 エンターテイナー+
                # （补的次数没牌接、没有牌被消耗），白丢温存存下的次数 → Skip，温存留到下回合。只限这种回合、夜空在 1 / 2：
                # 普通循环回合按顺序打完（使用者 2026-11-07：进了循环就只按循环打；再挑戦2 剩 6 / 4 のんびり+夜空 3 跳过后下回合进不了循环）
                if clear_first() and cycle.get("idol_first") and len(played) == len(order) - 1 and current in WARM \
                        and flower in ORDERS and name not in CORE - {ENTERTAINER, TEACH} \
                        and (state.remaining_turns is None or state.remaining_turns > 1):
                    return skip(f"循环：アイドルになります+ 已先打，温存下最后一张 {name} 不打，温存留到下回合")
                return play(by_name[name], "五卡循环")
            if len(played) == len(order) and cycle.get("idol_first"):
                return skip("本回合循环完成（アイドルになります+ 已先打）")
            if len(played) == len(order):
                once = set((state.piles or {}).get("once", []))
                extra = next((name for name, c in observed.items() if name not in CORE
                              and (c.tags.once or c.tags.is_sleep or name in once)), None)
                fifth = extra or cycle.get("fifth")
                if fifth and fifth.rstrip("+") == IDOL_CARD and len({ENTERTAINER, DRIVE, TEACH} & hand) < 2:
                    return skip("循环：第五张受アイドルになります+禁打条件限制")
                if fifth:
                    fp_now = memory.strategy_data.get("veil_fp")
                    if (fifth not in by_name and fifth in observed and fp_now is not None
                            and fp_now < (fp_delta(observed[fifth])[1] or 0)):
                        # 验证局 1-d R2 再挑戦 剩 4：第五张 アンダンテ+（全力値消費2），OD +2 后 巻き返し+ 花 1，剩 1 打不出。
                        # 发灰能用账本解释（全力値不够）才结束回合；别的原因发灰（读错、条件牌）照旧停机
                        return skip(f"循环：第五张 {fifth} 全力値不够（账本 {fp_now}），本回合结束")
                    if fifth not in by_name:
                        raise ResumeBlocked(f"VEIL 循环第五张尚未打出，当前不可用：{fifth}")
                    return play(by_name[fifth], "五卡循环收尾")
            return skip("本回合循环完成")

        if cycle_plan() and not (cycle and cycle["turn"] == state.turn):
            planned = plan_cycle(state, memory, hand_cards, actions, drinks, current, flower, count, played)
            if planned is not None:
                first, names = planned
                why = "→".join(names) if names else "不用再打"
                if first is None:
                    return skip(f"进循环规划：下回合开局就能进循环（{why}）")
                if first == WHEY:
                    return act(drinks[WHEY], f"进循环规划：{why}")
                return play(first, f"进循环规划：{why}")

        final_turn = last_turn_play() and state.remaining_turns == 1
        eligible = [c for name, c in by_name.items() if (final_turn or name.rstrip("+") != DRIVE.rstrip("+")) and (
            name.rstrip("+") != IDOL_CARD or len({ENTERTAINER, DRIVE, TEACH} & hand) >= 2
        )]
        if idol_clear() and not any(c.tags.once or c.tags.is_sleep for c in eligible):
            # 三选二挡住的 アイドルになります+ 是手里唯一能清的一次性牌：照打（画面不灰就是游戏允许；VEIL_IDOL_CLEAR）
            idol_card = next((c for n, c in by_name.items() if n.rstrip("+") == IDOL_CARD and c not in eligible), None)
            if idol_card is not None:
                eligible.append(idol_card)
        if compact and not final_turn:
            if flower is None and not played:
                raise ResumeBlocked("VEIL 已进入过渡，但夜空を従える花+剩余次数读不出")
            warm_next = warm_next_turn(state, memory)
            # Recompute all three needs on each frame. Teaching can cover both
            # the flower and draw, and can change the stance we need to restore.
            need_flower = flower == 3
            need_draw = state.piles.get("next_draw") not in (4, 5) and TEACH not in played
            if draw_all() and TEACH not in played:
                # 牌库张数（核心 4 + 第五张）：下回合抽不满就预约（VEIL_DRAW_ALL）
                total = len(state.hand) + sum(len(state.piles.get(k) or []) for k in ("deck", "grave", "unlocated"))
                need_draw = (state.piles.get("next_draw") or 3) < total
            if clear_first() and SODA in drinks:
                # 使用者 2026-11-07：过渡不喝ソーダ（当场抽的 2 张用不上、下回合只多抽 1）；留到下回合开局喝，抽 2 补齐核心
                need_draw = False
            need_warm = not warm_next and current not in WARM
            teach = by_name.get(TEACH) if TEACH not in played else None
            fp = memory.strategy_data.get("veil_fp")
            drive = by_name.get(DRIVE) if DRIVE not in played else None
            rebound = observed.get(REBOUND)
            idol = next((c for n, c in observed.items() if n.rstrip("+") == IDOL_CARD), None)
            # 过渡里 オーバードライブ 优先级很低（生成眠気，使用者 2026-11-07）：只在 アイドルになります+ 因全力値不够打不出，
            # 或 巻き返し+ 因全力値发灰、下回合又不会自动温存（一番星用完）时打（1-g R1 剩 5→1：切不回温存，五卡循环一直进不去）
            starved = [c for c in (idol,) if c is not None and not c.available and fp is not None
                       and fp < (fp_delta(c)[1] or FP_RESERVE_AT)]
            if need_warm and rebound is not None and REBOUND not in by_name and fp is not None \
                    and fp < (fp_delta(rebound)[1] or 1):
                starved.append(rebound)
            if drive_fp() and drive and starved and drive_useful(drive, starved, fp, current, hand):
                names = "、".join(norm(c.name) for c in starved)
                return play(drive, f"过渡：全力値 {fp} 打不了 {names}，先打 オーバードライブ 攒全力値")
            mods = cost_mods(memory.strategy_data.get("veil_log"), _master_text) if hp_guard() else None
            last_turn = state.remaining_turns is not None and state.remaining_turns <= 1

            def harmless(c):
                """过渡里打掉不碍事的一次性牌（使用者 2026-11-07，1-g）：不切強気（夜空计数不动，切温存可以）、下回合不切到非温存、
                不是核心牌；开体力闸时打完不跌破保底线。抽牌可以（牌组只剩核心牌，下回合照样抽得到）。"""
                if not (c.tags.once or c.tags.is_sleep) or norm(c.name) in CORE:
                    return False
                if destination(c) not in (None, *WARM) or next_stance(c) not in (None, *WARM):
                    return False
                if mods is not None and not last_turn and state.hp is not None:
                    loss = hp_loss(state, c, current, mods)
                    if loss and state.hp - loss < hp_floor():
                        return False
                return True

            if clear_first():
                # 1-g R2 剩 6：フルスロットル+（温存に変更、再动、抽 3、生成眠気）不占次数，先打，再把眠気清掉就是四卡；
                # 原来先打 教えてあげる+ 切強気、带着它 Skip，剩 3 才进循环
                free = next((c for c in eligible if c.tags.extra_actions and harmless(c)), None)
                if free:
                    return play(free, "过渡：先打不占次数的一次性牌")
            def shed_card():
                # 只生成眠気、不补次数的不打（清掉一张又塞回一张）
                return next((c for c in eligible if harmless(c) and not (c.tags.makes_sleep and not c.tags.extra_actions)), None)

            def cycle_reachable():
                """按过渡顺序（教えて / 推夜空 → 切回温存）模拟本回合：下回合开局能否温存、夜空 1/2、出牌次数够不够。"""
                acts, cur, fl = count, current, flower
                if need_flower or need_draw:
                    c = teach if teach else (next((c for c in eligible if destination(c) == "強気"), None)
                                             if not need_draw else None)
                    if c is None or cur is None:
                        return False
                    acts -= 1
                    cur, fl = after_play(cur, fl, c)
                if fl not in ORDERS:
                    return False
                if cur not in WARM and not warm_next:
                    if REBOUND not in by_name or REBOUND in played:
                        return False
                    acts -= 1
                return acts >= 0

            if clear_first() and not cycle_reachable() and (shed := shed_card()):
                # 使用者 2026-11-07：下回合能进循环是第一优先；预判下回合仍是过渡（次数不够 / 夜空回到 3 / 切不回温存）时，
                # 过渡牌白打，先清一次性牌
                return play(shed, "过渡：下回合仍进不了循环，先清一次性牌")
            def warm_back(c):
                """VEIL_CYCLE_PLAN：打完 c 后本回合还能回到温存（或下回合开局自动进温存）。推了夜空切不回温存，
                下回合开局在強気，这回合的过渡全白做（3-3 第 3 局 R2 剩 5：教えて 后又打 エンタ，T9–T11 一直过渡）。"""
                cur, _ = after_play(current, flower, c)
                if cur in WARM or warm_next or next_stance(c) in WARM:
                    return True
                if memory.strategy_data.get("veil_star") != 0:
                    return True  # 一番星次数不明（状态栏常读不出名字）：可能下回合自动のんびり，不拦（3-b 第 1 局 R1 attempt1 剩 4）
                left = count - 1 + c.tags.extra_actions + int(destination(c) == "強気" and current in WARM)
                return REBOUND in by_name and REBOUND not in played and c is not by_name[REBOUND] and left >= 1
            if teach and (need_flower or need_draw):
                if cycle_plan() and need_flower and not warm_back(teach):
                    return skip("过渡：推夜空后切不回温存，留到下回合")
                return play(teach, "过渡：推进夜空计数／预约抽牌" if need_flower else "过渡：预约下回合抽两张")
            if need_flower:
                strong = next((c for c in eligible if destination(c) == "強気"), None)
                if strong and cycle_plan() and not warm_back(strong):
                    return skip("过渡：推夜空后切不回温存，留到下回合")
                if strong:
                    return play(strong, "过渡：推进夜空计数")
            if need_warm and current is None and not played:
                raise ResumeBlocked("VEIL 过渡无法确认当前姿态或下回合切换")
            if need_warm and REBOUND in by_name and REBOUND not in played:
                return play(by_name[REBOUND], "过渡：切回温存")
            if clear_first():
                # 使用者 2026-11-07（1-g R1）：过渡尽量从五卡缩到四卡。过渡要打的（教えて / 推花 / 切温存）都打完、本来要等下回合时，
                # 用剩下的出牌次数清掉不碍事的一次性牌（ひと呼吸+、アイドルになります+、眠気 等）。花全力値可以：循环第一张
                # オーバードライブ 先加 2。只生成眠気、不补次数的不打（清掉一张又塞回一张）。
                shed = shed_card()
                if shed:
                    return play(shed, "过渡：清掉不碍事的一次性牌，五卡缩成四卡")
            return skip("过渡：等待下回合")

        if state.piles is None and CORE <= hand:
            raise ResumeBlocked("VEIL 核心同手，尚未核实完整牌库，暂停以确认剩余牌")
        stance_cards = {
            name.rstrip("+"): c for name, c in observed.items()
            if name.rstrip("+") in STANCE_LIMITED
        }
        guard = hp_guard()
        mods = cost_mods(memory.strategy_data.get("veil_log"), _master_text) if guard else None
        last_turn = state.remaining_turns is not None and state.remaining_turns <= 1

        def affordable(card):
            """体力闸（VEIL_HP_GUARD）：打完还剩不到保底线的牌不打；最后一回合不限。"""
            if not guard or last_turn or state.hp is None:
                return True
            loss = hp_loss(state, card, current, mods)
            if loss == 0 or state.hp - loss >= hp_floor():  # 不掉血的牌永远能打（体力已在线下时也不能全拦）
                return True
            # VEIL_HP_NET：可用体力（体力 + 元気）不减的、核心打分牌不拦，打完体力至少剩 1
            return hp_net() and state.hp - loss >= 1 and (
                norm(card.name) in CORE or usable_net(state, card, current, mods) >= 0)

        for name, spec in STANCE_LIMITED.items():
            allowed = in_stance(current, spec["stance"])
            card = stance_cards.get(name)
            if allowed and card and f"play:{card.slot}" in actions and affordable(card):
                if stance_later() and count == 1 and not card.tags.extra_actions:
                    # 它用掉最后一次出牌就结束回合：先打一张不改掉这个姿态的一次性再动牌，或先喝ホエイ（VEIL_STANCE_LATER）
                    keeps = (lambda c, want=spec["stance"]: in_stance(after_play(current, flower, c)[0], want))
                    first = next((c for c in eligible if c is not card and c.tags.extra_actions and c.tags.once
                                  and not c.tags.makes_sleep and keeps(c) and affordable(c) and "手札をすべて入れ替える" not in norm(c.text)), None)
                    if first is not None:
                        return play(first, f"清牌：先打不改姿态的再动牌，{name} 留后")
                    if WHEY in drinks and any(c is not card and (c.tags.once or c.tags.is_sleep) for c in eligible):
                        return act(drinks[WHEY], f"清牌：{name} 会用掉最后一次出牌，先喝ホエイ")
                return play(card, "清牌：姿态限定牌优先")
        if drive_fp() and (fp := memory.strategy_data.get("veil_fp")) is not None:
            drive = next((c for n, c in by_name.items() if n.rstrip("+") == DRIVE.rstrip("+")), None)
            starved = [n for n, c in observed.items() if not c.available
                       and n.rstrip("+") in (IDOL_CARD, REBOUND.rstrip("+"))
                       and fp < (fp_delta(c)[1] or (FP_RESERVE_AT if n.rstrip("+") == IDOL_CARD else 1))]
            if drive and starved and not any(fp_delta(c)[0] for c in eligible) and affordable(drive) \
                    and drive_useful(drive, [observed[n] for n in starved], fp, current, hand):
                return play(drive, f"清牌：全力値 {fp} 打不了 {'、'.join(starved)}，オーバードライブ 是唯一来源，先攒全力値")
        playable = list(eligible)  # 体力闸过滤前：被闸暂时压住的一次性牌（切回温存后再打）也算后面有牌可接
        if guard:
            cheap = [c for c in eligible if affordable(c)]
            if eligible and not cheap:
                return skip(f"清牌：体力闸，HP {state.hp} 打哪张都会跌破 {hp_floor()}")
            eligible = cheap
            if current == "強気" and not last_turn:
                # 強気 下体力消耗 ×2：不改姿态的掉血牌（ひと呼吸+、モチベ+…）留到温存（×0.5）再打，也不为它们喝ホエイ。
                # 使用者 2026-11-07（1-e R2 T1）：打了 インフルエンサー+、第一印象+ 后又喝ホエイ 打 モチベ+ / ひと呼吸+，
                # 体力 38→1 错过下回合出牌；理想是这两张后跳过回合，下回合进温存
                def minor(c):
                    loss = hp_loss(state, c, current, mods)
                    return minor_hp() and loss <= MINOR_LOSS and state.hp is not None and state.hp - loss >= hp_floor()

                keep = [c for c in eligible if hp_loss(state, c, current, mods) == 0 or destination(c) is not None or minor(c)
                        or (hp_net() and (norm(c.name) in CORE or usable_net(state, c, current, mods) >= 0))]
                if eligible and not keep:
                    return skip("清牌：強気下体力 ×2，不改姿态的掉血牌留到温存再打")
                eligible = keep
        if fp_reserve() and not last_turn:
            fp = memory.strategy_data.get("veil_fp")
            deck = memory.strategy_data.get("veil_deck")
            idol_left = deck is None or any(n.rstrip("+") == IDOL_CARD and k > 0 for n, k in deck.items())

            def keeps_reserve(card):
                gain, cost = fp_delta(card)
                return not cost or norm(card.name).rstrip("+") == IDOL_CARD or fp - cost + gain >= FP_RESERVE_AT

            if fp is not None and idol_left:
                kept = [c for c in eligible if keeps_reserve(c)]
                if eligible and not kept:
                    return skip(f"清牌：预留全力値，账本 {fp}，花完不足 {FP_RESERVE_AT}（留给 アイドルになります+）")
                eligible = kept
        if fp_cap() and not last_turn and (fp := memory.strategy_data.get("veil_fp")) is not None:
            def under_cap(card):
                gain, cost = fp_delta(card)
                return not gain or fp - cost + gain < FULL_POWER_AT

            capped = [c for c in eligible if under_cap(c)]
            if capped and len(capped) < len(eligible):
                # 只在还有别的牌可打时避开；全是加满的牌就照打，不空转。记一笔供审计（验证局 1-d 看不出它有没有起作用）
                log = memory.strategy_data.setdefault("veil_fp_cap_log", [])
                entry = {"turn": memory.turn, "fp": fp, "avoided": sorted(norm(c.name) for c in eligible if c not in capped)}
                if not log or log[-1] != entry:
                    log.append(entry)
                eligible = capped
        if not eligible:
            if stuck_soda() and not playable and SODA in drinks and len(state.hand) + 2 <= HAND_MAX:
                # VEIL_STUCK_SODA：还有出牌次数、手里没有能打的牌（不是被体力闸压住）→ 喝ソーダ 抽 2 继续清
                return act(drinks[SODA], "清牌：手里没有能打的牌，喝ソーダ 抽 2 继续清")
            return skip("清牌：没有允许打出的已识别牌")

        from hiro.brain.memory_ability import pending as ability_of

        abilities = memory.strategy_data.get("veil_abilities")

        def ability_kind(card):
            hit = ability_of(abilities, card.name)
            return hit["kind"] if hit else None

        def immediate_draw(card):
            return ((card.tags.draws and "次のターン" not in norm(card.text)) or "手札をすべて入れ替える" in norm(card.text)
                    or ability_kind(card) == "draw")

        def resulting_stance(card):
            return after_play(current, flower, card)[0]

        release_first = release_gain()

        def gains_now(card):
            # 回忆能力（再动 / 再动并抽牌）在打出后再给一次出牌次数
            # 温存解除：开关打开时看牌自己的切换（先结算牌，夜空 1→のんびり 在后），不看夜空覆盖后的最终姿态
            to_strong = destination(card) == "強気" if release_first else resulting_stance(card) == "強気"
            return (card.tags.extra_actions + int(to_strong and current in WARM)
                    + int(ability_kind(card) in ("draw", "play")))

        def unlocks(card):
            dest = resulting_stance(card)
            return any(in_stance(dest, spec["stance"]) and in_stance(current, spec["unlock_from"]) and name in stance_cards
                       for name, spec in STANCE_LIMITED.items())

        keep = keep_warm()
        once_known = set((state.piles or {}).get("once") or ()) | set(memory.strategy_data.get("veil_known_once") or ())
        initial_once = set(memory.strategy_data.get("veil_initial_once") or ())

        def clears(card):
            """打出后这张牌离开牌库（一次性牌 / 眠気）：清牌阶段真正推进的出牌。"""
            name = norm(card.name)
            if name == REBOUND and slot_keys.get(str(card.slot), name) == name:
                return name in initial_once  # 分了版本的巻き返し+ 按这一张的一次性（apply_variants 已按账本改好）
            return bool(card.tags.once or card.tags.is_sleep or name in once_known)

        extra = memory.strategy_data.get("veil_extra") or {}
        this_turn = extra.get("turn") == memory.turn
        stacks = count - 1  # 当前追加层数（基础 1 次之外）
        carry_now = not (this_turn and extra.get("start")) or bool(this_turn and extra.get("zeroed"))

        def reusable_ok(card):
            """不清牌的复用牌（エンターテイナー、教えてあげる…）：只在不减少「温存 + 能留到下回合的再动」时打。
            代价：温存下切強気用掉温存存着的一次再动；有可保留的追加层时，打牌消耗一层。
            收益：打出后得到的追加次数（温存→強気、牌面、回忆能力），只有能留到下回合（本回合开始时没有，或这一张把层数用到 0）才算。"""
            warm_spent = current in WARM and resulting_stance(card) == "強気"
            cost = int(warm_spent) + int(stacks > 0 and carry_now)
            gain = gains_now(card) if carry_now or stacks <= 1 else 0
            return gain >= cost

        def priority(card):
            dest = resulting_stance(card)
            delays_warm = current == "強気" and dest in WARM
            opens_followup = unlocks(card) and (count > 1 or gains_now(card) > 0 or WHEY in drinks)
            # 体力闸开着时：強気下先打切回温存的牌（「強気に変更」的牌留到温存 / のんびり 下打，消耗减半或为 0），再比掉血少
            # 切回温存只在本回合还有下一张要打时才有用（后面的牌消耗减半）；只剩 1 次出牌时不靠它排前（1-f R2 剩 8：
            # 教えてあげる+ 因夜空最后 1 次会变のんびり 被排在 ひと呼吸+ 前，一次性牌又被压后）
            hp_first = (dest in WARM and count > 1, -hp_loss(state, card, current, mods)) if guard and current == "強気" else ()
            return hp_first + ((clears(card),) if keep or clear_first() else ()) + (
                gains_now(card), opens_followup, not delays_warm,
                bool(card.tags.once and norm(card.name) != REBOUND),
                # 都是再动时立即抽牌减分（使用者 2026-11-07：先清不抽牌的再动牌，避免满手抽牌作废）；开关关时照旧加分
                # 同样不抽牌时不改姿态的先（温存下先清 みずたまりスキップ，切強気会让后面的牌消耗翻倍）
                ((not immediate_draw(card), dest == current) if draw_last() and gains_now(card) > 0
                 else (bool(immediate_draw(card)), False)),
                ability_kind(card) == "sleep", dest != current, card.badge_score or 0)

        if keep:
            eligible = [c for c in eligible if clears(c) or reusable_ok(c)]
            if not eligible:
                return skip("清牌：只剩复用牌，打出会用掉温存或可留到下回合的再动")

        # Preserve a later play for ひと呼吸 whenever another legal card is available.
        preferred = [c for c in eligible if norm(c.name).rstrip("+") not in PLAY_LATER]
        if clear_first() and not any(clears(c) for c in preferred):
            preferred = []  # ひと呼吸 只让给别的一次性牌：剩下的全是复用牌时它照常参与排序（一次性优先）
        best = max(preferred or eligible, key=priority)
        good_first = None
        start = current if current is not None else (NEUTRAL if memory.turn == 1 and not played else None)
        if clear_first() and start is not None and 2 <= len(eligible) <= 5:
            # 使用者 2026-11-07（1-g R2 再挑戦 剩 10）：のんびり 开局 第一印象（強気、解除+1）→ 達成感（温存、+1）→
            # 全身全霊（温存→強気 解除+1、+2）比实际 達成感 → 第一印象 → 全身全霊 多 1 次出牌。贪心一张张比、DRAW_LAST 只比两张
            # 的先后都看不出 → 枚举可打牌的出牌顺序，按姿态 / 夜空推演，取「打出张数、剩余次数」最多的顺序；best 开头的顺序
            # 一样好时不换。抽牌带来的新牌、体力不算（体力闸已在 eligible 里筛过）
            from itertools import permutations

            def gain_at(card, cur, fl):
                to_strong = destination(card) == "強気" if release_first else after_play(cur, fl, card)[0] == "強気"
                return card.tags.extra_actions + int(to_strong and cur in WARM) + int(ability_kind(card) in ("draw", "play"))

            warm_already = warm_next_turn(state, memory)

            def run(order):
                """（清掉的一次性牌数，剩余次数，少打复用牌，抽牌晚打）：清牌阶段只算一次性牌（复用牌用掉温存、
                1-f R1 一次性牌被拖后），抽进来的牌不因手牌满作废作最后比较。
                剩余次数按回合末算：牌都打完、没抽到新牌时剩下的次数作废（不算）；回合末留在温存 / のんびり 算 1 次
                （下回合第一次切強気的温存解除；下回合开局本来就会进温存时——预约「次のターン温存に変更」、一番星还有次数——
                停在強気也算，几种来源同时有也只算 1 次）。使用者 2026-11-07（1-h R1 T1）：喝ホエイ 后手里 プライド+（温存）、
                心・技・体（強気），应先打心・技・体 再打プライド+ 留温存到下回合，而不是温存→強気 拿一次用不掉的解除。"""
                acts, cur, fl, n, cleared, reused = count, start, flower, 0, 0, 0
                warm_later = warm_already
                zeroed = carry_now
                size, waste, draws = len(state.hand), 0, []
                for c in order:
                    if acts < 1 or cur is None:
                        break
                    acts += gain_at(c, cur, fl) - 1
                    if acts <= 1:
                        zeroed = True  # 再动层数用到 0：之后再拿到的能留到下回合
                    cur, fl = after_play(cur, fl, c)
                    if norm(c.name).startswith(STAR) or next_stance(c) in WARM:
                        warm_later = True
                    n += 1
                    if clears(c):
                        cleared += 1
                    else:
                        reused += 1
                    d = max(c.tags.draws or 0, int(ability_kind(c) == "draw")) if immediate_draw(c) else 0
                    size = size - 1 + d
                    waste += max(0, size - HAND_MAX)
                    size = min(size, HAND_MAX)
                    draws.append(-d)
                    if "手札をすべて入れ替える" in norm(c.text) and n < len(order):
                        break  # 1-h R2 剩 10：仕切り直し+ 换掉整手牌，排在它后面的牌（頂点へ+）打不到了
                if n == len(order) and not any(draws):
                    # 牌打完了、也没抽到牌：基础的 1 次用不掉；再动层数按 buff 保留规则留到下回合（track_extra：回合开始
                    # 没有层数、或本回合用到过 0 → 全留；否则衰退 1）。1-h R2 剩 10 Skip 时剩 2 次，剩 9 实际多打了 1 张
                    stacks = max(0, acts - 1)
                    acts = stacks if zeroed else max(0, stacks - 1)
                # 下回合开局本来就会进温存（预约 / 一番星）时回合末停在強気也行；几种来源只算一次（使用者 2026-11-07 补充）
                acts += int((cur in WARM or warm_later) and not last_turn)
                # 抽牌：先比手牌满作废的张数，再按出牌顺序比抽牌数（抽得少的先打）
                return (cleared, acts, -reused, -waste, tuple(draws))

            scored = [(run(o), o) for o in permutations(eligible)]
            # 前四项（清掉的一次性牌、剩余次数、少打复用牌、抽牌作废）严格更好才换；打平时不换，只把后面 DRAW_LAST 的换牌限制在
            # 最优顺序的开头里。抽得少的先打只在最优开头之间挑
            top4 = max(v[:4] for v, _ in scored)
            good = [(v, o) for v, o in scored if v[:4] == top4]
            good_first = {id(o[0]) for _, o in good}
            from_best = max((v[:4] for v, o in scored if o[0] is best), default=None)
            if from_best is not None and top4 > from_best:
                pick = max(good, key=lambda vo: (vo[0], priority(vo[1][0])))[1][0]
                why = ("多清 %d 张一次性牌" % (top4[0] - from_best[0]) if top4[0] > from_best[0] else
                       "多留 %d 次出牌" % (top4[1] - from_best[1]) if top4[1] > from_best[1] else
                       "少打复用牌" if top4[2] > from_best[2] else "抽牌少作废 %d 张" % (top4[3] - from_best[3]))
                return play(pick, f"清牌：按出牌顺序推演先打它（{why}）")
        late = next((c for c in eligible if norm(c.name).rstrip("+") in PLAY_LATER), None)
        if late and count == 1 and gains_now(late) > gains_now(best) and WHEY not in drinks:
            best = late
        if clear_first() and not last_turn and eligible and not any(clears(c) for c in playable):
            # 1-g R2 再挑戦 剩 10：全力値 0、巻き返し+ 灰着，手里只剩复用牌 エンターテイナー+ 也打了（切強気用掉温存，
            # 加的次数没有一次性牌可接）。清牌阶段可打的只剩复用牌时不打，温存留到下回合
            if strong_teach() and current not in WARM and flower in (2, 3) and TEACH in by_name and TEACH not in played:
                # 不在温存：没有温存可留。教えて 推夜空、预约下回合多抽 2（VEIL_STRONG_TEACH；1-c R2 attempt1 剩 8、7 连跳）
                return play(by_name[TEACH], "清牌：只剩复用牌、不在温存，打 教えてあげる+ 推夜空并预约抽牌")
            grey = [n for n, c in observed.items() if not c.available and clears(c)]
            return skip("清牌：可打的只剩复用牌" + (f"（{'、'.join(grey)} 发灰）" if grey else "") + "，不打、温存留到下回合")
        if count > len(eligible) and not unlocks(best):
            # 换掉整手牌的（仕切り直し+）只在手里没有别的可打牌时才先打，否则别的牌被换回牌库
            draw_card = next((c for c in sorted(eligible, key=priority, reverse=True) if immediate_draw(c)
                              and (not clear_first() or "手札をすべて入れ替える" not in norm(c.text) or len(eligible) == 1)), None)
            if draw_card:
                return play(draw_card, "清牌：额外行动优先用技能卡抽牌")
            # 手牌满时喝ソーダ 抽的 2 张作废（1-g R2 再挑戦 T1：5 张满手连喝三瓶）→ DRAW_LAST 下手牌放得下才喝
            if SODA in drinks and (not draw_last() or len(state.hand) + 2 <= HAND_MAX):
                return act(drinks[SODA], "清牌：有行动但缺手牌")
        if draw_last() and immediate_draw(best) and gains_now(best) > 0:
            # 换成「不抽牌、自己也加出牌次数」的牌先打：出牌次数不减少，手牌先少一张，best 抽的牌不会因手牌满作废。
            # 两种先后的总加次数（含姿态切换带来的温存解除）要不少于原顺序；姿态读不准（None）时不换。
            # 验证局 R2 剩 11 回合：のんびり、满手，オーバードライブ（再动、切強気）应先于 フルスロットル+（再动、抽 3）
            def gains_at(card, cur, fl):
                if cur is None:
                    return None
                to_strong = destination(card) == "強気" if release_first else after_play(cur, fl, card)[0] == "強気"
                return card.tags.extra_actions + int(to_strong and cur in WARM) + int(ability_kind(card) in ("draw", "play"))

            def total(first, second):
                g1 = gains_at(first, current, flower)
                cur2, fl2 = after_play(current, flower, first)
                g2 = gains_at(second, cur2, fl2)
                return None if g1 is None or g2 is None else g1 + g2

            # 一次性优先（CLEAR_FIRST）时，复用牌只在 best 抽的牌会溢出手牌（>5）时才拿来垫（1-f R2 T1：手牌 4 张，
            # インフルエンサー 只抽 1 张不会溢出，却先打了 教えてあげる+ 用掉温存）
            overflow = len(state.hand) - 1 + max(best.tags.draws or 0, int(ability_kind(best) == "draw")) > HAND_MAX
            plain = []
            for c in eligible:
                if c is best or immediate_draw(c) or not gains_now(c):
                    continue
                if good_first is not None and id(c) not in good_first:
                    continue  # 出牌顺序推演里它开头不是最优
                if clear_first() and not clears(c) and not overflow:
                    continue
                c_first, b_first = total(c, best), total(best, c)
                if c_first is not None and b_first is not None and c_first >= b_first:
                    plain.append(c)
            if plain:
                # 优先不改姿态的（みずたまりスキップ）：切強気会让后面的牌消耗翻倍
                pick = max(plain, key=lambda c: (resulting_stance(c) == current, priority(c)))
                return play(pick, "清牌：先打不抽牌的再动牌，抽牌的留后")
            if overflow and clear_first():
                # 1-g R2 再挑戦 T1：手牌 5 张，先打了 全身全霊（再动、抽 3，溢出 2 张），インフルエンサー（再动、抽 1）不溢出却留后。
                # 抽得少、打完手牌不溢出的一次性再动牌先打（两种先后总加次数不少于原顺序）
                fit = []
                for c in eligible:
                    if c is best or not immediate_draw(c) or not gains_now(c) or not clears(c):
                        continue
                    if good_first is not None and id(c) not in good_first:
                        continue
                    if len(state.hand) - 1 + max(c.tags.draws or 0, 1) > HAND_MAX:
                        continue
                    c_first, b_first = total(c, best), total(best, c)
                    if c_first is not None and b_first is not None and c_first >= b_first:
                        fit.append(c)
                if fit:
                    return play(max(fit, key=priority), "清牌：先打抽得少、手牌不溢出的再动牌")
        if release_first and count == 1 and len(eligible) > 1 and not gains_now(best):
            gainer = max((c for c in eligible if gains_now(c)), key=priority, default=None)
            # 一次性优先（CLEAR_FIRST / KEEP_WARM）时，best 是一次性牌、又没有ホエイ 要省：不拿复用牌（切強気用掉温存）插队
            if gainer is not None and (keep or clear_first()) and clears(best) and not clears(gainer) and WHEY not in drinks:
                gainer = None
            if gainer is not None:
                return play(gainer, "清牌：先打能加出牌次数的牌，不用ホエイ")
        if count == 1 and (len(eligible) > 1 or unlocks(best)) and not gains_now(best) and WHEY in drinks:
            return act(drinks[WHEY], "清牌：有手牌但出牌次数不足")
        return play(best, "清牌")
