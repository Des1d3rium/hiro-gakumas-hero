# 来源：digital-hiro hiro/kaa_env.py（c3fc27f）；调用 KAA（kotones-auto-assistant，GPL-3.0）；开源版：编成号 / 方案名改成环境变量
"""用代码构造 KAA 运行所需的 profile 与培育方案，不经 KAA 图形界面。

KAA 的培育流程通过 `kaa_context.conf()` / `produce_solution()` 读取配置；方案文件写在工作目录的 conf/ 下（别提交）。

环境变量：
- HIRO_SUPPORT_SET（new 必填）：支援卡编成号（1–20）。开局的租借格由 hiro.loop.kaa_patches 处理：编成已满直接下一步，
  租借格空着就停下（RentalPending），不让 KAA 直接确认列表第一张。写 auto = 用 KAA「おまかせ」（KAA 自己选支援卡和租借，
  会确认列表第一张租借卡），只有明确想这样时才写。
- HIRO_MEMORY_SET（new 必填）：回忆（メモリー）编成号。写 auto = おまかせ（会改写当前编成位，可能带进和本局方向冲突的牌）。
- HIRO_MEMORY_CARDS：回忆编成带进培育的技能卡 ID，逗号分隔（如 p_card-02-men-2_004,p_card-02-act-1_037）。
  决策器把它们算作已有（三选一里不必再找），续跑记账也从它们起算；不设 = 空。
- HIRO_KAA_PROFILE / HIRO_KAA_SOLUTION：KAA 的 profile 名与培育方案名（默认 hiro / hiro-gakumas）。
- 设备（与 KAA 支持的平台对齐，见 hiro.device）：
  HIRO_DEVICE = auto（macOS 上走 PlayCover）/ maatools / quartz / adb / mumu / leidian / dmm；
  HIRO_ADB = adb 地址 ip:port（adb / leidian 用；mumu 不设时由 MuMu SDK 自动取）；
  HIRO_SCREENSHOT = 截图方式（缺省：adb / leidian → adb，mumu → Windows 上 nemu_ipc、其他平台 adb，dmm → windows_background）；
  HIRO_EMULATOR_INSTANCE = MuMu / 雷电的实例 ID（MuMu 缺省 0，多开时指定）。
resume 不读编成号（培育已经开始）。
"""

from __future__ import annotations

import os

PROFILE_NAME = os.environ.get("HIRO_KAA_PROFILE") or "hiro"
SOLUTION_NAME = os.environ.get("HIRO_KAA_SOLUTION") or "hiro-gakumas"


AUTO = "auto"

# HIRO_DEVICE → (KAA lifecycle.type, 缺省截图方式)；截图方式的可选值以 kaa.config.validation 为准
_KAA_DEVICE = {"adb": ("custom", "adb"), "mumu": ("mumu12v5", None), "leidian": ("leidian", "adb"), "dmm": ("dmm", "windows_background")}


def kaa_backend_from_env():
    """按 HIRO_DEVICE / HIRO_ADB / HIRO_SCREENSHOT / HIRO_EMULATOR_INSTANCE 构造 KAA BackendConfig（PlayCover 路线为 None）。"""
    from hiro.device import requested_backend

    return kaa_backend(requested_backend(), adb=os.environ.get("HIRO_ADB"), screenshot=os.environ.get("HIRO_SCREENSHOT"),
                       instance=os.environ.get("HIRO_EMULATOR_INSTANCE"))  # fmt: skip


