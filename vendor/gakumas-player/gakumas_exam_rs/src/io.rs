//! 局面导入 / 导出（与 gakumas_exam/native.py 的 export_state 同一格式）。

use crate::card::{Card, CardSe, Se, StaminaSpec};
use crate::exam::{Contest, Counters, Exam, Gimmick, Support, COUNTER_KEYS};
use crate::master::{gb, gi, gs, gvi, gvs, EffectRow, GrowRow, Master, TriggerRow};
use crate::num::NativeRandom;
use crate::status::{class_id, class_name, Extra, Source, Status, StatusCollection, Trig};
use crate::sym::{intern, name, Sym, NONE};
use serde_json::{json, Map, Value};
use std::sync::Arc;

fn arr(v: &Value, k: &str) -> Vec<Value> {
    v.get(k).and_then(|x| x.as_array()).cloned().unwrap_or_default()
}
fn idx_list(v: &Value, k: &str) -> Vec<usize> {
    arr(v, k).iter().map(|x| x.as_u64().unwrap() as usize).collect()
}
fn s(x: Sym) -> Value {
    Value::String(name(x))
}
fn ss(x: &[Sym]) -> Value {
    Value::Array(x.iter().map(|&v| s(v)).collect())
}
fn pairs(x: &[(i64, i64)]) -> Value {
    Value::Array(x.iter().map(|(a, b)| json!([a, b])).collect())
}
fn read_pairs(v: &Value, k: &str) -> Vec<(i64, i64)> {
    arr(v, k).iter().map(|p| (p[0].as_i64().unwrap(), p[1].as_i64().unwrap())).collect()
}

pub fn grow_to_json(g: &GrowRow) -> Value {
    json!({
        "id": s(g.id), "effectType": g.effect_type, "value": g.value,
        "playProduceExamTriggerId": s(g.play_trigger), "playEffectProduceExamTriggerId": s(g.play_effect_trigger),
        "targetPlayEffectProduceExamTriggerIds": ss(&g.target_play_effect_triggers), "playProduceExamEffectId": s(g.play_effect),
        "targetPlayProduceExamEffectIds": ss(&g.target_play_effects), "produceCardStatusEnchantId": s(g.card_status_enchant),
        "playMovePositionType": g.play_move,
    })
}

pub fn effect_to_json(e: &EffectRow) -> Value {
    json!({
        "id": s(e.id), "effectType": e.effect_type, "effectValue1": e.v1, "effectValue2": e.v2, "effectCount": e.count,
        "effectTurn": e.turn, "produceCardSearchId": s(e.search), "pickRangeType": e.pick_range, "pickCountMin": e.pick_min,
        "pickCountMax": e.pick_max, "movePositionType": e.move_pos, "targetProduceCardId": s(e.target_card),
        "targetUpgradeCount": e.target_upgrade, "produceExamStatusEnchantId": s(e.status_enchant),
        "chainProduceExamEffectIds": ss(&e.chain_ids), "chainProduceExamEffectId": s(e.chain_id),
        "produceCardGrowEffectIds": ss(&e.grow_ids), "effectGroupIds": ss(&e.group_ids),
    })
}

pub fn trigger_to_json(t: &TriggerRow) -> Value {
    json!({
        "id": s(t.id), "phaseTypes": t.phase_types, "phaseValues": t.phase_values, "fieldStatusTypes": t.fs_types,
        "fieldStatusValues": t.fs_values, "fieldStatusCheckTypes": t.fs_checks, "fieldStatusProduceCardSearchIds": ss(&t.fs_searches),
        "produceCardSearchId": s(t.search), "upperSearchCount": t.upper, "lowerSearchCount": t.lower, "lessonType": t.lesson_type,
        "effectTypes": t.effect_types, "cardMovePositionType": t.card_move_pos,
    })
}

