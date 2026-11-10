"""H.I.F 社区规则：服务端决定、客户端代码里没有公式的部分，以社区 wiki 为准。

来源快照：strategy/evidence/hif-community-20261030.json
（seesaawiki「H.I.F」2026-10-25 版、wikiwiki「HIF/基本情報」）。
每个规则标注来源等级：
- COMMUNITY：社区明文或表格，直接采用；
- COMMUNITY_FIT：社区近似式或样本，按样本分段线性插值，样本之外的外推标 ASSUMED；
- ASSUMED：社区没有，先用本地假设（待实机校准）。
客户端代码已还原的规则（商店折扣逐层截断、倍率合成）优先于社区描述，不在这里重复。

星星 → 考试倍率：社区只有「未达基准约 1.85~2 倍、正好基准 2 倍、超过后 2~3 倍封顶、亲爱度 1.5 倍后小数截断」，
没有精确公式（N.I.A 的投票倍率社区也注明「算出計算式は調査中」）。multiplier.py 的曲线满足这些边界，形状仍是本地拟合。
"""
from __future__ import annotations
import math

SOURCE='strategy/evidence/hif-community-20261030.json'

# ---------------------------------------------------------------- 相談（普通商店）
# COMMUNITY：seesaawiki 相談价格表。R 未强化不出售。表中 R+ 100 高于 SR 50，原样采用，待实机核对。
SHOP_CARD_PRICE={
    ('ProduceCardRarity_R',1):100,
    ('ProduceCardRarity_Sr',0):50,
    ('ProduceCardRarity_Sr',1):110,
    ('ProduceCardRarity_Ssr',0):100,
    ('ProduceCardRarity_Ssr',1):150,
}
DRINK_PRICE={'ProduceDrinkRarity_R':50,'ProduceDrinkRarity_Sr':75,'ProduceDrinkRarity_Ssr':100}  # COMMUNITY，相談与轮间相同
# COMMUNITY：强化、删除按本局已执行次数递增（两者分别计数：选拔记忆分别继承「強化回数・削除回数」）。第 6 次以后按 250（ASSUMED）。
SERVICE_PRICES=(100,125,150,175,200,250)
SALE_PERMIL=300  # COMMUNITY：卡与饮料各自最左一格 SALE 三折扣
# COMMUNITY：H.I.F 加成「相談のスキルカードをN%割引」Lv1–6，只对购卡（交换）生效，不对强化/删除生效。
HIF_CARD_DISCOUNT_PERMIL=(0,50,100,150,200,250,300)


def service_price(times_done):
    return SERVICE_PRICES[min(times_done,len(SERVICE_PRICES)-1)]


# ---------------------------------------------------------------- 轮间（インターバル）
# COMMUNITY：画面为 换卡购买 4 格、セレクトチェンジ 2 格、饮料 2 格，下方 强化 / 定制 / 回复。
# 不适用 H.I.F 折扣，也不触发「相談」「特別指導」相关效果。
INTERVAL_SLOTS=dict(card=4,change=2,drink=2)
INTERVAL_CARD_PRICE={
    ('ProduceCardRarity_R',1):30,
    ('ProduceCardRarity_Sr',1):50,
    ('ProduceCardRarity_Ssr',0):50,
    ('ProduceCardRarity_Ssr',1):80,
}  # 社区：交换「必ず強化済み」，但同时列出未强化 SSR 50；两者都保留
INTERVAL_CHANGE_PRICE={'ProduceCardRarity_R':30,'ProduceCardRarity_Sr':50,'ProduceCardRarity_Ssr':80}
INTERVAL_REFRESH_PRICES=(10,10,20,30,40,50)  # 之后保持 50（wikiwiki：10,10,20,30,40,50,50,50…）
INTERVAL_UPGRADES_PER_CYCLE=1  # 每次刷新后可强化 1 张、定制 2 张（刷新重置次数）
INTERVAL_CUSTOMIZES_PER_CYCLE=2


def interval_refresh_price(times_done):
    return INTERVAL_REFRESH_PRICES[min(times_done,len(INTERVAL_REFRESH_PRICES)-1)]


def interval_upgrade_price(times_done):
    """轮间强化：不继承相談次数，首次 100，每次 +25。"""
    return 100+25*times_done


def interval_recover_price(hp):
    """轮间回复：每 2 体力 10P，不足 2 也收 10P。"""
    return 10*math.ceil(hp/2)


# ---------------------------------------------------------------- 日程行动
SP_BASE_PERMIL=100  # COMMUNITY（推测）：SP 课程发生率 = 基础 10% + 发生率增加（P 手帳可见，H.I.F 加成最多 +5%）


def sp_rate(increase_permil):
    return min(1.0,(SP_BASE_PERMIL+increase_permil)/1000)


