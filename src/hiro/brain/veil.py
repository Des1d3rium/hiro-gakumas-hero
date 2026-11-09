"""H.I.F 本战「五卡循环」引擎（engine = "cycle"）。第一份档案是 [VEIL] 秦谷美玲。

牌名、核心牌、出牌顺序、停手阈值、手牌上限、考试饮料、全力値阈值、姿态限定牌、开关默认值都来自偶像档案
（hiro/strategies/data/idols/<偶像>.json，使用者覆盖 conf/strategies/idols/<偶像>.json，见 docs/strategies.md），
由 hiro.brain.play.profile 导入时读一次。

出牌逻辑拆成 hiro.brain.play 的 9 块（docs/play-modules.md）：
profile（VEIL 配置与开关）· sim（回合推演）· deckview（牌库视图 / 局面上下文）· cycle（循环）· planner（序列搜索）·
phase（阶段调度 + 清牌）· guards（约束）· resources（饮料）· objectives（打分）。
这里保留 VEIL 专用的画面读数（夜空环旁的数字、全力値药丸）和 VeilAdvisor 入口，并把各块的名字转出来，
旧调用方（veil_exam、测试、工具）照旧 from hiro.brain.veil import …。
"""

from __future__ import annotations

from hiro.brain.play import cycle as _cycle, deckview as _deckview, guards as _guards, objectives as _objectives
from hiro.brain.play import phase as _phase, planner as _planner, profile as _profile, resources as _resources
from hiro.brain.play import sim as _sim

for _mod in (_profile, _sim, _deckview, _planner, _objectives, _guards, _resources, _cycle, _phase):
    globals().update({k: v for k, v in vars(_mod).items()
                      if not k.startswith("__") and k not in ("annotations", "cycle", "planner", "resources")})
del _mod


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


CYCLE_MARK = "五卡循环"  # 循环出牌决策理由的前缀（五卡循环 / 五卡循环收尾）；连打现在按 Decision.plan 的阶段判，不再认它
LEDGER = "veil-ledger"  # memory.strategy_data 里的 veil_* 账：姿态 / 夜空 / 全力値 / 牌组 / 牌堆 / 槽位记账键，由 VeilScreen 提供


class VeilAdvisor:
    """ExamAdvisor 插件：五卡循环出牌（只用于 H.I.F 本战 R1 / R2）。决策读 VeilScreen 记的账（requires），
    本回合的循环规划（veil_cycle）写回 memory.strategy_data。"""

    requires = frozenset({LEDGER})

    def burst_ok(self, contract) -> bool:
        """连打只接循环出牌：看决策器写进 Decision.plan 的阶段（play.cycle.PHASE），不读理由文字。"""
        from hiro.exec.cycle_tap import is_cycle_play

        return is_cycle_play(contract)

    def on_round_start(self, round_no, context):
        pass

    def on_round_end(self, result):
        pass

    def decide(self, state, memory, previews, legal):
        return _phase.decide(state, memory, previews, legal)
