//! 考试中的卡（gakumas_exam/cards.py）与卡的成长（gakumas_exam/grow.py）。

use crate::enums::{cost, gt, minmax, mv, sstat};
use crate::master::{CardRow, EffectRow, GrowRow, Master, SearchRow, TriggerRow};
use crate::sym::{Sym, NONE};
use std::cell::RefCell;
use std::sync::Arc;

pub type CardId = usize;

/// 卡附魔（ProduceCardStatusEffect）
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CardSe {
    pub id: Sym,
    pub trigger: Sym,
    pub grow_ids: Vec<Sym>,
    pub group_ids: Vec<Sym>,
    pub trigger_count: i64,
    /// _phaseCountDictionary（插入顺序）
    pub phase_count: Vec<(i64, i64)>,
    pub spend_turn: i64,
    pub spend_count: i64,
}

impl CardSe {
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
}

/// status_effect：None（未建，按成长后的行懒建）/ {}（没有）/ 有
#[derive(Clone, Debug, PartialEq)]
pub enum Se {
    Unset,
    Empty,
    Has(CardSe),
}

/// _staminaConsumptionSpecifyEffectList 的一项（ExamCardValueStatusEffect）
#[derive(Clone, Debug, Default, PartialEq)]
pub struct StaminaSpec {
    pub turn: i64,
    pub int_value: i64,
    pub bool_value: bool,
    pub count: i64,
    pub turn_limited: bool,
    pub count_limited: bool,
    pub passing: bool,
}

/// 成长后卡行的缓存。PyExam 要能交给别的 Python 线程（实机提前预演在后台线程跑），pyo3 要求它 Sync；
/// 引擎内部不开线程，所有访问都在持有 GIL 的 Python 调用里串行发生，所以这里手动声明 Sync 是安全的。
#[derive(Debug)]
pub struct RowCache(pub RefCell<Option<Arc<DerivedRow>>>);
unsafe impl Sync for RowCache {}
impl std::ops::Deref for RowCache {
    type Target = RefCell<Option<Arc<DerivedRow>>>;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

/// 成长后的卡行（row() 的结果）
#[derive(Debug)]
pub struct DerivedRow {
    pub base: Arc<CardRow>,
    pub stamina: i64,
    pub force_stamina: i64,
    pub cost_value: i64,
    pub play_trigger: Sym,
    pub status_enchant: Sym,
    pub is_initial: bool,
    pub play_move: i64,
    pub penetrate: bool,
    /// [(触发 id, 效果行, isOncePlayEffect)]
    pub effects: Vec<(Sym, Arc<EffectRow>, bool)>,
    pub group_ids: Vec<Sym>,
    pub play_trigger_row: Option<Arc<TriggerRow>>,
    pub play_effects: Vec<(Option<Arc<TriggerRow>>, Arc<EffectRow>, bool)>,
}

impl DerivedRow {
    pub fn has_effect_type(&self, t: i64) -> bool {
        self.effects.iter().any(|x| x.1.effect_type == t)
    }
}

#[derive(Debug)]
pub struct Card {
    pub card_id: Sym,
    pub base_upgrade: i64,
    pub tmp_upgrade: i64,
    pub support_upgrades: Vec<Sym>,
    pub customize_counts: Vec<i64>,
    pub custom_override: Option<Vec<(Sym, i64)>>,
    pub guid: Arc<str>,
    pub fixed_order: i64,
    pub play_count: i64,
    pub grow_after: Vec<GrowRow>,
    pub affect_grow: Vec<Sym>,
    pub se: Se,
    pub move_used_in_turn: bool,
    pub stamina_specify: Vec<StaminaSpec>,
    pub row: RowCache,
}

impl Clone for Card {
    fn clone(&self) -> Self {
        Card {
            card_id: self.card_id,
            base_upgrade: self.base_upgrade,
            tmp_upgrade: self.tmp_upgrade,
            support_upgrades: self.support_upgrades.clone(),
            customize_counts: self.customize_counts.clone(),
            custom_override: self.custom_override.clone(),
            guid: self.guid.clone(),
            fixed_order: self.fixed_order,
            play_count: self.play_count,
            grow_after: self.grow_after.clone(),
            affect_grow: self.affect_grow.clone(),
            se: self.se.clone(),
            move_used_in_turn: self.move_used_in_turn,
            stamina_specify: self.stamina_specify.clone(),
            row: RowCache(RefCell::new(self.row.borrow().clone())),
        }
    }
}

impl Card {
    pub fn new(card_id: Sym, upgrade: i64, customize_counts: Vec<i64>, guid: Arc<str>) -> Card {
        Card {
            card_id,
            base_upgrade: upgrade,
            tmp_upgrade: 0,
            support_upgrades: vec![],
            customize_counts,
            custom_override: None,
            guid,
            fixed_order: 0,
            play_count: 0,
            grow_after: vec![],
            affect_grow: vec![],
            se: Se::Unset,
            move_used_in_turn: false,
            stamina_specify: vec![],
            row: RowCache(RefCell::new(None)),
        }
    }

