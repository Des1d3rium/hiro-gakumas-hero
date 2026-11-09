"""给前端的本机读写服务（S8）：读写策略档案、查下拉框数据，不碰游戏。只监听 127.0.0.1。

    PYTHONPATH=src .venv/bin/python -m hiro.ui.serve [--port 8765]

接口见 docs/strategies.md「给前端的本机服务」。保存时按 schema 校验，不合法返回 400、不写文件。
正在跑的批次用的是开局快照（批次文件 profile），这里改的是下一局；GET /batches/<ID>/profile 给界面显示当前批次的版本。
路由都在 handle(method, path, body) 里，测试不用开端口。
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from hiro.strategies import profiles, registry, schema

ROOT = Path(__file__).resolve().parents[3]
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _db():
    from hiro.state.catalog import catalog

    return catalog().db


def _rows(sql: str, args=()) -> list[dict]:
    cur = _db().execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _cards(q: str | None) -> list[dict]:
    from hiro.brain.cards import description_text

    sql = "SELECT id, upgradeCount, name, rarity, category, planType, produceDescriptions, playMovePositionType FROM ProduceCard"
    args: tuple = ()
    if q:
        sql += " WHERE name LIKE ? OR id LIKE ?"
        args = (f"%{q}%", f"%{q}%")
    out = []
    for r in _rows(sql + " ORDER BY id, upgradeCount", args):
        text = description_text(json.loads(r["produceDescriptions"]) if r["produceDescriptions"] else None)
        out.append({"id": r["id"], "upgrade": r["upgradeCount"], "name": r["name"], "rarity": r["rarity"],
                    "category": r["category"], "plan": r["planType"], "text": text,
                    "once": "レッスン中1回" in (text or "")})
    return out


def _customize(card_id: str, upgrade: int) -> list[dict]:
    row = _rows("SELECT produceCardCustomizeIds, maxCustomizeCount FROM ProduceCard WHERE id = ? AND upgradeCount = ?",
                (card_id, upgrade))
    if not row:
        raise HttpError(404, f"没有卡 {card_id} 强化 {upgrade}")
    ids = json.loads(row[0]["produceCardCustomizeIds"] or "[]")
    out = []
    for cid in ids:
        levels = _rows("SELECT id, customizeCount, description, producePoint, produceCardGrowEffectIds FROM ProduceCardCustomize "
                       "WHERE id = ? ORDER BY customizeCount", (cid,))
        out.append({"id": cid, "levels": [{"count": x["customizeCount"], "price": x["producePoint"],
                                           "description": x["description"], "effects": json.loads(x["produceCardGrowEffectIds"] or "[]")}
                                          for x in levels]})
    return out


def _idols() -> list[dict]:
    have = set(registry.idols())
    try:
        rows = _rows("SELECT s.id AS id, ic.name AS name, ic.planType AS plan FROM IdolCardSkin s "
                     "LEFT JOIN IdolCard ic ON ic.id = s.idolCardId ORDER BY s.id")
    except Exception:  # noqa: BLE001  没有 game.db 时只列有档案的
        rows = [{"id": i, "name": None, "plan": None} for i in sorted(have)]
    return [{**r, "profile": r["id"] in have, "engine": registry.engine(r["id"]) if r["id"] in have else "default"} for r in rows]


def _memories() -> dict:
    deck_dir = ROOT / "reports" / "memories"
    decks = sorted(p.stem for p in deck_dir.glob("*.json")) if deck_dir.is_dir() else []
    prof_dir = profiles.strategies_dir() / "memories"
    profs = sorted(p.stem for p in prof_dir.glob("*.json")) if prof_dir.is_dir() else []
    return {"decks": [d for d in decks if not d.endswith(".abilities")], "profiles": profs}


def _check_id(x: str) -> str:
    if not _ID.fullmatch(x or ""):
        raise HttpError(400, f"不合法的 ID：{x!r}")
    return x


def _get_profile(kind: str, key: str) -> dict:
    if kind == "memory":
        path = profiles.memory_path(key)
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        merged = profiles.resolve(raw.get("idol") if raw else None, key)
        return {"file": str(path), "raw": raw, "merged": merged["profile"], "layers": merged["layers"]}
    path = profiles.strategies_dir() / "idols" / f"{key}.json"
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    return {"file": str(path), "raw": raw, "merged": registry.public(registry.idol_profile(key))}


def _put_profile(kind: str, key: str, body: dict) -> dict:
    if not isinstance(body, dict):
        raise HttpError(400, "要 JSON 对象")
    if kind == "memory":
        body = {"schema": "memory-profile/1", **body, "memory": key}
        problems = schema.check(body, "memory-profile")
        path = profiles.memory_path(key)
    else:
        body = {**body, "idol": key}
        path = profiles.strategies_dir() / "idols" / f"{key}.json"
        problems = []
    if problems:
        raise HttpError(400, "\n".join(problems[:30]))
    old = path.read_bytes() if path.is_file() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:  # 合并后再校验一次（偶像档案的覆盖文件只写一部分，要和随仓库的合起来才完整）
        merged = profiles.resolve(body.get("idol"), key) if kind == "memory" else registry.idol_profile(key)
        if kind == "idol" and merged is None:
            raise profiles.ProfileError("覆盖文件之外没有随仓库的档案，需要写全 schema / engine")
    except profiles.ProfileError as e:
        if old is None:
            path.unlink()
        else:
            path.write_bytes(old)
        raise HttpError(400, str(e)) from e
    return {"ok": True, "file": str(path)}


def _batch_profile(batch: str) -> dict:
    path = ROOT / "reports" / "batches" / f"{batch}.json"
    if not path.is_file():
        raise HttpError(404, f"没有批次 {batch}")
    state = json.loads(path.read_text(encoding="utf-8"))
    snap = state.get("profile")
    active = state.get("active") or {}
    return {"batch": batch, "snapshot": snap and {k: snap.get(k) for k in ("at", "idol", "memory", "sha256", "layers")},
            "active_run": active.get("run"), "active_profile_sha": active.get("profile_sha"),
            "completed": [{"run": r.get("run"), "profile_sha": r.get("profile_sha")} for r in state.get("completed") or []]}


def handle(method: str, raw_path: str, body: bytes | None = None) -> tuple[int, object]:
    url = urlparse(raw_path)
    parts = [p for p in url.path.split("/") if p]
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    try:
        if method == "GET":
            if len(parts) == 2 and parts[0] == "schemas" and parts[1] in ("memory-profile", "idol-profile", "buttons"):
                return 200, schema.load(parts[1])
            if parts == ["cards"]:
                return 200, _cards(q.get("q"))
            if parts == ["drinks"]:
                return 200, _rows("SELECT id, name, rarity, planType AS plan FROM ProduceDrink ORDER BY id")
            if len(parts) == 2 and parts[0] == "customize":
                return 200, _customize(_check_id(parts[1]), int(q.get("upgrade", 0)))
            if parts == ["idols"]:
                return 200, _idols()
            if parts == ["memories"]:
                return 200, _memories()
            if len(parts) == 3 and parts[0] == "profiles" and parts[1] in ("memory", "idol"):
                return 200, _get_profile(parts[1], _check_id(parts[2]))
            if len(parts) == 3 and parts[0] == "batches" and parts[2] == "profile":
                return 200, _batch_profile(_check_id(parts[1]))
            if parts == ["buttons"]:
                from hiro.ui import buttons

                return 200, buttons.table()
        if method == "PUT" and len(parts) == 3 and parts[0] == "profiles" and parts[1] in ("memory", "idol"):
            try:
                data = json.loads((body or b"").decode("utf-8"))
            except ValueError as e:
                raise HttpError(400, f"不是 JSON：{e}") from e
            return 200, _put_profile(parts[1], _check_id(parts[2]), data)
        raise HttpError(404, f"没有这个接口：{method} {url.path}")
    except HttpError as e:
        return e.status, {"error": str(e)}
    except profiles.ProfileError as e:
        return 400, {"error": str(e)}


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._send(*handle("GET", self.path))

    def do_PUT(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self._send(*handle("PUT", self.path, self.rfile.read(n) if n else b""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    print(f"http://127.0.0.1:{args.port}/  （Ctrl-C 结束）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
