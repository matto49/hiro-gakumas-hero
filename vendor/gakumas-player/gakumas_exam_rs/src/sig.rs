//! 置换表签名：与 gakumas_exam/battle.py 的 FidelityBattle.signature 覆盖同一组字段（相等关系一致）。
//! 标量 + 牌区（山札按序、其余按集合）+ 饮料 + 全部状态（含附魔计数）+ 各卡（guid、强化、卡附魔）+ 本回合用过的支援卡。
//! 不含：随机数、卡的考试中成长、出牌日志等（Python 版同样不含）。

use crate::card::Se;
use crate::exam::{Exam, COUNTER_KEYS};
use crate::sym::name;
use rustc_hash::FxHasher;
use std::hash::{Hash, Hasher};

pub fn signature(ex: &Exam) -> u64 {
    let mut h = FxHasher::default();
    // 标量
    (ex.stamina, ex.max_stamina, ex.block, ex.score, ex.remaining_actions, ex.current_turn, ex.extra_turn).hash(&mut h);
    (ex.exam_card_play_count, ex.turn_card_play_count, ex.ended).hash(&mut h);
    ex.turn_types.hash(&mut h);
    let mut counters: Vec<(&str, i64)> = COUNTER_KEYS.iter().enumerate().filter_map(|(i, k)| ex.counters.v[i].map(|v| (*k, v))).collect();
    for (k, v) in &ex.counters.other {
        counters.push((k.as_str(), *v));
    }
    counters.sort();
    counters.hash(&mut h);
    // 牌区：deck 按序，其余按 guid 排序
    let guids = |v: &[usize]| -> Vec<&str> { v.iter().map(|&c| &*ex.cards[c].guid).collect() };
    0u8.hash(&mut h);
    guids(&ex.deck).hash(&mut h);
    for z in [&ex.hand, &ex.grave, &ex.lost, &ex.hold] {
        let mut g = guids(z);
        g.sort_unstable();
        1u8.hash(&mut h);
        g.hash(&mut h);
    }
    // 饮料
    for d in &ex.drinks {
        name(*d).hash(&mut h);
    }
    2u8.hash(&mut h);
    // 状态
    for s in &ex.status.effects {
        (s.cls, s.uid, s.turn, s.limited, s.passing, s.value).hash(&mut h);
        s.extra.count.hash(&mut h);
        s.extra.search_id.hash(&mut h);
        for (k, v) in &s.extra.other {
            k.hash(&mut h);
            v.to_string().hash(&mut h);
        }
        if let Some(t) = &s.trig {
            (t.turn_count, t.limit_count, t.limit_in_turn, t.limit_in_turn_remain, t.enchant_id, t.origin_type, t.override_type).hash(&mut h);
            t.phase_count.hash(&mut h);
            (t.source.item, t.source.drink, t.source.card, t.source.origin, t.source.item_idol_origin).hash(&mut h);
            t.source.card_guid.as_deref().hash(&mut h);
            (&t.from_effect_ids, t.from_enchant, t.encore).hash(&mut h);
        }
        3u8.hash(&mut h);
    }
    // 各卡：{guid: 卡}（同 guid 取最后一张），按 (guid, 强化, 卡附魔) 排序
    let mut all: Vec<usize> = ex.all_cards();
    let mut by_guid: Vec<(&str, usize)> = vec![];
    for c in all.drain(..) {
        let g = &*ex.cards[c].guid;
        match by_guid.iter_mut().find(|x| x.0 == g) {
            Some(x) => x.1 = c,
            None => by_guid.push((g, c)),
        }
    }
    let mut keyed: Vec<(&str, i64, String)> = by_guid
        .into_iter()
        .map(|(g, c)| {
            let card = &ex.cards[c];
            let se = match &card.se {
                Se::Unset => String::from("N"),
                Se::Empty => String::from("{}"),
                Se::Has(x) => format!("{}|{}|{:?}|{}|{}", x.id, x.trigger_count, x.phase_count, x.spend_turn, x.spend_count),
            };
            (g, card.upgrade(), se)
        })
        .collect();
    keyed.sort();
    keyed.hash(&mut h);
    ex.turn_used_supports.hash(&mut h);
    h.finish()
}
