//! 考试状态（gakumas_exam/status.py）：状态对象、附魔（TriggerEffect）与集合上的纯查询 / 合并规则。
//! 需要读写考试其余部分的（阶段计数、计数器）在 exam.rs 里包一层。

use crate::enums::{cls, et, ph};
use crate::master::{EffectRow, TriggerRow};
use crate::num::{ceil_i, i2f, permil};
use crate::sym::{Sym, NONE};
use serde_json::Value;
use std::sync::{Arc, OnceLock, RwLock};

// ---------------------------------------------------------------- 状态类表
fn extra_classes() -> &'static RwLock<Vec<String>> {
    static T: OnceLock<RwLock<Vec<String>>> = OnceLock::new();
    T.get_or_init(|| RwLock::new(vec![]))
}

pub fn class_id(name: &str) -> u16 {
    if let Some(i) = crate::enums::CLASS_NAMES.iter().position(|c| *c == name) {
        return i as u16;
    }
    let n = crate::enums::CLASS_NAMES.len();
    {
        let t = extra_classes().read().unwrap();
        if let Some(i) = t.iter().position(|c| c == name) {
            return (n + i) as u16;
        }
    }
    let mut t = extra_classes().write().unwrap();
    t.push(name.to_string());
    (n + t.len() - 1) as u16
}

pub fn class_name(id: u16) -> String {
    let n = crate::enums::CLASS_NAMES.len();
    if (id as usize) < n {
        crate::enums::CLASS_NAMES[id as usize].to_string()
    } else {
        extra_classes().read().unwrap()[id as usize - n].clone()
    }
}

// ---------------------------------------------------------------- 状态对象
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Source {
    pub item: Sym,
    pub drink: Sym,
    pub card: Sym,
    pub card_guid: Option<Arc<str>>,
    pub origin: Sym,
    /// 存档里没有的键原样带着（导出比对用）
    pub item_idol_origin: Option<bool>,
}

#[derive(Clone, Debug)]
pub struct Trig {
    pub trigger: Arc<TriggerRow>,
    pub effects: Arc<Vec<Arc<EffectRow>>>,
    pub turn_count: i64,
    /// phase_count（插入顺序）
    pub phase_count: Vec<(i64, i64)>,
    pub limit_count: i64,
    pub limit_in_turn: i64,
    pub limit_in_turn_remain: i64,
    pub enchant_id: Sym,
    pub origin_type: i64,
    pub source: Source,
    pub override_type: i64,
    pub from_effect_ids: Vec<Sym>,
    pub from_enchant: bool,
    pub encore: bool,
}

impl Trig {
    pub fn phase(&self, p: i64) -> i64 {
        self.phase_count.iter().find(|x| x.0 == p).map(|x| x.1).unwrap_or(0)
    }
    pub fn inc_phase(&mut self, p: i64) {
        if let Some(x) = self.phase_count.iter_mut().find(|x| x.0 == p) {
            x.1 += 1;
        } else {
            self.phase_count.push((p, 1));
        }
    }
    pub fn set_phase(&mut self, p: i64, v: i64) {
        if let Some(x) = self.phase_count.iter_mut().find(|x| x.0 == p) {
            x.1 = v;
        } else {
            self.phase_count.push((p, v));
        }
    }
    pub fn has_phase_key(&self, p: i64) -> bool {
        self.phase_count.iter().any(|x| x.0 == p)
    }
    pub fn increment_turn_count(&mut self) {
        self.turn_count += 1;
        self.limit_in_turn_remain = self.limit_in_turn;
    }
}

/// 状态的类特有字段（存档字段名 → 值）
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Extra {
    pub count: Option<i64>,
    pub search_id: Option<Sym>,
    /// 其余字段原样（导出用）
    pub other: Vec<(String, Value)>,
}

