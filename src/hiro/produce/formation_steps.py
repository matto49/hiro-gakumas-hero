"""开局第 2 / 3 步的核对、自动编成、租借（不依赖 KAA）：编成方案和设备由调用方给。
旧主循环经 kaa_patches 的同名包装用（方案取 kaa_env.ACTIVE_PLAN、设备是 kotonebot）；新主循环（hiro.runtime.opening）直接用。"""

from __future__ import annotations

import logging
import os

from hiro.produce.formation_check import FormationMismatch

logger = logging.getLogger(__name__)


SUPPORT_SETTLE_READS, SUPPORT_SETTLE_GAP_S = 8, 0.5  # 翻编成号后卡片还在动画里：最多读几次、每次隔多久


def check_support(img, plan: dict | None, set_no=None, *, shoot=None, sleep=None) -> None:
    """有开局编成方案时核对第 2 步的 5 格支援卡；对不上抛 FormationMismatch（不开局）。核对本身出错只记日志。
    给了 shoot / sleep 时先等读数稳定（5 格都认出，或连续两次一样），刚翻过编成号时卡片还在动画里
    （账号 2 11-16：5 格都认不出却记成「核对通过」）。等完还是一格都认不出：核对不了，抛 FormationMismatch；
    部分认不出：记「核对不完整」，不说通过。"""
    if not plan:
        return
    from hiro.produce.formation_check import check_support_step2

    try:
        from hiro.vision.support_screens import read_support_formation

        read = read_support_formation(img)
        prev = None
        for _ in range(SUPPORT_SETTLE_READS if shoot is not None else 0):
            ids = (
                [s.get("support_card_id") for s in (read or {}).get("slots") or []]
                if (read or {}).get("view") == "step2"
                else None
            )
            if ids is not None and (all(ids) or ids == prev):
                break
            prev = ids
            (sleep or (lambda s: None))(SUPPORT_SETTLE_GAP_S)
            read = read_support_formation(shoot())
        problems, warnings = check_support_step2(read, plan)
    except Exception as e:  # noqa: BLE001
        logger.warning("支援卡编成核对失败（不拦开局）：%s", e)
        return
    for w in warnings:
        logger.warning("支援卡编成核对：%s", w)
    if problems:
        logger.error("支援卡编成和方案对不上，不开局：%s", "；".join(problems))
        raise FormationMismatch("支援卡编成和方案对不上：" + "；".join(problems))
    slots = (read or {}).get("slots") or []
    unknown = sum(1 for s in slots if not s.get("support_card_id"))
    if slots and unknown == len(slots):
        logger.error("支援卡编成 %d 格都认不出，核对不了，不开局", len(slots))
        raise FormationMismatch(f"支援卡编成 {len(slots)} 格都认不出（画面还在动画里？），核对不了")
    if unknown:
        logger.warning("支援卡编成核对不完整（编成 %s）：%d 格认不出，认出的都对", set_no, unknown)
        return
    logger.info("支援卡编成核对通过（编成 %s）", set_no)


def autofix_on(plan: dict | None) -> bool:
    """开局自动编成（produce.formation_writer）：HIRO_FORMATION_AUTOFIX=1 且有开局编成方案时才启用（默认关，执行步骤待实机验证）。"""
    return os.environ.get("HIRO_FORMATION_AUTOFIX") == "1" and bool(plan)


def autofix_support(dev, plan: dict | None) -> None:
    """第 2 步：编成和方案不一致的格子自动改（切到方案编成号之后）。dev：formation_writer.Device。失败抛 FormationMismatch。"""
    if not autofix_on(plan):
        return
    from hiro.produce.formation_writer import FormationWriteError, fix_support
    from hiro.vision.support_screens import read_support_formation, read_support_list

    try:
        done = fix_support(dev, plan, read_formation=read_support_formation, read_list=read_support_list)
    except FormationWriteError as e:
        logger.error("开局自动编成（支援卡）失败，不开局：%s", e)
        raise FormationMismatch(f"开局自动编成（支援卡）失败：{e}") from e
    if done:
        logger.info("开局自动编成（支援卡）：改了 %d 格 %s", len(done), done)


def rental_pick(plan: dict | None, attr: str | None, main_attr: str | None = None) -> bool:
    """租借格：有开局编成方案时按「加上这张后编成的价值」挑（support_select.rental_scorer，自己 5 张取方案的）；
    没有方案：主属性、等级最高（hiro.produce.rental）。设备用 rental 当前的（新主循环用 rental.use(dev) 换）。"""
    from hiro.produce import rental

    plan = plan or {}
    own = [
        {"support_card_id": o["support_card_id"], "level": o.get("level")}
        for o in (plan.get("support") or {}).get("own") or []
    ]
    scorer = None
    if own:
        from hiro.brain.support_select import rental_scorer

        scorer = rental_scorer(own, attr or main_attr, (plan.get("idol") or {}).get("plan"))
    return rental.pick_rental(attr=attr or rental.WANT_ATTR, scorer=scorer)