SUB_PARAMETER_MAJOR=0.75  # COMMUNITY（wikiwiki 统计）：副属性在剩余两项里，流行顺序靠前的约 75%，靠后的约 25%；开局决定、日程可见


def sub_parameter_weights(main,trend_order):
    """main：本次课程的主属性；trend_order：偶像的流行顺序（流1, 流2, 流3），例如篠澤広 ('Vo','Da','Vi')。"""
    rest=[a for a in trend_order if a!=main]
    return {rest[0]:SUB_PARAMETER_MAJOR,rest[1]:1-SUB_PARAMETER_MAJOR}


# COMMUNITY：外出内容固定，三档代价任选其一，每档都同时获得列出的全部效果（seesaawiki 与 wikiwiki 表格一致）：
# 回复最大体力 60% + 选一瓶饮料 + 选一张主效果（おすすめ効果）卡；付 50P 或接受眠気的，再多选一张强化过的主效果卡。
OUTING_OPTIONS=(
    dict(cost=dict(p=50),gains=('recover_60','pick_drink','pick_card_upgraded','pick_card')),
    dict(cost=dict(trouble=1),gains=('recover_60','pick_drink','pick_card_upgraded','pick_card')),
    dict(cost={},gains=('recover_60','pick_drink','pick_card')),
)
OUTING_RECOVER_PERMIL=600
# COMMUNITY：授業 选拔 3/6 日 120，10/17 日 150；本战 1/4 日 180（不套成长加成，参数主属性选日程时已定）。
CLASS_PARAMETER={('produce-007',3):120,('produce-007',6):120,('produce-007',10):150,('produce-007',17):150,
                 ('produce-008',1):180,('produce-008',4):180}
# COMMUNITY：授業三选一。选拔 3/6 日为「选择获得」（必为未强化），其余为セレクトチェンジ（强化状态继承，定制清空）。
# effect='A'/'B'：おすすめ効果A/B（ロジック：A=好印象，B=やる気；センス：A=好調，B=集中；アノマリー：A=強気，B=全力）。
CLASS_OPTIONS=(
    dict(cost=dict(hp=5),effect='A'),
    dict(cost=dict(hp=5),effect='B'),
    dict(cost=dict(trouble=1),effect='self'),  # 选拔 3/6 日：只提示本人おすすめ効果相关卡（社区推测）；之后：任意非麻烦卡换成其他卡
)
CLASS_GAIN_DAYS={('produce-007',3),('produce-007',6)}
PLAN_EFFECTS={'ProducePlanType_Plan1':('ExamParameterBuff','ExamLessonBuff'),        # センス：好調、集中
              'ProducePlanType_Plan2':('ExamReview','ExamCardPlayAggressive'),        # ロジック：好印象、やる気
              'ProducePlanType_Plan3':('ExamConcentration','ExamFullPower')}          # アノマリー：強気、全力
PRESENT_POINTS=80  # COMMUNITY：礼物 P 点 80 + 饮料（只出 SR/SSR）+ 技能卡


def effect_group(effect_type):
    """效果类型 → 卡牌可见效果组，例如 ExamCardPlayAggressive → effect_group-visible-exam_card_play_aggressive-000。"""
    name=effect_type.split('_',1)[-1]
    snake=''.join('_'+ch.lower() if ch.isupper() else ch for ch in name).lstrip('_')
    return f'effect_group-visible-{snake}-000'


def recommended_effect_group(idol_row):
    """偶像「おすすめ効果」（master：IdolCard.examEffectType）对应的卡牌效果组。外出只提示与之相关的卡（COMMUNITY）。"""
    return effect_group(idol_row['examEffectType'])


def is_recommended(card_row,idol_row):
    return recommended_effect_group(idol_row) in card_row['effectGroupIds']


def class_effect_group(idol_row,effect):
    """授業选项 A/B 对应的效果组（按计划类型），'self' 为本人おすすめ効果。"""
    if effect=='self':return recommended_effect_group(idol_row)
    return effect_group(PLAN_EFFECTS[idol_row['planType']][0 if effect=='A' else 1])


# ---------------------------------------------------------------- 考试后奖励
# COMMUNITY：选拔赛获得星星 = roundup(分段累计)，以下为亲爱度加成前的基础值；分段（上界分数, 每分星星）。
SELECTION_STAR_SEGMENTS={
    1:((3000,1/150),(7000,0.003),(15000,0.001)),
    2:((30000,11/6000),(70000,0.000825),(150000,0.000275)),
    3:((100000,0.00055),(200000,0.00033),(400000,0.00011)),
}
SELECTION_STAR_CAP={1:40,2:110,3:110}


def selection_star_base(exam,score):
    total=0.0;low=0
    for upper,rate in SELECTION_STAR_SEGMENTS[exam]:
        total+=rate*(min(score,upper)-low)
        if score<=upper:break
        low=upper
    return min(SELECTION_STAR_CAP[exam],math.ceil(total-1e-9))


