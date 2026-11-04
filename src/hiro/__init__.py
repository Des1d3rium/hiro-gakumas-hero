"""数字小广（digital-hiro）开源框架部分：契约、主循环核心、状态机账本、检验规则、LLM 适配层、操作层（设备 / 执行 / 安全拦截）。"""

from . import compat  # noqa: F401,E402  必须先于 kotonebot / KAA 导入（macOS 上补 ctypes.WinDLL、mouse 占位）
