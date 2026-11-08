# 来源：digital-hiro src/hiro/sim/__init__.py（develop 864e037）
"""离线对局环境：用 gakumas-player 的 gakumas_sim 引擎（社区公开 master + 实机校准）驱动决策层。

引擎随仓库在 vendor/gakumas-player（主数据另行下载：python vendor/gakumas-player/scripts/fetch_master.py，setup 脚本会做）；
环境变量 GAKUMAS_PLAYER 可指向别处的引擎目录。
"""

import os
import sys
from pathlib import Path

_CANDIDATES = [
    os.environ.get("GAKUMAS_PLAYER"),
    str(Path(__file__).resolve().parents[3] / "vendor" / "gakumas-player"),
]


def engine_root() -> Path:
    for c in _CANDIDATES:
        if c and (Path(c).expanduser() / "gakumas_sim" / "campus.py").exists():
            return Path(c).expanduser()
    raise RuntimeError("找不到 gakumas_sim：应在 vendor/gakumas-player，或设置 GAKUMAS_PLAYER")


def master_ready() -> bool:
    """模拟器主数据（catalog.sqlite）已构建。没有时模拟相关决策退回社区规则。"""
    cur = ROOT / "data" / "master" / "current.json"
    if not cur.exists():
        return False
    import json

    return (ROOT / "data" / "master" / json.loads(cur.read_text())["database"]).exists()


ROOT = engine_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "simulator" / "fixtures"


# 行动用完时游戏会自动结束回合（battle._sim_state 的注释、第十局本战 R1），引擎却在行动数 0 时还允许喝饮料：
# 预演里「先出牌、行动用完再喝 初星ホエイプロテイン（使用数 +1）再出牌」和「先喝再出」一样好（第二十五局 R1 / R2 最后一回合 ホエイ 一直和最佳动作「差 0」），
# 实机打完最后一张就进下一回合，ホエイ 整局没喝掉；第二十一局 R1 剩 4 回合的 ブースト、第二十五局 R2 剩 7 回合的 ホエイ 都是行动 0 时决定喝、下一帧已是下回合。
# 打开时：行动 < 1 只剩「结束回合」（喝饮料要在最后一张牌之前）。
AUTO_END_AT_ZERO = True


def _patch_auto_end() -> None:
    try:
        from gakumas_sim.core import Battle
    except Exception:  # noqa: BLE001  引擎导入失败时由调用方报错
        return
    orig = Battle.legal_actions
    if getattr(orig, "_hiro_auto_end", False):
        return

    def legal_actions(self):
        acts = orig(self)
        if AUTO_END_AT_ZERO and acts and self.actions < 1:
            return [a for a in acts if a["kind"] == "end_turn"] or acts
        return acts

    legal_actions._hiro_auto_end = True
    Battle.legal_actions = legal_actions


_patch_auto_end()
