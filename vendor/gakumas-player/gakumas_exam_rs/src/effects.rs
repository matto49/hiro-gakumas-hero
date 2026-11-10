//! 效果执行器（gakumas_exam/effects.py）：按 ProduceExamEffectType 分派。

use crate::card::{Card, CardId};
use crate::enums::{cls, et, ph};
use crate::exam::{ck, Cmd, Ctx, Exam};
use crate::master::{EffectRow, TriggerRow};
use crate::num::{ceil_i, floor_i, i2f, permil, ratio_int, trunc_i, EPS32, INT_MAX, NEG_EPS32};
use crate::status::{rules, Rule, Status};
use crate::sym::NONE;
use std::sync::Arc;

/// CalculateAddingParameter 的倍率附加（_lesson_with_rate）
#[derive(Clone, Copy, Default)]
pub struct Add {
    pub pb: Option<f32>,
    pub conc: Option<f32>,
    pub full: Option<f32>,
    pub enth: Option<f32>,
}

#[derive(Clone, Copy)]
enum Res {
    Review,
    Aggressive,
    ParameterBuff,
    LessonBuff,
    Stamina,
    Block,
}

impl Exam {
    pub fn calculate_adding_parameter(&self, value: i64, add: Option<Add>) -> i64 {
        let sc = &self.status;
        let st = &self.sk;
        if sc.has(cls::SlumpStatusEffect) {
            return 0;
        }
        let mut pb = 1.0f32;
        if sc.has(cls::ParameterBuffStatusEffect) {
            let mut x = permil(st.param_buff) + -1.0f32;
            if sc.has(cls::ParameterBuffMultiplePerTurnStatusEffect) {
                x += i2f(st.param_buff_mpt * sc.parameter_buff_turn()) / 1000.0f32;
            }
            if let Some(a) = add {
                x *= a.pb.unwrap_or(1.0);
            }
            pb = x + 1.0f32;
        }
        let mut dbf = i2f(if sc.has(cls::ParameterDebuffStatusEffect) { st.gimmick_param_debuff } else { 1000 });
        let focus = sc.lesson_buff();
        let focus_dn = sc.lesson_debuff();
        let mut enth = sc.enthusiastic();
        let lpm = sc.multiple(cls::LessonParameterMultipleStatusEffect);
        let down = sc.multiple0(cls::LessonParameterMultipleDownStatusEffect);
        let down_f = (1.0f32 - down).max(0.0);
        let mut dep = 0.0f32;
        if sc.has(cls::LessonParameterMultipleDependReviewOrAggressiveStatusEffect) {
            let r = sc.review().min(sc.aggressive());
            dep = permil(st.depend_mult) * i2f(r);
            dep = permil(st.depend_max).min(dep);
        }
        let lbm = sc.multiple(cls::LessonBuffMultipleStatusEffect);
        let mut stance = 1.0f32;
        let (t, step) = (sc.idol_type, sc.idol_step);
        if t == 1 {
            let p = if step == 1 { st.conc_lesson1 } else { st.conc_lesson2 };
            let rate = add.and_then(|a| a.conc).unwrap_or(1.0);
            stance = ((permil(p) + -1.0f32) + sc.multiple0(cls::ConcentrationLessonMultipleAdditiveStatusEffect)) * ((rate + -1.0f32) + 1.0f32) + 1.0f32;
        } else if t == 2 {
            let p = if step == 1 { st.pres_lesson1 } else { st.pres_lesson2 };
            stance = permil(p);
        } else if t == 3 {
            let rate = add.and_then(|a| a.full).unwrap_or(1.0);
            stance = ((permil(st.full_lesson) + -1.0f32) + sc.multiple0(cls::FullPowerLessonMultipleAdditiveStatusEffect)) * ((rate + -1.0f32) + 1.0f32)
                + 1.0f32;
        } else if t == 4 {
            stance = i2f(st.over_pres_lesson);
        }
        if let Some(e) = add.and_then(|a| a.enth) {
            enth = ceil_i(e * i2f(enth));
        }
        dbf /= 1000.0f32;
        let mut v = value;
        let c = sc.lesson_change_more(v);
        if c >= 0 {
            v = c;
        }
        let c = sc.lesson_change_less(v);
        if c >= 0 {
            v = c;
        }
        let base = (ceil_i(lbm * i2f(focus)) - focus_dn + enth + v).max(0);
        let mut r = (base as f64 as f32) * pb;
        r *= dbf;
        r = (lpm + dep) * r;
        r = down_f * r;
        r = stance * r;
        ceil_i(r + EPS32).min(INT_MAX)
    }