    #[inline]
    pub fn upgrade(&self) -> i64 {
        self.base_upgrade + self.tmp_upgrade + self.support_upgrades.len() as i64
    }
    #[inline]
    pub fn upgrade_raw(&self) -> i64 {
        self.base_upgrade + self.tmp_upgrade
    }
    pub fn invalidate(&self) {
        *self.row.borrow_mut() = None;
    }

    pub fn base_row(&self, m: &Master) -> Arc<CardRow> {
        let mut u = self.upgrade();
        while u >= 0 {
            if let Some(r) = m.card(self.card_id, u) {
                return r.clone();
            }
            u -= 1;
        }
        panic!("ProduceCard {} 不存在", crate::sym::name(self.card_id))
    }

    pub fn customizations(&self, m: &Master) -> Vec<(Sym, i64)> {
        if let Some(o) = &self.custom_override {
            if !o.is_empty() {
                return o.clone();
            }
        }
        let ids = self.base_row(m).customize_ids.clone();
        ids.iter().zip(self.customize_counts.iter()).filter(|(_, &n)| n != 0).map(|(&i, &n)| (i, n)).collect()
    }

    pub fn customize_count(&self) -> i64 {
        if let Some(o) = &self.custom_override {
            if !o.is_empty() {
                return o.iter().map(|x| x.1).sum();
            }
        }
        self.customize_counts.iter().sum()
    }

    fn customize_grows(&self, m: &Master) -> Vec<GrowRow> {
        let mut out = vec![];
        for (id, n) in self.customizations(m) {
            if let Some(gs) = m.customize.get(&(id, n)) {
                for g in gs {
                    out.push(m.grow(*g).clone());
                }
            }
        }
        out
    }

    pub fn row(&self, m: &Master) -> Arc<DerivedRow> {
        if let Some(r) = self.row.borrow().as_ref() {
            return r.clone();
        }
        let base = self.base_row(m);
        let mut d = apply_layer(m, Layer::Base(base), &self.customize_grows(m));
        if !self.grow_after.is_empty() {
            d = apply_layer(m, Layer::Derived(d), &self.grow_after);
        }
        let r = Arc::new(d);
        *self.row.borrow_mut() = Some(r.clone());
        r
    }

    /// ensure_status_effect：未建时按成长后的 produceCardStatusEnchantId 建
    pub fn ensure_se(&mut self, m: &Master) -> Option<&mut CardSe> {
        if let Se::Unset = self.se {
            let eid = self.row(m).status_enchant;
            self.se = match if eid != NONE { m.card_enchants.get(&eid) } else { None } {
                Some(se) => Se::Has(CardSe {
                    id: se.id,
                    trigger: se.trigger,
                    grow_ids: se.grow_ids.clone(),
                    group_ids: se.group_ids.clone(),
                    trigger_count: se.trigger_count,
                    phase_count: vec![],
                    spend_turn: 0,
                    spend_count: 0,
                }),
                None => Se::Empty,
            };
        }
        match &mut self.se {
            Se::Has(se) if se.id != NONE => Some(se),
            _ => None,
        }
    }

    pub fn stamina_cost(&self, m: &Master) -> i64 {
        if !self.stamina_specify.is_empty() {
            return self.stamina_specify.iter().map(|x| x.int_value).min().unwrap();
        }
        let r = self.row(m);
        if r.penetrate {
            r.force_stamina
        } else {
            r.stamina
        }
    }

    pub fn set_temporary_stamina_fix(&mut self, value: i64, count: i64, turn: i64) {
        self.stamina_specify = vec![StaminaSpec {
            turn,
            int_value: value,
            bool_value: false,
            count,
            turn_limited: turn > 0,
            count_limited: count > 0,
            passing: false,
        }];
    }

    pub fn spend_turn(&mut self) {
        let mut keep = Vec::with_capacity(self.stamina_specify.len());
        for mut x in self.stamina_specify.drain(..) {
            if x.turn_limited && x.passing {
                x.turn -= 1;
                if x.turn <= 0 {
                    continue;
                }
            }
            keep.push(x);
        }
        self.stamina_specify = keep;
    }

