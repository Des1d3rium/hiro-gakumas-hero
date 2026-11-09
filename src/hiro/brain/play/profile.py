"""偶像配置（play 第 9 块）：一个偶像的出牌方案里，所有按名字写死的东西都放这里。

数据来自偶像档案（hiro/strategies/data/idols/<偶像>.json，使用者覆盖 conf/strategies/idols/<偶像>.json，
见 docs/strategies.md）：核心牌、循环顺序、饮料、停手阈值、全力値阈值、姿态限定牌、开关默认值。导入时读一次：
批次绑定了档案快照（hiro.strategies.profiles.bind）就用快照里的偶像档案，本局不变。
VEIL 的核心牌、循环顺序是使用者给的打法（2026-11-09：「写死的地方是我建议的出牌顺序，对 VEIL 有效」）；
换偶像时另写一份档案。出牌策略开关（VEIL_<名字>）也在这里，sim / guards / cycle 等模块按名字查。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlayProfile:
    """一个偶像的出牌配置。roles 给循环里各张牌的角色名（策略代码按角色引用，不按卡名）。"""

    idol: str
    core: frozenset[str]  # 循环牌组（复用牌）
    orders: dict[int, tuple[str, ...]]  # 按 P 道具计数（夜空剩余次数）给的循环顺序
    roles: dict[str, str]  # 角色 → 卡名：scorer / drive / teach / rebound
    drinks: dict[str, str]  # 角色 → 饮料名：boost / soda / whey / extra_turn
    limits: dict[str, int]  # 停手阈值：本轮分数超过就 Skip
    counter_item: str | None = None  # 循环依赖的 P 道具（计数器）id
    start_turn_card: str | None = None  # 回合开始自动切姿态的牌（附魔）
    fifth_gated: str | None = None  # 第五张里打出条件依赖核心牌在手的牌（去「+」的基名）
    fp_reserve_at: int = 3  # 为 fifth_gated 预留的全力値（主数据 全力値消費）
    sleep_generators: frozenset[str] = field(default_factory=frozenset)
    hand_max: int = 5
    full_power_at: int = 10


IDOL_ID = "i_card-skin-hmsz-3-016"  # 这个引擎缺省用的偶像档案
# play 重构新增的开关（默认关，也写进了档案 flags.known）：批次绑定的旧档案快照里没有时补上，trace / profile show 照样列出
PLAY_FLAGS = ("SIM_UNIFIED", "FP_PLAY_STANCE", "ENCHANT_RUNTIME", "DRAW_PROB", "FULL_PLAN")


def _load_profile(idol: str) -> dict:
    from hiro.strategies import profiles, registry

    snap = profiles.bound()
    if snap and snap.get("idol") == idol and snap.get("idol_profile"):
        return snap["idol_profile"]
    profile = registry.idol_profile(idol)
    if profile is None or profile.get("engine") != "cycle":
        raise RuntimeError(f"没有偶像 {idol} 的 cycle 引擎档案（hiro/strategies/data/idols/）")
    return profile


PROFILE = _load_profile(IDOL_ID)  # 档案原文（dict）：veil_exam 写 run.json、batch 工具读 full_power 等
_CARDS = PROFILE["cards"]
ENTERTAINER, DRIVE, TEACH, REBOUND = (_CARDS[k] for k in ("entertainer", "drive", "teach", "rebound"))
_DRINKS = PROFILE["drinks"]
BOOST, SODA, WHEY = (_DRINKS[k] for k in ("boost", "soda", "whey"))
EXTRA_TURN = _DRINKS["extra_turn"]  # 初星湯：ターン追加+1体力消費2消費体力増加1ターン

VEIL = PlayProfile(
    idol=PROFILE["idol"],
    core=frozenset(_CARDS[k] for k in PROFILE["core"]),
    orders={int(k): tuple(_CARDS[r] for r in v) for k, v in PROFILE["orders"].items()},
    roles={"scorer": ENTERTAINER, "drive": DRIVE, "teach": TEACH, "rebound": REBOUND},
    drinks={"boost": BOOST, "soda": SODA, "whey": WHEY, "extra_turn": EXTRA_TURN},
    limits=dict(PROFILE["limits"]),
    counter_item="pitem_03-3-303-1",  # 夜空を従える花+
    start_turn_card=_CARDS["star"],  # 以降3回まで、ターン開始時、非温存状態の場合、のんびりに変更
    fifth_gated=_CARDS["idol_card"],
    fp_reserve_at=PROFILE["full_power"]["reserve"],  # アイドルになります+：全力値消費3（主数据）
    sleep_generators=frozenset({DRIVE, "フルスロットル", "フルスロットル+"}),
    hand_max=PROFILE["hand_max"],
    full_power_at=PROFILE["full_power"]["at"],
)

# 旧名（veil.py 及其调用方按这些名字用）
IDOL = VEIL.idol
CORE = VEIL.core
ORDERS = VEIL.orders  # 夜空剩余次数 → 出牌顺序
LIMITS = VEIL.limits  # 各轮停手阈值
HAND_MAX = VEIL.hand_max  # 手牌上限：抽牌超出的作废
FULL_POWER_AT = VEIL.full_power_at  # 全力値到 10 自动进全力（实机）
IDOL_CARD = VEIL.fifth_gated  # 去「+」的基名
FP_RESERVE_AT = VEIL.fp_reserve_at
STAR = VEIL.start_turn_card
STANCE_LIMITED = dict(PROFILE.get("stance_limited") or {})  # 姿态限定牌（基名）→ {stance, unlock_from}（写法见 sim.in_stance）
PLAY_LATER = frozenset(PROFILE.get("play_later") or ())  # 有别的牌可打时留后的牌（基名）
MINOR_LOSS = PROFILE["minor_loss"]  # 強気下实际掉血 < 8、打完仍在保底线上的牌不留到温存（使用者 2026-11-08：「当前会消耗小于8的都可以打」）
PLAN_MAX = PROFILE["plan_max"]  # 进循环规划最多推演的出牌数（手牌 5 + ホエイ 1）


def hp_floor():
    """体力闸的保底线：打完这张至少还剩这么多体力（HIRO_VEIL_HP_FLOOR，默认 10）。"""
    try:
        return int(os.environ.get("HIRO_VEIL_HP_FLOOR", "10"))
    except ValueError:
        return 10


# 默认开的策略开关（使用者 2026-11-07，22 个 trace 回放后定）：设 VEIL_<name>=0 关掉。其余默认关，=1 打开
# 2026-11-08 trusted trace 复盘后：DRAW_ALL / LAST_TURN / CYCLE_SODA / STUCK_SODA 也默认开（使用者确认）；HP_NET 默认关
# 2026-11-08 验证局 1-c R1 倒数第三回合后：FP_RESERVE / CLEAR_FIRST / MINOR_HP 默认开（使用者：「按方案改」）
# 2026-11-08 验证局 1-d R2 剩 12：HP_NET 默认开（使用者：「做方案A」）——体力 12 时 みずたまりスキップ+（元気+10、再动）被保底线拦
# 默认开的一组现在在偶像档案 flags.default_on（以上记录照旧作来历说明）。
DEFAULT_ON = frozenset(PROFILE["flags"]["default_on"])
STRATEGY_FLAGS = tuple(PROFILE["flags"]["known"]) + tuple(f for f in PLAY_FLAGS if f not in PROFILE["flags"]["known"])


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


def stance_unlock():
    """VEIL_STANCE_UNLOCK=1：清牌阶段可打的只剩复用牌、手里姿态限定的一次性牌（一心不乱+ 要強気 / ガタゴトすやすや+ 要温存）
    因姿态不对发灰时，先打一张能切到那个姿态、打完还剩出牌次数的复用牌，下一手清掉它。
    例：codex-20261109-1-i R2 attempt 1，のんびり 下 一心不乱+ 发灰，「只剩复用牌、不打」连跳 7 回合，牌堆一直 6 张进不了循环，敗退。"""
    return _flag("STANCE_UNLOCK")


def hp_zero():
    """VEIL_HP_ZERO=1：状态栏有「消費体力変化」时（全身全霊+：次に使用したスキルカードの消費体力を0にする），下一张牌不掉体力。
    原来体力闸当它照常掉血。例：3-3 第 1 局 R1 attempt2 剩 9，インフルエンサー++ 因此被留到温存。"""
    return _flag("HP_ZERO")


def cycle_tap():
    """VEIL_CYCLE_TAP=1：五卡循环回合连打（使用者 2026-11-08）。回合里第一张循环牌照常整帧读取、决策、点击；它落地后，
    剩下的循环牌不再每张重读手牌：按首读的手牌顺序删掉打出的那张，按 1–5 张的手牌布局（hiro.vision.hif.HAND_LAYOUT）
    算出下一张在第几格、点哪里，直接打。每张之前只看轻量签名（剩余回合 + 卡名首字母角标）核对张数、位置与发灰，
    对不上（多抽了牌、回合结束、发灰、弹窗、没落地）就退回整帧读取。实现见 hiro.exec.cycle_tap。默认关。"""
    return _flag("CYCLE_TAP")




def sim_unified():
    """VEIL_SIM_UNIFIED=1：所有推演都按同一套完整规则（play.sim.gain 的完整口径：温存解除按开关、回忆能力算进出牌次数、
    过渡可达性也算再动）。默认关：各调用处保持重构前各自的口径（见 sim.gain 的 release / ability 参数），决策和原来逐帧一致。"""
    return _flag("SIM_UNIFIED")


def fp_play_stance():
    """VEIL_FP_PLAY_STANCE=1：带姿态条件的全力値（涙の思い出「温存に変更 … 強気の場合、全力値+3」）按打出时的姿态判（默认关）。
    原来按「本张牌先切的姿态」判，在強気下打出一律记 0。11-08 / 11-09 trace 里 5 次強気下打出画面都 +3 / +4，
    5 次温存 / のんびり 下打出画面都不变（tools/audit/sim_replay.py；1-p 两轮账本因此一直比画面少 3）。"""
    return _flag("FP_PLAY_STANCE")


def enchant_runtime():
    """VEIL_ENCHANT_RUNTIME=1：夜空、一番星和打出的牌装上的附魔都走通用附魔引擎（hiro.brain.play.enchants + anomaly），
    不再按道具名写死的规则（默认关）。夜空 / 一番星的剩余次数仍是 flower / star 两个整数（画面读的就是它们），
    引擎判断不了（没有主数据）时退回原来的规则。"""
    return _flag("ENCHANT_RUNTIME")


def full_plan():
    """VEIL_FULL_PLAN=1：本回合进了全力过渡就按规划打完（默认关）：
    ① 小牌组条件只管「进不进」全力过渡，进了之后 OD 生成眠気（牌库多一张）也接着按规划打，不掉到清牌；
    ② 接续按规划里哪几步已经打出（按出牌记录逐步对名字），不按本回合已打张数。
    codex-20261109-1-q R2 剩 2（夜空 1，规划 OD → 教えてあげる+ → 眠気）：打 OD 生成眠気后牌库 6 张、小牌组不成立，
    下一手落到清牌打了眠気；再下一手按张数取第 3 步收尾，眠気已没了，打了巻き返し+，教えてあげる+（下回合抽 2）没打。"""
    return _flag("FULL_PLAN")


def draw_prob():
    """VEIL_DRAW_PROB=1：过渡里要不要预约下回合多抽（教えてあげる+ 抽牌 +2）按抽牌概率判（默认关）：
    预约后「下回合开局核心 4 张都在手」的概率（hiro.brain.play.draws，按牌堆账精确算，取区间下限）比不预约高 1% 以上才预约。
    原来的口径：下回合抽牌数小于牌库张数就预约（VEIL_DRAW_ALL），不管核心牌已经在山札里必到。
    校准：tools/audit/draw_calibration.py（11-09 全部 trace，2682 个预测，分档预测和实际抽到比例一致）。"""
    return _flag("DRAW_PROB")