fn card_from_json(v: &Value) -> Card {
    let mut c = Card::new(gs(v, "card_id"), gi(v, "base_upgrade"), gvi(v, "customize_counts"), Arc::from(v["guid"].as_str().unwrap_or("")));
    c.tmp_upgrade = gi(v, "tmp_upgrade");
    c.support_upgrades = gvs(v, "support_upgrades");
    c.custom_override = match v.get("custom_override") {
        Some(Value::Array(a)) => Some(a.iter().map(|p| (p[0].as_str().map(intern).unwrap_or(NONE), p[1].as_i64().unwrap_or(0))).collect()),
        _ => None,
    };
    c.fixed_order = gi(v, "fixed_order");
    c.play_count = gi(v, "play_count");
    c.grow_after = arr(v, "grow_after").iter().map(GrowRow::from_json).collect();
    c.affect_grow = gvs(v, "affect_grow");
    c.se = match v.get("se") {
        None | Some(Value::Null) => Se::Unset,
        Some(Value::Object(o)) if o.is_empty() => Se::Empty,
        Some(x) => Se::Has(CardSe {
            id: gs(x, "_id"),
            trigger: gs(x, "_produceExamTriggerId"),
            grow_ids: gvs(x, "_produceCardGrowEffectIdList"),
            group_ids: gvs(x, "_effectGroupIdList"),
            trigger_count: gi(x, "_triggerCount"),
            phase_count: read_pairs(x, "phase_count"),
            spend_turn: gi(x, "_spendTurn"),
            spend_count: gi(x, "_spendCount"),
        }),
    };
    c.move_used_in_turn = gb(v, "move_used_in_turn");
    c.stamina_specify = arr(v, "stamina_specify")
        .iter()
        .map(|x| StaminaSpec {
            turn: gi(x, "_turn"),
            int_value: gi(x, "_intValue"),
            bool_value: gb(x, "_boolValue"),
            count: gi(x, "_count"),
            turn_limited: gb(x, "_isTurnLimited"),
            count_limited: gb(x, "_isCountLimited"),
            passing: gb(x, "_isPassingTurnStart"),
        })
        .collect();
    c
}

fn card_to_json(c: &Card) -> Value {
    let se = match &c.se {
        Se::Unset => Value::Null,
        Se::Empty => json!({}),
        Se::Has(x) => json!({
            "_id": s(x.id), "_produceExamTriggerId": s(x.trigger), "_produceCardGrowEffectIdList": ss(&x.grow_ids),
            "_effectGroupIdList": ss(&x.group_ids), "_triggerCount": x.trigger_count, "phase_count": pairs(&x.phase_count),
            "_spendTurn": x.spend_turn, "_spendCount": x.spend_count,
        }),
    };
    json!({
        "card_id": s(c.card_id), "base_upgrade": c.base_upgrade, "tmp_upgrade": c.tmp_upgrade, "support_upgrades": ss(&c.support_upgrades),
        "customize_counts": c.customize_counts,
        "custom_override": match &c.custom_override { None => Value::Null, Some(o) => Value::Array(o.iter().map(|(i, n)| json!([name(*i), n])).collect()) },
        "guid": &*c.guid, "fixed_order": c.fixed_order, "play_count": c.play_count,
        "grow_after": Value::Array(c.grow_after.iter().map(grow_to_json).collect()), "affect_grow": ss(&c.affect_grow), "se": se,
        "move_used_in_turn": c.move_used_in_turn,
        "stamina_specify": Value::Array(c.stamina_specify.iter().map(|x| json!({
            "_turn": x.turn, "_intValue": x.int_value, "_boolValue": x.bool_value, "_count": x.count, "_isTurnLimited": x.turn_limited,
            "_isCountLimited": x.count_limited, "_isPassingTurnStart": x.passing})).collect()),
    })
}