#[derive(Clone, Debug)]
pub struct Status {
    pub cls: u16,
    pub uid: i64,
    pub turn: i64,
    pub limited: bool,
    pub passing: bool,
    pub value: i64,
    pub extra: Extra,
    pub trig: Option<Box<Trig>>,
}

impl Status {
    pub fn new(cls: u16, turn: i64, value: i64, limited: bool, passing: bool) -> Status {
        Status { cls, uid: 0, turn, limited, passing, value, extra: Extra::default(), trig: None }
    }
    #[inline]
    pub fn is_trigger(&self) -> bool {
        self.trig.is_some()
    }
    pub fn spend_turn(&mut self) {
        if self.limited {
            self.turn -= 1;
        }
    }
}

// ---------------------------------------------------------------- TryAdd 规则
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Kind {
    SingleValue,
    SingleTurn,
    TurnkeyValue,
    TurnkeyTurn,
    New,
    NewValue,
    SingleValueTurn,
}

#[derive(Clone, Copy, Debug)]
pub struct Rule {
    pub cls: u16,
    pub kind: Kind,
    /// IsBlockAddStatus 用的效果类型（-1 = 无）
    pub block: i64,
    /// 增加后记的阶段计数（-1 = 无）
    pub phase: i64,
    /// (AdditiveFix 类, AdditiveMultiple 类)
    pub mods: Option<(u16, u16)>,
}

const fn r(cls: u16, kind: Kind, block: i64, phase: i64, mods: Option<(u16, u16)>) -> Rule {
    Rule { cls, kind, block, phase, mods }
}