    pub fn set_passing_turn_start(&mut self) {
        for x in self.stamina_specify.iter_mut() {
            x.passing = true;
        }
    }

    pub fn add_grow(&mut self, g: &GrowRow, lesson_add_multiple: f32) {
        merge(&mut self.grow_after, g, lesson_add_multiple);
        self.affect_grow.push(g.id);
        self.invalidate();
    }
}

// ---------------------------------------------------------------- 检索

pub fn is_search_target(m: &Master, card: &Card, search: Option<&SearchRow>) -> bool {
    let s = match search {
        None => return true,
        Some(s) => s,
    };
    if s.is_self {
        return true;
    }
    let r = card.row(m);
    if s.tag != NONE && r.base.search_tag != s.tag {
        return false;
    }
    if !s.card_ids.is_empty() && !s.card_ids.contains(&card.card_id) {
        return false;
    }
    if !s.upgrades.is_empty() && !s.upgrades.contains(&card.upgrade()) {
        return false;
    }
    if s.plan != 0 && r.base.plan != s.plan {
        return false;
    }
    if !s.categories.is_empty() && !s.categories.contains(&r.base.category) {
        return false;
    }
    if !by_stamina(&r, s.st_minmax, s.st_min, s.st_max, s.cost_type) {
        return false;
    }
    if s.status_type != 0 && !search_status(&r, s.status_type) {
        return false;
    }
    if s.effect_type != 0 && !r.play_effects.iter().any(|x| x.1.effect_type == s.effect_type) {
        return false;
    }
    if !s.group_ids.is_empty() && !r.group_ids.iter().any(|g| s.group_ids.contains(g)) {
        return false;
    }
    if !s.rarities.is_empty() && !s.rarities.contains(&r.base.rarity) {
        return false;
    }
    if s.customized && card.customize_count() <= 0 {
        return false;
    }
    true
}

fn by_stamina(r: &DerivedRow, kind: i64, lo: i64, hi: i64, cost_type: i64) -> bool {
    if kind == 0 {
        return true;
    }
    let v = if cost_type != 0 {
        if r.base.cost_type != cost_type {
            return false;
        }
        r.cost_value
    } else {
        r.stamina
    };
    if kind == minmax::MinMax {
        lo <= v && v <= hi
    } else if kind == minmax::Min {
        v >= lo
    } else if kind == minmax::Max {
        v <= hi
    } else {
        true
    }
}

fn search_status(r: &DerivedRow, st: i64) -> bool {
    if st == sstat::LostPlay {
        r.play_move == mv::Lost
    } else if st == sstat::GravePlay {
        r.play_move == mv::Grave
    } else if st == sstat::EndTurnLost {
        r.base.end_turn_lost
    } else if st == sstat::Initial {
        r.is_initial
    } else if st == sstat::Restrict {
        r.base.is_restrict
    } else {
        true
    }
}

// ---------------------------------------------------------------- 成长（grow.py）

const LESSON_ICON: &[i64] = &[1, 23, 52, 56, 118, 132, 147, 183, 184, 185];
const LESSON_TYPES: &[i64] = &[1, 19, 23, 29, 52, 56, 118, 124, 125, 131, 132, 142, 144, 147, 163, 164, 165, 183, 184, 185, 186, 216];
const BLOCK_ICON: &[i64] = &[3, 133, 143, 161];

#[derive(Clone, Copy, PartialEq)]
pub enum Field {
    V1,
    Count,
    Turn,
}

/// 值类成长：成长类型 → (作用的效果类型, 字段, 是否减少)
pub fn value_spec(t: i64) -> Option<(&'static [i64], Field, bool)> {
    Some(match t {
        1 => (LESSON_ICON, Field::V1, false),
        2 => (LESSON_ICON, Field::V1, true),
        3 => (LESSON_TYPES, Field::Count, false),
        4 => (LESSON_TYPES, Field::Count, true),
        5 => (BLOCK_ICON, Field::V1, false),
        6 => (BLOCK_ICON, Field::V1, true),
        7 => (&[49], Field::V1, false),
        8 => (&[49], Field::V1, true),
        16 => (&[2], Field::Turn, false),
        17 => (&[2], Field::Turn, true),
        18 => (&[10], Field::V1, false),
        19 => (&[10], Field::V1, true),
        20 => (&[31], Field::V1, false),
        21 => (&[31], Field::V1, true),
        22 => (&[42], Field::V1, false),
        23 => (&[42], Field::V1, true),
        24 => (&[4], Field::V1, false),
        25 => (&[4], Field::V1, true),
        26 => (&[129], Field::Turn, false),
        27 => (&[129], Field::Turn, true),
        28 => (&[5], Field::Turn, false),
        29 => (&[5], Field::Turn, true),
        30 => (&[69], Field::Turn, false),
        31 => (&[69], Field::Turn, true),
        50 => (&[19], Field::V1, false),
        51 => (&[125], Field::V1, false),
        52 => (&[124], Field::V1, false),
        _ => return None,
    })
}

/// ExamExtensions.GetCostType（成长类型 40..54）
pub fn grow_cost_type(t: i64) -> i64 {
    match t {
        40 | 41 => cost::ExamLessonBuff,
        42 | 43 => cost::ExamReview,
        44 | 45 => cost::ExamCardPlayAggressive,
        46 | 47 => cost::ExamParameterBuff,
        48 | 49 => cost::ExamFullPowerPoint,
        53 | 54 => cost::ExamParameterBuffMultiplePerTurn,
        _ => 0,
    }
}

const REDUCE_MASK: u64 = 0x00415500aaaa5554;

pub fn is_reduce(t: i64) -> bool {
    (0..0x37).contains(&t) && (REDUCE_MASK >> t) & 1 == 1
}

/// GetInverseEffectType：成对的加 / 减互换
pub fn inverse(t: i64) -> i64 {
    match t {
        1..=8 | 10..=31 | 40..=49 | 53 | 54 => {
            // 成对：(1,2)(3,4)…(7,8)(10,11)…(30,31)(40,41)…(48,49)(53,54)
            let base = if (1..=8).contains(&t) {
                1
            } else if (10..=31).contains(&t) {
                10
            } else if (40..=49).contains(&t) {
                40
            } else {
                53
            };
            if (t - base) % 2 == 0 {
                t + 1
            } else {
                t - 1
            }
        }
        _ => t,
    }
}

pub fn positive_key(t: i64) -> i64 {
    t.min(inverse(t))
}

fn last<'a>(grows: &'a [GrowRow], t: i64) -> Option<&'a GrowRow> {
    grows.iter().rev().find(|g| g.effect_type == t)
}

