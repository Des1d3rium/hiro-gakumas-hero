"""主循环 / 续跑共用的异常（不依赖 KAA，防空转等轻量模块也能 import）。"""


class ResumeBlocked(RuntimeError):
    """需要用户处理才能继续（例如培育在其他端末进行中）。停机报警，不重试。"""