use Kind::*;
pub mod rules {
    use super::*;
    pub const Aggressive: Rule = r(cls::AggressiveStatusEffect, SingleValue, et::ExamCardPlayAggressive, ph::ExamAggressiveUpInterval,
        Some((cls::AggressiveAdditiveFixStatusEffect, cls::AggressiveAdditiveStatusEffect)));
    pub const LessonBuff: Rule = r(cls::LessonBuffStatusEffect, SingleValue, et::ExamLessonBuff, ph::ExamLessonBuffUpInterval,
        Some((cls::LessonBuffAdditiveFixStatusEffect, cls::LessonBuffAdditiveStatusEffect)));
    pub const FullPowerPoint: Rule = r(cls::FullPowerPointStatusEffect, SingleValue, et::ExamFullPowerPoint, ph::ExamFullPowerPointUpInterval,
        Some((cls::FullPowerPointAdditiveFixStatusEffect, cls::FullPowerPointAdditiveStatusEffect)));
    pub const Review: Rule = r(cls::ReviewStatusEffect, SingleTurn, et::ExamReview, ph::ExamReviewUpInterval,
        Some((cls::ReviewAdditiveFixStatusEffect, cls::ReviewAdditiveStatusEffect)));
    pub const ParameterBuff: Rule = r(cls::ParameterBuffStatusEffect, SingleTurn, et::ExamParameterBuff, ph::ExamParameterBuffUpInterval,
        Some((cls::ParameterBuffAdditiveFixStatusEffect, cls::ParameterBuffAdditiveStatusEffect)));
    pub const AntiDebuff: Rule = r(cls::AntiDebuffStatusEffect, SingleValue, -1, -1, None);
    pub const LessonDebuff: Rule = r(cls::LessonDebuffStatusEffect, SingleValueTurn, et::ExamGimmickLessonDebuff, -1, None);
    pub const StartTurnCardDrawDown: Rule = r(cls::StartTurnCardDrawDownStatusEffect, NewValue, et::ExamGimmickStartTurnCardDrawDown, -1, None);
    pub const StaminaRecoverAdd: Rule = r(cls::StaminaRecoverAddStatusEffect, NewValue, -1, -1, None);
    pub const StaminaReduceChange: Rule = r(cls::StaminaReduceChangeStatusEffect, NewValue, -1, -1, None);
    pub const Uplifting: Rule = r(cls::UpliftingStatusEffect, SingleValue, -1, -1, None);
    pub const BlockAddDownFix: Rule = r(cls::BlockAddDownFixStatusEffect, SingleValue, 107, -1, None);
    pub const BlockAddDownRestriction: Rule = r(cls::BlockAddDownRestrictionStatusEffect, SingleTurn, 74, -1, None);
    pub const BlockAddDown: Rule = r(cls::BlockAddDownStatusEffect, SingleTurn, et::ExamBlockAddDown, -1, None);
    pub const BlockRestriction: Rule = r(cls::BlockRestrictionStatusEffect, SingleTurn, et::ExamBlockRestriction, -1, None);
    pub const BuffConsumptionAdd: Rule = r(cls::BuffConsumptionAddStatusEffect, SingleTurn, et::ExamBuffConsumptionAdd, -1, None);
    pub const BuffConsumptionDown: Rule = r(cls::BuffConsumptionDownStatusEffect, SingleTurn, 198, -1, None);
    pub const LessonParameterMultipleDependReviewOrAggressive: Rule =
        r(cls::LessonParameterMultipleDependReviewOrAggressiveStatusEffect, SingleTurn, 181, -1, None);
    pub const Panic: Rule = r(cls::PanicStatusEffect, SingleTurn, et::ExamPanic, -1, None);
    pub const ParameterBuffMultiplePerTurn: Rule =
        r(cls::ParameterBuffMultiplePerTurnStatusEffect, SingleTurn, et::ExamParameterBuffMultiplePerTurn, -1, None);
    pub const ParameterBuffTurnEndReduceLock: Rule = r(cls::ParameterBuffTurnEndReduceLockStatusEffect, SingleTurn, 197, -1, None);
    pub const ParameterDebuff: Rule = r(cls::ParameterDebuffStatusEffect, SingleTurn, et::ExamGimmickParameterDebuff, -1, None);
    pub const ReviewTurnEndReduceLock: Rule = r(cls::ReviewTurnEndReduceLockStatusEffect, SingleTurn, 196, -1, None);
    pub const Slump: Rule = r(cls::SlumpStatusEffect, SingleTurn, et::ExamGimmickSlump, -1, None);
    pub const StaminaConsumptionAddDown: Rule = r(cls::StaminaConsumptionAddDownStatusEffect, SingleTurn, 85, -1, None);
    pub const StaminaConsumptionAdd: Rule = r(cls::StaminaConsumptionAddStatusEffect, SingleTurn, et::ExamStaminaConsumptionAdd, -1, None);
    pub const StaminaConsumptionDownAdd: Rule = r(cls::StaminaConsumptionDownAddStatusEffect, SingleTurn, 89, -1, None);
    pub const StaminaConsumptionDown: Rule = r(cls::StaminaConsumptionDownStatusEffect, SingleTurn, et::ExamStaminaConsumptionDown, -1, None);
    pub const StaminaRecoverRestriction: Rule =
        r(cls::StaminaRecoverRestrictionStatusEffect, SingleTurn, et::ExamStaminaRecoverRestriction, -1, None);
    pub const StanceLockConcentration: Rule = r(cls::StanceLockConcentrationEffect, SingleTurn, et::ExamStanceLockConcentration, -1, None);
    pub const StanceLockFullPower: Rule = r(cls::StanceLockFullPowerEffect, SingleTurn, et::ExamStanceLockFullPower, -1, None);
    pub const StanceLockPreservation: Rule = r(cls::StanceLockPreservationEffect, SingleTurn, et::ExamStanceLockPreservation, -1, None);
    pub const StanceLock: Rule = r(cls::StanceLockStatusEffect, SingleTurn, et::StanceLock, -1, None);
    pub const GetCardUpgrade: Rule = r(cls::GetCardUpgradeStatusEffect, New, 90, -1, None);
    pub const HandHold: Rule = r(cls::HandHoldStatusEffect, New, 82, -1, None);
    pub const AggressiveAdditiveFix: Rule = r(cls::AggressiveAdditiveFixStatusEffect, TurnkeyValue, -1, -1, None);
    pub const AggressiveAdditive: Rule = r(cls::AggressiveAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const ConcentrationLessonMultipleAdditive: Rule = r(cls::ConcentrationLessonMultipleAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const EnthusiasticAdditive: Rule = r(cls::EnthusiasticAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const EnthusiasticMultiple: Rule = r(cls::EnthusiasticMultipleStatusEffect, TurnkeyValue, -1, -1, None);
    pub const Enthusiastic: Rule = r(cls::EnthusiasticStatusEffect, TurnkeyValue, -1, -1, None);
    pub const FullPowerLessonMultipleAdditive: Rule = r(cls::FullPowerLessonMultipleAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const FullPowerPointAdditiveFix: Rule = r(cls::FullPowerPointAdditiveFixStatusEffect, TurnkeyValue, -1, -1, None);
    pub const FullPowerPointAdditive: Rule = r(cls::FullPowerPointAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const GrowEffectLessonAddAdditive: Rule = r(cls::GrowEffectLessonAddAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const LessonBuffAdditiveFix: Rule = r(cls::LessonBuffAdditiveFixStatusEffect, TurnkeyValue, -1, -1, None);
    pub const LessonBuffAdditive: Rule = r(cls::LessonBuffAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const LessonBuffMultiple: Rule = r(cls::LessonBuffMultipleStatusEffect, TurnkeyValue, -1, -1, None);
    pub const LessonParameterMultipleDown: Rule =
        r(cls::LessonParameterMultipleDownStatusEffect, TurnkeyValue, et::ExamLessonValueMultipleDown, -1, None);
    pub const LessonParameterMultiple: Rule = r(cls::LessonParameterMultipleStatusEffect, TurnkeyValue, et::ExamLessonValueMultiple, -1, None);
    pub const ParameterBuffAdditiveFix: Rule = r(cls::ParameterBuffAdditiveFixStatusEffect, TurnkeyValue, -1, -1, None);
    pub const ParameterBuffAdditive: Rule = r(cls::ParameterBuffAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const ReviewAdditiveFix: Rule = r(cls::ReviewAdditiveFixStatusEffect, TurnkeyValue, -1, -1, None);
    pub const ReviewAdditive: Rule = r(cls::ReviewAdditiveStatusEffect, TurnkeyValue, -1, -1, None);
    pub const ReviewCountAdd: Rule = r(cls::ReviewCountAddStatusEffect, TurnkeyValue, -1, -1, None);
    pub const ReviewMultiple: Rule = r(cls::ReviewMultipleStatusEffect, TurnkeyValue, -1, -1, None);
    pub const StaminaConsumptionAddFix: Rule =
        r(cls::StaminaConsumptionAddFixStatusEffect, TurnkeyValue, et::ExamStaminaConsumptionAddFix, -1, None);
    pub const StaminaConsumptionDownFix: Rule = r(cls::StaminaConsumptionDownFixStatusEffect, TurnkeyValue, -1, -1, None);
    pub const LessonChangeSpecifyLessThan: Rule = r(cls::LessonChangeSpecifyLessThanStatusEffect, TurnkeyTurn, -1, -1, None);
    pub const LessonChangeSpecifyMoreThan: Rule =
        r(cls::LessonChangeSpecifyMoreThanStatusEffect, TurnkeyTurn, et::ExamLessonChangeSpecifyMoreThan, -1, None);
}

// ---------------------------------------------------------------- 集合
#[derive(Clone, Debug, Default)]
pub struct StatusCollection {
    pub effects: Vec<Status>,
    pub create_count: i64,
    pub idol_type: i64,
    pub idol_step: i64,
}

impl StatusCollection {
    pub fn add(&mut self, mut st: Status) -> i64 {
        self.create_count += 1;
        st.uid = self.create_count;
        self.effects.push(st);
        self.create_count
    }

    /// RemoveStatusEffect（按 uid）
    pub fn remove_uid(&mut self, uid: i64) {
        if let Some(i) = self.effects.iter().position(|s| s.uid == uid) {
            self.effects.remove(i);
        }
    }

    #[inline]
    pub fn first(&self, c: u16) -> Option<&Status> {
        self.effects.iter().find(|s| s.cls == c)
    }
    #[inline]
    pub fn first_mut(&mut self, c: u16) -> Option<&mut Status> {
        self.effects.iter_mut().find(|s| s.cls == c)
    }
    #[inline]
    pub fn first_idx(&self, c: u16) -> Option<usize> {
        self.effects.iter().position(|s| s.cls == c)
    }
    #[inline]
    pub fn has(&self, c: u16) -> bool {
        self.effects.iter().any(|s| s.cls == c)
    }
    #[inline]
    pub fn sum(&self, c: u16) -> i64 {
        self.effects.iter().filter(|s| s.cls == c).map(|s| s.value).sum()
    }
    #[inline]
    pub fn val(&self, c: u16) -> i64 {
        self.first(c).map(|s| s.value).unwrap_or(0)
    }
    #[inline]
    pub fn turn_of(&self, c: u16) -> i64 {
        self.first(c).map(|s| s.turn).unwrap_or(0)
    }
    pub fn last_turn(&self, c: u16) -> i64 {
        self.effects.iter().rev().find(|s| s.cls == c).map(|s| s.turn).unwrap_or(0)
    }

    /// Get*AdditiveMultiple：1 + Σ value/1000
    pub fn multiple(&self, c: u16) -> f32 {
        let mut r = 1.0f32;
        for s in self.effects.iter().filter(|s| s.cls == c) {
            r += permil(s.value);
        }
        r
    }
    /// Σ value/1000（不含基准 1）
    pub fn multiple0(&self, c: u16) -> f32 {
        let mut r = 0.0f32;
        for s in self.effects.iter().filter(|s| s.cls == c) {
            r += permil(s.value);
        }
        r
    }

    // ---- 取值
    pub fn aggressive(&self) -> i64 {
        self.val(cls::AggressiveStatusEffect)
    }
    pub fn lesson_buff(&self) -> i64 {
        self.val(cls::LessonBuffStatusEffect)
    }
    pub fn lesson_debuff(&self) -> i64 {
        self.sum(cls::LessonDebuffStatusEffect)
    }
    pub fn full_power_point(&self) -> i64 {
        self.val(cls::FullPowerPointStatusEffect)
    }
    pub fn review(&self) -> i64 {
        self.turn_of(cls::ReviewStatusEffect)
    }
    pub fn parameter_buff_turn(&self) -> i64 {
        self.turn_of(cls::ParameterBuffStatusEffect)
    }
    pub fn parameter_buff_mpt_turn(&self) -> i64 {
        self.turn_of(cls::ParameterBuffMultiplePerTurnStatusEffect)
    }
    pub fn parameter_debuff_turn(&self) -> i64 {
        self.turn_of(cls::ParameterDebuffStatusEffect)
    }
    pub fn enthusiastic(&self) -> i64 {
        self.sum(cls::EnthusiasticStatusEffect)
    }
    pub fn block_add_down_fix(&self) -> i64 {
        self.sum(cls::BlockAddDownFixStatusEffect)
    }
    pub fn start_turn_draw_down(&self) -> i64 {
        self.sum(cls::StartTurnCardDrawDownStatusEffect)
    }
    pub fn review_count_add(&self) -> i64 {
        self.sum(cls::ReviewCountAddStatusEffect)
    }
    pub fn stamina_down_fix(&self) -> i64 {
        self.sum(cls::StaminaConsumptionDownFixStatusEffect)
    }
    pub fn stamina_add_fix(&self) -> i64 {
        self.sum(cls::StaminaConsumptionAddFixStatusEffect)
    }
    pub fn stamina_reduce_change(&self) -> i64 {
        self.val(cls::StaminaReduceChangeStatusEffect)
    }
    pub fn stance_lock(&self) -> i64 {
        self.turn_of(cls::StanceLockStatusEffect)
    }
    pub fn is_step_max(&self) -> bool {
        if self.idol_type == 1 || self.idol_type == 2 {
            self.idol_step > 1
        } else {
            true
        }
    }

    // ---- 合并（不含阶段计数；返回是否加上）
    fn modify(&self, value: i64, mods: Option<(u16, u16)>, is_fix: bool) -> i64 {
        match mods {
            Some((fix_c, mul_c)) if !is_fix => {
                let fix = self.sum(fix_c);
                let mul = self.multiple(mul_c);
                ceil_i(mul * i2f(value + fix))
            }
            _ => value,
        }
    }

    /// IsBlockAddStatus：低下类效果在有低下状態無効时消耗一次并拦下
    pub fn is_block_add(&mut self, block: i64, debuff_types: &[i64]) -> bool {
        if block < 0 || !debuff_types.contains(&block) {
            return false;
        }
        match self.first(cls::AntiDebuffStatusEffect) {
            None => return false,
            Some(a) if a.value == 0 => return false,
            _ => {}
        }
        self.use_anti_debuff();
        true
    }

    pub fn use_anti_debuff(&mut self) {
        if let Some(i) = self.first_idx(cls::AntiDebuffStatusEffect) {
            self.effects[i].value -= 1;
            if self.effects[i].value <= 0 {
                self.effects.remove(i);
            }
        }
    }

    pub fn try_add_raw(&mut self, rule: &Rule, value: i64, turn: i64, is_fix: bool, debuff_types: &[i64]) -> bool {
        if self.is_block_add(rule.block, debuff_types) {
            return false;
        }
        let c = rule.cls;
        match rule.kind {
            SingleValue => {
                let v = self.modify(value, rule.mods, is_fix);
                let idx = if c != cls::AntiDebuffStatusEffect {
                    self.effects.iter().rposition(|s| s.cls == c)
                } else {
                    self.first_idx(c)
                };
                match idx {
                    Some(i) => self.effects[i].value = (self.effects[i].value + v).max(0),
                    None => {
                        self.add(Status::new(c, -1, v, false, false));
                    }
                }
            }
            SingleTurn => {
                let t = self.modify(turn, rule.mods, is_fix);
                match self.first_idx(c) {
                    Some(i) => {
                        if self.effects[i].limited {
                            self.effects[i].turn += t;
                        }
                    }
                    None => {
                        self.add(Status::new(c, t, 0, t >= 0, false));
                    }
                }
            }
            TurnkeyValue => match self.effects.iter().rposition(|s| s.cls == c && s.turn == turn) {
                Some(i) => self.effects[i].value = (self.effects[i].value + value).max(0),
                None => {
                    self.add(Status::new(c, turn, value, turn >= 0, c == cls::EnthusiasticStatusEffect));
                }
            },
            TurnkeyTurn => {
                if !self.effects.iter().any(|s| s.cls == c && s.turn == turn) {
                    self.add(Status::new(c, turn, value, turn >= 0, false));
                }
            }
            New | NewValue => {
                self.add(Status::new(c, turn, value, turn >= 0, false));
            }
            SingleValueTurn => match self.effects.iter().rposition(|s| s.cls == c) {
                Some(i) => self.effects[i].value = (self.effects[i].value + value).max(0),
                None => {
                    self.add(Status::new(c, turn, value, turn >= 0, false));
                }
            },
        }
        true
    }

    // ---- 消耗
    pub fn consume_value(&mut self, c: u16, n: i64) {
        if let Some(i) = self.first_idx(c) {
            self.effects[i].value = (self.effects[i].value - n).max(0);
            if self.effects[i].value <= 0 {
                self.effects.remove(i);
            }
        }
    }
    pub fn consume_turn(&mut self, c: u16, n: i64) {
        if let Some(i) = self.first_idx(c) {
            self.effects[i].turn = (self.effects[i].turn - n).max(0);
            if self.effects[i].turn <= 0 {
                self.effects.remove(i);
            }
        }
    }

    pub fn try_add_playable_value_add(&mut self, count: i64) {
        match self.first_mut(cls::PlayableValueAddStatusEffect) {
            Some(s) => s.value += count,
            None => {
                self.add(Status::new(cls::PlayableValueAddStatusEffect, 1, count, true, false));
            }
        }
    }

    pub fn set_review(&mut self, n: i64) {
        if let Some(i) = self.first_idx(cls::ReviewStatusEffect) {
            self.effects[i].turn = n;
            if n <= 0 {
                self.effects.remove(i);
            }
        }
    }
    pub fn set_aggressive(&mut self, n: i64) {
        if let Some(s) = self.first_mut(cls::AggressiveStatusEffect) {
            s.value = n;
        }
    }
    /// 画面读数覆盖やる気（续推 / 画面重建用，不触发任何时点）：有就改值，没有就按 TryAdd 的形状新建；n <= 0 移除
    pub fn override_aggressive(&mut self, n: i64) {
        match self.first_idx(cls::AggressiveStatusEffect) {
            Some(i) if n <= 0 => {
                self.effects.remove(i);
            }
            Some(i) => self.effects[i].value = n,
            None if n > 0 => {
                self.add(Status::new(cls::AggressiveStatusEffect, -1, n, false, false));
            }
            None => {}
        }
    }
    /// 画面读数覆盖好印象（值存在 _turn）：同上
    pub fn override_review(&mut self, n: i64) {
        match self.first_idx(cls::ReviewStatusEffect) {
            Some(i) if n <= 0 => {
                self.effects.remove(i);
            }
            Some(i) => self.effects[i].turn = n,
            None if n > 0 => {
                self.add(Status::new(cls::ReviewStatusEffect, n, 0, true, false));
            }
            None => {}
        }
    }
    pub fn set_parameter_buff(&mut self, n: i64) {
        if let Some(i) = self.first_idx(cls::ParameterBuffStatusEffect) {
            self.effects[i].turn = n;
            if n <= 0 {
                self.effects.remove(i);
            }
        }
    }

    pub fn try_add_enthusiastic_turn_additive(&mut self, value: i64) {
        for s in self.effects.iter_mut().filter(|s| s.cls == cls::EnthusiasticStatusEffect) {
            if s.limited {
                s.turn += value;
            }
        }
    }

    pub fn set_passing_all(&mut self) {
        for s in self.effects.iter_mut() {
            s.passing = true;
        }
    }

    /// 熱意以外：MoreThan / LessThan 待还原（Python 版恒为 -1）
    pub fn lesson_change_more(&self, _v: i64) -> i64 {
        -1
    }
    pub fn lesson_change_less(&self, _v: i64) -> i64 {
        -1
    }

    pub fn trigger_effect(trigger: Arc<TriggerRow>, effects: Arc<Vec<Arc<EffectRow>>>, origin_type: i64, turn: i64, limit_count: i64,
                          limit_in_turn: i64, enchant_id: Sym, source: Source, override_type: i64) -> Status {
        let mut s = Status::new(cls::TriggerEffectStatusEffect, turn, 0, turn >= 0, false);
        s.trig = Some(Box::new(Trig {
            trigger,
            effects,
            turn_count: 0,
            phase_count: vec![],
            limit_count,
            limit_in_turn,
            limit_in_turn_remain: limit_in_turn,
            enchant_id,
            origin_type,
            source,
            override_type,
            from_effect_ids: vec![],
            from_enchant: false,
            encore: false,
        }));
        s
    }
}

pub const NO_SYM: Sym = NONE;