enum Layer {
    Base(Arc<CardRow>),
    Derived(DerivedRow),
}

fn apply_layer(m: &Master, layer: Layer, grows: &[GrowRow]) -> DerivedRow {
    let mut d = match layer {
        Layer::Base(base) => {
            let effects = base.play_effects.iter().map(|l| (l.trigger, m.effect(l.effect), l.once)).collect();
            DerivedRow {
                stamina: base.stamina,
                force_stamina: base.force_stamina,
                cost_value: base.cost_value,
                play_trigger: base.play_trigger,
                status_enchant: base.status_enchant,
                is_initial: base.is_initial,
                play_move: base.play_move,
                penetrate: base.force_stamina > 0,
                effects,
                group_ids: vec![],
                play_trigger_row: None,
                play_effects: vec![],
                base,
            }
        }
        Layer::Derived(d) => d,
    };
    if !grows.is_empty() {
        let pen = d.penetrate;
        let (ka, kb) = if pen { (gt::CostPenetrateAdd, gt::CostPenetrateReduce) } else { (gt::CostAdd, gt::CostReduce) };
        let hits: Vec<&GrowRow> = grows.iter().filter(|g| g.effect_type == ka || g.effect_type == kb).collect();
        if !hits.is_empty() {
            let mut v = if pen { d.force_stamina } else { d.stamina };
            for g in &hits {
                v = if is_reduce(g.effect_type) { v - g.value } else { v + g.value };
            }
            if pen {
                d.force_stamina = v.max(0);
            } else {
                d.stamina = v.max(0);
            }
        }
        let hits: Vec<&GrowRow> = grows.iter().filter(|g| grow_cost_type(g.effect_type) != 0).collect();
        if !hits.is_empty() {
            let mut v = d.cost_value;
            for g in &hits {
                if grow_cost_type(g.effect_type) == d.base.cost_type {
                    v = if is_reduce(g.effect_type) { v - g.value } else { v + g.value };
                }
            }
            d.cost_value = v.max(0);
        }
        if let Some(g) = last(grows, gt::PlayTriggerChange) {
            d.play_trigger = g.play_trigger;
        }
        if let Some(g) = last(grows, gt::CardStatusEnchantChange) {
            d.status_enchant = g.card_status_enchant;
        }
        if last(grows, gt::InitialAdd).is_some() {
            d.is_initial = true;
        }
        if let Some(g) = last(grows, gt::PlayMovePositionTypeChange) {
            d.play_move = g.play_move;
        }
        for g in grows {
            if g.effect_type == gt::PlayEffectTriggerChange {
                for x in d.effects.iter_mut() {
                    if g.target_play_effect_triggers.contains(&x.0) {
                        x.0 = g.play_effect_trigger;
                    }
                }
            }
        }
        affect_effect_list(m, &mut d.effects, grows);
    }
    let mut groups: Vec<Sym> = vec![];
    for (_, e, _) in &d.effects {
        for x in &e.group_ids {
            if !groups.contains(x) {
                groups.push(*x);
            }
        }
    }
    if d.status_enchant != NONE {
        if let Some(se) = m.card_enchants.get(&d.status_enchant) {
            for x in &se.group_ids {
                if !groups.contains(x) {
                    groups.push(*x);
                }
            }
        }
    }
    d.group_ids = groups;
    d.play_trigger_row = m.trigger(d.play_trigger);
    d.play_effects = d.effects.iter().map(|(t, e, o)| (m.trigger(*t), e.clone(), *o)).collect();
    d
}