fn source_from_json(v: &Value) -> Source {
    Source {
        item: gs(v, "item"),
        drink: gs(v, "drink"),
        card: gs(v, "card"),
        card_guid: v.get("card_guid").and_then(|x| x.as_str()).map(Arc::from),
        origin: gs(v, "origin"),
        item_idol_origin: v.get("item_idol_origin").and_then(|x| x.as_bool()),
    }
}

fn source_to_json(x: &Source) -> Value {
    let mut m = Map::new();
    if x.item != NONE {
        m.insert("item".into(), s(x.item));
    }
    if x.drink != NONE {
        m.insert("drink".into(), s(x.drink));
    }
    if x.card != NONE {
        m.insert("card".into(), s(x.card));
    }
    if let Some(g) = &x.card_guid {
        m.insert("card_guid".into(), Value::String(g.to_string()));
    }
    if x.origin != NONE {
        m.insert("origin".into(), s(x.origin));
    }
    if let Some(b) = x.item_idol_origin {
        m.insert("item_idol_origin".into(), Value::Bool(b));
    }
    Value::Object(m)
}

fn status_from_json(v: &Value) -> Status {
    let mut extra = Extra::default();
    if let Some(Value::Object(o)) = v.get("extra") {
        for (k, x) in o {
            match k.as_str() {
                "_count" => extra.count = x.as_i64(),
                "_searchId" => extra.search_id = Some(x.as_str().map(intern).unwrap_or(NONE)),
                _ => extra.other.push((k.clone(), x.clone())),
            }
        }
    }
    let trig = match v.get("trig") {
        None | Some(Value::Null) => None,
        Some(t) => Some(Box::new(Trig {
            trigger: Arc::new(TriggerRow::from_json(&t["trigger"])),
            effects: Arc::new(arr(t, "effects").iter().map(|e| Arc::new(EffectRow::from_json(e))).collect()),
            turn_count: gi(t, "turn_count"),
            phase_count: read_pairs(t, "phase_count"),
            limit_count: gi(t, "limit_count"),
            limit_in_turn: gi(t, "limit_in_turn"),
            limit_in_turn_remain: gi(t, "limit_in_turn_remain"),
            enchant_id: gs(t, "enchant_id"),
            origin_type: gi(t, "origin_type"),
            source: source_from_json(&t["source"]),
            override_type: gi(t, "override_type"),
            from_effect_ids: gvs(t, "from_effect_ids"),
            from_enchant: gb(t, "from_enchant"),
            encore: gb(t, "encore"),
        })),
    };
    Status {
        cls: class_id(v["cls"].as_str().unwrap()),
        uid: gi(v, "uid"),
        turn: gi(v, "turn"),
        limited: gb(v, "limited"),
        passing: gb(v, "passing"),
        value: gi(v, "value"),
        extra,
        trig,
    }
}

fn status_to_json(st: &Status) -> Value {
    let mut extra = Map::new();
    if let Some(c) = st.extra.count {
        extra.insert("_count".into(), json!(c));
    }
    if let Some(sid) = st.extra.search_id {
        extra.insert("_searchId".into(), s(sid));
    }
    for (k, x) in &st.extra.other {
        extra.insert(k.clone(), x.clone());
    }
    let trig = match &st.trig {
        None => Value::Null,
        Some(t) => json!({
            "trigger": trigger_to_json(&t.trigger), "effects": Value::Array(t.effects.iter().map(|e| effect_to_json(e)).collect()),
            "turn_count": t.turn_count, "phase_count": pairs(&t.phase_count), "limit_count": t.limit_count, "limit_in_turn": t.limit_in_turn,
            "limit_in_turn_remain": t.limit_in_turn_remain, "enchant_id": s(t.enchant_id), "origin_type": t.origin_type,
            "source": source_to_json(&t.source), "override_type": t.override_type, "from_effect_ids": ss(&t.from_effect_ids),
            "from_enchant": t.from_enchant, "encore": t.encore,
        }),
    };
    json!({"cls": class_name(st.cls), "uid": st.uid, "turn": st.turn, "limited": st.limited, "passing": st.passing, "value": st.value,
           "extra": Value::Object(extra), "trig": trig})
}