def kaa_backend(device: str, *, adb: str | None = None, screenshot: str | None = None, instance: str | None = None,
                platform: str | None = None):  # fmt: skip
    """按 HIRO_DEVICE 等构造 KAA 的 BackendConfig；PlayCover 路线（auto / maatools / quartz）返回 None，不动 KAA 默认值。"""
    import sys

    from kaa.config.base_config import (
        AutoConnection, BackendConfig, CustomDevice, DmmDevice, LeidianDevice, MuMu12V5Device, TcpConnection,
    )  # fmt: skip

    if device not in _KAA_DEVICE:
        return None
    kind, shot = _KAA_DEVICE[device]
    if kind == "mumu12v5" and shot is None:
        shot = "nemu_ipc" if (platform or sys.platform) == "win32" else "adb"
    shot = screenshot or shot
    lifecycle = {
        "custom": lambda: CustomDevice(type="custom"),
        "mumu12v5": lambda: MuMu12V5Device(type="mumu12v5", instance_id=instance or "0"),  # KAA 不设会报错；单开就是 0
        "leidian": lambda: LeidianDevice(type="leidian", instance_id=instance),
        "dmm": lambda: DmmDevice(type="dmm"),
    }[kind]()
    if adb:
        host, _, port = adb.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(f"HIRO_ADB 要写成 ip:port，例如 127.0.0.1:16384，收到 {adb!r}")
        connection = TcpConnection(type="tcp", ip=host, port=int(port))
    elif kind == "mumu12v5":
        connection = AutoConnection(type="auto")
    else:
        connection = TcpConnection(type="tcp")  # 127.0.0.1:5555；dmm 不用 adb
    return BackendConfig(lifecycle=lifecycle, connection=connection, screenshot_impl=shot)


def _set_env(name: str) -> int | str | None:
    """编成号：整数、"auto"（おまかせ）或 None（没设）。"""
    v = (os.environ.get(name) or "").strip()
    if not v:
        return None
    if v.lower() == AUTO:
        return AUTO
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"{name} 应为编成号（整数）或 auto，收到 {v!r}") from None


SUPPORT_SET = _set_env("HIRO_SUPPORT_SET")
MEMORY_SET = _set_env("HIRO_MEMORY_SET")
MEMORY_CARDS = tuple(c.strip() for c in (os.environ.get("HIRO_MEMORY_CARDS") or "").split(",") if c.strip())


class SetupMissing(ValueError):
    """new 开局缺编成号。"""


def init_kaa(scenario, *, idol: str | None = None, battle_strategy: str = "bandai", require_sets: bool = True):
    """建 / 更新 KAA 培育方案并初始化 KAA 上下文。require_sets（new 开局）时 HIRO_SUPPORT_SET / HIRO_MEMORY_SET 必须给。"""
    from kaa.config.produce import ProduceSolutionManager
    from kaa.config.schema import KaaConfig
    from kaa.kaa_context import init as kaa_init

    missing = [n for n, v in (("HIRO_SUPPORT_SET", SUPPORT_SET), ("HIRO_MEMORY_SET", MEMORY_SET)) if v is None]
    if require_sets and missing:
        raise SetupMissing(f"开局需要编成号：请设 {' / '.join(missing)}（游戏里编好的编成号；确实想用おまかせ写 auto）")
    manager = ProduceSolutionManager()
    solution = next((s for s in manager.list() if s.name == SOLUTION_NAME), None) or manager.new(SOLUTION_NAME)
    solution.data.mode = scenario
    solution.data.idol = idol
    solution.data.battle_strategy = battle_strategy
    solution.data.skip_commu = True
    if MEMORY_SET is not None:
        solution.data.auto_set_memory = MEMORY_SET == AUTO
        if MEMORY_SET != AUTO:
            solution.data.memory_set = MEMORY_SET
    if SUPPORT_SET is not None:
        solution.data.auto_set_support_card = SUPPORT_SET == AUTO
        if SUPPORT_SET != AUTO:
            solution.data.support_card_set = SUPPORT_SET
    manager.save(solution.id, solution)

    config = KaaConfig()
    config.tasks.produce.enabled = True
    config.tasks.produce.produce_count = 1
    config.tasks.produce.selected_solution_id = solution.id
    backend = kaa_backend_from_env()
    if backend is not None:
        from kaa.config.validation import validate_profile_config

        config.backend = backend
        errors = [i.message for i in validate_profile_config(config) if i.severity == "error"]
        if errors:
            raise SetupMissing("设备设置有误：" + "；".join(errors))
    kaa_init(config, PROFILE_NAME)
    return config
