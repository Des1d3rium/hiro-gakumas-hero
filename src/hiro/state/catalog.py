"""主数据目录：对 AssetSource.master_db()（KAA game.db 同结构的公开主数据）的薄适配。

只实现决策层用到的四个读法：one / by_id / rows / card。列里是 JSON 文本的（列表 / 对象）解成 Python 对象。
每个线程一份连接（sqlite 连接不能跨线程用）。拿不到主数据时抛 CatalogUnavailable，调用方按「未知」处理。
"""

from __future__ import annotations

import json
import sqlite3
import threading


class CatalogUnavailable(RuntimeError):
    pass


def _decode(row: sqlite3.Row) -> dict:
    out = {}
    for k in row.keys():
        v = row[k]
        if isinstance(v, str) and v[:1] in "[{":
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


class Catalog:
    def __init__(self, db: sqlite3.Connection | None = None):
        if db is None:
            from hiro import plugins

            src = plugins.get("asset_source")
            db = src.master_db() if src is not None else None
        if db is None:
            raise CatalogUnavailable("没有主数据（AssetSource.master_db() 返回 None）")
        db.row_factory = sqlite3.Row
        self.db = db

    def rows(self, table: str) -> list[dict]:
        return [_decode(r) for r in self.db.execute(f'SELECT * FROM "{table}"')]

    def by_id(self, table: str, key: str) -> list[dict]:
        return [_decode(r) for r in self.db.execute(f'SELECT * FROM "{table}" WHERE id = ?', (key,))]

    def one(self, table: str, key: str, variant: int | None = None) -> dict:
        rows = self.by_id(table, key)
        if variant is not None and rows and "upgradeCount" in rows[0]:
            rows = [r for r in rows if r.get("upgradeCount") == variant]
        if len(rows) != 1:
            raise KeyError(f"{table}:{key} 找到 {len(rows)} 行")
        return rows[0]

    def card(self, card_id: str, upgrade: int = 0) -> dict:
        return self.one("ProduceCard", card_id, upgrade)


_LOCAL = threading.local()


def catalog() -> Catalog:
    c = getattr(_LOCAL, "catalog", None)
    if c is None:
        c = _LOCAL.catalog = Catalog()
    return c