pub fn import(m: Arc<Master>, v: &Value) -> Exam {
    let mut ex = Exam::new(m, intern(v["setting_id"].as_str().unwrap_or("p_exam_setting-1")));
    ex.rng = NativeRandom::new(v["rng"].as_u64().unwrap());
    ex.cards = arr(v, "cards").iter().map(card_from_json).collect();
    let z = &v["zones"];
    ex.hand = idx_list(z, "hand");
    ex.deck = idx_list(z, "deck");
    ex.grave = idx_list(z, "grave");
    ex.lost = idx_list(z, "lost");
    ex.hold = idx_list(z, "hold");
    ex.playing = v.get("playing").and_then(|x| x.as_u64()).map(|x| x as usize);
    let st = &v["status"];
    ex.status = StatusCollection {
        effects: arr(st, "effects").iter().map(status_from_json).collect(),
        create_count: gi(st, "create_count"),
        idol_type: gi(st, "idol_type"),
        idol_step: gi(st, "idol_step"),
    };
    ex.plan_type = gi(v, "plan_type");
    ex.exam_type = gi(v, "exam_type");
    ex.stamina = gi(v, "stamina");
    ex.max_stamina = gi(v, "max_stamina");
    ex.block = gi(v, "block");
    ex.score = gi(v, "score");
    ex.current_turn = gi(v, "current_turn");
    ex.limit_turn = gi(v, "limit_turn");
    ex.remain_turn = gi(v, "remain_turn");
    ex.extra_turn = gi(v, "extra_turn");
    ex.turn_types = gvi(v, "turn_types");
    ex.bonus_permil = [None, None, None, None];
    for p in arr(v, "bonus_permil") {
        let k = p[0].as_i64().unwrap();
        if (1..=3).contains(&k) {
            ex.bonus_permil[k as usize] = p[1].as_i64();
        }
    }
    ex.exam_card_play_count = gi(v, "exam_card_play_count");
    ex.turn_card_play_count = gi(v, "turn_card_play_count");
    let mut counters = Counters::default();
    if let Some(Value::Object(o)) = v.get("counters") {
        for (k, x) in o {
            let n = x.as_i64().unwrap_or(0);
            match COUNTER_KEYS.iter().position(|c| c == k) {
                Some(i) => counters.v[i] = Some(n),
                None => counters.other.push((k.clone(), n)),
            }
        }
    }
    ex.counters = counters;
    ex.drinks = gvs(v, "drinks");
    ex.items = arr(v, "items").iter().map(|p| (p[0].as_str().map(intern).unwrap_or(NONE), p[1].as_i64().unwrap_or(0))).collect();
    ex.supports = arr(v, "supports")
        .iter()
        .map(|p| Support { id: p[0].as_str().map(intern).unwrap_or(NONE), filter: p[1].as_i64().unwrap_or(0), permil: p[2].as_i64().unwrap_or(0) })
        .collect();
    ex.turn_used_supports = gvs(v, "turn_used_supports");
    ex.clear_border = gi(v, "clear_border");
    ex.play_log = idx_list(v, "play_log");
    ex.initial_deal = gb(v, "initial_deal");
    ex.gimmick_schedule = arr(v, "gimmick_schedule")
        .iter()
        .map(|p| {
            let g = &p[1];
            (
                p[0].as_i64().unwrap(),
                Gimmick {
                    id: gs(g, "id"),
                    effect: gs(g, "produceExamEffectId"),
                    fs_type: gi(g, "fieldStatusType"),
                    fs_value: gi(g, "fieldStatusValue"),
                    fs_check: gi(g, "fieldStatusCheckType"),
                    fs_search: gs(g, "fieldStatusProduceCardSearchId"),
                },
            )
        })
        .collect();
    ex.ended = gb(v, "ended");
    ex.remaining_actions = gi(v, "remaining_actions");
    ex.pending_start = gb(v, "pending_start");
    ex.guid_seq = gi(v, "guid_seq");
    ex.contest = match v.get("contest") {
        Some(c) if c.is_object() => Some(Contest { id: gi(c, "id"), used: gi(c, "used"), counter: gi(c, "counter"), gi_direct: gi(c, "gi_direct") }),
        _ => None,
    };
    for n in arr(v, "npcs") {
        ex.npc_scores.push(n[0].as_array().map(|a| a.iter().map(|x| x.as_i64().unwrap_or(0)).collect()).unwrap_or_default());
        ex.npc_current.push(n[1].as_i64());
    }
    ex
}

