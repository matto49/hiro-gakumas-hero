"""培育里资源决策的模拟工具（用户 2026-11-02 第十六局复盘）：饮料拿 / 换 / 买、相談、轮间的选项按「下一场考试多拿多少分」比较。

和轮间（hiro.sim.interval，B3）同一套方法：把「这一步之后的局面」合成为下一场考试的开局，同一批种子配对打完整局，比较期望分。
- 续打策略：社区打法（policies.community_action），快，几十秒内能比完几个选项；固定考题的离线对比用实机同款专家（tools/resource/bench.py）。
  注意：社区续打第 1 回合就喝抽牌饮料（用户批注说这样转乱牌堆），会高估抽牌饮料；所以抽牌限量的默认规则要模拟显著（> 2 个标准误）才偏离。
- 牌组：状态机记账的牌组；没记账（选拔里常见）时用参考牌组 REFERENCE_DECK（第十六局选拔结束时的牌组），只用于选项之间比较。
- 考试模板：选拔 / R2 用 12 回合模板（选拔不补応援棒），R1 用 9 回合模板。倍率没有时用参考倍率。
"""

from __future__ import annotations

from hiro.sim import interval as iv

R1_TEMPLATE = "campus-20261019/r1-opening"
R2_TEMPLATE = "campus-20261019/r2-opening"
TEMPLATE_FOR = {"selection": R2_TEMPLATE, "r1": R1_TEMPLATE, "r2": R2_TEMPLATE}
# 选拔考试的三属性倍率没有现成数据时用的参考值（只影响绝对分，选项之间照样可比；第十四 / 十五局选拔③ 约 Vo 2.6 / Da 2.3 / Vi 1.6）
REFERENCE_PERMIL = {"Vo": 26000, "Da": 23000, "Vi": 16000}
# 参考牌组（第十六局本战开局牌组去掉本战才拿到的 せのび，23 张；traces/lead35-main16-20261102-132949/finals_context.json）：牌组没记账时用
REFERENCE_DECK = [
    ("p_card-02-men-2_111", 1), ("p_card-00-sup-3_027", 2), ("p_card-02-men-2_052", 0), ("p_card-02-act-2_062", 1), ("p_card-02-men-2_057", 2),
    ("p_card-02-ido-3_105", 1), ("p_card-02-men-3_002", 1), ("p_card-02-men-3_040", 1), ("p_card-02-men-1_006", 1), ("p_card-02-sup-3_178", 1),
    ("p_card-02-act-1_037", 1), ("p_card-02-men-2_004", 0), ("p_card-02-men-2_100", 1), ("p_card-02-act-3_045", 1), ("p_card-02-act-1_071", 0),
    ("p_card-02-men-1_006", 1), ("p_card-02-men-2_008", 0), ("p_card-02-men-1_006", 1), ("p_card-02-act-2_047", 0), ("p_card-02-act-3_039", 1),
    ("p_card-02-men-1_031", 1), ("p_card-02-men-2_056", 1), ("p_card-02-sup-3_094", 1),
]  # fmt: skip
SEEDS = range(100)
POLICY = "community"


def setup_for(
    exam: str, *, deck: list | None, drinks: list, hp: int, max_hp: int, permil: dict | None = None
) -> tuple[iv.R2Setup, list[str]]:
    """下一场考试（selection / r1 / r2）的合成开局。返回 (setup, 近似说明)。"""
    notes = []
    d = [tuple(c) for c in deck or []]
    if not d:
        d = list(REFERENCE_DECK)
        notes.append("牌组没记账：用参考牌组")
    pm = {a: v for a, v in (permil or {}).items() if v}
    if not pm:
        from hiro.state.knowledge import FINALS_REFERENCE_PERMIL

        pm = dict(REFERENCE_PERMIL if exam == "selection" else FINALS_REFERENCE_PERMIL)
        notes.append("倍率没有：用参考倍率")
    for a in ("Vo", "Da", "Vi"):
        pm.setdefault(a, min(pm.values()))
    setup = iv.R2Setup(deck=d, drinks=[x for x in drinks if x], hp=hp, max_hp=max_hp, permil=pm, template=TEMPLATE_FOR.get(exam, R2_TEMPLATE),
                       fill=exam == "r2")  # fmt: skip
    return setup, notes


def _eval(setups: list, seeds=SEEDS, workers: int = 4, policy: str = POLICY, **kw) -> list[dict]:
    """配对评估（第一个是基准），去掉逐局分数只留 mean / se / gain / gain_se。"""
    rs = iv.evaluate(setups, list(seeds), policy, workers=workers, **kw)
    for r in rs:
        r.pop("scores", None)
    return rs


BATCHES = (16, 32, 64, 100)  # 自适应：每批之后看最好的候选是否已经显著（≥ 32 局）或者到 100 局也不可能显著，就提前停


def adaptive_eval(
    setups: list, *, threshold: float = 2.0, batches=BATCHES, workers: int = 4, policy: str = POLICY, **kw
) -> list[dict]:
    """和 _eval 一样的配对评估（第一个是基准），但分批跑：
    - 最好的候选已经好过 threshold 个标准误（且至少 32 局）→ 停；
    - 按现在的 t 值外推到最后一批也到不了 0.75 × threshold（t × √(最后一批 / 当前局数)）→ 停；
    - 否则加局。结果多一个 n（实际局数）。显著性判断仍由调用方按 gain / gain_se 做。"""
    import math
    import statistics

    scores = [[] for _ in setups]
    done = 0
    out = []
    for n in batches:
        res = iv.evaluate(setups, list(range(done, n)), policy, workers=workers, **kw)
        for i, r in enumerate(res):
            scores[i].extend(r["scores"])
        done = n
        base = scores[0]
        out = []
        for xs in scores:
            d = [x - y for x, y in zip(xs, base)]
            out.append({"mean": round(statistics.fmean(xs)), "gain": round(statistics.fmean(d)),
                        "gain_se": round(statistics.stdev(d) / math.sqrt(n)) if n > 1 else 0, "n": n})  # fmt: skip
        others = out[1:]
        if not others:
            break
        best = max(others, key=lambda r: r["gain"])
        t = best["gain"] / max(best["gain_se"], 1)
        # 无望：按现在的 t 外推到最后一批，连 0.75 × threshold 都到不了（留余量：64 局 t = 1.47、外推 1.84 时，100 局实际是 2.06）
        if (t > threshold and n >= 32) or t * math.sqrt(batches[-1] / n) < 0.75 * threshold:
            break
    return out


def _warm(setup) -> bool:
    iv.bare_exam(setup)
    return True


_WARMED: set = set()


def prewarm(exam: str, *, deck: list | None, max_hp: int, permil: dict | None = None, workers: int = 4) -> bool:
    """牌组变化后，在后台让常驻进程池的每个进程先把这场考试编译好（约 6 秒），下一次饮料决策不用等编译（协调 2026-11-03：
    「可以在上一页的动画期间提前算」）。不阻塞；同一组合只预热一次。返回是否提交了预热。"""
    setup, _ = setup_for(exam, deck=deck, drinks=[], hp=max_hp, max_hp=max_hp, permil=permil)
    key = iv._with(setup, drinks=[], hp=setup.max_hp).key()
    if key in _WARMED or iv.sim_workers(workers) <= 1:
        return False  # 预热过 / 不开进程池（Windows 默认：spawn 子进程会重新导入整套主模块，第十五局内存耗尽）
    _WARMED.add(key)
    pool = iv._pool(workers)
    for _ in range(workers):
        pool.submit(_warm, setup)
    return True