def memory_card_problems(img, plan: dict) -> tuple[list[str], list[str]]:
    """第 3 步四格回忆的技能卡小图标 vs 方案的回忆卡。HIRO_MEMORY_CARD_CHECK：block（默认，有把握认错就拦）/ warn / off。"""
    mode = (os.environ.get("HIRO_MEMORY_CARD_CHECK") or "block").lower()
    if mode == "off":
        return [], []
    try:
        from hiro.produce.formation_check import check_memory_cards
        from hiro.vision.memory_icons import read_step3_icons

        reads = read_step3_icons(img, expect=list((plan.get("memory") or {}).get("cards") or []))
        logger.info("第 3 步回忆图标：%s", "；".join(f"{r['slot'] + 1}:{r['card']} {r['score']}" for r in reads))
        problems, warnings = check_memory_cards(reads, plan)
    except Exception as e:  # noqa: BLE001
        return [], [f"回忆卡面核对失败（不拦开局）：{e}"]
    if mode == "warn":
        return [], warnings + [p + "（HIRO_MEMORY_CARD_CHECK=warn，不拦）" for p in problems]
    return problems, warnings


# 开局第 3 步只核对、不写回忆（协调 11-17：第 3 步选择页只露约 3 行、排序也和菜单不同，按库存行号找不准）
MEMORY_MENU_HINT = (
    "回忆请在编成菜单里写：PYTHONPATH=src python tools/write_formation.py --mode <hajime|nia|hif> --what memory"
)


STEP3_SETTLE_READS, STEP3_SETTLE_GAP_S = 8, 0.5


def settled_step3(shoot, plan: dict, sleep=None):
    """第 3 步翻完编成号后等四格回忆图标停稳，返回那一帧：连续两次读到的卡一样（含认不认得出），或者四格都认出、
    都是方案的卡就直接用。读不到稳定结果时用最后一帧（11-17 Mac p15c：翻 1 → 20 的动画还没完就读了，四格都认不出；
    0.6 秒后的帧四格全对）。"""
    from hiro.devices.active import sleep as _sleep
    from hiro.vision.memory_icons import card_key, read_step3_icons

    sleep = sleep or _sleep  # 过 Runtime 检查点（录屏暂停能拦住），不用 time.sleep（test_exec_checkpoint）
    want = {card_key(c) for c in (plan.get("memory") or {}).get("cards") or [] if card_key(c)}
    prev, img = None, None
    for i in range(STEP3_SETTLE_READS):
        img = shoot()
        cur = [(r.get("card") if r.get("known") else None) for r in read_step3_icons(img)]
        if want and all(cur) and set(cur) <= want:
            return img
        if cur == prev:
            return img
        prev = cur
        if i + 1 < STEP3_SETTLE_READS:
            sleep(STEP3_SETTLE_GAP_S)
    return img


def check_memory_set(img, plan: dict | None, set_no=None, *, shoot=None, sleep=None) -> None:
    """有开局编成方案时，选完回忆编成后读「N/20」核对编成号，再核对四格回忆卡；对不上抛 FormationMismatch
    （第 3 步不自动写回忆，提示去编成菜单写，见 MEMORY_MENU_HINT）。给了 shoot 就先等四格图标停稳（settled_step3）再核对。"""
    if not plan:
        return
    if shoot is not None:
        try:
            img = settled_step3(shoot, plan, sleep)
        except Exception as e:  # noqa: BLE001
            logger.warning("第 3 步等回忆图标停稳失败，用当前帧核对：%s", e)
    from hiro.produce.formation_check import check_memory_step3, memory_set_number
    from hiro.vision.ocr import screen_texts

    try:
        problems, warnings = check_memory_step3(
            memory_set_number([(t.text, tuple(t.rect)) for t in screen_texts(img)]), plan
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("回忆编成核对失败（不拦开局）：%s", e)
        return
    if not problems:
        p2, w2 = memory_card_problems(img, plan)
        problems, warnings = problems + p2, warnings + w2
    for w in warnings:
        logger.warning("回忆编成核对：%s", w)
    if problems:
        logger.error("回忆编成和方案对不上，不开局：%s。%s", "；".join(problems), MEMORY_MENU_HINT)
        raise FormationMismatch("回忆编成和方案对不上：" + "；".join(problems) + "。" + MEMORY_MENU_HINT)
    logger.info("回忆编成核对通过（メモリー編成 %s）", set_no)