    pub fn add_parameter_fix(&mut self, v: i64) {
        let mut v = v;
        if self.exam_type != 0 {
            let t = self.current_parameter_type();
            let bonus = if (1..=3).contains(&t) { self.bonus_permil[t as usize].unwrap_or(1000) } else { 1000 };
            v = ceil_i(permil(bonus) * (v as f64 as f32) + EPS32);
        }
        v = v.min(INT_MAX);
        self.score += v;
        self.counters.add(ck::CurrentTurnTotalAddParameter, v);
    }

    pub fn add_parameter(&mut self, v: i64) {
        let v = if self.status.has(cls::SlumpStatusEffect) { 0 } else { v };
        self.add_parameter_fix(v);
    }

    pub fn calculate_add_block(&self, value: i64, rate: f32) -> i64 {
        let sc = &self.status;
        if value < 1 {
            return 0;
        }
        if sc.has(cls::BlockRestrictionStatusEffect) {
            return 0;
        }
        let mut v = ceil_i(i2f(sc.aggressive()) * rate) + value;
        if v < 1 {
            return v.max(0);
        }
        let restr = sc.has(cls::BlockAddDownRestrictionStatusEffect);
        if sc.has(cls::BlockAddDownStatusEffect) && !restr {
            v -= ratio_int(v, trunc_i(1000.0f32 - i2f(self.sk.block_add_down)), true);
            if v < 1 {
                return v.max(0);
            }
        }
        if sc.block_add_down_fix() >= 1 && !restr {
            v -= sc.block_add_down_fix();
        }
        v.max(0)
    }

    pub fn add_block_fix(&mut self, value: i64) {
        let d = (-self.block).max(value);
        let n = self.block + d;
        self.set_block(n, false);
    }

    fn calculate_stamina_recover(&self, value: i64) -> i64 {
        if self.status.has(cls::StaminaRecoverRestrictionStatusEffect) {
            0
        } else {
            value
        }
    }

    fn add_stamina_fix(&mut self, v: i64) {
        self.stamina = (self.stamina + v).min(self.max_stamina).max(0);
    }

    pub fn add_full_power_point(&mut self, v: i64, ctx: Option<Ctx>) {
        let before = self.status.full_power_point();
        if self.try_add(&rules::FullPowerPoint, v, -1, ctx, false) {
            let d = self.status.full_power_point() - before;
            self.counters.add(ck::FullPowerPointGetSum, d);
        }
    }

    fn search_n(&mut self, e: &EffectRow) -> i64 {
        if e.search == NONE {
            return 0;
        }
        let s = self.search(e.search);
        self.get_search_card_list(s.as_deref(), None).len() as i64
    }

    fn pick(&mut self, e: &EffectRow, ctx: Option<Ctx>) -> Vec<CardId> {
        let s = if e.search != NONE { self.search(e.search) } else { None };
        self.pick_search_cards(s.as_deref(), e.pick_range, e.pick_min, e.pick_max, ctx)
    }

    fn depend(&mut self, e: &EffectRow, res: Res) {
        let sc = &self.status;
        let x = match res {
            Res::Review => sc.review(),
            Res::Aggressive => sc.aggressive(),
            Res::ParameterBuff => sc.parameter_buff_turn(),
            Res::Stamina => self.stamina,
            _ => self.block,
        };
        let rate = i2f(e.v1) / 1000.0f32;
        let base = ceil_i(rate * i2f(x) + EPS32);
        for _ in 0..e.count.max(0) {
            let v = self.calculate_adding_parameter(base, None);
            self.add_parameter(v);
        }
        let n = ceil_i((1.0f32 - permil(e.v2)) * i2f(x));
        if n == x {
            return;
        }
        match res {
            Res::Review => self.status.set_review(n),
            Res::Aggressive => self.status.set_aggressive(n),
            Res::ParameterBuff => self.status.set_parameter_buff(n),
            Res::Stamina => {
                let before = self.stamina;
                self.stamina = n;
                self.add_current_turn_consume_stamina(before - n);
            }
            _ => self.set_block(n, true),
        }
    }

