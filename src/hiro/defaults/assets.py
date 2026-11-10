"""AssetSource 的开源默认实现：KAA 公开数据 + 使用者自建目录。代码本身不下载游戏原始素材，不做客户端解包；识别用的素材包随仓库放在 assets/pack/。

两个来源（都可以用环境变量改位置）：

- KAA 公开数据（kaa-game-data 的发布包，KAA 运行时下载到 resources/game_data/）：
  game.db（主数据）、skill_cards/（技能卡卡面）、drinks/（P 饮料图）。HIRO_KAA_GAME_DATA 改目录。
- 使用者自建目录（从自己的截图 / 客户端取，不随仓库发布；效果图标 / P 道具图标用 tools/assets/capture_icons.py 从自己的考试截图生成）：
  HIRO_USER_ASSETS，默认 resources/user_assets/
    cards/            技能卡卡面（有则优先于 KAA）
    icons/<kind>/     图标：drink（P 饮料，有则优先于 KAA）、pitem（P 道具）、status（考试效果）；RGBA 或白底 RGB，可带 index.json（资源名 → 名称）
    font/             数字字体 Campus-Alphanumeric-SemiBold.ttf（HUD 读数字形比对；没有则走 ddddocr + Apple Vision 核对）
    layout.json       UI 布局（同 hiro.vision.layout 的结构；没有则用仓库里的实测框 layout_default.json）
    sprites/          界面 Sprite（页面签名 signatures.json 引用的文件名）
    support/          支援卡图（编成页识别）

- 随仓库的素材包 assets/pack/（目录结构同上；HIRO_BUNDLED_ASSETS 改位置，设成 0 / off 关掉）。同类目录自建的和随仓库的
  取文件更多的一个；layout.json、字体先自建后随仓库。素材包里的图片、字体权利属于游戏的权利人，不属于本仓库的 GPL，见 assets/pack/README.md。

拿不到的项一律返回 None，识别层各自退路（见 hiro.vision.assets）；默认素材下各读数可用 / 降级 / 不可用见 docs/assets.md。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, ClassVar


def _env_dir(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or default)


class PublicAssetSource:
    def __init__(self, kaa_game_data: str | Path | None = None, user_assets: str | Path | None = None,
                 bundled_assets: str | Path | None = None) -> None:
        self.kaa = Path(kaa_game_data) if kaa_game_data else _env_dir("HIRO_KAA_GAME_DATA", "resources/game_data")
        self.user = Path(user_assets) if user_assets else _env_dir("HIRO_USER_ASSETS", "resources/user_assets")
        self.bundled = self._bundled_dir(bundled_assets)
        self._db: sqlite3.Connection | None = None

    @staticmethod
    def _bundled_dir(given: str | Path | None) -> Path | None:
        raw = str(given) if given else os.environ.get("HIRO_BUNDLED_ASSETS")
        if raw is not None and raw.strip().lower() in ("0", "off", "no", "false", "none"):
            return None
        p = Path(raw) if raw else Path(__file__).resolve().parents[3] / "assets" / "pack"
        return p if p.is_dir() else None

    def _roots(self) -> list[Path]:
        """自建目录在前、随仓库素材包在后。"""
        return [self.user, *([self.bundled] if self.bundled else [])]

    # ---------------------------------------------------------------- 目录
    @staticmethod
    def _first_dir(*paths: Path) -> Path | None:
        return next((p for p in paths if p.is_dir() and any(p.iterdir())), None)

    @staticmethod
    def _fullest_dir(*paths: Path) -> Path | None:
        """候选目录里文件最多的一个（并列取靠前的）：自建的只有几张时，不挡住随仓库的完整图库。"""
        best, best_n = None, 0
        for p in paths:
            n = sum(1 for _ in p.iterdir()) if p.is_dir() else 0
            if n > best_n:
                best, best_n = p, n
        return best

    def card_art_dir(self) -> Path | None:
        return self._fullest_dir(*(r / "cards" for r in self._roots()), self.kaa / "skill_cards")

    def icon_dir(self, kind: str) -> Path | None:
        extra = (self.kaa / "drinks",) if kind == "drink" else ()
        return self._fullest_dir(*(r / "icons" / kind for r in self._roots()), *extra)

    DIGIT_FONT = "Campus-Alphanumeric-SemiBold.ttf"  # HUD 数字所用字体的文件名（游戏客户端里的同名文件）

    def font_path(self) -> Path | None:
        """数字字体：font/ 下优先 DIGIT_FONT，没有就取唯一的一个字体文件（多个且没有约定名时不猜，返回 None）。"""
        for root in self._roots():
            folder = root / "font"
            if not folder.is_dir():
                continue
            if (folder / self.DIGIT_FONT).is_file():
                return folder / self.DIGIT_FONT
            fonts = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in (".ttf", ".otf")]
            if len(fonts) == 1:
                return fonts[0]
        return None

    def layout(self) -> dict[str, Any] | None:
        for root in self._roots():
            p = root / "layout.json"
            if p.is_file():
                return json.loads(p.read_text())
        return None

    def sprites_dir(self) -> Path | None:
        return self._fullest_dir(*(r / "sprites" for r in self._roots()))

    def support_dir(self) -> Path | None:
        return self._fullest_dir(*(r / "support" for r in self._roots()))

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

    SCENARIO: ClassVar[dict[str, str]] = {"qualify": "produce-007", "main": "produce-008", "produce-007": "produce-007", "produce-008": "produce-008"}

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

    def support_effects(self, support_id: str, level: int, *, limit_rank: str | None = None, plan: str | None = None) -> list[dict[str, Any]] | None:
        """支援卡技能：用培育层公式 gakumas_produce.supports.compile_card 从社区公开 master 编译（和主仓库同一套）。
        主数据没构建（vendor/gakumas-player/scripts/fetch_master.py）或编不出来时 None，编成按属性张数 / 等级排。"""
        try:
            from hiro.brain.deciders import _sim_ready

            if not _sim_ready():
                return None
            from gakumas_produce import supports
        except Exception:  # noqa: BLE001  引擎导入失败
            return None
        c = self._produce_catalog()
        try:
            card = c.one("SupportCard", support_id)
        except Exception:  # noqa: BLE001  主数据里没有这张卡
            return None
        plan = plan or card["planType"]
        if limit_rank is not None:
            ranks = [limit_rank]
        else:  # 等级够得着的突破段，从低到高试（和主仓库 formation._skills 一样）
            rows = sorted(c.by_id("SupportCardLevelLimit", card["supportCardLevelLimitId"]), key=lambda r: r["levelLimit"])
            ranks = [r["rank"] for r in rows if r["levelLimit"] >= level]
        for rank in ranks:
            try:
                out = supports.compile_card(c, support_id, level, rank, plan)
            except Exception:  # noqa: BLE001  等级 / 突破 / 方案对不上、未移植的形状
                continue
            return [{"effect_type": s["effect"]["produceEffectType"], "value": s["effect"]["effectValueMin"], "trigger_id": s["trigger"]["id"],
                     "limit": s["limit"]} for s in out["skills"]]  # fmt: skip
        return None

    def _produce_catalog(self):
        """培育层公式用的主数据目录（gakumas_exam.catalog，sqlite 连接每个线程一份）。"""
        import threading

        from gakumas_exam.catalog import Catalog

        local = self.__dict__.setdefault("_produce_local", threading.local())
        c = getattr(local, "c", None)
        if c is None:
            c = local.c = Catalog()
        return c

    def exam_requirements(self, scenario: str) -> dict[str, Any] | None:
        """考试要求 / 倍率换算 / 星级基准：用培育层公式 gakumas_produce（vendor/gakumas-player，社区公开 master）算。
        主数据没构建（vendor/gakumas-player/scripts/fetch_master.py）时 None，选课退回补最低属性 / 主属性规则。"""
        if scenario != "hif":
            return None
        try:
            from hiro.brain.deciders import _sim_ready

            if not _sim_ready():
                return None
            from gakumas_produce import multiplier
            from gakumas_produce.attributes import assessment, hif_preview
            from gakumas_exam.catalog import Catalog
        except Exception:  # noqa: BLE001  引擎导入失败
            return None
        import threading

        local = threading.local()

        def cat():  # sqlite 连接不能跨线程用（主仓库第二十一局）：每个线程一份
            c = getattr(local, "c", None)
            if c is None:
                c = local.c = Catalog()
            return c

        return {
            "preview": lambda mode, position, params, idol_card_id: hif_preview(mode, position, params, idol_card_id=idol_card_id, catalog=cat()),
            "assessment": lambda value, requirement: assessment(value, requirement, 110, 110),
            "star_baseline": lambda mode, position, idol_card_id: multiplier.star_baseline(mode, position, idol_card_id, cat()),
            "star_factor": multiplier.star_factor,
            "exam_bonus": multiplier.exam_bonus,
        }
