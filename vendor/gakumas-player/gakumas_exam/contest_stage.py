"""比赛赛季关卡效果（服务端下发，不在主数据里）。

    from gakumas_exam.contest_stage import attach
    attach(ex, 190)        # new_exam 之后、第一次出牌之前挂上

目前收录赛季 55 的三段（gakumas-data stages.csv 190–192 的描述），按实机记录核对过触发口径
（gakumas-tools 4ff900a 的比赛彩排）：
- 190 センス：剩余 ≤5 回合、本回合第 2、4… 张卡打出时，在卡自身效果之前 集中+4、集中强化 15%（最多 5 次）。
  实机：temari-5319 T6 第 2 张バランス感覚 集中 9 → 13 → 16；seika-5469-3 第 2 张決めポーズ 7 → 11；每回合只出一张的局不触发。
- 191 ロジック：卡的直接效果每第 4 次增加好印象时，好印象 +7，并让下一次好印象结算多算一次（最多 4 次）。
  实机：ume T8 = 第 4 次（T1 ラブリーウインク、T6/T7/T8 目線の基本）。
- 192 アノマリー：累计全力值 ≥10 后（按出牌前判定）每回合第 5 张卡打出时、卡效果之前，牌组里带全力值效果的卡
  成长 +20 分、+1 段（最多 3 次；回合开始计数清零）。实机记录里没有触发过（正例未核；时点与计数口径按 190 / 191 的实机结论类推）。
以机关身份执行（Ctx.gimmick），不当作卡 / 附魔的直接效果。
"""
from .exam import Ctx
from .master import E


class ContestStage:
    def __init__(self, ex, stage_id):
        self.ex, self.id, self.used, self.counter, self.gi_direct = ex, stage_id, 0, 0, 0

    def ctx(self):
        return Ctx(self.ex, gimmick=f'contest_stage-{self.id}')

    def turn_start(self):
        self.counter = 0

    def before_card(self):
        """出牌时、卡自身效果之前（gakumas-tools 的 cardUsed：付费后、卡效果前；条件按出这张之前的状态判定，
        计数器条件看 +1 之前的值，所以 DSL 里的 %4==3 / %5==4 = 第 4 / 第 5 次）。"""
        ex = self.ex
        if self.id == 190 and self.used < 5 and ex.remain_turn <= 5 and ex.turn_card_play_count % 2 == 1:
            self.used += 1
            ex.status.TryAddLessonBuffStatus(4, self.ctx(), False)
            ex.status.TryAddLessonBuffMultipleStatus(150, -1, self.ctx())
        if self.id == 192 and ex.counters.get('fullPowerPointGetSumCount', 0) >= 10:
            self.counter += 1
            if self.counter % 5 == 0 and self.used < 3:
                self.used += 1
                FP = E('ProduceExamEffectType')['ExamFullPowerPoint']
                cards = [c for c in ex.cards_at(6) + ex.zones['lost'] if any(e['effectType'] == FP for _, e, _ in c.play_effects())]
                ex.AddGrowEffects(cards, ['g_effect-lesson_add-20', 'g_effect-lesson_count_add-1'], self.ctx(), valid_check=False)

    def after_card(self):
        pass

    def on_direct_review(self):
        self.gi_direct += 1
        if self.gi_direct % 4 == 0 and self.used < 4:
            self.used += 1
            st = self.ex.status
            st.TryAddReviewStatus(7, self.ctx(), True)
            st.TryAddReviewCountAddStatus(1, 1, self.ctx())


def attach(ex, stage_id):
    """在这局考试实例上挂关卡效果（包装 play / start_turn / TryAddReviewStatus，不改类本身）。返回 ContestStage。"""
    stg = ContestStage(ex, stage_id)
    play, start_turn = ex.play, ex.start_turn

    def play_hooked(card, *a, **k):
        stg.before_card()
        r = play(card, *a, **k)
        stg.after_card()
        return r

    def start_turn_hooked(*a, **k):
        stg.turn_start()
        return start_turn(*a, **k)

    ex.play, ex.start_turn = play_hooked, start_turn_hooked
    if stage_id == 191:
        st = ex.status
        orig = st.TryAddReviewStatus

        def review_hooked(turn, ctx=None, isFix=False):
            b = st.GetReview()
            r = orig(turn, ctx, isFix)
            if st.GetReview() > b and ctx is not None and ctx.card is not None and ctx.enchant is None and ctx.gimmick is None:
                stg.on_direct_review()
            return r
        st.TryAddReviewStatus = review_hooked
    ex.contest_stage = stg
    return stg