    fn convert(&mut self, e: &EffectRow, src: Res, dst: Res, ctx: Option<Ctx>) {
        let sc = &self.status;
        let x = match src {
            Res::ParameterBuff => sc.parameter_buff_turn(),
            Res::LessonBuff => sc.lesson_buff(),
            Res::Review => sc.review(),
            Res::Aggressive => sc.aggressive(),
            _ => self.block,
        };
        let v = ceil_i((i2f(e.v1) / 1000.0f32) * i2f(x) + EPS32);
        let times = if matches!(dst, Res::LessonBuff | Res::ParameterBuff) || (matches!(src, Res::Review) && matches!(dst, Res::Aggressive)) {
            1
        } else {
            e.count
        };
        for _ in 0..times.max(0) {
            match dst {
                Res::LessonBuff => {
                    self.try_add_value(&rules::LessonBuff, v, ctx);
                }
                Res::ParameterBuff => {
                    self.try_add_turn(&rules::ParameterBuff, v, ctx);
                }
                Res::Review => {
                    self.try_add_review(v, ctx, false);
                }
                Res::Aggressive => {
                    self.try_add_value(&rules::Aggressive, v, ctx);
                }
                Res::Block => {
                    let b = self.calculate_add_block(v, 1.0);
                    self.add_block_fix(b);
                }
                _ => {}
            }
        }
        let n = ceil_i((1.0f32 - permil(e.v2)) * i2f(x));
        if let Res::Block = src {
            if x == n {
                return;
            }
            self.set_block(n, true);
            return;
        }
        if x - n == 0 {
            return;
        }
        match src {
            Res::ParameterBuff => self.status.consume_turn(cls::ParameterBuffStatusEffect, x - n),
            Res::LessonBuff => self.status.consume_value(cls::LessonBuffStatusEffect, x - n),
            Res::Review => self.consume_review(x - n),
            Res::Aggressive => self.status.consume_value(cls::AggressiveStatusEffect, x - n),
            _ => {}
        }
    }

    fn status_enchant(&mut self, e: &EffectRow, ctx: Option<Ctx>) {
        let m = self.m.clone();
        let en = m.exam_enchants.get(&e.status_enchant).unwrap_or_else(|| panic!("ProduceExamStatusEnchant {} 不存在", crate::sym::name(e.status_enchant)));
        let trig = m.trigger(en.trigger).unwrap_or_else(|| Arc::new(TriggerRow::default()));
        let effects: Arc<Vec<Arc<EffectRow>>> = Arc::new(en.effects.iter().map(|i| m.effect(*i)).collect());
        let limit = if e.count > 0 { e.count } else { -1 };
        let in_turn = if e.v1 > 0 { e.v1 } else { -1 };
        self.try_add_trigger_effect(trig, effects, 1, e.turn, limit, in_turn, en.id, ctx, 31);
    }

    fn effect_timer(&mut self, e: &EffectRow, ctx: Option<Ctx>, phase: i64) {
        let mut chain: Vec<Arc<EffectRow>> = e.chain_ids.iter().map(|i| self.m.effect(*i)).collect();
        if chain.is_empty() && e.chain_id != NONE {
            chain = vec![self.m.effect(e.chain_id)];
        }
        if chain.is_empty() {
            return;
        }
        let trig = Arc::new(TriggerRow { phase_types: vec![phase], phase_values: vec![e.v1], ..Default::default() });
        self.try_add_trigger_effect(trig, Arc::new(chain), 2, -1, 1, -1, NONE, ctx, 30);
    }

    fn lesson_n(&mut self, base: i64, times: i64, add: Option<Add>) {
        for _ in 0..times.max(0) {
            let v = self.calculate_adding_parameter(base, add);
            self.add_parameter(v);
        }
    }

    fn stance_exec(&mut self, stance: i64, fixed_step: Option<i64>, e: &EffectRow, ctx: Option<Ctx>) {
        if (stance == 1 || stance == 2 || stance == 4) && self.status.idol_type == 3 {
            return;
        }
        let ok = self.set_stance(stance, fixed_step.unwrap_or(e.v1), ctx, false);
        if ok && stance == 3 {
            self.full_power_hold_to_hand(ctx);
        }
    }