# COMMUNITY_FIT：本战获得星星、R1 获得 P 点只有样本（区间样本取中点），按分段线性插值。
FINALS_STAR_SAMPLES={
    1:((0,0),(655,1),(135852,41),(142968,43),(172380,52),(331838,100),(363050,104),(370240,105),(414997,110),(461694,116),(478582,118)),
    2:((0,0),(643,1),(104179,20),(197560,38),(229233,43),(470063,91),(497657,97),(715913,129),(732764,130),(752572,132),
       (891092,142),(972766,148),(1042225,150)),
}
FINALS_STAR_CAP=150  # 社区确认 R2 封顶 150；R1 样本到 478582 仍在上升，封顶值未知（ASSUMED 同为 150）
R1_POINT_SAMPLES=((0,0),(655,0),(135852,118),(142968,122),(172380,137),(189257,145),(210818,154),(223541,158),(258588,168),
                  (290152,178),(299771,180),(313839,183),(320938,185),(331838,187),(358595,192),(363050,193),(366975,194),
                  (372226,195),(394152,199),(410669,200))
R1_POINT_CAP=200


def _interp(samples,x):
    for (x0,y0),(x1,y1) in zip(samples,samples[1:]):
        if x<=x1:return y0+(y1-y0)*(x-x0)/(x1-x0)
    (x0,y0),(x1,y1)=samples[-2],samples[-1]
    return y1+(y1-y0)*(x-x1)/(x1-x0)  # ASSUMED：按最后一段斜率外推


def finals_star_base(round_index,score):
    return min(FINALS_STAR_CAP,round(_interp(FINALS_STAR_SAMPLES[round_index],score)))


def r1_points(score):
    return min(R1_POINT_CAP,round(_interp(R1_POINT_SAMPLES,score)))


def star_gain(base,star_permil):
    """亲爱度等星星获得加成（合计千分比，例如亲爱度 37 为 1500），小数截断（社区：通常课程亲爱度 37 获得 7 = floor(5×1.5)）。"""
    return base*star_permil//1000


# COMMUNITY：选拔赛属性奖励。合计分数 → 上升基准值（达到上界时为 140/440/520），按 固定:配分 拆开，
# 固定部分三项均分，配分部分按 Vo/Da/Vi 各自回合得分的比例从低到高分配（带小数结转）。之后再套成长加成。
SELECTION_PARAM_CAP={1:(10463,140),2:(80611,440),3:(204894,520)}
SELECTION_PARAM_SPLIT={1:(3,4),2:(6,5),3:(15,11)}  # 固定:配分
SELECTION_PARAM_SAMPLES={  # 未达上界时的样本（社区暂定版只有样本）
    1:((0,0),(3037,46),(4325,62),(7506,124),(8860,133),(9442,138),(9695,139),(10463,140)),
    2:((0,0),(31853,176),(49578,347),(52479,362),(56496,373),(57916,378),(71196,417),(80611,440)),
    3:((0,0),(44370,118),(73660,193),(107167,300),(136238,426),(158365,461),(160875,463),(164864,468),(195234,515),(204894,520)),
}


def selection_param_base(exam,score):
    threshold,cap=SELECTION_PARAM_CAP[exam]
    return cap if score>=threshold else round(_interp(SELECTION_PARAM_SAMPLES[exam],score))


def selection_param_split(exam,turn_scores):
    """turn_scores：{'Vo':..,'Da':..,'Vi':..} 各属性回合的得分。返回加成前的三项上升值（社区公式，未达上限部分为社区暂定版）：
    r = 上升基准值 / 上限；下位 = floor(r×(固定上限/3 + 配分上限×下位分/合计))，小数结转给中位；上位 = 基准值 − 下位 − 中位。"""
    total=sum(turn_scores.values())
    base=selection_param_base(exam,total)
    cap=SELECTION_PARAM_CAP[exam][1]
    f,d=SELECTION_PARAM_SPLIT[exam]
    fixed_cap=cap*f//(f+d);dist_cap=cap-fixed_cap
    r=base/cap
    if total<=0:
        each=base//3;return {k:each for k in turn_scores}
    order=sorted(turn_scores,key=lambda k:turn_scores[k])
    out={};carry=0.0
    for k in order[:2]:
        v=carry+r*(fixed_cap/3+dist_cap*turn_scores[k]/total);whole=math.floor(v+1e-9);carry=v-whole
        out[k]=whole
    out[order[2]]=base-sum(out.values())
    return out


# COMMUNITY：选拔第 1、2 场通过后（取得/定制 P 道具之前）各随机删除 2 张名字含「基本」的卡；随机方式未知（ASSUMED 均匀）。
SELECTION_BASIC_REMOVALS={1:2,2:2,3:0}
