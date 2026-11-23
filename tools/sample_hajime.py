"""初（はじめ）采样驱动：用 KAA 的标准策略（StandardStrategy）在 Mac 上自动打一局初，一路存帧，给新主循环接初 / N.I.A 做判页样本。

协调 11-17（页面清单第 3 节 P0）。只用于采样：不走我们的决策层，KAA 认不出的页（含事件选项页：kaa_patches 不让 KAA 盲选第一项）
画面 3 分钟没变化就停下，帧留着，交给人手动接。

    PYTHONPATH=src python tools/sample_hajime.py [--difficulty regular|pro|master] [--idol i_card-skin-shro-3-007]
                                                 [--out <帧目录>] [--resume]

- --resume：培育进行中时续跑，不开新局（hiro.loop.resume.enter_produce：首页点「プロデュース中」→「再開する」，已在培育里就直接接上；
  破弃提示只点キャンセル 停下）。--difficulty 要和进行中的那局一致（KAA 的控制器按它走）。不加 --resume 时开局前照旧 guard_new，
  看到「再開する」就停、提示改用 --resume。
- 横屏 LIVE：PlayCover 上 LIVE 横屏会黑屏（KAA 等 select cover 180s 后停，协调 11-17 初最終試験结算），每次截图喂一帧给
  hiro.loop.landscape.LandscapeGuard（同 Runtime / HiroController：黑屏 20s 进全屏，LIVE 结束退全屏）；LIVE 中不按 STALL_S 停。
- 选项页：KAA 想盲点的剧情 / 事件 / 初始二选一被 kaa_patches 拦下后会每秒重试；8 秒内拦下 BLIND_STOP 次就存一帧、停下交人
  （协调 11-17 第 4 次采样：开局剧情二选一刷了 58 次没停）。

- 编成：按开局编成方案 conf/formation-plan.json（HIRO_FORMATION_PLAN 可改；支援卡 / 回忆编成号从方案来，也可用 HIRO_SUPPORT_SET /
  HIRO_MEMORY_SET 覆盖）。第 2 步租借格走 hiro.produce.rental（kaa_patches 的 RENTAL_PICK），挑不到才 RentalPending 停。
- 规矩同 run_hif：kaa_patches 全装（「プロデュースデータの破棄」只点キャンセル、不用ジュエル、编成核对），开局前 guard_new。
  AP 不够就停（run_hif 的自动补 AP 只认 H.I.F 的按钮），手动喝 APドリンク 后重跑。
- 存帧：每次截图和上一张存下的帧相比有明显变化（灰度缩略图平均差 ≥ --diff），或 KAA 判的页面类型变了，就存一帧原尺寸截图：
  <序号>-<时分秒>-<KAA 页面>.jpg（JPEG 质量 JPEG_QUALITY：原尺寸 PNG 一局 2316 张占 4.1 GB 把盘写满，协调 11-17 改 q95 后 1.0 GB）；frames.jsonl 每行 {seq, t, file, scene, ctx（最近一条 KAA / kotonebot 日志）, native, logical_diff}。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import hiro  # noqa: F401  兼容层（要在 kotonebot 之前）

logger = logging.getLogger("sample_hajime")

STALL_S = 180.0  # 画面没变、KAA 页面也没变这么久：停下（帧留着，人接手）
JPEG_QUALITY = 95
BLIND_STOP, BLIND_WINDOW_S = (
    3,
    8.0,
)  # 选项页被拦下：这么多秒内拦下这么多次就停（同 HiroController 的判据，过场动画偶尔误报一次不停）


class SampleStall(RuntimeError):
    """画面 STALL_S 秒没有变化：停下交给人。"""


class SampleBlocked(RuntimeError):
    """KAA 想盲点的选项页（剧情 / 事件 / 初始二选一）连续被拦下：存帧后停下交给人。"""


class Sampler:
    """挂在 kotonebot 设备上：screenshot_raw 记住最近一张原生截图，screenshot_hook_after 拿逻辑分辨率帧判断要不要存。"""

    def __init__(self, out: Path, diff: float, clock=time.monotonic, landscape=None) -> None:
        self.out, self.diff, self.clock = out, diff, clock
        self.out.mkdir(parents=True, exist_ok=True)
        self.jsonl = (self.out / "frames.jsonl").open("a", encoding="utf-8")
        self.seq = (
            sum(1 for _ in (self.out / "frames.jsonl").open(encoding="utf-8"))
            if (self.out / "frames.jsonl").exists()
            else 0
        )
        self.last_thumb = None
        self.last_scene = None
        self.scene = "start"  # KAA ProducePage.check_scene 最近一次的结果
        self.ctx = ""  # 最近一条 KAA / kotonebot 日志
        self.raw = None
        self.progress_t = clock()
        self.blind_hits: list[float] = []
        self.landscape = landscape  # hiro.loop.landscape.LandscapeGuard（main 里按 is_playcover 建）；None = 不管横屏

    # ---- 钩子
    def wrap_raw(self, device) -> None:
        orig = device.screenshot_raw

        def raw():
            img = orig()
            self.raw = img
            return img

        device.screenshot_raw = raw
        device.screenshot_hook_after = self.after

    def after(self, img):
        import cv2
        import numpy as np

        thumb = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (90, 160)).astype(np.float32)
        d = 255.0 if self.last_thumb is None else float(np.abs(thumb - self.last_thumb).mean())
        now = self.clock()
        if self.landscape is not None:
            frame = self.raw if self.raw is not None else img  # 原尺寸截图：横竖方向按它判（逻辑分辨率帧是缩放过的）
            self.landscape.tick(frame)
            if self.landscape.in_live(frame):
                self.progress_t = now  # LIVE 演出 / 黑屏等全屏切换：画面本来就不变，不按 STALL_S 停
        if self.blind_choice(now):
            self.scene = "BLIND_CHOICE"
            self.save(img, d)
            raise SampleBlocked(f"KAA 想盲点的选项页 {BLIND_WINDOW_S:.0f}s 内被拦下 {len(self.blind_hits)} 次（剧情 / 事件 / 初始二选一），"
                                f"停下交人（最近日志：{self.ctx}）")  # fmt: skip
        if d >= self.diff or self.scene != self.last_scene:
            self.save(img, d)
            self.last_thumb, self.last_scene, self.progress_t = thumb, self.scene, now
        elif now - self.progress_t >= STALL_S:
            raise SampleStall(
                f"画面和 KAA 页面（{self.scene}）{now - self.progress_t:.0f}s 没变化，停下（最近日志：{self.ctx}）"
            )
        return img

    def blind_choice(self, now: float) -> bool:
        """kaa_patches 拦下 KAA 盲点（BLIND_CHOICE 置位）就记一次；BLIND_WINDOW_S 秒内满 BLIND_STOP 次返回 True。"""
        from hiro.loop import kaa_patches

        if not kaa_patches.BLIND_CHOICE["pending"]:
            return False
        kaa_patches.BLIND_CHOICE["pending"] = False
        self.blind_hits = [t for t in self.blind_hits if now - t < BLIND_WINDOW_S] + [now]
        logger.warning("选项页被拦下（第 %d 次 / %.0f 秒内）", len(self.blind_hits), BLIND_WINDOW_S)
        return len(self.blind_hits) >= BLIND_STOP

    def save(self, img, d: float) -> None:
        import cv2

        self.seq += 1
        raw = self.raw if self.raw is not None else img
        name = f"{self.seq:05d}-{time.strftime('%H%M%S')}-{self.scene}.jpg"
        cv2.imwrite(str(self.out / name), raw, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        rec = {"seq": self.seq, "t": time.strftime("%Y-%m-%d %H:%M:%S"), "file": name, "scene": self.scene, "ctx": self.ctx,
               "native": [int(raw.shape[1]), int(raw.shape[0])], "logical_diff": round(d, 1)}  # fmt: skip
        self.jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.jsonl.flush()

    def log_handler(self) -> logging.Handler:
        sampler = self

        class H(logging.Handler):
            def emit(self, record):
                if record.name.startswith(("kaa", "kotonebot")):
                    sampler.ctx = f"{record.name}: {record.getMessage()}"[:200]

        return H(level=logging.INFO)

    def watch_scene(self) -> None:
        """包 KAA ProducePage.check_scene：记下它判的页面类型（帧名 / jsonl 里用）。"""
        from kaa.tasks.produce.new.page import ProducePage

        orig = ProducePage.check_scene
        sampler = self

        def check_scene(page):
            scene = orig(page)
            sampler.scene = getattr(getattr(scene, "type", None), "name", None) or "UNKNOWN"
            return scene

        ProducePage.check_scene = check_scene


def resume(produce_module, scenario, idol: str) -> None:
    """续跑进行中的初：hiro.loop.resume.enter_produce 进培育（破弃提示只点キャンセル 并抛 ResumeBlocked），
    再用 KAA 原版 ProduceController + StandardStrategy 接着打（同 KAA resume_produce，场景按 --difficulty，不靠再開对话框认）。"""
    from hiro.loop.resume import enter_produce

    from kaa.kaa_context import clear_produce_session, init_produce_session, produce_solution
    from kaa.tasks.produce.session import ProduceSession

    how = enter_produce()
    logger.info("续跑：%s（%s）", how, scenario)
    deck = produce_module.resolve_deck(idol, produce_solution().data.card_deck_id)
    init_produce_session(ProduceSession(idol_card=idol, scenario=scenario, is_resumed=True, deck=deck))
    try:
        produce_module.ProduceController(scenario=scenario, strategy=produce_module.StandardStrategy).run()
    finally:
        clear_produce_session()


DIFFICULTY = {"regular": "REGULAR", "pro": "PRO", "master": "MASTER"}


def main() -> int:
    ap = argparse.ArgumentParser(description="初 采样驱动（KAA 标准策略打一局，一路存帧）")
    ap.add_argument("--difficulty", choices=list(DIFFICULTY), default="regular")
    ap.add_argument("--idol", default="i_card-skin-shro-3-007")
    ap.add_argument("--out", default=".temp/hajime-nia-mac/frames")
    ap.add_argument("--diff", type=float, default=4.0, help="灰度缩略图（90x160）平均差达到这个就存一帧")
    ap.add_argument("--resume", action="store_true", help="培育进行中：续跑（不开新局）；--difficulty 要和那一局一致")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")

    from hiro import kaa_env
    from hiro.device import connect
    from hiro.kaa_env import SetupMissing, init_kaa

    from kaa.config.const import HajimeScenario
    from kotonebot.backend.context import init_context, manual_context

    scenario = getattr(HajimeScenario, DIFFICULTY[args.difficulty])
    try:
        init_kaa(scenario, idol=args.idol, require_sets=True)
    except SetupMissing as e:
        print(str(e), file=sys.stderr)
        return 2
    sampler = Sampler(Path(os.path.expanduser(args.out)), args.diff)
    logging.getLogger().addHandler(sampler.log_handler())

    device = connect()
    from hiro.device import is_playcover
    from hiro.loop.landscape import LandscapeGuard

    sampler.landscape = LandscapeGuard(active=is_playcover())
    sampler.wrap_raw(device)
    init_context(target_device=device)
    manual_context().begin()
    sampler.watch_scene()

    from hiro.loop import kaa_patches

    kaa_patches.RENTAL_ATTR = kaa_env.PLAN_MAIN_ATTR  # 租借格按方案主属性挑（hiro.produce.rental）
    kaa_patches.apply()  # 破弃框只点キャンセル、编成核对、租借、不盲选第一项……（同 run_hif）
    import importlib

    from hiro.loop.resume import ResumeBlocked, finish_produce_end, guard_new

    from kaa.tasks.actions.scenes import at_home
    from kotonebot import device as kdev

    produce_module = importlib.import_module(
        "kaa.tasks.produce.produce"
    )  # KAA 原版 ProduceController + StandardStrategy
    logger.info("初 %s 采样：偶像 %s，支援卡编成 %s，回忆编成 %s，存帧 → %s", args.difficulty, args.idol, kaa_env.SUPPORT_SET,
                kaa_env.MEMORY_SET, sampler.out)  # fmt: skip
    try:
        if args.resume:
            resume(produce_module, scenario, args.idol)
        else:
            kdev.screenshot()
            if not at_home():
                finish_produce_end(go_home=True)
            guard_new()  # 别的设备有进行中的培育：KAA 会自动确认破弃，先拦住；本机有进行中的培育也停（改用 --resume）
            if produce_module.do_produce(args.idol, scenario) is False:
                # AP 不足（KAA 已点キャンセル退出）。run_hif 的自动补 AP 是按 H.I.F 按钮弹窗的，初的页面不一样：这里不自动喝，停下交人
                print("AP 不足：KAA 已点キャンセル退出。请手动喝 APドリンク（不用ジュエル）后重跑", file=sys.stderr)
                return 3
    except (ResumeBlocked, kaa_patches.RentalPending, kaa_patches.FormationMismatch) as e:
        logger.error("%s", e)
        print(str(e), file=sys.stderr)
        return 3
    except (SampleStall, SampleBlocked) as e:
        logger.error("%s", e)
        print(f"{e}\n帧在 {sampler.out}（共 {sampler.seq} 张），请人工接手", file=sys.stderr)
        return 4
    except Exception as e:  # noqa: BLE001  KAA 认不出 / 崩了：帧留着，交给人
        logger.exception("采样中断：%s", e)
        print(f"采样中断：{e}\n帧在 {sampler.out}（共 {sampler.seq} 张），请人工接手", file=sys.stderr)
        return 4
    print(f"初 {args.difficulty} 一局打完，帧 {sampler.seq} 张 → {sampler.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