    pub fn execute_effect(&mut self, e: &Arc<EffectRow>, ctx: Option<Ctx>) {
        let t = e.effect_type;
        let tt = |s: &mut Exam, rule: &Rule| {
            s.try_add_turn(rule, e.turn, ctx);
        };
        let vt = |s: &mut Exam, rule: &Rule| {
            s.try_add_vt(rule, e.v1, e.turn, ctx);
        };
        match t {
            et::ExamCardCreateId => {
                let mut n = if e.pick_min != 0 { e.pick_min } else { 1 };
                if e.pick_max != 0 && e.pick_max != n {
                    n = self.rng.integer(n, e.pick_max);
                }
                let mut ids = vec![];
                for _ in 0..n.max(0) {
                    let g = self.new_guid();
                    ids.push(self.add_card_obj(Card::new(e.target_card, e.target_upgrade, vec![], g)));
                }
                self.add_card(&ids, e.move_pos, ctx);
            }
            et::ExamCardMove => {
                let cards = self.pick(e, ctx);
                self.move_card(&cards, e.move_pos, ctx);
            }
            et::ExamLesson => self.lesson_n(e.v1, e.count, None),
            et::ExamLessonFix => {
                for _ in 0..e.count.max(0) {
                    self.add_parameter_fix(e.v1);
                }
            }
            et::ExamBlock => {
                let b = self.calculate_add_block(e.v1, 1.0);
                self.add_block_fix(b);
            }
            et::ExamBlockFix => self.add_block_fix(e.v1),
            et::ExamReview => {
                self.try_add_review(e.v1, ctx, false);
            }
            et::ExamCardPlayAggressive => {
                self.try_add_value(&rules::Aggressive, e.v1, ctx);
            }
            et::ExamLessonBuff => {
                self.try_add_value(&rules::LessonBuff, e.v1, ctx);
            }
            et::ExamParameterBuff => tt(self, &rules::ParameterBuff),
            et::ExamParameterBuffMultiplePerTurn => tt(self, &rules::ParameterBuffMultiplePerTurn),
            et::ExamPlayableValueAdd => self.status.try_add_playable_value_add(e.count),
            et::ExamCardDraw => {
                let got = self.draw_card(e.v1, ctx, true);
                self.counters.add(ck::TotalDrawCardCount, got.len() as i64);
            }
            et::ExamStaminaRecoverFix => {
                let v = self.calculate_stamina_recover(e.v1);
                self.add_stamina_fix(v);
            }
            et::ExamStaminaRecoverMultiple => {
                let v = ceil_i((i2f(self.max_stamina) * i2f(e.v1)) / 1000.0f32);
                let v = self.calculate_stamina_recover(v);
                self.add_stamina_fix(v);
            }
            et::ExamStaminaReduceFix => {
                let v = e.v1;
                if v < 1 {
                    return;
                }
                if !self.reduce_stamina_fix(v) {
                    return;
                }
                if let Some(i) = self.status.first_idx(cls::UpliftingStatusEffect) {
                    if self.status.effects[i].value >= 1 {
                        self.status.effects[i].value -= 1;
                        if self.status.effects[i].value <= 0 {
                            self.status.effects.remove(i);
                        }
                    }
                }
            }
            et::ExamStaminaReduce => {
                let v = e.v1;
                if v < 1 {
                    return;
                }
                let r = (v as f64 as f32) / 1000.0f32 + NEG_EPS32;
                let red = trunc_i(r * i2f(self.max_stamina));
                let mut n = i2f(self.stamina - red).max(0.0);
                n = n.min(i2f(self.max_stamina));
                let n = trunc_i(n);
                if n == self.stamina {
                    return;
                }
                let before = self.stamina;
                self.stamina = n;
                self.add_current_turn_consume_stamina(before - n);
            }
            et::ExamLessonValueMultiple => vt(self, &rules::LessonParameterMultiple),
            et::ExamLessonDependExamReview => self.depend(e, Res::Review),
            et::ExamLessonDependExamCardPlayAggressive => self.depend(e, Res::Aggressive),
            et::ExamLessonDependBlock => self.depend(e, Res::Block),
            et::ExamLessonDependParameterBuff => self.depend(e, Res::ParameterBuff),
            et::ExamLessonDependStamina => self.depend(e, Res::Stamina),
            et::ExamBlockAddMultipleAggressive => {
                let v = self.calculate_add_block(e.v1, permil(e.v2) + 1.0f32);
                for _ in 0..e.count.max(0) {
                    self.add_block_fix(v);
                }
            }
            et::ExamCardSearchEffectPlayCountBuff => {
                let mut st = Status::new(cls::PlayCountBuffStatusEffect, e.turn, e.v1, e.turn >= 0, false);
                st.extra.count = Some(e.count);
                st.extra.search_id = Some(e.search);
                self.status.add(st);
            }
            et::ExamHandGraveCountCardDraw => {
                let cards = self.hand.clone();
                self.move_card(&cards, crate::enums::mv::Grave, ctx);
                self.draw_card(cards.len() as i64, ctx, true);
            }
            et::ExamCardUpgrade => {
                let picked = self.pick(e, ctx);
                let cards: Vec<CardId> =
                    picked.into_iter().filter(|&c| self.cards[c].upgrade_raw() == 0 && self.m.card(self.cards[c].card_id, 1).is_some()).collect();
                for c in cards {
                    self.cards[c].tmp_upgrade = 1;
                    self.cards[c].invalidate();
                }
            }
            et::ExamStatusEnchant => self.status_enchant(e, ctx),
            et::ExamEffectTimer => self.effect_timer(e, ctx, ph::ExamTurnTimer),
            et::ExamEffectTimerEndTurn => self.effect_timer(e, ctx, ph::ExamEndTurnTimer),
            et::ExamReviewValueMultiple | et::ExamAggressiveValueMultiple => {
                let review = t == et::ExamReviewValueMultiple;
                let x = if review { self.status.review() } else { self.status.aggressive() };
                if x == 0 {
                    return;
                }
                let v = ceil_i(permil(e.v1) * i2f(x));
                if review {
                    self.try_add_review(v, ctx, true);
                } else {
                    self.try_add(&rules::Aggressive, v, -1, ctx, true);
                }
            }
            et::ExamReviewMultiple => vt(self, &rules::ReviewMultiple),
            et::ExamReviewAdditive => vt(self, &rules::ReviewAdditive),
            et::ExamStaminaConsumptionDown => tt(self, &rules::StaminaConsumptionDown),
            et::ExamStaminaConsumptionAdd => tt(self, &rules::StaminaConsumptionAdd),
            et::ExamStaminaConsumptionDownFix => vt(self, &rules::StaminaConsumptionDownFix),
            et::ExamBlockRestriction => tt(self, &rules::BlockRestriction),
            et::ExamAntiDebuff => {
                self.try_add_value(&rules::AntiDebuff, e.count, ctx);
            }
            et::ExamExtraTurn => {
                self.extra_turn += 1;
                self.remain_turn += 1;
            }
            // ---- TURN_ONLY
            et::ExamGimmickSlump => tt(self, &rules::Slump),
            et::ExamBlockAddDown => tt(self, &rules::BlockAddDown),
            et::ExamGimmickParameterDebuff => tt(self, &rules::ParameterDebuff),
            et::ExamStaminaRecoverRestriction => tt(self, &rules::StaminaRecoverRestriction),
            et::ExamLessonValueMultipleDependReviewOrAggressive => tt(self, &rules::LessonParameterMultipleDependReviewOrAggressive),
            et::ExamBuffConsumptionAdd => tt(self, &rules::BuffConsumptionAdd),
            et::StanceLock => tt(self, &rules::StanceLock),
            et::ExamPanic => tt(self, &rules::Panic),
            et::ExamBlockAddDownRestriction => tt(self, &rules::BlockAddDownRestriction),
            et::ExamBuffConsumptionDown => tt(self, &rules::BuffConsumptionDown),
            et::ExamParameterBuffTurnEndReduceLock => tt(self, &rules::ParameterBuffTurnEndReduceLock),
            et::ExamReviewTurnEndReduceLock => tt(self, &rules::ReviewTurnEndReduceLock),
            et::ExamStaminaConsumptionAddDown => tt(self, &rules::StaminaConsumptionAddDown),
            et::ExamStaminaConsumptionDownAdd => tt(self, &rules::StaminaConsumptionDownAdd),
            et::ExamStanceLockConcentration => tt(self, &rules::StanceLockConcentration),
            et::ExamStanceLockFullPower => tt(self, &rules::StanceLockFullPower),
            et::ExamStanceLockPreservation => tt(self, &rules::StanceLockPreservation),
            et::ExamGetCardUpgrade => tt(self, &rules::GetCardUpgrade),
            // ---- VALUE_TURN
            et::ExamLessonValueMultipleDown => vt(self, &rules::LessonParameterMultipleDown),
            et::ExamEnthusiasticAdditive => vt(self, &rules::EnthusiasticAdditive),
            et::ExamLessonBuffAdditive => vt(self, &rules::LessonBuffAdditive),
            et::ExamGimmickStartTurnCardDrawDown => vt(self, &rules::StartTurnCardDrawDown),
            et::ExamEnthusiasticMultiple => vt(self, &rules::EnthusiasticMultiple),
            et::ExamFullPowerPointAdditive => vt(self, &rules::FullPowerPointAdditive),
            et::ExamLessonBuffMultiple => vt(self, &rules::LessonBuffMultiple),
            et::ExamGimmickLessonDebuff => vt(self, &rules::LessonDebuff),
            et::ExamAggressiveAdditive => vt(self, &rules::AggressiveAdditive),
            et::ExamFullPowerLessonMultipleAdditive => vt(self, &rules::FullPowerLessonMultipleAdditive),
            et::ExamReviewCountAdd => vt(self, &rules::ReviewCountAdd),
            et::ExamParameterBuffAdditive => vt(self, &rules::ParameterBuffAdditive),
            et::ExamLessonBuffAdditiveFix => vt(self, &rules::LessonBuffAdditiveFix),
            et::ExamConcentrationLessonMultipleAdditive => vt(self, &rules::ConcentrationLessonMultipleAdditive),
            et::ExamAggressiveAdditiveFix => vt(self, &rules::AggressiveAdditiveFix),
            et::ExamParameterBuffAdditiveFix => vt(self, &rules::ParameterBuffAdditiveFix),
            et::ExamGrowEffectLessonAddAdditive => vt(self, &rules::GrowEffectLessonAddAdditive),
            et::ExamFullPowerPointAdditiveFix => vt(self, &rules::FullPowerPointAdditiveFix),
            et::ExamGimmickEnthusiastic => vt(self, &rules::Enthusiastic),
            et::ExamReviewAdditiveFix => vt(self, &rules::ReviewAdditiveFix),
            et::ExamStaminaReduceChange => vt(self, &rules::StaminaReduceChange),
            et::ExamStaminaConsumptionAddFix => {
                self.try_add_vt(&rules::StaminaConsumptionAddFix, e.v1.max(0), e.turn, ctx);
            }
            // ---- VALUE_ONLY
            et::ExamGimmickSleepy => {
                self.try_add_value(&rules::BlockAddDownFix, e.v1, ctx);
            }
            et::ExamEnthusiasticTurnAdd => self.status.try_add_enthusiastic_turn_additive(e.v1),
            et::ExamStaminaRecoverAdd => {
                self.try_add_vt(&rules::StaminaRecoverAdd, e.v1, -1, ctx);
            }
            et::ExamUplifting => {
                self.try_add_value(&rules::Uplifting, e.v1, ctx);
            }
            et::ExamHandHold => {
                self.try_add_turn(&rules::HandHold, 1, ctx);
            }
            // ---- 打分变体
            et::ExamMultipleLessonBuffLesson => {
                let lb = self.status.lesson_buff();
                let lbm = self.status.multiple(cls::LessonBuffMultipleStatusEffect);
                let add = ceil_i((lbm * i2f(e.v2 * lb)) / 1000.0f32);
                self.lesson_n(add + e.v1, e.count, None);
            }
            et::ExamLessonAddMultipleParameterBuff
            | et::ExamMultipleEnthusiasticLesson
            | et::ExamMultipleConcentrationLesson
            | et::ExamMultipleFullPowerLesson => {
                let rate = permil(e.v2) + 1.0f32;
                let mut a = Add::default();
                match t {
                    et::ExamLessonAddMultipleParameterBuff => a.pb = Some(rate),
                    et::ExamMultipleEnthusiasticLesson => a.enth = Some(rate),
                    et::ExamMultipleConcentrationLesson => a.conc = Some(rate),
                    _ => a.full = Some(rate),
                }
                let v = self.calculate_adding_parameter(e.v1, Some(a));
                for _ in 0..e.count.max(0) {
                    self.add_parameter(v);
                }
            }
            et::ExamLessonAddMultipleLessonBuff => {
                let lb = self.status.lesson_buff();
                let v = ceil_i(i2f(e.v1 * lb) / 1000.0f32);
                self.try_add(&rules::LessonBuff, v, -1, ctx, true);
            }
            et::ExamLessonFullPowerPoint => {
                let total = self.counters.get(ck::FullPowerPointGetSum);
                for _ in 0..e.count.max(0) {
                    let base = e.v1 + floor_i(permil(e.v2) * i2f(total));
                    let v = self.calculate_adding_parameter(base, None);
                    self.add_parameter(v);
                }
            }
            et::ExamLessonAddBlock => {
                for _ in 0..e.count.max(0) {
                    let v = self.calculate_adding_parameter(e.v1, None);
                    self.add_parameter(v);
                    if v >= 1 {
                        let b = self.calculate_add_block(v, 1.0);
                        self.add_block_fix(b);
                    }
                }
            }
            et::ExamLessonPerSearchCount => {
                let n = self.search_n(e);
                let base = e.v1 + ceil_i(permil(e.v2) * i2f(n));
                self.lesson_n(base, e.count, None);
            }
            // ---- 依存 B / C
            et::ExamLessonDependStaminaConsumptionSum => {
                let base = ceil_i((i2f(e.v1) / 1000.0f32) * i2f(self.counters.get(ck::TotalConsumeStamina)));
                self.lesson_n(base, e.count, None);
            }
            et::ExamLessonDependBlockConsumptionSum => {
                let base = ceil_i(permil(e.v1) * i2f(self.counters.get(ck::BlockConsumptionSum)));
                self.lesson_n(base, e.count, None);
            }
            et::ExamLessonDependPlayCardCountSum => {
                let base = e.v1 + e.v2 * self.exam_card_play_count;
                self.lesson_n(base, e.count, None);
            }
            et::ExamLessonDependEnthusiasticGetSum => {
                let base = ceil_i(permil(e.v2) * i2f(self.counters.get(ck::EnthusiasticGetSum))) + e.v1;
                self.lesson_n(base, e.count.max(1), None);
            }
            et::ExamLessonDependBlockAndSearchCount | et::ExamLessonDependReviewAndSearchCount | et::ExamLessonDependAggressiveAndSearchCount => {
                let x = if t == et::ExamLessonDependBlockAndSearchCount {
                    self.block
                } else if t == et::ExamLessonDependReviewAndSearchCount {
                    self.status.review()
                } else {
                    self.status.aggressive()
                };
                let n = self.search_n(e);
                let rate = i2f(e.v1) / 1000.0f32 + permil(e.v2) * i2f(n);
                let base = ceil_i(rate * i2f(x) + EPS32);
                self.lesson_n(base, e.count, None);
            }
            // ---- 状态互转 / 累计
            et::ExamLessonBuffDependParameterBuff => self.convert(e, Res::ParameterBuff, Res::LessonBuff, ctx),
            et::ExamParameterBuffDependLessonBuff => self.convert(e, Res::LessonBuff, Res::ParameterBuff, ctx),
            et::ExamReviewDependExamBlock => self.convert(e, Res::Block, Res::Review, ctx),
            et::ExamBlockDependExamReview => self.convert(e, Res::Review, Res::Block, ctx),
            et::ExamReviewDependExamCardPlayAggressive => self.convert(e, Res::Aggressive, Res::Review, ctx),
            et::ExamAggressiveDependReview => self.convert(e, Res::Review, Res::Aggressive, ctx),
            et::ExamBlockDependBlockConsumptionSum => {
                let v = ceil_i(permil(e.v1) * i2f(self.counters.get(ck::BlockConsumptionSum)));
                let b = self.calculate_add_block(v, 1.0);
                self.add_block_fix(b);
            }
            et::ExamReviewDependReviewConsumptionSum => {
                let v = ceil_i(permil(e.v1) * i2f(self.counters.get(ck::ReviewConsumptionSum)));
                self.try_add_review(v, ctx, false);
            }
            et::ExamFullPowerPointDependFullPowerPointGetSum => {
                let v = ceil_i(permil(e.v1) * i2f(self.counters.get(ck::FullPowerPointGetSum)));
                self.add_full_power_point(v, ctx);
            }
            et::ExamBlockPerUseCardCount => {
                let v = e.v1 + self.exam_card_play_count * e.v2;
                let b = self.calculate_add_block(v, 1.0);
                self.add_block_fix(b);
            }
            et::ExamLessonBuffPerSearchCount
            | et::ExamReviewPerSearchCount
            | et::ExamParameterBuffPerSearchCount
            | et::ExamAggressivePerSearchCount
            | et::ExamBlockPerSearchCount
            | et::ExamFullPowerPointPerSearchCount => {
                let n = self.search_n(e);
                let v = e.v1 + ceil_i(permil(e.v2) * i2f(n));
                match t {
                    et::ExamLessonBuffPerSearchCount => {
                        self.try_add_value(&rules::LessonBuff, v, ctx);
                    }
                    et::ExamReviewPerSearchCount => {
                        self.try_add_review(v, ctx, false);
                    }
                    et::ExamParameterBuffPerSearchCount => {
                        self.try_add_turn(&rules::ParameterBuff, v, ctx);
                    }
                    et::ExamAggressivePerSearchCount => {
                        self.try_add_value(&rules::Aggressive, v, ctx);
                    }
                    et::ExamBlockPerSearchCount => {
                        let b = self.calculate_add_block(v, 1.0);
                        self.add_block_fix(b);
                    }
                    _ => self.add_full_power_point(v, ctx),
                }
            }
            et::ExamEffectPerSearchCount => {
                let n = self.search_n(e);
                let times = ceil_i(permil(e.v2) * i2f(n));
                if times < 1 || e.chain_id == NONE {
                    return;
                }
                let ce = self.m.effect(e.chain_id);
                self.insert((0..times).map(|_| Cmd::Effect(ce.clone(), ctx)).collect());
            }
            et::ExamChainEffect | et::ExamChainEffectPerPassedTurn | et::ExamChainEffectPerRemainTurn => {
                let k = if t == et::ExamChainEffect {
                    e.v1
                } else if t == et::ExamChainEffectPerPassedTurn {
                    self.current_turn
                } else {
                    self.remain_turn
                };
                if k < 1 || e.chain_id == NONE {
                    return;
                }
                let ce = self.m.effect(e.chain_id);
                self.insert((0..k).map(|_| Cmd::Effect(ce.clone(), ctx)).collect());
            }
            // ---- 减少
            et::ExamParameterBuffReduce => self.status.consume_turn(cls::ParameterBuffStatusEffect, e.v1),
            et::ExamAggressiveReduce => self.status.consume_value(cls::AggressiveStatusEffect, e.v1),
            et::ExamReviewReduce => self.consume_review(e.v1),
            et::ExamLessonBuffReduce => self.status.consume_value(cls::LessonBuffStatusEffect, e.v1),
            et::ExamParameterBuffMultiplePerTurnReduce => self.status.consume_turn(cls::ParameterBuffMultiplePerTurnStatusEffect, e.v1),
            et::ExamFullPowerPointReduce => self.status.consume_value(cls::FullPowerPointStatusEffect, e.v1),
            // ---- 卡
            et::ExamCardCreateSearch => {
                let copies = e.v1.max(1);
                let found = self.pick(e, ctx);
                let mut out = vec![];
                for src in found {
                    for _ in 0..copies {
                        let (cid, up, cc) = (self.cards[src].card_id, self.cards[src].upgrade_raw(), self.cards[src].customize_counts.clone());
                        let g = self.new_guid();
                        out.push(self.add_card_obj(Card::new(cid, up, cc, g)));
                    }
                }
                self.add_card(&out, e.move_pos, ctx);
            }
            et::ExamCardDuplicate => {
                let copies = e.v1.max(1);
                let found = self.pick(e, ctx);
                for src in found {
                    let mut c = self.cards[src].clone();
                    c.guid = self.new_guid();
                    c.support_upgrades.clear();
                    c.invalidate();
                    let id = self.add_card_obj(c);
                    let ids = vec![id; copies as usize];
                    self.add_card(&ids, e.move_pos, ctx);
                }
            }
            et::ExamForcePlayCardSearch | et::ExamForcePlayCardSearchWithCost => {
                let with_cost = t == et::ExamForcePlayCardSearchWithCost;
                let picked = self.pick(e, ctx);
                let found: Vec<CardId> =
                    picked.into_iter().filter(|&c| self.row(c).play_effects.iter().all(|x| x.1.effect_type != et::ExamForcePlayCardSearch)).collect();
                if found.is_empty() {
                    return;
                }
                if !with_cost && e.v1 >= 1 {
                    self.damage_stamina(e.v1, false, None);
                }
                for c in found {
                    self.queue.push_back(Cmd::UsePool(c, with_cost));
                }
            }
            et::ExamDebuffRecover => {
                let m = self.m.clone();
                let mut targets: Vec<i64> =
                    self.status.effects.iter().filter(|s| m.debuff_status_classes.contains(&s.cls)).map(|s| s.uid).collect();
                targets.sort_by(|a, b| b.cmp(a));
                if e.v1 >= 1 {
                    targets.truncate(e.v1 as usize);
                }
                for uid in targets {
                    self.status.remove_uid(uid);
                }
            }
            et::ExamItemFireLimitAdd => {
                for s in self.status.effects.iter_mut() {
                    if let Some(tg) = s.trig.as_mut() {
                        if tg.source.item != NONE && tg.source.item_idol_origin.unwrap_or(true) && tg.limit_count >= 0 {
                            tg.limit_count += e.v1;
                        }
                    }
                }
            }
            et::ExamStatusEnchantEncore => {
                self.status_enchant(e, ctx);
                if let Some(last) = self.status.effects.last_mut() {
                    if let Some(tg) = last.trig.as_mut() {
                        tg.encore = true;
                    }
                }
            }
            et::ExamGimmickPlayCardLimit => {
                let m = self.m.clone();
                if self.status.is_block_add(et::ExamGimmickPlayCardLimit, &m.debuff_effect_types) {
                    return;
                }
                let mut st = Status::new(cls::SearchPlayCardLimitStatusEffect, e.turn, 0, e.turn >= 0, false);
                st.extra.search_id = Some(e.search);
                self.status.add(st);
            }
            et::ExamSearchPlayCardStaminaConsumptionChange => {
                let mut st = Status::new(cls::SearchPlayCardStaminaConsumptionChangeStatusEffect, e.turn, e.v1, e.turn >= 0, false);
                st.extra.count = Some(e.count);
                st.extra.search_id = Some(e.search);
                st.extra.other.push(("_fromExamEffectId".to_string(), serde_json::Value::String(crate::sym::name(e.id))));
                self.status.add(st);
            }
            et::ExamStaminaDamage => self.damage_stamina(e.v1, false, None),
            et::ExamBlockValueMultiple => {
                let v = ceil_i(permil(e.v1) * i2f(self.block)) - self.block;
                self.add_block_fix(v);
            }
            et::ExamFullPowerPoint => self.add_full_power_point(e.v1, ctx),
            et::ExamConcentration => self.stance_exec(1, None, e, ctx),
            et::ExamPreservation => self.stance_exec(2, None, e, ctx),
            et::ExamOverPreservation => self.stance_exec(4, Some(1), e, ctx),
            et::ExamFullPower => self.stance_exec(3, Some(1), e, ctx),
            et::ExamStanceReset => {
                if self.status.idol_type == 3 {
                    return;
                }
                self.set_stance(0, 0, ctx, false);
            }
            et::ExamAddGrowEffect => {
                let cards = self.pick(e, ctx);
                let g = e.grow_ids.clone();
                self.add_grow_effects(&cards, &g, ctx, true);
            }
            _ => {
                self.unimplemented += 1;
            }
        }
    }
}
