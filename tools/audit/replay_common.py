"""批量 trace 回放的公共件：按 trace 多进程并行（--jobs）+ 每帧读图结果缓存（第二次起不读图）。

缓存放在 cache/replay/<名>.json（被 .gitignore 挡住），键 = 帧文件相对路径 + 大小 + 修改时间；
整份缓存带一个版本号 = 识别代码源文件内容 + 素材索引文件的哈希，改了识别代码或素材就自动作废、重新读图。

    --jobs N      并行进程数（默认 CPU 核数；1 = 不开子进程，便于调试）
    --no-cache    不读也不写缓存，每帧都重新读图
    --cache-only  只用缓存，缓存里没有的帧当作读不到（None），完全不读图
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "cache" / "replay"


def add_args(ap) -> None:
    ap.add_argument("--jobs", "-j", type=int, default=os.cpu_count() or 1, help="并行进程数（1 = 不开子进程）")
    ap.add_argument("--no-cache", action="store_true", help="不用读图缓存，每帧重新读图")
    ap.add_argument("--cache-only", action="store_true", help="只用缓存，不读图（缓存里没有的帧当读不到）")


def code_version(sources: Iterable[str | Path]) -> str:
    """识别代码 / 素材的版本号：文件内容哈希（不存在的文件记名字），任何一个变了缓存就作废。"""
    h = hashlib.sha1()
    for s in sources:
        p = ROOT / s
        h.update(str(s).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


class FrameCache:
    """一个工具一份缓存：{"version": …, "frames": {键: 结果}}。结果须能 JSON 化。

    子进程各自读一份、只把新算的结果交回主进程，由主进程合并写盘（不会并发写文件）。"""

    def __init__(self, name: str, version: str, *, mode: str = "rw"):
        self.path, self.version, self.mode = CACHE_DIR / f"{name}.json", version, mode  # mode: rw / off / ro
        self.frames: dict = {}
        self.new: dict = {}
        self.hits = self.misses = 0
        if mode != "off" and self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if d.get("version") == version:
                    self.frames = d.get("frames") or {}
            except Exception:  # noqa: BLE001  缓存坏了就当没有
                self.frames = {}

    @staticmethod
    def key(frame: Path) -> str | None:
        try:
            st = frame.stat()
        except OSError:
            return None
        try:
            rel = frame.resolve().relative_to(ROOT)
        except ValueError:
            rel = frame.resolve()
        return f"{rel}|{st.st_size}|{int(st.st_mtime)}"

    def get(self, frame: Path, compute: Callable[[Path], object]):
        """缓存命中直接返回；没命中时 ro 模式返回 None，否则 compute(frame) 并记下。"""
        k = self.key(frame) if self.mode != "off" else None
        if k is not None and k in self.frames:
            self.hits += 1
            return self.frames[k]
        self.misses += 1
        if self.mode == "ro":
            return None
        v = compute(frame)
        if k is not None:
            self.frames[k] = self.new[k] = v
        return v

    def merge(self, new: dict) -> None:
        self.frames.update(new)
        self.new.update(new)

    def save(self) -> None:
        if self.mode != "rw" or not self.new:
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": self.version, "frames": self.frames}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def cache_mode(args) -> str:
    return "off" if args.no_cache else "ro" if args.cache_only else "rw"


def run_traces(fn: Callable, traces: list, jobs: int, *, initializer: Callable | None = None) -> list:
    """对每份 trace 调 fn(trace)，按输入顺序返回结果。jobs<=1 或只有一份时在本进程跑。

    fn / initializer 必须是模块顶层函数（子进程要能 pickle）。子进程用 spawn 启动（macOS 默认），各自做一次 initializer。"""
    if jobs <= 1 or len(traces) <= 1:
        if initializer:
            initializer()
        return [fn(t) for t in traces]
    # 卡图匹配等本身会用 numpy / OpenCV 多线程：每个进程限成单线程，否则 N 个进程 × 多线程互相抢核，反而更慢
    saved = {k: os.environ.get(k) for k in THREAD_VARS}
    os.environ.update({k: "1" for k in THREAD_VARS})
    try:
        with ProcessPoolExecutor(max_workers=min(jobs, len(traces)), initializer=_worker_init, initargs=(initializer,)) as ex:
            return list(ex.map(fn, traces))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")


def _worker_init(initializer: Callable | None) -> None:
    try:
        import cv2

        cv2.setNumThreads(1)
    except Exception:  # noqa: BLE001
        pass
    try:  # OCR 退路（ddddocr / RapidOCR）的 onnxruntime 按核数开线程，环境变量管不到：所有会话改成单线程
        import onnxruntime

        base = onnxruntime.InferenceSession

        class _SingleThreadSession(base):
            def __init__(self, path_or_bytes, sess_options=None, *a, **kw):
                o = sess_options or onnxruntime.SessionOptions()
                o.intra_op_num_threads = o.inter_op_num_threads = 1
                super().__init__(path_or_bytes, o, *a, **kw)

        onnxruntime.InferenceSession = _SingleThreadSession
    except Exception:  # noqa: BLE001
        pass
    if initializer:
        initializer()