pub fn export(ex: &Exam) -> Value {
    let idx = |v: &[usize]| Value::Array(v.iter().map(|&i| json!(i)).collect());
    let mut counters = Map::new();
    for (i, k) in COUNTER_KEYS.iter().enumerate() {
        if let Some(n) = ex.counters.v[i] {
            counters.insert(k.to_string(), json!(n));
        }
    }
    for (k, n) in &ex.counters.other {
        counters.insert(k.clone(), json!(n));
    }
    json!({
        "setting_id": s(ex.setting.id), "rng": ex.rng.state,
        "cards": Value::Array(ex.cards.iter().map(card_to_json).collect()),
        "zones": {"hand": idx(&ex.hand), "deck": idx(&ex.deck), "grave": idx(&ex.grave), "lost": idx(&ex.lost), "hold": idx(&ex.hold)},
        "playing": ex.playing,
        "status": {"effects": Value::Array(ex.status.effects.iter().map(status_to_json).collect()), "create_count": ex.status.create_count,
                   "idol_type": ex.status.idol_type, "idol_step": ex.status.idol_step},
        "plan_type": ex.plan_type, "exam_type": ex.exam_type, "stamina": ex.stamina, "max_stamina": ex.max_stamina, "block": ex.block,
        "score": ex.score, "current_turn": ex.current_turn, "limit_turn": ex.limit_turn, "remain_turn": ex.remain_turn,
        "extra_turn": ex.extra_turn, "turn_types": ex.turn_types,
        "bonus_permil": Value::Array((1..=3).filter_map(|k| ex.bonus_permil[k].map(|v| json!([k, v]))).collect()),
        "exam_card_play_count": ex.exam_card_play_count, "turn_card_play_count": ex.turn_card_play_count,
        "counters": Value::Object(counters), "drinks": ss(&ex.drinks),
        "items": Value::Array(ex.items.iter().map(|(i, n)| json!([name(*i), n])).collect()),
        "supports": Value::Array(ex.supports.iter().map(|x| json!([name(x.id), x.filter, x.permil])).collect()),
        "turn_used_supports": ss(&ex.turn_used_supports), "clear_border": ex.clear_border, "play_log": idx(&ex.play_log),
        "initial_deal": ex.initial_deal,
        "gimmick_schedule": Value::Array(ex.gimmick_schedule.iter().map(|(t, g)| json!([t, {
            "id": s(g.id), "produceExamEffectId": s(g.effect), "fieldStatusType": g.fs_type, "fieldStatusValue": g.fs_value,
            "fieldStatusCheckType": g.fs_check, "fieldStatusProduceCardSearchId": s(g.fs_search)}])).collect()),
        "ended": ex.ended, "remaining_actions": ex.remaining_actions, "pending_start": ex.pending_start, "guid_seq": ex.guid_seq,
        "contest": match &ex.contest { None => Value::Null, Some(c) => json!({"id": c.id, "used": c.used, "counter": c.counter, "gi_direct": c.gi_direct}) },
        "npcs": Value::Array(ex.npc_scores.iter().zip(ex.npc_current.iter()).map(|(a, b)| json!([a, b])).collect()),
    })
}
