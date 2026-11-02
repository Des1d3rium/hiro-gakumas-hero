"""识别原始输出 → 契约 Observation 的通用打包（开源版）。

FIELD_ADAPTERS：页面名 → fn(raw: dict) -> dict（把识别器的原始字段转成契约字段）。
field_meta：页面名 → fn(scene, raw, fields) -> dict[str, FieldMeta]（可选，默认按 method="vision"）。
"""

from __future__ import annotations

FIELD_ADAPTERS: dict = {}
FIELD_META_FNS: dict = {}


def register_adapter(scene: str, fn, meta_fn=None) -> None:
    """注册某个页面的字段转换（以及可选的字段元信息）。"""
    FIELD_ADAPTERS[scene] = fn
    if meta_fn is not None:
        FIELD_META_FNS[scene] = meta_fn


def field_meta(scene: str, raw: dict, fields: dict) -> dict:
    from hiro.contracts import FieldMeta

    fn = FIELD_META_FNS.get(scene)
    if fn is not None:
        return fn(scene, raw, fields)
    return {k: FieldMeta(method="vision") for k, v in fields.items() if v is not None}


def observation(seq: int, t: float, scene: str, raw: dict | None, *, area: str = "unknown", screenshot: str | None = None,
                source: str = "screen", stable_frames: int = 1, timings_ms: dict | None = None, ready: bool = False):  # fmt: skip
    """识别原始输出（hiro.vision.extract 的 fields）→ hiro.contracts.Observation。

    raw 为 None 或带 error 时 fields 为空；ready=True 表示 raw 已经是契约字段（例如存档投影、考试循环自己拼的手牌），不再转换。
    每个读到的字段附 FieldMeta（方法与置信度按字段，见 FIELD_METHODS / FIELD_CONFIDENCE）。
    """
    from hiro.contracts import FIELD_CATALOG, FieldMeta, Observation, PageObs

    fields: dict = {}
    meta: dict = {}
    adapter = FIELD_ADAPTERS.get(scene)
    if ready and raw:
        fields = dict(raw)
        meta = {k: FieldMeta(method=source) for k, v in fields.items() if v is not None}
    elif adapter is not None and raw and "error" not in raw:
        try:
            fields = adapter(raw)
            meta = field_meta(scene, raw, fields)
        except Exception as e:  # 转换失败只记错误，不拖垮采集 / 主循环
            fields, meta = {}, {"_error": FieldMeta(method="vision", note=f"adapter: {e!r}")}
        for n in FIELD_CATALOG.get(scene, ()):
            fields.setdefault(n, None)  # 登记过、这帧没读到 → 显式 None（不猜）
    elif raw and "error" in raw:
        meta = {"_error": FieldMeta(method="vision", note=str(raw["error"]))}
    return Observation(
        seq=seq, t=t, page=PageObs(name=scene, area=area, stable_frames=stable_frames),
        fields=fields, field_meta=meta, screenshot=screenshot, source=source, timings_ms=dict(timings_ms or {}),
    )  # fmt: skip
