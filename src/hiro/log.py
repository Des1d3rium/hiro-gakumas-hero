"""日志：装了 kotonebot 就用它的 logging（和 KAA 的日志在一起），没装（CI / 只用纯逻辑模块）就用标准库。"""

try:
    from kotonebot import logging  # noqa: F401
except ImportError:  # pragma: no cover - 取决于环境
    import logging  # noqa: F401
