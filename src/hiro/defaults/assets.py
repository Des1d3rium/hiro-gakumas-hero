"""AssetSource 的开源默认实现：KAA 公开数据 + 使用者自建目录。不带、不下载任何游戏原始素材，不做客户端解包。

两个来源（都可以用环境变量改位置）：

- KAA 公开数据（kaa-game-data 的发布包，KAA 运行时下载到 resources/game_data/）：
  game.db（主数据）、skill_cards/（技能卡卡面）、drinks/（P 饮料图）。HIRO_KAA_GAME_DATA 改目录。
- 使用者自建目录（tools/assets/capture_icons.py 从自己的截图裁）：HIRO_USER_ASSETS，默认 resources/user_assets/
    cards/            技能卡卡面（有则优先于 KAA）
    icons/<kind>/     图标：drink（P 饮料）、pitem（P 道具）、status（考试效果）；可带 index.json（资源名 → 名称）
    font/             数字字体 Campus-Alphanumeric-SemiBold.ttf（HUD 读数字形比对；没有则数字全走 ddddocr）
    layout.json       UI 布局（同 hiro.vision.layout 的结构；没有则用仓库里的实测框 layout_default.json）
    sprites/          界面 Sprite（页面签名 signatures.json 引用的文件名）
    support/          支援卡图（编成页识别）

拿不到的项一律返回 None，识别层各自退路（见 hiro.vision.assets）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def _env_dir(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or default)


class PublicAssetSource:
    def __init__(self, kaa_game_data: str | Path | None = None, user_assets: str | Path | None = None) -> None:
        self.kaa = Path(kaa_game_data) if kaa_game_data else _env_dir("HIRO_KAA_GAME_DATA", "resources/game_data")
        self.user = Path(user_assets) if user_assets else _env_dir("HIRO_USER_ASSETS", "resources/user_assets")
        self._db: sqlite3.Connection | None = None

    # ---------------------------------------------------------------- 目录
    @staticmethod
    def _first_dir(*paths: Path) -> Path | None:
        return next((p for p in paths if p.is_dir() and any(p.iterdir())), None)

    def card_art_dir(self) -> Path | None:
        return self._first_dir(self.user / "cards", self.kaa / "skill_cards")

    def icon_dir(self, kind: str) -> Path | None:
        extra = (self.kaa / "drinks",) if kind == "drink" else ()
        return self._first_dir(self.user / "icons" / kind, *extra)

    DIGIT_FONT = "Campus-Alphanumeric-SemiBold.ttf"  # HUD 数字所用字体的文件名（游戏客户端里的同名文件）

    def font_path(self) -> Path | None:
        """数字字体：font/ 下优先 DIGIT_FONT，没有就取唯一的一个字体文件（多个且没有约定名时不猜，返回 None）。"""
        folder = self.user / "font"
        if not folder.is_dir():
            return None
        if (folder / self.DIGIT_FONT).is_file():
            return folder / self.DIGIT_FONT
        fonts = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in (".ttf", ".otf")]
        return fonts[0] if len(fonts) == 1 else None

    def layout(self) -> dict[str, Any] | None:
        p = self.user / "layout.json"
        return json.loads(p.read_text()) if p.is_file() else None

    def sprites_dir(self) -> Path | None:
        return self._first_dir(self.user / "sprites")

    def support_dir(self) -> Path | None:
        return self._first_dir(self.user / "support")

    # ---------------------------------------------------------------- 主数据
    def master_db(self) -> sqlite3.Connection | None:
        if self._db is None:
            p = self.kaa / "game.db"
            if not p.is_file():
                return None
            db = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True, check_same_thread=False)
            db.row_factory = sqlite3.Row
            if not db.execute("select 1 from sqlite_master where type='table' and name='ProduceCard'").fetchone():
                db.close()
                return None  # 空库（KAA 首次运行会建空文件）：当作没有主数据
            self._db = db
        return self._db

    def _rows(self, sql: str) -> list[sqlite3.Row] | None:
        db = self.master_db()
        if db is None:
            return None
        try:
            return list(db.execute(sql))
        except sqlite3.Error:
            return None

    def card_catalog(self) -> dict[str, dict[str, Any]] | None:
        """卡 id（含强化次数后缀 @n）→ {name, rarity, plan, category, cost_type, cost, asset, initial}。"""
        rows = self._rows("select id, upgradeCount, name, rarity, planType, category, costType, costValue, assetId, isInitial from ProduceCard")
        if rows is None:
            return None
        return {f"{r['id']}@{r['upgradeCount']}": {"id": r["id"], "upgrade": r["upgradeCount"], "name": r["name"], "rarity": r["rarity"],
                                                    "plan": r["planType"], "category": r["category"], "cost_type": r["costType"],
                                                    "cost": r["costValue"], "asset": r["assetId"], "initial": bool(r["isInitial"])}
                for r in rows}  # fmt: skip

    def drink_catalog(self) -> dict[str, dict[str, Any]] | None:
        rows = self._rows("select id, name, rarity, planType, assetId from ProduceDrink")
        if rows is None:
            return None
        return {r["id"]: {"name": r["name"], "rarity": r["rarity"], "plan": r["planType"], "asset": r["assetId"]} for r in rows}

    def pitem_catalog(self) -> dict[str, dict[str, Any]] | None:
        rows = self._rows("select id, name, rarity, planType, assetId from ProduceItem")
        if rows is None:
            return None
        return {r["id"]: {"name": r["name"], "rarity": r["rarity"], "plan": r["planType"], "asset": r["assetId"]} for r in rows}

    SCENARIO = {"qualify": "produce-007", "main": "produce-008", "produce-007": "produce-007", "produce-008": "produce-008"}

    def schedule(self, scenario: str) -> list[dict[str, Any]] | None:
        """H.I.F 培育日程（选拔 20 项 / 本战 9 项，位置从 1 起；选行动页必需）。scenario：qualify / main 或 produce-007 / produce-008。
        数据随仓库：schedule_hif.json（由 KAA 公开主数据编出，选行动位置与实机记录核对过）；使用者自建目录里有同名文件时优先。"""
        mode = self.SCENARIO.get(scenario)
        if mode is None:
            return None
        for p in (self.user / "schedule_hif.json", Path(__file__).with_name("schedule_hif.json")):
            if p.is_file():
                stages = json.loads(p.read_text(encoding="utf-8")).get(mode)
                if stages:
                    return [dict(st) for st in stages]
        return None

    def support_effects(self, support_id: str, level: int) -> list[dict[str, Any]] | None:
        return None  # 支援卡效果：公开数据里暂无可靠整理，编成按属性张数 / 等级排（策略层退路）

    def exam_requirements(self, scenario: str) -> dict[str, Any] | None:
        return None  # 考试要求 / 倍率换算 / 星级基准：公开数据里没有可靠来源，选课退回补最低属性 / 主属性规则