fn affect_effect_list(m: &Master, entries: &mut Vec<(Sym, Arc<EffectRow>, bool)>, grows: &[GrowRow]) {
    for g in grows {
        let t = g.effect_type;
        if t == gt::EffectAdd {
            entries.push((NONE, m.effect(g.play_effect), false));
        } else if t == gt::EffectDelete {
            entries.retain(|x| !g.target_play_effects.contains(&x.1.id));
        } else if t == gt::EffectChange {
            for x in entries.iter_mut() {
                if g.target_play_effects.contains(&x.1.id) {
                    x.1 = m.effect(g.play_effect);
                }
            }
        }
    }
    for g in grows {
        if let Some((targets, field, reduce)) = value_spec(g.effect_type) {
            let d = if reduce { -g.value } else { g.value };
            for x in entries.iter_mut() {
                if targets.contains(&x.1.effect_type) {
                    let mut e = (*x.1).clone();
                    match field {
                        Field::V1 => e.v1 = (e.v1 + d).max(1),
                        Field::Count => e.count = (e.count + d).max(1),
                        Field::Turn => e.turn = (e.turn + d).max(1),
                    }
                    x.1 = Arc::new(e);
                }
            }
        }
    }
}

const STAMINA_COST: &[i64] = &[gt::CostReduce, gt::CostAdd, gt::CostPenetrateReduce, gt::CostPenetrateAdd];

/// IsValidCardGrowEffect
pub fn grow_valid(m: &Master, card: &Card, g: &GrowRow) -> bool {
    let t = g.effect_type;
    let r = card.row(m);
    if STAMINA_COST.contains(&t) {
        let pen = t == gt::CostPenetrateReduce || t == gt::CostPenetrateAdd;
        if r.base.cost_type == 0 && pen == (r.force_stamina != 0) {
            return true;
        }
    } else if grow_cost_type(t) != 0 && grow_cost_type(t) == r.base.cost_type {
        return true;
    }
    match value_spec(t) {
        None => false,
        Some((targets, _, _)) => r.play_effects.iter().any(|x| targets.contains(&x.1.effect_type)),
    }
}

/// ExamEffectUtility.AddGrowEffect（作用于 _growEffectExamStartAfterList）
pub fn merge(grows: &mut Vec<GrowRow>, g: &GrowRow, lesson_add_multiple: f32) {
    let mut g = g.clone();
    if g.effect_type == gt::LessonAdd && lesson_add_multiple != 1.0 {
        g.value = crate::num::ceil_i(lesson_add_multiple * crate::num::i2f(g.value));
    }
    let key = positive_key(g.effect_type);
    for i in 0..grows.len() {
        if positive_key(grows[i].effect_type) == key {
            if is_reduce(g.effect_type) == is_reduce(grows[i].effect_type) {
                grows[i].value += g.value;
            } else {
                let d = grows[i].value - g.value;
                if d == 0 {
                    grows.remove(i);
                } else if d > 0 {
                    grows[i].value = d;
                } else {
                    grows[i].effect_type = inverse(grows[i].effect_type);
                    grows[i].value = -d;
                }
            }
            return;
        }
    }
    grows.push(g);
}
