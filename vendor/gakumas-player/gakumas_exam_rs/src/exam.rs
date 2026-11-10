//! 考试局面与流程：gakumas_exam/exam.py、difftrig.py、triggers.py 的逐函数移植。
//! 函数名、调用顺序与 Python 参照实现一致（随机数调用次数和顺序必须相同，对拍）。

use crate::card::{is_search_target, Card, CardId, CardSe};
use crate::enums::{cat, cls, cost, et, fs, lt, mv, mvtrig, ph, pos, range};
use crate::master::{EffectRow, Master, SearchRow, Setting, SettingK, TriggerRow};
use crate::num::{ceil_i, i2f, permil, NativeRandom};
use crate::status::{rules, Rule, Source, Status, StatusCollection};
use crate::sym::{Sym, NONE};
use std::collections::VecDeque;
use std::sync::Arc;

/// 卡附魔的指针变更计数加两次（exam.CARD_STANCE_COUNT_DOUBLE）
pub const CARD_STANCE_COUNT_DOUBLE: bool = true;

// ---------------------------------------------------------------- 计数器
pub const COUNTER_KEYS: &[&str] = &[
    "fullPowerPointGetSumCount",
    "blockConsumptionSumCount",
    "reviewConsumptionSumCount",
    "stanceChangeCount",
    "stanceFullPowerChangeCount",
    "stanceConcentrationChangeCount",
    "stancePreservationChangeCount",
    "totalConsumeStamina",
    "totalDrawCardCount",
    "currentTurnTotalAddParameter",
    "currentTurnTotalConsumeStamina",
    "currentTurnTotalBlock",
    "enthusiasticGetSumCount",
];
pub mod ck {
    pub const FullPowerPointGetSum: usize = 0;
    pub const BlockConsumptionSum: usize = 1;
    pub const ReviewConsumptionSum: usize = 2;
    pub const StanceChange: usize = 3;
    pub const StanceFullPowerChange: usize = 4;
    pub const StanceConcentrationChange: usize = 5;
    pub const StancePreservationChange: usize = 6;
    pub const TotalConsumeStamina: usize = 7;
    pub const TotalDrawCardCount: usize = 8;
    pub const CurrentTurnTotalAddParameter: usize = 9;
    pub const CurrentTurnTotalConsumeStamina: usize = 10;
    pub const CurrentTurnTotalBlock: usize = 11;
    pub const EnthusiasticGetSum: usize = 12;
}

#[derive(Clone, Debug, Default)]
pub struct Counters {
    pub v: [Option<i64>; 13],
    /// 存档里其他（规则不读）的键
    pub other: Vec<(String, i64)>,
}

impl Counters {
    #[inline]
    pub fn get(&self, k: usize) -> i64 {
        self.v[k].unwrap_or(0)
    }
    #[inline]
    pub fn add(&mut self, k: usize, n: i64) {
        self.v[k] = Some(self.get(k) + n);
    }
}

// ---------------------------------------------------------------- 上下文与命令
/// ExamEffectCalculateContext（只保留规则读的部分）
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Ctx {
    pub card: Option<CardId>,
    pub trigger_card: Option<CardId>,
    pub enchant: bool,
    pub gimmick: bool,
    pub active: bool,
}

impl Ctx {
    pub fn base() -> Ctx {
        Ctx { active: true, ..Default::default() }
    }
    pub fn card(c: CardId) -> Ctx {
        Ctx { card: Some(c), active: true, ..Default::default() }
    }
    pub fn gimmick() -> Ctx {
        Ctx { gimmick: true, active: false, ..Default::default() }
    }
}

/// ctx is None or ctx.IsEnchantTriggerActive()
#[inline]
fn active(ctx: Option<Ctx>) -> bool {
    ctx.map(|c| c.active).unwrap_or(true)
}

#[derive(Clone, Debug)]
pub enum Cmd {
    Effect(Arc<EffectRow>, Option<Ctx>),
    /// 卡附魔命中：把附魔的成长加给这张卡（valid_check=False）
    AddGrows(CardId, Vec<Sym>),
    AfterPlay(CardId, Ctx, Vec<Cmd>),
    MovePlayed(CardId, Vec<Cmd>),
    AfterMove,
    UsePool(CardId, bool),
    Panic,
}

// ---------------------------------------------------------------- 差分
pub const OBS_KEYS: [i64; 6] = [2, 3, 10, 31, 42, 49];

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Obs {
    pub stamina: i64,
    pub score: i64,
    pub keys: [i64; 6],
    pub stance: (i64, i64),
    pub stance_count: i64,
}

#[derive(Clone, Debug)]
pub enum Ev {
    HandAdd { cards: Vec<CardId>, frm: i64, etype: i64 },
    Move { cards: Vec<CardId>, pos: i64 },
    Stamina { mix: bool },
    Status,
}

#[derive(Clone, Debug, Default)]
pub struct Support {
    pub id: Sym,
    pub filter: i64,
    pub permil: i64,
}

#[derive(Clone, Debug, Default)]
pub struct Gimmick {
    pub id: Sym,
    pub effect: Sym,
    pub fs_type: i64,
    pub fs_value: i64,
    pub fs_check: i64,
    pub fs_search: Sym,
}

#[derive(Clone, Debug, Default)]
pub struct Contest {
    pub id: i64,
    pub used: i64,
    pub counter: i64,
    pub gi_direct: i64,
}

#[derive(Clone)]
pub struct Exam {
    pub m: Arc<Master>,
    pub setting: Arc<Setting>,
    pub sk: Arc<SettingK>,
    pub rng: NativeRandom,
    pub status: StatusCollection,
    pub cards: Vec<Card>,
    pub hand: Vec<CardId>,
    pub deck: Vec<CardId>,
    pub grave: Vec<CardId>,
    pub lost: Vec<CardId>,
    pub hold: Vec<CardId>,
    pub playing: Option<CardId>,
    pub plan_type: i64,
    pub exam_type: i64,
    pub stamina: i64,
    pub max_stamina: i64,
    pub block: i64,
    pub score: i64,
    pub current_turn: i64,
    pub limit_turn: i64,
    pub remain_turn: i64,
    pub extra_turn: i64,
    pub turn_types: Vec<i64>,
    /// bonus_permil：下标 1..3（缺省 1000）
    pub bonus_permil: [Option<i64>; 4],
    pub exam_card_play_count: i64,
    pub turn_card_play_count: i64,
    pub counters: Counters,
    pub drinks: Vec<Sym>,
    /// (_id, _fireCount)
    pub items: Vec<(Sym, i64)>,
    pub supports: Vec<Support>,
    pub turn_used_supports: Vec<Sym>,
    pub clear_border: i64,
    pub play_log: Vec<CardId>,
    pub queue: VecDeque<Cmd>,
    diff: Option<Vec<Ev>>,
    suppressed: Option<Vec<(Obs, Obs)>>,
    pub initial_deal: bool,
    pub gimmick_schedule: Vec<(i64, Gimmick)>,
    pub ended: bool,
    pub remaining_actions: i64,
    pub pending_start: bool,
    pub guid_seq: i64,
    pub contest: Option<Contest>,
    queue_after_draw: Vec<Cmd>,
    /// 对手各回合分数（名次用，规则不读）
    pub npc_scores: Vec<Vec<i64>>,
    pub npc_current: Vec<Option<i64>>,
    /// 未实现的效果类型执行次数（Python 版记 unimplemented 事件）
    pub unimplemented: u64,
    /// settle_from_save：费用已付，consume_card_cost 不再扣
    pub skip_cost: bool,
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Zone {
    Hand,
    Deck,
    Grave,
    Lost,
    Hold,
    Playing,
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Action {
    Play(usize),
    Drink(usize),
    End,
}

impl Exam {
    pub fn new(m: Arc<Master>, setting_id: Sym) -> Exam {
        let (setting, sk) = m.settings.get(&setting_id).cloned().unwrap_or_else(|| panic!("ExamSetting {} 不存在", crate::sym::name(setting_id)));
        Exam {
            m,
            setting,
            sk,
            rng: NativeRandom::new(1),
            status: StatusCollection::default(),
            cards: vec![],
            hand: vec![],
            deck: vec![],
            grave: vec![],
            lost: vec![],
            hold: vec![],
            playing: None,
            plan_type: 0,
            exam_type: 1,
            stamina: 0,
            max_stamina: 0,
            block: 0,
            score: 0,
            current_turn: 0,
            limit_turn: 0,
            remain_turn: 0,
            extra_turn: 0,
            turn_types: vec![],
            bonus_permil: [None, Some(1000), Some(1000), Some(1000)],
            exam_card_play_count: 0,
            turn_card_play_count: 0,
            counters: Counters::default(),
            drinks: vec![],
            items: vec![],
            supports: vec![],
            turn_used_supports: vec![],
            clear_border: 0,
            play_log: vec![],
            queue: VecDeque::new(),
            diff: None,
            suppressed: None,
            initial_deal: true,
            gimmick_schedule: vec![],
            ended: false,
            remaining_actions: 0,
            pending_start: false,
            guid_seq: 0,
            contest: None,
            queue_after_draw: vec![],
            npc_scores: vec![],
            npc_current: vec![],
            unimplemented: 0,
            skip_cost: false,
        }
    }

    // ------------------------------------------------------------------ 基本量
    pub fn current_parameter_type(&self) -> i64 {
        let i = self.current_turn - 1;
        if i >= 0 && (i as usize) < self.turn_types.len() {
            self.turn_types[i as usize]
        } else {
            *self.turn_types.last().unwrap_or(&1)
        }
    }

    pub fn row(&self, c: CardId) -> Arc<crate::card::DerivedRow> {
        self.cards[c].row(&self.m)
    }

    fn ensure_se(&mut self, c: CardId) -> Option<&mut CardSe> {
        let m = self.m.clone();
        self.cards[c].ensure_se(&m)
    }

    pub fn new_guid(&mut self) -> Arc<str> {
        self.guid_seq += 1;
        Arc::from(format!("gen-{:06}", self.guid_seq).as_str())
    }

    pub fn add_card_obj(&mut self, c: Card) -> CardId {
        self.cards.push(c);
        self.cards.len() - 1
    }

    // ------------------------------------------------------------------ 卡区
    pub fn zone_vec(&mut self, z: Zone) -> &mut Vec<CardId> {
        match z {
            Zone::Hand => &mut self.hand,
            Zone::Deck => &mut self.deck,
            Zone::Grave => &mut self.grave,
            Zone::Lost => &mut self.lost,
            Zone::Hold => &mut self.hold,
            Zone::Playing => unreachable!(),
        }
    }

    pub fn all_cards(&self) -> Vec<CardId> {
        let mut out = Vec::with_capacity(self.cards.len());
        out.extend_from_slice(&self.hand);
        out.extend_from_slice(&self.deck);
        out.extend_from_slice(&self.grave);
        out.extend_from_slice(&self.lost);
        out.extend_from_slice(&self.hold);
        if let Some(p) = self.playing {
            out.push(p);
        }
        out
    }

    /// DeckAll：手牌、牌堆、弃牌、保留、出牌中（不含除外）
    pub fn deck_all(&self) -> Vec<CardId> {
        let mut out = Vec::with_capacity(self.cards.len());
        out.extend_from_slice(&self.hand);
        out.extend_from_slice(&self.deck);
        out.extend_from_slice(&self.grave);
        out.extend_from_slice(&self.hold);
        if let Some(p) = self.playing {
            out.push(p);
        }
        out
    }

    pub fn cards_at(&self, p: i64, ctx: Option<Ctx>) -> Vec<CardId> {
        if p == pos::Hand {
            self.hand.clone()
        } else if p == pos::Deck {
            self.deck.clone()
        } else if p == pos::Grave {
            self.grave.clone()
        } else if p == pos::Lost {
            self.lost.clone()
        } else if p == pos::Hold {
            self.hold.clone()
        } else if p == pos::DeckAll || p == pos::NotLost {
            self.deck_all()
        } else if p == pos::DeckGrave {
            let mut v = self.deck.clone();
            v.extend_from_slice(&self.grave);
            v
        } else if p == pos::Playing || p == pos::PlayHand {
            self.playing.into_iter().collect()
        } else if p == pos::Self_ {
            match ctx.and_then(|c| c.card) {
                Some(c) => vec![c],
                None => vec![],
            }
        } else if p == pos::Target {
            match ctx {
                Some(c) => c.card.or(c.trigger_card).into_iter().collect(),
                None => vec![],
            }
        } else {
            vec![]
        }
    }

    pub fn search_target(&self, c: CardId, s: Option<&SearchRow>) -> bool {
        is_search_target(&self.m, &self.cards[c], s)
    }

    pub fn get_search_card_list(&mut self, search: Option<&SearchRow>, ctx: Option<Ctx>) -> Vec<CardId> {
        let s = match search {
            None => return vec![],
            Some(s) => s,
        };
        let mut cards: Vec<CardId> = self.cards_at(s.position, ctx).into_iter().filter(|&c| self.search_target(c, Some(s))).collect();
        if s.order == 2 {
            cards.reverse();
        } else if s.order == 3 && !cards.is_empty() {
            for _ in 0..cards.len() {
                self.rng.signed();
            }
            let mut keyed: Vec<(i64, usize, CardId)> = cards.iter().enumerate().map(|(i, &c)| (self.rng.signed(), i, c)).collect();
            keyed.sort();
            cards = keyed.into_iter().map(|x| x.2).collect();
        }
        if s.limit != 0 && cards.len() as i64 > s.limit {
            cards.truncate(s.limit as usize);
        }
        cards
    }

    pub fn search(&self, id: Sym) -> Option<Arc<SearchRow>> {
        self.m.search(id).cloned()
    }

    pub fn zone_of(&self, c: CardId) -> Option<Zone> {
        if self.hand.contains(&c) {
            Some(Zone::Hand)
        } else if self.deck.contains(&c) {
            Some(Zone::Deck)
        } else if self.grave.contains(&c) {
            Some(Zone::Grave)
        } else if self.lost.contains(&c) {
            Some(Zone::Lost)
        } else if self.hold.contains(&c) {
            Some(Zone::Hold)
        } else if self.playing == Some(c) {
            Some(Zone::Playing)
        } else {
            None
        }
    }

    pub fn remove_card(&mut self, c: CardId) -> Option<Zone> {
        let z = self.zone_of(c);
        match z {
            Some(Zone::Playing) => self.playing = None,
            Some(z) => {
                let v = self.zone_vec(z);
                let i = v.iter().position(|&x| x == c).unwrap();
                v.remove(i);
            }
            None => {}
        }
        z
    }

    fn hand_limit(&self) -> usize {
        self.setting.hand_limit as usize
    }

    // ------------------------------------------------------------------ 生成 / 移动
    pub fn add_card(&mut self, cards: &[CardId], p: i64, _ctx: Option<Ctx>) {
        let mut added_hand = vec![];
        for &c in cards {
            if p == mv::DeckRandom {
                let i = self.rng.integer(0, self.deck.len() as i64) as usize;
                self.deck.insert(i, c);
            } else if p == mv::DeckFirst {
                self.deck.insert(0, c);
            } else if p == mv::DeckLast {
                self.deck.push(c);
            } else if p == mv::Hand {
                if self.hand.len() < self.hand_limit() {
                    self.hand.push(c);
                    added_hand.push(c);
                } else {
                    self.deck.insert(0, c);
                }
            } else if p == mv::Grave {
                self.grave.push(c);
            } else if p == mv::Lost {
                self.lost.push(c);
            } else if p == mv::Hold {
                self.hold.push(c);
            }
        }
        if !added_hand.is_empty() {
            self.note_diff(Ev::HandAdd { cards: added_hand.clone(), frm: 0, etype: 0 });
        }
        if p != mv::Hand {
            self.note_diff(Ev::Move { cards: cards.to_vec(), pos: p });
        }
        if !added_hand.is_empty() {
            self.support_upgrade(&added_hand);
        }
    }

    pub fn move_card(&mut self, cards: &[CardId], p: i64, _ctx: Option<Ctx>) {
        let mut cards: Vec<CardId> = cards.to_vec();
        if p == mv::DeckFirst {
            let mut groups: Vec<(Option<Zone>, Vec<CardId>)> = vec![];
            for &c in &cards {
                let z = self.zone_of(c);
                match groups.iter_mut().find(|g| g.0 == z) {
                    Some(g) => g.1.push(c),
                    None => groups.push((z, vec![c])),
                }
            }
            cards = groups.into_iter().flat_map(|g| g.1).collect();
        }
        let mut added_hand = vec![];
        let mut top = vec![];
        if p == mv::Hand {
            let room = self.setting.hand_limit - self.hand.len() as i64;
            let n = cards.len() as i64;
            let take: Vec<CardId> = if 0 < room && room < n {
                cards[(n - room) as usize..].to_vec()
            } else if room >= n {
                cards.clone()
            } else {
                vec![]
            };
            for &c in &cards {
                if take.contains(&c) {
                    continue;
                }
                if self.zone_of(c) == Some(Zone::Deck) {
                    let i = self.deck.iter().position(|&x| x == c).unwrap();
                    self.deck.remove(i);
                    self.deck.insert(0, c);
                }
            }
            cards = take;
        }
        for &c in &cards {
            let src = self.remove_card(c);
            if src == Some(Zone::Hand) && p != mv::Hand && !self.cards[c].support_upgrades.is_empty() {
                self.cards[c].support_upgrades.clear();
                self.cards[c].invalidate();
            }
            if p == mv::Hand {
                if self.hand.len() < self.hand_limit() {
                    self.hand.push(c);
                    added_hand.push(c);
                } else {
                    self.deck.insert(0, c);
                }
            } else if p == mv::DeckFirst {
                top.push(c);
            } else if p == mv::DeckLast {
                self.deck.push(c);
            } else if p == mv::DeckRandom {
                let i = self.rng.integer(0, self.deck.len() as i64) as usize;
                self.deck.insert(i, c);
            } else if p == mv::Grave {
                self.grave.push(c);
            } else if p == mv::Lost {
                self.lost.push(c);
            } else if p == mv::Hold {
                self.hold.push(c);
            }
        }
        if !top.is_empty() {
            let mut d = top;
            d.extend_from_slice(&self.deck);
            self.deck = d;
        }
        if p != mv::Hand {
            self.note_diff(Ev::Move { cards: cards.clone(), pos: p });
        }
        if !added_hand.is_empty() {
            self.note_diff(Ev::HandAdd { cards: added_hand.clone(), frm: -1, etype: 0 });
            self.support_upgrade(&added_hand);
        }
    }

    pub fn pick_search_cards(&mut self, search: Option<&SearchRow>, pick_range: i64, cmin: i64, cmax: i64, ctx: Option<Ctx>) -> Vec<CardId> {
        let cards = self.get_search_card_list(search, ctx);
        if pick_range == range::All || pick_range == 0 {
            return cards;
        }
        let n = if cmax != 0 { cmax as usize } else { cards.len() };
        if pick_range == range::Random {
            if cards.is_empty() {
                return vec![];
            }
            let lo = if cmin != 0 { cmin } else { 1 };
            let hi = if cmax != 0 { cmax } else if cmin != 0 { cmin } else { 1 };
            let k = self.rng.integer(lo, hi + 1) as usize;
            let mut ranked: Vec<(i64, usize, CardId)> = cards.iter().enumerate().map(|(i, &c)| (self.rng.signed(), i, c)).collect();
            ranked.sort_by(|a, b| (a.0, a.1).cmp(&(b.0, b.1)));
            return ranked.into_iter().take(k).map(|x| x.2).collect();
        }
        cards.into_iter().take(n).collect()
    }

    // ------------------------------------------------------------------ 抽牌
    pub fn shuffle_grave_into_deck(&mut self) {
        if self.grave.is_empty() {
            return;
        }
        let mut g = std::mem::take(&mut self.grave);
        self.rng.shuffle(&mut g);
        self.deck.extend(g);
    }

    pub fn draw_card(&mut self, count: i64, ctx: Option<Ctx>, is_effect: bool) -> Vec<CardId> {
        let limit = self.hand_limit();
        let mut received = vec![];
        for _ in 0..count.max(0) {
            if self.hand.len() >= limit {
                break;
            }
            if self.deck.is_empty() {
                if self.grave.is_empty() {
                    break;
                }
                self.shuffle_grave_into_deck();
            }
            let c = self.deck.remove(0);
            self.hand.push(c);
            received.push(c);
        }
        if !received.is_empty() {
            self.note_diff(Ev::HandAdd { cards: received.clone(), frm: pos::Deck, etype: if is_effect { et::ExamCardDraw } else { 0 } });
            if active(ctx) {
                self.support_upgrade(&received);
            }
        }
        received
    }

    pub fn support_upgrade(&mut self, added: &[CardId]) {
        let param = self.current_parameter_type();
        let mut planned: Vec<(CardId, Sym)> = vec![];
        for &card in added {
            let (cid, up) = (self.cards[card].card_id, self.cards[card].upgrade());
            if self.m.card(cid, up + 1).is_none() {
                continue;
            }
            for i in 0..self.supports.len() {
                let s = self.supports[i].clone();
                if self.turn_used_supports.contains(&s.id) {
                    continue;
                }
                if !(s.filter == 0 || s.filter == param) {
                    continue;
                }
                let roll = self.rng.integer(0, 1000);
                if roll < s.permil {
                    self.turn_used_supports.push(s.id);
                    planned.push((card, s.id));
                }
            }
        }
        for (card, key) in planned {
            let (cid, up) = (self.cards[card].card_id, self.cards[card].upgrade());
            if self.m.card(cid, up + 1).is_some() {
                self.cards[card].support_upgrades.push(key);
                self.cards[card].invalidate();
            }
        }
    }

    // ------------------------------------------------------------------ 触发
    pub fn increment_enchant_phase_count(&mut self, phases: &[i64], ctx: Option<Ctx>) {
        for s in self.status.effects.iter_mut() {
            if let Some(t) = s.trig.as_mut() {
                for &p in phases {
                    if t.trigger.has_phase(p) {
                        t.inc_phase(p);
                    }
                }
            }
        }
        self.inc_card_enchant_phase(phases, &mut |ex, _c, tr| ex.trigger_field_valid(Some(tr), ctx, None));
    }

    /// difftrig.inc_card_enchant_phase
    pub fn inc_card_enchant_phase(&mut self, phases: &[i64], filter: &mut dyn FnMut(&mut Exam, CardId, &TriggerRow) -> bool) {
        for c in self.deck_all() {
            let trig_id = match self.ensure_se(c) {
                Some(se) => se.trigger,
                None => continue,
            };
            let tr = match self.m.trigger(trig_id) {
                Some(t) => t,
                None => continue,
            };
            for &p in phases {
                if tr.has_phase(p) && filter(self, c, &tr) {
                    if let Some(se) = self.ensure_se(c) {
                        se.inc_phase(p);
                    }
                }
            }
        }
    }

    #[inline]
    pub fn available(s: &Status) -> bool {
        let t = s.trig.as_ref().unwrap();
        if t.limit_count >= 0 && t.limit_count < 1 {
            return false;
        }
        if t.limit_in_turn >= 0 && t.limit_in_turn_remain <= 0 {
            return false;
        }
        true
    }

    /// enchants(ex, pred)：可用的附魔 uid（按创建顺序）
    pub fn enchants(&mut self, pred: &mut dyn FnMut(&mut Exam, usize) -> bool) -> Vec<i64> {
        let mut out = vec![];
        let uids: Vec<i64> = self.status.effects.iter().filter(|s| s.is_trigger()).map(|s| s.uid).collect();
        for uid in uids {
            let i = match self.status.effects.iter().position(|s| s.uid == uid) {
                Some(i) => i,
                None => continue,
            };
            if !Exam::available(&self.status.effects[i]) {
                continue;
            }
            if pred(self, i) {
                out.push(uid);
            }
        }
        out
    }

    pub fn trig_of(&self, i: usize) -> Arc<TriggerRow> {
        self.status.effects[i].trig.as_ref().unwrap().trigger.clone()
    }

    pub fn phase_enchants(&mut self, phase: i64, ctx: Option<Ctx>) -> Vec<i64> {
        self.enchants(&mut |ex, i| {
            let tr = ex.trig_of(i);
            tr.has_phase(phase) && ex.trigger_field_valid(Some(&tr), ctx, None)
        })
    }

    pub fn timer_enchants(&mut self, phase: i64) -> Vec<i64> {
        self.enchants(&mut |ex, i| {
            let tr = ex.trig_of(i);
            if tr.has_phase(phase) && ex.trigger_field_valid(Some(&tr), None, None) {
                let tc = ex.status.effects[i].trig.as_ref().unwrap().turn_count;
                tc == tr.v0()
            } else {
                false
            }
        })
    }

    pub fn interval_enchants(&mut self, phase: i64) -> Vec<i64> {
        self.enchants(&mut |ex, i| {
            let tr = ex.trig_of(i);
            if tr.has_phase(phase) && ex.trigger_field_valid(Some(&tr), None, None) {
                let tc = ex.status.effects[i].trig.as_ref().unwrap().turn_count;
                tc % tr.v0_or1() == 0
            } else {
                false
            }
        })
    }

    pub fn remove_timer_triggered(&mut self, phase: i64) {
        let mut i = self.status.effects.len();
        while i > 0 {
            i -= 1;
            let hit = match &self.status.effects[i].trig {
                Some(t) => {
                    let ph0 = t.trigger.phase_types.first().copied();
                    ph0 == Some(phase) && t.turn_count == t.trigger.v0()
                }
                None => false,
            };
            if hit {
                self.status.effects.remove(i);
            }
        }
    }

    fn find_card_by_guid(&self, guid: &str) -> Option<CardId> {
        self.all_cards().into_iter().find(|&c| &*self.cards[c].guid == guid)
    }

    pub fn fire(&mut self, uids: &[i64]) -> Vec<Cmd> {
        let mut cmds = vec![];
        for &uid in uids {
            let i = match self.status.effects.iter().position(|s| s.uid == uid) {
                Some(i) => i,
                None => continue,
            };
            if !Exam::available(&self.status.effects[i]) {
                continue;
            }
            let (item, ov, guid, effects) = {
                let t = self.status.effects[i].trig.as_mut().unwrap();
                if t.limit_count >= 0 {
                    t.limit_count -= 1;
                }
                if t.limit_in_turn >= 0 {
                    t.limit_in_turn_remain -= 1;
                }
                (t.source.item, t.override_type, t.source.card_guid.clone(), t.effects.clone())
            };
            if item != NONE {
                for it in self.items.iter_mut() {
                    if it.0 == item {
                        it.1 += 1;
                    }
                }
            }
            let trigger_card = guid.as_deref().and_then(|g| self.find_card_by_guid(g));
            let ctx = Ctx { card: None, trigger_card, enchant: true, gimmick: false, active: item == NONE && (ov == 30 || ov == 69) };
            for e in effects.iter() {
                cmds.push(Cmd::Effect(e.clone(), Some(ctx)));
            }
        }
        cmds
    }

    // ------------------------------------------------------------------ 命令执行
    pub fn run(&mut self, cmds: Vec<Cmd>) {
        self.queue.extend(cmds);
        while let Some(c) = self.queue.pop_front() {
            self.execute(c);
        }
    }

    pub fn insert(&mut self, cmds: Vec<Cmd>) {
        for c in cmds.into_iter().rev() {
            self.queue.push_front(c);
        }
    }

    fn execute(&mut self, cmd: Cmd) {
        match cmd {
            Cmd::Effect(e, ctx) => {
                self.with_result_triggers(|ex| ex.execute_effect(&e, ctx), ctx, true);
            }
            Cmd::AddGrows(c, grows) => self.add_grow_effects(&[c], &grows, None, false),
            Cmd::AfterPlay(c, ctx, consume) => self.after_play(c, ctx, consume),
            Cmd::MovePlayed(c, after) => self.move_played(c, after),
            Cmd::AfterMove => self.after_move(),
            Cmd::UsePool(c, with_cost) => self.use_pool(c, with_cost),
            Cmd::Panic => self.panic(),
        }
    }

    pub fn observe(&self) -> Obs {
        let mut keys = [0, self.block, 0, 0, 0, 0];
        let mut seen = 0u8;
        for s in &self.status.effects {
            let (k, v) = match s.cls {
                cls::ParameterBuffStatusEffect => (0, s.turn),
                cls::LessonBuffStatusEffect => (2, s.value),
                cls::ReviewStatusEffect => (3, s.turn),
                cls::AggressiveStatusEffect => (4, s.value),
                cls::FullPowerPointStatusEffect => (5, s.value),
                _ => continue,
            };
            if seen & (1 << k) == 0 {
                seen |= 1 << k;
                keys[k] = v;
            }
        }
        Obs {
            stamina: self.stamina,
            score: self.score,
            keys,
            stance: (self.status.idol_type, self.status.idol_step),
            stance_count: self.counters.get(ck::StanceChange),
        }
    }

    pub fn with_result_triggers<F: FnOnce(&mut Exam)>(&mut self, f: F, ctx: Option<Ctx>, insert: bool) -> Vec<Cmd> {
        let outer = self.diff.replace(vec![]);
        let outer_s = self.suppressed.replace(vec![]);
        let pre = self.observe();
        f(self);
        let events = std::mem::replace(&mut self.diff, outer).unwrap_or_default();
        let windows = std::mem::replace(&mut self.suppressed, outer_s).unwrap_or_default();
        let mut pre = pre;
        for (a, b) in &windows {
            pre.stamina += b.stamina - a.stamina;
            pre.score += b.score - a.score;
            for k in 0..6 {
                pre.keys[k] += b.keys[k] - a.keys[k];
            }
        }
        let post = self.observe();
        let cmds = self.result_triggers(pre, post, &events, ctx);
        if insert {
            if !cmds.is_empty() {
                self.insert(cmds);
            }
            vec![]
        } else {
            cmds
        }
    }

    pub fn note_diff(&mut self, ev: Ev) {
        if let Some(d) = self.diff.as_mut() {
            d.push(ev);
        }
    }

    pub fn suppress_triggers<F: FnOnce(&mut Exam)>(&mut self, f: F) {
        let pre = self.observe();
        f(self);
        let post = self.observe();
        if let Some(s) = self.suppressed.as_mut() {
            s.push((pre, post));
        }
    }

    // ------------------------------------------------------------------ 资源
    pub fn add_stamina(&mut self, v: i64) {
        self.stamina = (self.stamina + v).min(self.max_stamina).max(0);
    }

    pub fn add_current_turn_consume_stamina(&mut self, n: i64) {
        self.counters.add(ck::CurrentTurnTotalConsumeStamina, n);
        self.counters.add(ck::TotalConsumeStamina, n);
    }

    pub fn set_block(&mut self, n: i64, is_consume: bool) {
        if is_consume {
            self.counters.add(ck::BlockConsumptionSum, (self.block - n).max(0));
        }
        if n > self.block {
            self.counters.add(ck::CurrentTurnTotalBlock, n - self.block);
        }
        self.block = n;
    }

    pub fn calculate_damage(&self, value: i64, penetrate: bool, use_fix: bool) -> (i64, i64) {
        let sc = &self.status;
        let st = &self.sk;
        let mut x = i2f(value);
        let (t, step) = (sc.idol_type, sc.idol_step);
        let p = if t == 1 {
            Some(if step == 1 { st.conc_stamina1 } else { st.conc_stamina2 })
        } else if t == 2 {
            Some(if step == 1 { st.pres_stamina1 } else { st.pres_stamina2 })
        } else if t == 4 {
            Some(st.over_pres_stamina)
        } else {
            None
        };
        if let Some(p) = p {
            x = permil(p) * x;
        }
        if sc.has(cls::StaminaConsumptionDownStatusEffect) {
            let q = if sc.has(cls::StaminaConsumptionDownAddStatusEffect) { st.stamina_down_add } else { st.stamina_down };
            x *= (1000.0f32 - i2f(q)) / 1000.0f32;
        }
        if sc.has(cls::StaminaConsumptionAddStatusEffect) {
            let q = if sc.has(cls::StaminaConsumptionAddDownStatusEffect) { st.stamina_add_down } else { st.stamina_add };
            x *= (i2f(q) + 1000.0f32) / 1000.0f32;
        }
        let mut v = ceil_i(x);
        if use_fix {
            v += sc.stamina_add_fix().max(0);
            let d = sc.stamina_down_fix();
            if d >= 1 {
                v = (v - d).max(0);
            }
            if v <= sc.stamina_reduce_change() && sc.stamina_reduce_change() >= 1 {
                v = self.setting.stamina_reduce_change;
            }
        }
        let block = if penetrate { 0 } else { self.block };
        let dmg = (v - block).max(0);
        (dmg, v - dmg)
    }

    pub fn damage_stamina(&mut self, value: i64, penetrate: bool, _ctx: Option<Ctx>) {
        let (dmg, blk) = self.calculate_damage(value, penetrate, true);
        let before = self.stamina;
        if blk >= 1 {
            self.set_block((self.block - blk).max(0), true);
        }
        if dmg >= 1 {
            self.stamina = (self.stamina - dmg).min(self.max_stamina).max(0);
            self.add_current_turn_consume_stamina(before - self.stamina);
        }
        if dmg >= 1 || blk >= 1 {
            self.note_diff(Ev::Stamina { mix: dmg >= 1 && blk >= 1 });
        }
    }

    pub fn reduce_stamina_fix(&mut self, v: i64) -> bool {
        if v < 1 {
            return false;
        }
        let before = self.stamina;
        self.stamina = (self.stamina - v).max(0);
        self.add_current_turn_consume_stamina(before - self.stamina);
        self.stamina != before
    }

    pub fn calculate_buff_cost(&self, value: i64) -> i64 {
        let sc = &self.status;
        let mut x = i2f(value);
        if sc.has(cls::BuffConsumptionDownStatusEffect) {
            x = ((1000.0f32 - i2f(self.sk.buff_down)) / 1000.0f32) * x;
        }
        if sc.has(cls::BuffConsumptionAddStatusEffect) {
            x *= (i2f(self.sk.buff_add) + 1000.0f32) / 1000.0f32;
        }
        ceil_i(x)
    }

    pub fn get_cost_buff_value(&self, cost_type: i64) -> i64 {
        let sc = &self.status;
        if cost_type == cost::ExamLessonBuff {
            sc.lesson_buff()
        } else if cost_type == cost::ExamReview {
            sc.review()
        } else if cost_type == cost::ExamCardPlayAggressive {
            sc.aggressive()
        } else if cost_type == cost::ExamParameterBuff {
            sc.parameter_buff_turn()
        } else if cost_type == cost::ExamFullPowerPoint {
            sc.full_power_point()
        } else if cost_type == cost::ExamParameterBuffMultiplePerTurn {
            sc.parameter_buff_mpt_turn()
        } else {
            0
        }
    }

    pub fn cost_key(cost_type: i64) -> i64 {
        match cost_type {
            1 => 10,
            2 => 31,
            3 => 42,
            4 => 2,
            5 => 49,
            6 => 129,
            _ => panic!("cost type {cost_type}"),
        }
    }

    pub fn consume_buff_cost(&mut self, cost_type: i64, value: i64) {
        let c = self.calculate_buff_cost(value);
        let before = self.get_cost_buff_value(cost_type);
        if cost_type == cost::ExamLessonBuff {
            self.status.consume_value(cls::LessonBuffStatusEffect, c);
        } else if cost_type == cost::ExamReview {
            self.consume_review(c);
        } else if cost_type == cost::ExamCardPlayAggressive {
            self.status.consume_value(cls::AggressiveStatusEffect, c);
        } else if cost_type == cost::ExamParameterBuff {
            self.status.consume_turn(cls::ParameterBuffStatusEffect, c);
        } else if cost_type == cost::ExamFullPowerPoint {
            self.status.consume_value(cls::FullPowerPointStatusEffect, c);
        } else if cost_type == cost::ExamParameterBuffMultiplePerTurn {
            self.status.consume_turn(cls::ParameterBuffMultiplePerTurnStatusEffect, c);
        } else {
            panic!("cost type {cost_type}");
        }
        let after = self.get_cost_buff_value(cost_type);
        if after != before {
            self.note_diff(Ev::Status);
        }
    }

    pub fn consume_review(&mut self, n: i64) {
        let before = self.status.review();
        self.status.consume_turn(cls::ReviewStatusEffect, n);
        let d = before - self.status.review();
        self.counters.add(ck::ReviewConsumptionSum, d);
    }

    // ---- TryAdd（含阶段计数与关卡钩子）
    pub fn try_add(&mut self, rule: &Rule, value: i64, turn: i64, ctx: Option<Ctx>, is_fix: bool) -> bool {
        let m = self.m.clone();
        let added = self.status.try_add_raw(rule, value, turn, is_fix, &m.debuff_effect_types);
        if !added {
            return false;
        }
        if rule.phase >= 0 {
            if let Some(c) = ctx {
                if c.active {
                    self.increment_enchant_phase_count(&[rule.phase], ctx);
                }
            }
        }
        true
    }

    /// TryAddReviewStatus（比赛关卡 191 包了一层：卡的直接效果每第 4 次增加好印象）
    pub fn try_add_review(&mut self, turn: i64, ctx: Option<Ctx>, is_fix: bool) -> bool {
        let b = self.status.review();
        let r = self.try_add(&rules::Review, 0, turn, ctx, is_fix);
        if let Some(cs) = &self.contest {
            if cs.id == 191 {
                if let Some(c) = ctx {
                    if self.status.review() > b && c.card.is_some() && !c.enchant && !c.gimmick {
                        self.contest_on_direct_review();
                    }
                }
            }
        }
        r
    }

    pub fn try_add_value(&mut self, rule: &Rule, value: i64, ctx: Option<Ctx>) -> bool {
        self.try_add(rule, value, -1, ctx, false)
    }
    pub fn try_add_turn(&mut self, rule: &Rule, turn: i64, ctx: Option<Ctx>) -> bool {
        if rule.cls == cls::ReviewStatusEffect {
            return self.try_add_review(turn, ctx, false);
        }
        self.try_add(rule, 0, turn, ctx, false)
    }
    pub fn try_add_vt(&mut self, rule: &Rule, value: i64, turn: i64, ctx: Option<Ctx>) -> bool {
        self.try_add(rule, value, turn, ctx, false)
    }

    pub fn try_add_trigger_effect(&mut self, trigger: Arc<TriggerRow>, effects: Arc<Vec<Arc<EffectRow>>>, origin_type: i64, turn: i64,
                                  limit_count: i64, limit_in_turn: i64, enchant_id: Sym, ctx: Option<Ctx>, override_type: i64) -> i64 {
        let mut source = Source::default();
        if let Some(c) = ctx.and_then(|c| c.card) {
            source.card = self.cards[c].card_id;
            source.card_guid = Some(self.cards[c].guid.clone());
        }
        let st = StatusCollection::trigger_effect(trigger, effects, origin_type, turn, limit_count, limit_in_turn, enchant_id, source, override_type);
        self.status.add(st)
    }

    // ------------------------------------------------------------------ 主阶段动作
    pub fn legal_actions(&mut self) -> Vec<Action> {
        let mut acts = vec![];
        if self.ended {
            return acts;
        }
        if self.remaining_actions > 0 {
            for i in 0..self.hand.len() {
                let c = self.hand[i];
                if self.card_playable(c) {
                    acts.push(Action::Play(i));
                }
            }
            for i in 0..self.drinks.len() {
                acts.push(Action::Drink(i));
            }
        }
        acts.push(Action::End);
        acts
    }

    pub fn card_playable(&mut self, card: CardId) -> bool {
        let r = self.row(card);
        if r.base.is_restrict {
            return false;
        }
        let ctx = Ctx::card(card);
        if let Some(tr) = r.play_trigger_row.clone() {
            if !self.trigger_field_valid(Some(&tr), Some(ctx), None) {
                return false;
            }
        }
        if self.is_search_play_card_limit(card) || self.is_play_card_limit_pva_limit(card) {
            return false;
        }
        if r.base.cost_type != 0 {
            return self.get_cost_buff_value(r.base.cost_type) >= self.calculate_buff_cost(r.cost_value);
        }
        let pen = r.penetrate;
        let (dmg, blk) = self.calculate_damage(self.cards[card].stamina_cost(&self.m), pen, true);
        dmg + blk <= self.stamina + if pen { 0 } else { self.block }
    }

    fn search_hit(&self, st: &Status, card: CardId) -> bool {
        let sid = st.extra.search_id.unwrap_or(NONE);
        let s = if sid != NONE { self.m.search(sid) } else { None };
        self.search_target(card, s.map(|x| &**x))
    }

    pub fn is_search_play_card_limit(&self, card: CardId) -> bool {
        self.status.effects.iter().filter(|s| s.cls == cls::SearchPlayCardLimitStatusEffect).any(|s| self.search_hit(s, card))
    }

    pub fn is_play_card_limit_pva_limit(&self, card: CardId) -> bool {
        match self.status.first(cls::PlayCardLimitPlayableValueAddStatusEffect) {
            Some(s) => !self.search_hit(s, card),
            None => false,
        }
    }

    pub fn use_search_play_card_consumption_change(&mut self, c: u16, card: CardId) -> i64 {
        let mut best: Option<usize> = None;
        for i in 0..self.status.effects.len() {
            let st = &self.status.effects[i];
            if st.cls != c || !self.search_hit(st, card) {
                continue;
            }
            match best {
                None => best = Some(i),
                Some(b) => {
                    let bs = &self.status.effects[b];
                    if st.limited && (!bs.limited || st.turn < bs.turn) {
                        best = Some(i);
                    }
                }
            }
        }
        let b = match best {
            None => return -1,
            Some(b) => b,
        };
        let value = self.status.effects[b].value;
        let n = self.status.effects[b].extra.count.unwrap_or(0) - 1;
        if n >= 0 {
            self.status.effects[b].extra.count = Some(n);
            if n == 0 {
                self.status.effects.remove(b);
            }
        }
        value
    }

    pub fn play(&mut self, card: CardId) {
        if self.contest.is_some() {
            self.contest_before_card();
        }
        let ctx = Ctx::card(card);
        let i = self.hand.iter().position(|&x| x == card).unwrap();
        self.hand.remove(i);
        self.playing = Some(card);
        self.remaining_actions -= 1;
        let mut cmds = vec![];
        self.increment_play_counts(card);
        let pe = self.card_play_enchants(card, ctx);
        cmds.extend(self.fire(&pe));
        let ev = [card];
        cmds.extend(self.card_enchant_cmds(&mut |ex, _c, tr| ex.card_enchant_play_valid(tr, _c, card, ctx), Some(&ev)));
        let repeats = 1 + self.use_play_count_buff(card);
        let r = self.row(card);
        let play_count = self.cards[card].play_count;
        for rep in 0..repeats {
            for (tr, e, once) in r.play_effects.iter() {
                if *once && (rep > 0 || play_count > 0) {
                    continue;
                }
                let ok = match tr {
                    None => true,
                    Some(t) => self.trigger_field_valid(Some(t), Some(ctx), None),
                };
                if ok {
                    cmds.push(Cmd::Effect(e.clone(), Some(ctx)));
                }
            }
        }
        let mut consume: Vec<Cmd> = vec![];
        let front = {
            let consume_ref = &mut consume;
            self.with_result_triggers(
                |ex| {
                    consume_ref.extend(ex.consume_card_cost(card, ctx));
                    if ex.status.idol_type == 1 {
                        let v = if ex.status.idol_step == 1 { ex.sk.conc_pen1 } else { ex.sk.conc_pen2 };
                        ex.suppress_triggers(|ex| {
                            ex.reduce_stamina_fix(v);
                        });
                    }
                },
                Some(ctx),
                false,
            )
        };
        let mut all = front;
        all.extend(cmds);
        all.push(Cmd::AfterPlay(card, ctx, consume));
        self.run(all);
        if self.contest.is_some() {
            // after_card：无动作
        }
    }

    /// settle_from_save 用：费用已付，不再扣
    pub fn play_no_cost(&mut self, card: CardId) {
        self.skip_cost = true;
        self.play(card);
        self.skip_cost = false;
    }

    pub fn consume_card_cost(&mut self, card: CardId, ctx: Ctx) -> Vec<Cmd> {
        if self.skip_cost {
            return vec![];
        }
        let r = self.row(card);
        if r.base.cost_type != 0 {
            let mut v = self.use_search_play_card_consumption_change(cls::SearchPlayCardBuffConsumptionChangeStatusEffect, card);
            if v == -1 {
                v = r.cost_value;
            }
            if v < 1 {
                return vec![];
            }
            self.consume_buff_cost(r.base.cost_type, v);
            let key = Exam::cost_key(r.base.cost_type);
            let ench = self.enchants(&mut |ex, i| {
                let tr = ex.trig_of(i);
                ex.consume_trigger(&tr, key, Some(ctx))
            });
            let mut out = self.fire(&ench);
            out.extend(self.card_enchant_cmds(&mut |ex, _c, tr| ex.consume_trigger(tr, key, Some(ctx)), None));
            return out;
        }
        let mut v = self.use_search_play_card_consumption_change(cls::SearchPlayCardStaminaConsumptionChangeStatusEffect, card);
        if v == -1 {
            v = self.cards[card].stamina_cost(&self.m);
        }
        self.damage_stamina(v, r.penetrate, Some(ctx));
        vec![]
    }

    const COUNT_PHASES: [i64; 3] = [ph::ExamPlayCountInterval, ph::ExamPlayTurnCountInterval, ph::ExamPlayCountIntervalAfter];

    pub fn increment_play_counts(&mut self, card: CardId) {
        let m = self.m.clone();
        for i in 0..self.status.effects.len() {
            let hit: Vec<i64> = match &self.status.effects[i].trig {
                Some(t) => Exam::COUNT_PHASES.iter().copied().filter(|p| t.trigger.has_phase(*p)).collect(),
                None => continue,
            };
            // Python 版按触发阶段顺序列出命中的计数阶段
            let hit: Vec<i64> = {
                let t = self.status.effects[i].trig.as_ref().unwrap();
                t.trigger.phase_types.iter().copied().filter(|p| hit.contains(p)).collect()
            };
            if hit.is_empty() {
                continue;
            }
            let sid = self.status.effects[i].trig.as_ref().unwrap().trigger.search;
            if sid != NONE && !self.search_target(card, m.search(sid).map(|x| &**x)) {
                continue;
            }
            let t = self.status.effects[i].trig.as_mut().unwrap();
            for p in hit {
                t.inc_phase(p);
            }
        }
        self.inc_card_enchant_phase(&Exam::COUNT_PHASES, &mut |ex, c, tr| {
            if tr.search == NONE {
                return true;
            }
            let s = ex.m.search(tr.search).cloned();
            let srch = s.as_deref();
            (srch.map(|x| x.is_self).unwrap_or(false) && c == card) || ex.search_target(card, srch)
        });
    }

    pub fn card_play_enchants(&mut self, card: CardId, ctx: Ctx) -> Vec<i64> {
        self.enchants(&mut |ex, i| {
            let tr = ex.trig_of(i);
            if !ex.target_search_ok(&tr, card) {
                return false;
            }
            if tr.has_phase(ph::ExamCardPlay) {
                ex.trigger_field_valid(Some(&tr), Some(ctx), Some(&[card]))
            } else if tr.has_phase(ph::ExamPlayCountInterval) || tr.has_phase(ph::ExamPlayTurnCountInterval) {
                let p = if tr.has_phase(ph::ExamPlayCountInterval) { ph::ExamPlayCountInterval } else { ph::ExamPlayTurnCountInterval };
                let n = ex.status.effects[i].trig.as_ref().unwrap().phase(p);
                let v = tr.v0_or1();
                n >= 1 && n % v == 0 && ex.trigger_field_valid(Some(&tr), Some(ctx), Some(&[card]))
            } else {
                false
            }
        })
    }

    pub fn card_play_after_enchants(&mut self, card: CardId, ctx: Ctx) -> Vec<i64> {
        self.enchants(&mut |ex, i| {
            let tr = ex.trig_of(i);
            if !ex.target_search_ok(&tr, card) {
                return false;
            }
            if tr.has_phase(ph::ExamCardPlayAfter) {
                ex.trigger_field_valid(Some(&tr), Some(ctx), Some(&[card]))
            } else if tr.has_phase(ph::ExamPlayCountIntervalAfter) {
                let n = ex.status.effects[i].trig.as_ref().unwrap().phase(ph::ExamPlayCountIntervalAfter);
                let v = tr.v0_or1();
                n >= 1 && n % v == 0 && ex.trigger_field_valid(Some(&tr), Some(ctx), Some(&[card]))
            } else {
                false
            }
        })
    }

    fn card_se_phase(&self, c: CardId, p: i64) -> i64 {
        match &self.cards[c].se {
            crate::card::Se::Has(se) => se.phase(p),
            _ => 0,
        }
    }

    pub fn card_enchant_play_valid(&mut self, tr: &TriggerRow, se_card: CardId, card: CardId, ctx: Ctx) -> bool {
        if !self.target_search_ok(tr, card) {
            return false;
        }
        if tr.has_phase(ph::ExamCardPlay) {
            return self.trigger_field_valid(Some(tr), Some(ctx), Some(&[card]));
        }
        for p in [ph::ExamPlayCountInterval, ph::ExamPlayTurnCountInterval] {
            if tr.has_phase(p) {
                let n = self.card_se_phase(se_card, p);
                return self.interval_trigger(tr, p, n, Some(ctx), true);
            }
        }
        false
    }

    pub fn card_enchant_play_after_valid(&mut self, tr: &TriggerRow, se_card: CardId, card: CardId, ctx: Ctx) -> bool {
        if !self.target_search_ok(tr, card) {
            return false;
        }
        if tr.has_phase(ph::ExamCardPlayAfter) {
            return self.trigger_field_valid(Some(tr), Some(ctx), Some(&[card]));
        }
        if tr.has_phase(ph::ExamPlayCountIntervalAfter) {
            let n = self.card_se_phase(se_card, ph::ExamPlayCountIntervalAfter);
            return self.interval_trigger(tr, ph::ExamPlayCountIntervalAfter, n, Some(ctx), true);
        }
        false
    }

    pub fn use_play_count_buff(&mut self, card: CardId) -> i64 {
        let m = self.m.clone();
        for i in 0..self.status.effects.len() {
            if self.status.effects[i].cls != cls::PlayCountBuffStatusEffect {
                continue;
            }
            let sid = self.status.effects[i].extra.search_id.unwrap_or(NONE);
            if sid == NONE || self.search_target(card, m.search(sid).map(|x| &**x)) {
                let s = &mut self.status.effects[i];
                let n = s.extra.count.unwrap_or(1) - 1;
                s.extra.count = Some(n);
                let v = s.value;
                if n <= 0 {
                    self.status.effects.remove(i);
                }
                return v;
            }
        }
        0
    }

    fn after_play(&mut self, card: CardId, ctx: Ctx, consume: Vec<Cmd>) {
        self.exam_card_play_count += 1;
        self.turn_card_play_count += 1;
        self.cards[card].play_count += 1;
        self.play_log.push(card);
        let pe = self.card_play_after_enchants(card, ctx);
        let mut after = self.fire(&pe);
        let ev = [card];
        after.extend(self.card_enchant_cmds(&mut |ex, c, tr| ex.card_enchant_play_after_valid(tr, c, card, ctx), Some(&ev)));
        let mut all = consume;
        all.extend(after);
        self.insert(vec![Cmd::MovePlayed(card, all)]);
    }

    fn move_played(&mut self, card: CardId, after: Vec<Cmd>) {
        if self.playing == Some(card) {
            self.playing = None;
        }
        if !self.cards[card].support_upgrades.is_empty() {
            self.cards[card].support_upgrades.clear();
            self.cards[card].invalidate();
        }
        let lost = self.row(card).play_move == mv::Lost;
        let front = self.with_result_triggers(
            |ex| {
                if ex.zone_of(card).is_some() {
                    return;
                }
                if lost {
                    ex.lost.push(card);
                } else {
                    ex.grave.push(card);
                }
                ex.note_diff(Ev::Move { cards: vec![card], pos: if lost { mv::Lost } else { mv::Grave } });
            },
            Some(Ctx::card(card)),
            false,
        );
        let mut all = front;
        all.extend(after);
        all.push(Cmd::AfterMove);
        self.insert(all);
    }

    fn after_move(&mut self) {
        if let Some(i) = self.status.first_idx(cls::PlayableValueAddStatusEffect) {
            if self.status.effects[i].value > 0 {
                self.status.effects[i].value -= 1;
                self.remaining_actions += 1;
                if self.status.effects[i].value <= 0 {
                    self.status.effects.remove(i);
                }
            }
        }
        if self.remaining_actions <= 0 {
            self.end_turn(false);
        }
    }

    // ------------------------------------------------------------------ 指针
    fn inc_stance_change_count(&mut self, ctx: Option<Ctx>) {
        self.counters.add(ck::StanceChange, 1);
        if active(ctx) {
            let phases = [ph::ExamStanceChangeCount, ph::ExamStanceChangeCountInterval];
            self.increment_enchant_phase_count(&phases, ctx);
            if !CARD_STANCE_COUNT_DOUBLE {
                return;
            }
            for c in self.deck_all() {
                let trig = match self.ensure_se(c) {
                    Some(se) => se.trigger,
                    None => continue,
                };
                let hit = self.m.trigger(trig).map(|t| phases.iter().any(|p| t.has_phase(*p))).unwrap_or(false);
                if hit {
                    let se = self.ensure_se(c).unwrap();
                    se.inc_phase(ph::ExamStanceChangeCount);
                    se.inc_phase(ph::ExamStanceChangeCountInterval);
                }
            }
        }
    }

    pub fn set_stance(&mut self, stance: i64, step: i64, ctx: Option<Ctx>, fp_consume: bool) -> bool {
        let (cur, cur_step) = (self.status.idol_type, self.status.idol_step);
        let sc = &self.status;
        if cur == stance && sc.is_step_max() {
            return false;
        }
        if stance == 2 && cur == 4 {
            return false;
        }
        if sc.stance_lock() > 0 {
            return false;
        }
        if stance == 3 && sc.last_turn(cls::StanceLockFullPowerEffect) > 0 {
            return false;
        }
        if stance == 1 && sc.last_turn(cls::StanceLockConcentrationEffect) >= 1 {
            return false;
        }
        if (stance == 2 || stance == 4) && sc.last_turn(cls::StanceLockPreservationEffect) > 0 {
            return false;
        }
        let act = active(ctx);
        if stance == 3 && fp_consume {
            self.status.consume_value(cls::FullPowerPointStatusEffect, 10);
        }
        if (cur == 2 || cur == 4) && !(stance == 2 || stance == 4) {
            self.suppress_triggers(|ex| ex.preservation_release(cur, cur_step, stance, ctx));
        }
        if stance == 4 {
            if !(cur == 2 || cur == 4) {
                self.inc_stance_change_count(ctx);
                self.counters.add(ck::StancePreservationChange, 1);
                if act {
                    self.increment_enchant_phase_count(&[ph::ExamStanceChangePreservationInterval], ctx);
                }
            }
            self.status.idol_type = 4;
            self.status.idol_step = 1;
        } else if stance == 2 {
            let new_step = (step + if cur == 2 { 1 } else { 0 }).min(2);
            if !(cur == 2 || cur == 4) {
                self.inc_stance_change_count(ctx);
                self.counters.add(ck::StancePreservationChange, 1);
                if act {
                    self.increment_enchant_phase_count(&[ph::ExamStanceChangePreservationInterval], ctx);
                }
            }
            self.status.idol_type = 2;
            self.status.idol_step = new_step;
        } else if stance == 3 {
            self.inc_stance_change_count(ctx);
            self.counters.add(ck::StanceFullPowerChange, 1);
            if act {
                self.increment_enchant_phase_count(&[ph::ExamStanceChangeFullPowerInterval], ctx);
            }
            self.status.idol_type = 3;
            self.status.idol_step = 1;
            let n = self.sk.full_power_pva;
            self.add_playable_value_add(n);
        } else if stance == 1 {
            let new_step = (step + if cur == 1 { 1 } else { 0 }).min(2);
            if cur != 1 {
                self.inc_stance_change_count(ctx);
                self.counters.add(ck::StanceConcentrationChange, 1);
                if act {
                    self.increment_enchant_phase_count(&[ph::ExamStanceChangeConcentrationInterval], ctx);
                }
            }
            self.status.idol_type = 1;
            self.status.idol_step = new_step;
        } else if stance == 0 {
            self.status.idol_type = 0;
            self.status.idol_step = 0;
        }
        true
    }

    fn preservation_release(&mut self, cur: i64, cur_step: i64, stance: i64, ctx: Option<Ctx>) {
        let s = self.sk.clone();
        let blk = if cur == 4 {
            self.add_playable_value_add(s.over_rel_pva);
            s.over_rel_block
        } else if cur_step == 1 {
            self.add_playable_value_add(s.rel_pva1);
            s.rel_block1
        } else {
            self.add_playable_value_add(s.rel_pva2);
            s.rel_block2
        };
        if blk != 0 {
            self.add_block_fix(blk);
        }
        let before = self.status.enthusiastic();
        if cur == 4 && stance == 3 {
            let v = s.over_rel_full_lesson;
            if v != 0 {
                let g = crate::master::GrowRow {
                    id: crate::sym::intern("over_preservation_release"),
                    effect_type: crate::enums::gt::LessonAdd,
                    value: v,
                    ..Default::default()
                };
                let mult = self.status.multiple(cls::GrowEffectLessonAddAdditiveStatusEffect);
                for c in self.deck_all() {
                    if crate::card::grow_valid(&self.m, &self.cards[c], &g) {
                        self.cards[c].add_grow(&g, mult);
                    }
                }
            }
            return;
        }
        let base = if cur == 4 {
            s.over_rel_enth
        } else if cur_step == 1 {
            s.rel_enth1
        } else {
            s.rel_enth2
        };
        let add = self.status.sum(cls::EnthusiasticAdditiveStatusEffect);
        let mul = self.status.multiple(cls::EnthusiasticMultipleStatusEffect);
        let v = if add == 0 && ((mul as f64) - 1.0).abs() < 1e-6 { base } else { ceil_i(i2f(add + base) * mul) };
        if v != 0 && self.try_add_vt(&rules::Enthusiastic, v, 1, ctx) {
            let d = self.status.enthusiastic() - before;
            self.counters.add(ck::EnthusiasticGetSum, d);
        }
    }

    pub fn add_playable_value_add(&mut self, n: i64) {
        if n != 0 {
            self.status.try_add_playable_value_add(n);
        }
    }

    pub fn unset_full_power(&mut self) {
        self.status.idol_type = 0;
        self.status.idol_step = 0;
    }

    pub fn full_power_hold_to_hand(&mut self, ctx: Option<Ctx>) {
        let n = (self.setting.hand_limit - self.hand.len() as i64).min(2);
        if n > 0 && !self.hold.is_empty() {
            let take: Vec<CardId> = self.hold.iter().take(n as usize).copied().collect();
            self.move_card(&take, mv::Hand, ctx);
        }
    }

    pub fn add_grow_effects(&mut self, cards: &[CardId], grow_ids: &[Sym], _ctx: Option<Ctx>, valid_check: bool) {
        let grows: Vec<crate::master::GrowRow> = grow_ids.iter().map(|g| self.m.grow(*g).clone()).collect();
        let plan: Vec<(CardId, Vec<usize>)> = cards
            .iter()
            .map(|&c| (c, (0..grows.len()).filter(|&i| !valid_check || crate::card::grow_valid(&self.m, &self.cards[c], &grows[i])).collect()))
            .collect();
        let mult = self.status.multiple(cls::GrowEffectLessonAddAdditiveStatusEffect);
        for (c, gs) in plan {
            for i in gs {
                self.cards[c].add_grow(&grows[i], mult);
            }
        }
    }

    pub fn use_pool(&mut self, card: CardId, _with_cost: bool) {
        if self.zone_of(card).is_none() {
            return;
        }
        self.remove_card(card);
        self.hand.push(card);
        let actions = self.remaining_actions;
        self.remaining_actions += 1;
        self.play(card);
        self.remaining_actions = actions;
    }

    pub fn drink(&mut self, index: usize) {
        let d = self.drinks.remove(index);
        let ctx = Ctx::base();
        let mut cmds = vec![];
        let m = self.m.clone();
        if let Some(effs) = m.drinks.get(&d) {
            for key in effs {
                if let Some(&eid) = m.drink_effects.get(key) {
                    if eid != NONE {
                        cmds.push(Cmd::Effect(m.effect(eid), Some(ctx)));
                    }
                }
            }
        }
        self.run(cmds);
    }

    pub fn step(&mut self, a: Action) {
        self.advance();
        match a {
            Action::Play(i) => {
                let c = self.hand[i];
                self.play(c);
            }
            Action::Drink(i) => self.drink(i),
            Action::End => self.end_turn(true),
        }
    }

    // ------------------------------------------------------------------ 回合
    pub fn start_turn(&mut self) {
        if let Some(cs) = self.contest.as_mut() {
            cs.counter = 0;
        }
        self.current_turn += 1;
        self.remain_turn = self.limit_turn + self.extra_turn - self.current_turn + 1;
        self.turn_card_play_count = 0;
        self.turn_used_supports.clear();
        for k in [ck::CurrentTurnTotalConsumeStamina, ck::CurrentTurnTotalBlock, ck::CurrentTurnTotalAddParameter, ck::TotalDrawCardCount] {
            if self.counters.v[k].is_some() {
                self.counters.v[k] = Some(0);
            }
        }
        for s in self.status.effects.iter_mut() {
            if let Some(t) = s.trig.as_mut() {
                if t.has_phase_key(ph::ExamPlayTurnCountInterval) {
                    t.set_phase(ph::ExamPlayTurnCountInterval, 0);
                }
            }
        }
        self.remaining_actions = 1;
        if self.current_turn == 1 {
            let tp = [ph::ExamTurnTimer, ph::ExamTurnInterval, ph::ExamEndTurnTimer, ph::ExamEndTurnInterval];
            for s in self.status.effects.iter_mut() {
                if let Some(t) = s.trig.as_mut() {
                    if t.trigger.phase_types.iter().any(|p| tp.contains(p)) {
                        t.increment_turn_count();
                    }
                }
            }
        }
        let base = Ctx::base();
        let cmds = self.with_result_triggers(
            |ex| {
                if ex.status.idol_type == 3 {
                    ex.unset_full_power();
                }
                if ex.status.full_power_point() >= 10 {
                    ex.set_stance(3, 1, Some(base), true);
                }
            },
            Some(base),
            false,
        );
        self.run(cmds);
        if self.current_turn >= 2 {
            for c in self.deck_all() {
                self.cards[c].spend_turn();
            }
        }
        self.run_gimmick();
        let st = self.phase_enchants(ph::ExamStartTurn, Some(base));
        let mut cmds = self.fire(&st);
        cmds.extend(self.card_enchant_cmds(&mut |ex, _c, tr| ex.phase_trigger(Some(tr), ph::ExamStartTurn, Some(base)), None));
        self.run(cmds);
        let mut enchants: Vec<i64> = vec![];
        if self.current_turn <= 1 {
            enchants.extend(self.phase_enchants(ph::StartExamPlay, Some(base)));
        }
        for u in self.phase_enchants(ph::StartPlay, Some(base)) {
            if !enchants.contains(&u) {
                enchants.push(u);
            }
        }
        for u in self.timer_enchants(ph::ExamTurnTimer) {
            if !enchants.contains(&u) {
                enchants.push(u);
            }
        }
        if self.current_turn >= 2 {
            for u in self.interval_enchants(ph::ExamTurnInterval) {
                if !enchants.contains(&u) {
                    enchants.push(u);
                }
            }
        }
        let card_phases: Vec<i64> = if self.current_turn <= 1 { vec![ph::StartExamPlay, ph::StartPlay] } else { vec![ph::StartPlay] };
        let draw = (self.setting.turn_start_distribute - self.status.start_turn_draw_down()).max(0);
        self.queue_after_draw = vec![];
        let cmds = self.with_result_triggers(
            |ex| {
                if ex.current_turn < 2 && ex.initial_deal {
                    ex.set_initial_card(draw, Some(base));
                } else {
                    let got = ex.draw_card(draw, None, false);
                    if !got.is_empty() && ex.status.has(cls::PanicStatusEffect) {
                        ex.queue_after_draw.push(Cmd::Panic);
                    }
                }
                if ex.status.idol_type == 3 {
                    ex.full_power_hold_to_hand(Some(base));
                }
            },
            Some(base),
            false,
        );
        self.run(cmds);
        let qad = std::mem::take(&mut self.queue_after_draw);
        self.run(qad);
        let mut cmds = self.fire(&enchants);
        let cur = self.current_turn;
        cmds.extend(self.card_enchant_cmds(
            &mut |ex, _c, tr| {
                for &p in &card_phases {
                    if ex.phase_trigger(Some(tr), p, Some(base)) {
                        return true;
                    }
                }
                if tr.has_phase(ph::ExamTurnTimer) && tr.v0() == cur {
                    return ex.trigger_field_valid(Some(tr), Some(base), None);
                }
                false
            },
            None,
        ));
        self.run(cmds);
        self.remove_timer_triggered(ph::ExamTurnTimer);
        if self.current_turn <= 1 {
            self.remove_exam_start_triggered();
        }
        self.status.set_passing_all();
        for c in self.deck_all() {
            self.cards[c].set_passing_turn_start();
        }
    }

    fn panic(&mut self) {
        let cand = self.setting.panic_candidates.clone();
        let hand = self.hand.clone();
        for c in hand {
            let v = cand[self.rng.integer(0, cand.len() as i64) as usize];
            self.cards[c].set_temporary_stamina_fix(v, 0, 1);
        }
    }

    fn run_gimmick(&mut self) {
        let g = match self.gimmick_schedule.iter().find(|x| x.0 == self.current_turn) {
            Some(g) => g.1.clone(),
            None => return,
        };
        let c = Ctx::gimmick();
        let ok = if g.fs_type != 0 {
            let tr = TriggerRow {
                fs_types: vec![g.fs_type],
                fs_values: vec![g.fs_value],
                fs_checks: vec![g.fs_check],
                fs_searches: vec![g.fs_search],
                ..Default::default()
            };
            self.trigger_field_valid(Some(&tr), Some(c), None)
        } else {
            true
        };
        if ok {
            let e = self.m.effect(g.effect);
            self.run(vec![Cmd::Effect(e, Some(c))]);
        }
    }

    fn remove_exam_start_triggered(&mut self) {
        let only = [ph::ExamStartExam, ph::StartExamPlay];
        self.status.effects.retain(|s| match &s.trig {
            Some(t) => !(!t.trigger.phase_types.is_empty() && t.trigger.phase_types.iter().all(|p| only.contains(p))),
            None => true,
        });
    }

    fn set_initial_card(&mut self, draw: i64, ctx: Option<Ctx>) -> Vec<CardId> {
        let limit = self.hand_limit();
        let mut to_hand = vec![];
        let mut overflow = vec![];
        let rev: Vec<CardId> = self.deck.iter().rev().copied().collect();
        for c in rev {
            if !self.row(c).is_initial {
                continue;
            }
            let i = self.deck.iter().position(|&x| x == c).unwrap();
            self.deck.remove(i);
            if self.hand.len() < limit {
                self.hand.push(c);
                to_hand.push(c);
            } else {
                overflow.push(c);
            }
        }
        self.deck.extend(overflow);
        if !to_hand.is_empty() {
            self.note_diff(Ev::HandAdd { cards: to_hand.clone(), frm: pos::Deck, etype: et::ExamCardDraw });
            if active(ctx) {
                self.support_upgrade(&to_hand.clone());
            }
        }
        if (self.hand.len() as i64) < draw {
            let n = draw - self.hand.len() as i64;
            let got = self.draw_card(n, ctx, false);
            to_hand.extend(got);
        }
        to_hand
    }

    pub fn end_turn(&mut self, skipped: bool) {
        if skipped {
            let r = self.setting.turn_end_recovery;
            self.add_stamina(r);
            let e = self.phase_enchants(ph::ExamTurnSkip, None);
            let cmds = self.fire(&e);
            self.run(cmds);
        }
        let mut enchants = self.phase_enchants(ph::ExamEndTurn, None);
        if self.current_turn >= 2 {
            for u in self.interval_enchants(ph::ExamEndTurnInterval) {
                if !enchants.contains(&u) {
                    enchants.push(u);
                }
            }
        }
        for u in self.timer_enchants(ph::ExamEndTurnTimer) {
            if !enchants.contains(&u) {
                enchants.push(u);
            }
        }
        let cmds = self.fire(&enchants);
        self.run(cmds);
        let hand = std::mem::take(&mut self.hand);
        for c in hand {
            if !self.cards[c].support_upgrades.is_empty() {
                self.cards[c].support_upgrades.clear();
                self.cards[c].invalidate();
            }
            if self.row(c).base.end_turn_lost {
                self.lost.push(c);
            } else {
                self.grave.push(c);
            }
        }
        let review = self.status.review();
        if review >= 1 {
            let v = ceil_i(self.status.multiple(cls::ReviewMultipleStatusEffect) * (review as f64 as f32));
            let n = 1 + self.status.review_count_add();
            let synth = Arc::new(EffectRow { effect_type: et::ExamLesson, v1: v, count: 1, turn: -1, ..Default::default() });
            let cmds = (0..n.max(0)).map(|_| Cmd::Effect(synth.clone(), Some(Ctx::base()))).collect();
            self.run(cmds);
        }
        let mut i = self.status.effects.len();
        while i > 0 {
            i -= 1;
            let rm = match &self.status.effects[i].trig {
                Some(t) => t.limit_count >= 0 && t.limit_count <= 0 && t.source.item == NONE,
                None => false,
            };
            if rm {
                self.status.effects.remove(i);
            }
        }
        self.remaining_actions = 0;
        if self.current_turn >= self.limit_turn + self.extra_turn {
            self.remain_turn = 0;
            self.ended = true;
            return;
        }
        self.remove_timer_triggered(ph::ExamEndTurnTimer);
        self.spend_turn_all();
        self.pending_start = true;
    }

    /// StatusCollection.SpendTurn（回合末衰减）
    fn spend_turn_all(&mut self) {
        let lock_review = self.status.has(cls::ReviewTurnEndReduceLockStatusEffect);
        let lock_pb = self.status.has(cls::ParameterBuffTurnEndReduceLockStatusEffect);
        let mut i = self.status.effects.len();
        while i > 0 {
            i -= 1;
            let s = &mut self.status.effects[i];
            if let Some(t) = s.trig.as_mut() {
                t.increment_turn_count();
            }
            if (lock_review && s.cls == cls::ReviewStatusEffect) || (lock_pb && s.cls == cls::ParameterBuffStatusEffect) {
                continue;
            }
            if !s.limited || !s.passing {
                continue;
            }
            let before = s.turn;
            s.spend_turn();
            let after = s.turn;
            let is_review = s.cls == cls::ReviewStatusEffect;
            if is_review {
                self.counters.add(ck::ReviewConsumptionSum, (before - after).max(0));
            }
            if after <= 0 {
                self.status.effects.remove(i);
            }
        }
    }

    pub fn advance(&mut self) {
        if self.pending_start && !self.ended {
            self.pending_start = false;
            self.start_turn();
        }
    }

    // ------------------------------------------------------------------ 比赛关卡
    fn contest_before_card(&mut self) {
        let cs = self.contest.clone().unwrap();
        let ctx = Some(Ctx::gimmick());
        if cs.id == 190 && cs.used < 5 && self.remain_turn <= 5 && self.turn_card_play_count % 2 == 1 {
            self.contest.as_mut().unwrap().used += 1;
            self.try_add(&rules::LessonBuff, 4, -1, ctx, false);
            self.try_add_vt(&rules::LessonBuffMultiple, 150, -1, ctx);
        }
        if cs.id == 192 && self.counters.get(ck::FullPowerPointGetSum) >= 10 {
            let c = self.contest.as_mut().unwrap();
            c.counter += 1;
            if c.counter % 5 == 0 && c.used < 3 {
                c.used += 1;
                let mut cards = self.cards_at(6, None);
                cards.extend_from_slice(&self.lost);
                let cards: Vec<CardId> = cards.into_iter().filter(|&c| self.row(c).has_effect_type(et::ExamFullPowerPoint)).collect();
                let g = [crate::sym::intern("g_effect-lesson_add-20"), crate::sym::intern("g_effect-lesson_count_add-1")];
                self.add_grow_effects(&cards, &g, ctx, false);
            }
        }
    }

    fn contest_on_direct_review(&mut self) {
        let c = self.contest.as_mut().unwrap();
        c.gi_direct += 1;
        if c.gi_direct % 4 == 0 && c.used < 4 {
            c.used += 1;
            let ctx = Some(Ctx::gimmick());
            self.try_add(&rules::Review, 0, 7, ctx, true);
            self.try_add_vt(&rules::ReviewCountAdd, 1, 1, ctx);
        }
    }

    // ================================================================== triggers.py
    fn field_status(&mut self, kind: i64, value: i64, search: Option<&SearchRow>, ctx: Option<Ctx>) -> bool {
        let sc = &self.status;
        if kind == fs::PlayCardSkill || kind == fs::PlayCardLesson || kind == fs::PlayCardSupport || kind == fs::PlayCardSearch {
            let mut n = self.play_log.len();
            if self.playing.is_some() && n > 0 {
                n -= 1;
            }
            let last = if n > 0 { Some(self.play_log[n - 1]) } else { None };
            let last = match last {
                None => return false,
                Some(l) => l,
            };
            if kind == fs::PlayCardSearch {
                return self.search_target(last, search);
            }
            if kind == fs::PlayCardSkill {
                return self.row(last).base.category == cat::MentalSkill;
            }
            if kind == fs::PlayCardLesson {
                return self.row(last).base.category == cat::ActiveSkill;
            }
            return false;
        }
        if kind == fs::CardSearchCountUp {
            return self.get_search_card_list(search, ctx).len() as i64 >= value;
        }
        if kind == fs::ParameterBuff {
            return sc.parameter_buff_turn() > 0;
        }
        if kind == fs::StaminaUpMultiple {
            return i2f(self.stamina) / i2f(self.max_stamina) >= i2f(value) / 1000.0f32;
        }
        if kind == fs::StaminaLessMultiple {
            return i2f(self.stamina) / i2f(self.max_stamina) <= i2f(value) / 1000.0f32;
        }
        if kind == fs::StaminaConsumptionDown {
            return sc.has(cls::StaminaConsumptionDownStatusEffect);
        }
        if kind == fs::ConcentrationUp {
            return sc.idol_type == 1 && (value == 0 || sc.idol_step >= value);
        }
        if kind == fs::PreservationUp {
            if sc.idol_type == 2 && (value == 0 || sc.idol_step >= value) {
                return true;
            }
            return sc.idol_type == 4;
        }
        if kind == fs::FullPowerUp {
            return sc.idol_type == 3;
        }
        if kind == fs::NoBlock {
            return self.block == 0;
        }
        if kind == fs::TurnProgressUp {
            return self.current_turn > value;
        }
        if kind == fs::ConditionThresholdMultiple || kind == fs::ConditionThresholdMultipleDown {
            let border = if self.clear_border != 0 { self.clear_border } else { 1 };
            let r = i2f(self.score) / i2f(border);
            let v = i2f(value) / 1000.0f32;
            return if kind == fs::ConditionThresholdMultiple { v <= r } else { v >= r };
        }
        if kind == fs::LessonBuffUp {
            return sc.lesson_buff() >= value;
        }
        if kind == fs::BlockUp {
            return self.block >= value;
        }
        if kind == fs::ReviewUp {
            return sc.review() >= value;
        }
        if kind == fs::GimmickLessonDebuffUp {
            return sc.lesson_debuff() == value;
        }
        if kind == fs::GimmickSleepyUp {
            return sc.block_add_down_fix() >= value;
        }
        if kind == fs::ParameterBuffUp {
            return sc.parameter_buff_turn() >= value;
        }
        if kind == fs::BlockAddDown {
            return sc.has(cls::BlockAddDownStatusEffect);
        }
        if kind == fs::GimmickParameterDebuff {
            return sc.parameter_debuff_turn() == value;
        }
        if kind == fs::RemainingTurn {
            return self.remain_turn <= value;
        }
        if kind == fs::CardPlayAggressiveUp {
            return sc.aggressive() >= value;
        }
        if kind == fs::ParameterLessThan {
            return self.score <= value;
        }
        if kind == fs::FullPowerPointUp {
            return sc.full_power_point() >= value;
        }
        if kind == fs::FullPowerPointGetSumUp {
            return self.counters.get(ck::FullPowerPointGetSum) >= value;
        }
        if kind == fs::NoStance {
            return sc.idol_type == 0;
        }
        if kind == fs::StanceChangeCountUp {
            return self.counters.get(ck::StanceChange) >= value;
        }
        if kind == fs::ConcentrationChangeCountUp {
            return self.counters.get(ck::StanceConcentrationChange) >= value;
        }
        if kind == fs::PreservationChangeCountUp {
            return self.counters.get(ck::StancePreservationChange) >= value;
        }
        if kind == fs::FullPowerChangeCountUp {
            return self.counters.get(ck::StanceFullPowerChange) >= value;
        }
        if kind == fs::ParameterBuffMultiplePerTurnUp {
            return sc.parameter_buff_mpt_turn() >= value;
        }
        if kind == fs::EnthusiasticUp {
            return sc.enthusiastic() >= value;
        }
        if kind == fs::EnchantCountUp {
            let n = sc
                .effects
                .iter()
                .filter(|s| s.cls == cls::TriggerEffectStatusEffect)
                .filter(|s| s.trig.as_ref().unwrap().trigger.phase_types.iter().any(|p| *p != ph::ExamTurnTimer && *p != ph::ExamEndTurnTimer))
                .count() as i64;
            return n >= value;
        }
        if kind == fs::TurnPlayCardCountUp {
            return self.turn_card_play_count >= value;
        }
        if kind == fs::DeckCardAllNoDuplicate {
            let cards = self.deck_all();
            if cards.is_empty() {
                return false;
            }
            let mut ids: Vec<Sym> = cards.iter().map(|&c| self.cards[c].card_id).collect();
            let n = ids.len();
            ids.sort_unstable();
            ids.dedup();
            return ids.len() == n;
        }
        if kind == fs::DebuffCountUp {
            let n = sc.effects.iter().filter(|s| self.m.debuff_status_classes.contains(&s.cls)).count() as i64;
            return n >= value;
        }
        false
    }

    fn field_status_valid(&mut self, t: &TriggerRow, ctx: Option<Ctx>) -> bool {
        for (i, &kind) in t.fs_types.iter().enumerate() {
            let value = t.fs_values.get(i).copied().unwrap_or(0);
            let neg = t.fs_checks.get(i).copied().unwrap_or(0) == crate::enums::check::Not;
            let sid = t.fs_searches.get(i).copied().unwrap_or(NONE);
            let search = if sid != NONE { self.m.search(sid).cloned() } else { None };
            if self.field_status(kind, value, search.as_deref(), ctx) == neg {
                return false;
            }
        }
        true
    }

    fn lesson_type_valid(&self, lesson_type: i64) -> bool {
        if lesson_type == 0 {
            return true;
        }
        let cur = self.current_parameter_type();
        if lesson_type == lt::LessonVocal {
            cur == 1
        } else if lesson_type == lt::LessonDance {
            cur == 2
        } else if lesson_type == lt::LessonVisual {
            cur == 3
        } else {
            false
        }
    }

    pub fn trigger_field_valid(&mut self, t: Option<&TriggerRow>, ctx: Option<Ctx>, target_cards: Option<&[CardId]>) -> bool {
        let t = match t {
            None => return true,
            Some(t) => t,
        };
        if !self.lesson_type_valid(t.lesson_type) {
            return false;
        }
        if !self.field_status_valid(t, ctx) {
            return false;
        }
        if t.search != NONE {
            let search = self.m.search(t.search).cloned().unwrap_or_else(|| panic!("ProduceCardSearch {} 不存在", crate::sym::name(t.search)));
            if search.position == pos::Target && target_cards.is_none() {
                return true;
            }
            let n = match target_cards {
                Some(tc) => tc.iter().filter(|&&c| self.search_target(c, Some(&search))).count() as i64,
                None => self.get_search_card_list(Some(&search), ctx).len() as i64,
            };
            if t.lower != 0 && n < t.lower {
                return false;
            }
            if t.upper != 0 && n > t.upper {
                return false;
            }
        }
        true
    }

    pub fn phase_trigger(&mut self, t: Option<&TriggerRow>, phase: i64, ctx: Option<Ctx>) -> bool {
        match t {
            Some(t) => t.has_phase(phase) && self.trigger_field_valid(Some(t), ctx, None),
            None => false,
        }
    }

    pub fn card_phase_trigger(&mut self, t: &TriggerRow, phase: i64, cards: &[CardId], movable: i64, ctx: Option<Ctx>) -> bool {
        if !t.has_phase(phase) {
            return false;
        }
        if !self.trigger_field_valid(Some(t), ctx, Some(cards)) {
            return false;
        }
        if t.card_move_pos != 0 && t.card_move_pos != movable {
            return false;
        }
        if t.search == NONE {
            return true;
        }
        let s = self.m.search(t.search).cloned().unwrap();
        if s.position != pos::Target {
            return true;
        }
        cards.iter().any(|&c| self.search_target(c, Some(&s)))
    }

    pub fn change_effect_trigger(&mut self, t: &TriggerRow, key: i64, d: i64, ctx: Option<Ctx>) -> bool {
        if !t.has_phase(ph::ExamStatusChange) {
            return false;
        }
        if let Some(&p0) = t.phase_values.first() {
            if p0 > d {
                return false;
            }
        }
        if !t.effect_types.contains(&key) {
            return false;
        }
        self.trigger_field_valid(Some(t), ctx, None)
    }

    pub fn consume_trigger(&mut self, t: &TriggerRow, key: i64, ctx: Option<Ctx>) -> bool {
        if !t.has_phase(ph::ExamBuffConsume) {
            return false;
        }
        if !self.trigger_field_valid(Some(t), ctx, None) {
            return false;
        }
        t.effect_types.is_empty() || t.effect_types.contains(&key)
    }

    pub fn interval_trigger(&mut self, t: &TriggerRow, phase: i64, n: i64, ctx: Option<Ctx>, at_least_one: bool) -> bool {
        if !t.has_phase(phase) {
            return false;
        }
        let v = t.v0();
        if at_least_one && n < 1 {
            return false;
        }
        if v == 0 {
            if n != 0 {
                return false;
            }
        } else if n % v != 0 {
            return false;
        }
        self.trigger_field_valid(Some(t), ctx, None)
    }

    pub fn timer_trigger(&mut self, t: &TriggerRow, phase: i64, n: i64, ctx: Option<Ctx>) -> bool {
        t.has_phase(phase) && t.v0() == n && self.trigger_field_valid(Some(t), ctx, None)
    }

    fn same_genre(a: i64, b: i64) -> bool {
        a == b || ((a == 2 || a == 4) && (b == 2 || b == 4))
    }

    pub fn stance_change_trigger(&mut self, t: &TriggerRow, before: i64, after: i64, step: i64, ctx: Option<Ctx>) -> bool {
        let phase = if after == 2 || after == 4 {
            ph::ExamStanceChangePreservation
        } else if after == 1 {
            ph::ExamStanceChangeConcentration
        } else {
            return false;
        };
        let step = if after == 4 { 3 } else { step };
        if !self.phase_trigger(Some(t), phase, ctx) {
            return false;
        }
        let v = t.v0();
        if v == 0 {
            return !Exam::same_genre(before, after);
        }
        step == v
    }

    pub fn stance_change_from_trigger(&mut self, t: &TriggerRow, before: i64, after: i64, step: i64, ctx: Option<Ctx>) -> bool {
        let phase = if before == 2 || before == 4 {
            ph::ExamStanceChangeFromPreservation
        } else if before == 1 {
            ph::ExamStanceChangeFromConcentration
        } else {
            return false;
        };
        if !self.phase_trigger(Some(t), phase, ctx) {
            return false;
        }
        let v = t.v0();
        if v == 0 {
            return !Exam::same_genre(before, after);
        }
        step == v
    }

    pub fn target_search_ok(&self, t: &TriggerRow, card: CardId) -> bool {
        if t.search == NONE {
            return true;
        }
        let s = self.m.search(t.search).cloned().unwrap();
        if s.position != pos::Target {
            return true;
        }
        self.search_target(card, Some(&s))
    }

    // ================================================================== difftrig.py
    /// GetTriggeredCardStatusEffectCommand
    pub fn card_enchant_cmds(&mut self, pred: &mut dyn FnMut(&mut Exam, CardId, &TriggerRow) -> bool, event_cards: Option<&[CardId]>) -> Vec<Cmd> {
        let mut cmds = vec![];
        for c in self.deck_all() {
            let (trig, tc, spent) = match self.ensure_se(c) {
                Some(se) => (se.trigger, se.trigger_count, se.spend_count),
                None => continue,
            };
            if tc != 0 && spent >= tc {
                continue;
            }
            let tr = match self.m.trigger(trig) {
                Some(t) => t,
                None => continue,
            };
            if tr.search != NONE {
                if let Some(ev) = event_cards {
                    let is_self = self.m.search(tr.search).map(|s| s.is_self).unwrap_or(false);
                    if is_self && !ev.contains(&c) {
                        continue;
                    }
                }
            }
            if !pred(self, c, &tr) {
                continue;
            }
            let se = self.ensure_se(c).unwrap();
            se.spend_count += 1;
            let grows = se.grow_ids.clone();
            cmds.push(Cmd::AddGrows(c, grows));
        }
        cmds
    }

    fn phase_l_c(&mut self, phase: i64, ctx: Option<Ctx>, cards: Option<&[CardId]>) -> Vec<Cmd> {
        let e = self.enchants(&mut |ex, i| {
            let tr = ex.trig_of(i);
            ex.phase_trigger(Some(&tr), phase, ctx)
        });
        let mut out = self.fire(&e);
        out.extend(self.card_enchant_cmds(&mut |ex, _c, tr| ex.phase_trigger(Some(tr), phase, ctx), cards));
        out
    }

    fn move_effect_cmds(&mut self, card: CardId) -> Vec<Cmd> {
        let r = self.row(card);
        if r.base.move_effect_ids.is_empty() {
            return vec![];
        }
        let c = Ctx::card(card);
        let mut out = vec![];
        for (i, &eid) in r.base.move_effect_ids.iter().enumerate() {
            let tr = match r.base.move_trigger_ids.get(i) {
                Some(&t) if t != NONE => self.m.trigger(t),
                _ => None,
            };
            let ok = match &tr {
                None => true,
                Some(t) => self.trigger_field_valid(Some(t), Some(c), Some(&[card])),
            };
            if ok {
                out.push(Cmd::Effect(self.m.effect(eid), Some(c)));
            }
        }
        self.cards[card].move_used_in_turn = true;
        out
    }

    fn move_effects_for(&mut self, ev: &Ev) -> Vec<Cmd> {
        let mut out = vec![];
        match ev {
            Ev::HandAdd { cards, frm, etype } => {
                for &c in cards {
                    let mt = self.row(c).base.move_trigger_type;
                    if mt == mvtrig::Hand || (mt == mvtrig::Draw && *frm == pos::Deck && *etype == 4) {
                        out.extend(self.move_effect_cmds(c));
                    }
                }
            }
            Ev::Move { cards, pos: p } => {
                let want = if *p == mv::Grave {
                    Some(mvtrig::Grave)
                } else if *p == mv::Lost {
                    Some(mvtrig::Lost)
                } else if *p == mv::Hold {
                    Some(mvtrig::Hold)
                } else {
                    None
                };
                for &c in cards {
                    let mt = self.row(c).base.move_trigger_type;
                    if mt != 0 && want == Some(mt) {
                        out.extend(self.move_effect_cmds(c));
                    }
                }
            }
            _ => {}
        }
        out
    }

    fn result_triggers(&mut self, pre: Obs, post: Obs, events: &[Ev], ctx: Option<Ctx>) -> Vec<Cmd> {
        let ctx_active = ctx.map(|c| c.active).unwrap_or(false);
        if !ctx_active {
            let mut out = vec![];
            for e in events {
                if matches!(e, Ev::HandAdd { .. } | Ev::Move { .. }) {
                    out.extend(self.move_effects_for(e));
                }
            }
            return out;
        }
        let mut out = vec![];
        if post.score > pre.score {
            out.extend(self.phase_l_c(ph::ExamLesson, ctx, None));
        }
        if post.stamina < pre.stamina || events.iter().any(|e| matches!(e, Ev::Stamina { mix: true })) {
            out.extend(self.phase_l_c(ph::ExamStaminaReduce, ctx, None));
            if self.playing.is_some() {
                out.extend(self.phase_l_c(ph::ExamStaminaReduceCard, ctx, None));
            }
        }
        for e in events {
            match e {
                Ev::HandAdd { .. } => out.extend(self.move_effects_for(e)),
                Ev::Move { cards, pos: p } => {
                    for &c in cards {
                        out.extend(self.move_card_triggers(c, *p, ctx));
                    }
                    out.extend(self.move_effects_for(e));
                }
                _ => {}
            }
        }
        if post.stance != pre.stance || post.stance_count != pre.stance_count {
            out.extend(self.stance_triggers(pre, post, ctx));
        }
        // B 段：状态 key 增加（升序 2, 3, 10, 31, 42, 49）
        for (ki, &key) in OBS_KEYS.iter().enumerate() {
            let (b, a) = (pre.keys[ki], post.keys[ki]);
            if a <= b {
                continue;
            }
            let d = a - b;
            let e = self.enchants(&mut |ex, i| {
                let tr = ex.trig_of(i);
                ex.change_effect_trigger(&tr, key, d, ctx)
            });
            out.extend(self.fire(&e));
            out.extend(self.card_enchant_cmds(&mut |ex, _c, tr| ex.change_effect_trigger(tr, key, d, ctx), None));
            let ph_up = match key {
                2 => Some(ph::ExamParameterBuffUpInterval),
                10 => Some(ph::ExamLessonBuffUpInterval),
                31 => Some(ph::ExamReviewUpInterval),
                42 => Some(ph::ExamAggressiveUpInterval),
                49 => Some(ph::ExamFullPowerPointUpInterval),
                _ => None,
            };
            if let Some(p) = ph_up {
                let e = self.enchants(&mut |ex, i| {
                    let tr = ex.trig_of(i);
                    let n = ex.status.effects[i].trig.as_ref().unwrap().phase(p);
                    ex.interval_trigger(&tr, p, n, ctx, true)
                });
                out.extend(self.fire(&e));
                out.extend(self.card_enchant_cmds(
                    &mut |ex, c, tr| {
                        let n = ex.card_se_phase(c, p);
                        ex.interval_trigger(tr, p, n, ctx, true)
                    },
                    None,
                ));
            }
        }
        out
    }

    fn move_card_triggers(&mut self, card: CardId, p: i64, ctx: Option<Ctx>) -> Vec<Cmd> {
        let phase = if p == mv::Hand {
            ph::ExamCardMoveHand
        } else if p == mv::Grave {
            ph::ExamCardMoveGrave
        } else if p == mv::Lost {
            ph::ExamCardMoveLost
        } else {
            return vec![];
        };
        let cards = [card];
        let e = self.enchants(&mut |ex, i| {
            let tr = ex.trig_of(i);
            ex.card_phase_trigger(&tr, phase, &cards, p, ctx)
        });
        let mut out = self.fire(&e);
        out.extend(self.card_enchant_cmds(&mut |ex, _c, tr| ex.card_phase_trigger(tr, phase, &cards, p, ctx), Some(&cards)));
        out
    }

    fn interval_l_c(&mut self, p: i64, ctx: Option<Ctx>, at_least_one: bool) -> Vec<Cmd> {
        let e = self.enchants(&mut |ex, i| {
            let tr = ex.trig_of(i);
            let n = ex.status.effects[i].trig.as_ref().unwrap().phase(p);
            ex.interval_trigger(&tr, p, n, ctx, at_least_one)
        });
        let mut out = self.fire(&e);
        out.extend(self.card_enchant_cmds(
            &mut |ex, c, tr| {
                let n = ex.card_se_phase(c, p);
                ex.interval_trigger(tr, p, n, ctx, at_least_one)
            },
            None,
        ));
        out
    }

    fn stance_triggers(&mut self, pre: Obs, post: Obs, ctx: Option<Ctx>) -> Vec<Cmd> {
        let ((b, bs), (a, as_)) = (pre.stance, post.stance);
        let st_diff = b != a;
        let step_diff = bs != as_;
        let cnt_diff = pre.stance_count != post.stance_count;
        let mut out = vec![];
        if st_diff && a != 0 {
            out.extend(self.phase_l_c(ph::ExamStanceChange, ctx, None));
        }
        if cnt_diff {
            let p = ph::ExamStanceChangeCount;
            let e = self.enchants(&mut |ex, i| {
                let tr = ex.trig_of(i);
                let n = ex.status.effects[i].trig.as_ref().unwrap().phase(p);
                ex.timer_trigger(&tr, p, n, ctx)
            });
            out.extend(self.fire(&e));
            out.extend(self.card_enchant_cmds(
                &mut |ex, c, tr| {
                    let n = ex.card_se_phase(c, p);
                    ex.timer_trigger(tr, p, n, ctx)
                },
                None,
            ));
            out.extend(self.interval_l_c(ph::ExamStanceChangeCountInterval, ctx, false));
        }
        if b == 1 || b == 2 || b == 4 {
            let e = self.enchants(&mut |ex, i| {
                let tr = ex.trig_of(i);
                ex.stance_change_from_trigger(&tr, b, a, bs, ctx)
            });
            out.extend(self.fire(&e));
            out.extend(self.card_enchant_cmds(&mut |ex, _c, tr| ex.stance_change_from_trigger(tr, b, a, bs, ctx), None));
        } else if b == 3 {
            out.extend(self.phase_l_c(ph::ExamStanceChangeFromFullPower, ctx, None));
        }
        if a == 0 {
            out.extend(self.phase_l_c(ph::ExamStanceReset, ctx, None));
        } else if a == 1 || a == 2 || a == 4 {
            if st_diff || step_diff {
                let e = self.enchants(&mut |ex, i| {
                    let tr = ex.trig_of(i);
                    ex.stance_change_trigger(&tr, b, a, as_, ctx)
                });
                out.extend(self.fire(&e));
                out.extend(self.card_enchant_cmds(&mut |ex, _c, tr| ex.stance_change_trigger(tr, b, a, as_, ctx), None));
            }
            if st_diff {
                let p = if a == 1 { ph::ExamStanceChangeConcentrationInterval } else { ph::ExamStanceChangePreservationInterval };
                out.extend(self.interval_l_c(p, ctx, false));
            }
        } else if a == 3 {
            out.extend(self.phase_l_c(ph::ExamStanceChangeFullPower, ctx, None));
            out.extend(self.interval_l_c(ph::ExamStanceChangeFullPowerInterval, ctx, false));
        }
        out
    }
}
