//! 主数据：由 Python 侧 gakumas_exam.native.export_master 导出的 JSON（表行已转整数枚举）。
//! 行结构只保留规则会读的字段；缺省值与 Python 的 .get(..., 默认) 一致。

use crate::sym::{intern, Sym, NONE};
use rustc_hash::FxHashMap;
use serde_json::Value;
use std::sync::Arc;

// ---------------------------------------------------------------- JSON 取值助手
pub fn gi(v: &Value, k: &str) -> i64 {
    match v.get(k) {
        Some(Value::Number(n)) => n.as_i64().unwrap_or_else(|| n.as_f64().unwrap_or(0.0) as i64),
        Some(Value::Bool(b)) => *b as i64,
        _ => 0,
    }
}
pub fn gb(v: &Value, k: &str) -> bool {
    match v.get(k) {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0) != 0,
        _ => false,
    }
}
pub fn gs(v: &Value, k: &str) -> Sym {
    match v.get(k) {
        Some(Value::String(s)) => intern(s),
        _ => NONE,
    }
}
pub fn gvi(v: &Value, k: &str) -> Vec<i64> {
    match v.get(k) {
        Some(Value::Array(a)) => a.iter().map(|x| x.as_i64().unwrap_or(0)).collect(),
        _ => vec![],
    }
}
pub fn gvs(v: &Value, k: &str) -> Vec<Sym> {
    match v.get(k) {
        Some(Value::Array(a)) => a.iter().map(|x| x.as_str().map(intern).unwrap_or(NONE)).collect(),
        _ => vec![],
    }
}

// ---------------------------------------------------------------- 行
#[derive(Clone, Debug, Default)]
pub struct EffectRow {
    pub id: Sym,
    pub effect_type: i64,
    pub v1: i64,
    pub v2: i64,
    pub count: i64,
    pub turn: i64,
    pub search: Sym,
    pub pick_range: i64,
    pub pick_min: i64,
    pub pick_max: i64,
    pub move_pos: i64,
    pub target_card: Sym,
    pub target_upgrade: i64,
    pub status_enchant: Sym,
    pub chain_ids: Vec<Sym>,
    pub chain_id: Sym,
    pub grow_ids: Vec<Sym>,
    pub group_ids: Vec<Sym>,
}

impl EffectRow {
    pub fn from_json(v: &Value) -> Self {
        EffectRow {
            id: gs(v, "id"),
            effect_type: gi(v, "effectType"),
            v1: gi(v, "effectValue1"),
            v2: gi(v, "effectValue2"),
            count: gi(v, "effectCount"),
            turn: gi(v, "effectTurn"),
            search: gs(v, "produceCardSearchId"),
            pick_range: gi(v, "pickRangeType"),
            pick_min: gi(v, "pickCountMin"),
            pick_max: gi(v, "pickCountMax"),
            move_pos: gi(v, "movePositionType"),
            target_card: gs(v, "targetProduceCardId"),
            target_upgrade: gi(v, "targetUpgradeCount"),
            status_enchant: gs(v, "produceExamStatusEnchantId"),
            chain_ids: gvs(v, "chainProduceExamEffectIds"),
            chain_id: gs(v, "chainProduceExamEffectId"),
            grow_ids: gvs(v, "produceCardGrowEffectIds"),
            group_ids: gvs(v, "effectGroupIds"),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct TriggerRow {
    pub id: Sym,
    pub phase_types: Vec<i64>,
    pub phase_values: Vec<i64>,
    pub fs_types: Vec<i64>,
    pub fs_values: Vec<i64>,
    pub fs_checks: Vec<i64>,
    pub fs_searches: Vec<Sym>,
    pub search: Sym,
    pub upper: i64,
    pub lower: i64,
    pub lesson_type: i64,
    pub effect_types: Vec<i64>,
    pub card_move_pos: i64,
}

impl TriggerRow {
    pub fn from_json(v: &Value) -> Self {
        TriggerRow {
            id: gs(v, "id"),
            phase_types: gvi(v, "phaseTypes"),
            phase_values: gvi(v, "phaseValues"),
            fs_types: gvi(v, "fieldStatusTypes"),
            fs_values: gvi(v, "fieldStatusValues"),
            fs_checks: gvi(v, "fieldStatusCheckTypes"),
            fs_searches: gvs(v, "fieldStatusProduceCardSearchIds"),
            search: gs(v, "produceCardSearchId"),
            upper: gi(v, "upperSearchCount"),
            lower: gi(v, "lowerSearchCount"),
            lesson_type: gi(v, "lessonType"),
            effect_types: gvi(v, "effectTypes"),
            card_move_pos: gi(v, "cardMovePositionType"),
        }
    }
    #[inline]
    pub fn has_phase(&self, p: i64) -> bool {
        self.phase_types.contains(&p)
    }
    /// (phaseValues or [0])[0]
    #[inline]
    pub fn v0(&self) -> i64 {
        self.phase_values.first().copied().unwrap_or(0)
    }
    /// (phaseValues or [1])[0] or 1
    #[inline]
    pub fn v0_or1(&self) -> i64 {
        let v = self.phase_values.first().copied().unwrap_or(1);
        if v == 0 {
            1
        } else {
            v
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct SearchRow {
    pub id: Sym,
    pub is_self: bool,
    pub tag: Sym,
    pub card_ids: Vec<Sym>,
    pub upgrades: Vec<i64>,
    pub plan: i64,
    pub categories: Vec<i64>,
    pub st_minmax: i64,
    pub st_min: i64,
    pub st_max: i64,
    pub cost_type: i64,
    pub status_type: i64,
    pub effect_type: i64,
    pub group_ids: Vec<Sym>,
    pub rarities: Vec<i64>,
    pub customized: bool,
    pub position: i64,
    pub order: i64,
    pub limit: i64,
}

impl SearchRow {
    fn from_json(v: &Value) -> Self {
        SearchRow {
            id: gs(v, "id"),
            is_self: gb(v, "isSelf"),
            tag: gs(v, "cardSearchTag"),
            card_ids: gvs(v, "produceCardIds"),
            upgrades: gvi(v, "upgradeCounts"),
            plan: gi(v, "planType"),
            categories: gvi(v, "cardCategories"),
            st_minmax: gi(v, "staminaMinMaxType"),
            st_min: gi(v, "staminaMin"),
            st_max: gi(v, "staminaMax"),
            cost_type: gi(v, "costType"),
            status_type: gi(v, "cardStatusType"),
            effect_type: gi(v, "examEffectType"),
            group_ids: gvs(v, "effectGroupIds"),
            rarities: gvi(v, "cardRarities"),
            customized: gb(v, "isCustomized"),
            position: gi(v, "cardPositionType"),
            order: gi(v, "orderType"),
            limit: gi(v, "limitCount"),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct PlayLink {
    pub trigger: Sym,
    pub effect: Sym,
    pub once: bool,
}

#[derive(Clone, Debug, Default)]
pub struct CardRow {
    pub id: Sym,
    pub upgrade: i64,
    pub category: i64,
    pub rarity: i64,
    pub plan: i64,
    pub search_tag: Sym,
    pub cost_type: i64,
    pub cost_value: i64,
    pub stamina: i64,
    pub force_stamina: i64,
    pub play_move: i64,
    pub is_initial: bool,
    pub is_restrict: bool,
    pub end_turn_lost: bool,
    pub play_trigger: Sym,
    pub status_enchant: Sym,
    pub play_effects: Vec<PlayLink>,
    pub customize_ids: Vec<Sym>,
    pub move_trigger_type: i64,
    pub move_effect_ids: Vec<Sym>,
    pub move_trigger_ids: Vec<Sym>,
}

impl CardRow {
    fn from_json(v: &Value) -> Self {
        let play_effects = match v.get("playEffects") {
            Some(Value::Array(a)) => a
                .iter()
                .map(|x| PlayLink { trigger: gs(x, "produceExamTriggerId"), effect: gs(x, "produceExamEffectId"), once: gb(x, "isOncePlayEffect") })
                .collect(),
            _ => vec![],
        };
        CardRow {
            id: gs(v, "id"),
            upgrade: gi(v, "upgradeCount"),
            category: gi(v, "category"),
            rarity: gi(v, "rarity"),
            plan: gi(v, "planType"),
            search_tag: gs(v, "searchTag"),
            cost_type: gi(v, "costType"),
            cost_value: gi(v, "costValue"),
            stamina: gi(v, "stamina"),
            force_stamina: gi(v, "forceStamina"),
            play_move: gi(v, "playMovePositionType"),
            is_initial: gb(v, "isInitial"),
            is_restrict: gb(v, "isRestrict"),
            end_turn_lost: gb(v, "isEndTurnLost"),
            play_trigger: gs(v, "playProduceExamTriggerId"),
            status_enchant: gs(v, "produceCardStatusEnchantId"),
            play_effects,
            customize_ids: gvs(v, "produceCardCustomizeIds"),
            move_trigger_type: gi(v, "moveEffectTriggerType"),
            move_effect_ids: gvs(v, "moveProduceExamEffectIds"),
            move_trigger_ids: gvs(v, "moveProduceExamTriggerIds"),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct GrowRow {
    pub id: Sym,
    pub effect_type: i64,
    pub value: i64,
    pub play_trigger: Sym,
    pub play_effect_trigger: Sym,
    pub target_play_effect_triggers: Vec<Sym>,
    pub play_effect: Sym,
    pub target_play_effects: Vec<Sym>,
    pub card_status_enchant: Sym,
    pub play_move: i64,
}

impl GrowRow {
    pub fn from_json(v: &Value) -> Self {
        GrowRow {
            id: gs(v, "id"),
            effect_type: gi(v, "effectType"),
            value: gi(v, "value"),
            play_trigger: gs(v, "playProduceExamTriggerId"),
            play_effect_trigger: gs(v, "playEffectProduceExamTriggerId"),
            target_play_effect_triggers: gvs(v, "targetPlayEffectProduceExamTriggerIds"),
            play_effect: gs(v, "playProduceExamEffectId"),
            target_play_effects: gvs(v, "targetPlayProduceExamEffectIds"),
            card_status_enchant: gs(v, "produceCardStatusEnchantId"),
            play_move: gi(v, "playMovePositionType"),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct ExamEnchantRow {
    pub id: Sym,
    pub trigger: Sym,
    pub effects: Vec<Sym>,
}

#[derive(Clone, Debug, Default)]
pub struct CardEnchantRow {
    pub id: Sym,
    pub trigger: Sym,
    pub grow_ids: Vec<Sym>,
    pub group_ids: Vec<Sym>,
    pub trigger_count: i64,
}

#[derive(Clone, Debug, Default)]
pub struct Setting {
    pub id: Sym,
    pub hand_limit: i64,
    pub turn_start_distribute: i64,
    pub turn_end_recovery: i64,
    pub panic_candidates: Vec<i64>,
    pub stamina_reduce_change: i64,
    pub v: FxHashMap<String, i64>,
}

impl Setting {
    fn from_json(v: &Value) -> Self {
        let mut map = FxHashMap::default();
        if let Value::Object(o) = v {
            for (k, x) in o {
                if let Some(n) = x.as_i64() {
                    map.insert(k.clone(), n);
                }
            }
        }
        Setting {
            id: gs(v, "id"),
            hand_limit: gi(v, "handLimit"),
            turn_start_distribute: gi(v, "turnStartDistribute"),
            turn_end_recovery: gi(v, "examTurnEndRecoveryStamina"),
            panic_candidates: gvi(v, "produceExamPanicStaminaCandidates"),
            stamina_reduce_change: gi(v, "examStaminaReduceChange"),
            v: map,
        }
    }
}

/// 设定里按名取的常量（热路径用的预先取到结构体字段里）。
#[derive(Clone, Debug, Default)]
pub struct SettingK {
    pub conc_stamina1: i64,
    pub conc_stamina2: i64,
    pub pres_stamina1: i64,
    pub pres_stamina2: i64,
    pub over_pres_stamina: i64,
    pub stamina_down: i64,
    pub stamina_down_add: i64,
    pub stamina_add: i64,
    pub stamina_add_down: i64,
    pub buff_down: i64,
    pub buff_add: i64,
    pub param_buff: i64,
    pub param_buff_mpt: i64,
    pub gimmick_param_debuff: i64,
    pub depend_mult: i64,
    pub depend_max: i64,
    pub conc_lesson1: i64,
    pub conc_lesson2: i64,
    pub pres_lesson1: i64,
    pub pres_lesson2: i64,
    pub full_lesson: i64,
    pub over_pres_lesson: i64,
    pub block_add_down: i64,
    pub conc_pen1: i64,
    pub conc_pen2: i64,
    pub full_power_pva: i64,
    pub over_rel_pva: i64,
    pub over_rel_block: i64,
    pub over_rel_enth: i64,
    pub over_rel_full_lesson: i64,
    pub rel_pva1: i64,
    pub rel_pva2: i64,
    pub rel_block1: i64,
    pub rel_block2: i64,
    pub rel_enth1: i64,
    pub rel_enth2: i64,
}

impl SettingK {
    pub fn of(s: &Setting) -> Self {
        let g = |k: &str| *s.v.get(k).unwrap_or(&0);
        SettingK {
            conc_stamina1: g("examConcentrationStaminaMultiplePermil1"),
            conc_stamina2: g("examConcentrationStaminaMultiplePermil2"),
            pres_stamina1: g("examPreservationStaminaMultiplePermil1"),
            pres_stamina2: g("examPreservationStaminaMultiplePermil2"),
            over_pres_stamina: g("examOverPreservationStaminaMultiplePermil"),
            stamina_down: g("examStaminaConsumptionDownPermil"),
            stamina_down_add: g("examStaminaConsumptionDownAddPermil"),
            stamina_add: g("examStaminaConsumptionAddPermil"),
            stamina_add_down: g("examStaminaConsumptionAddDownPermil"),
            buff_down: g("examBuffConsumptionDownPermil"),
            buff_add: g("examBuffConsumptionAddPermil"),
            param_buff: g("examParameterBuffPermil"),
            param_buff_mpt: g("examParameterBuffMultiplePerTurnPermil"),
            gimmick_param_debuff: g("examGimmickParameterDebuffPermil"),
            depend_mult: g("examLessonValueMultipleDependReviewOrAggressiveMultiplePermil"),
            depend_max: g("examLessonValueMultipleDependReviewOrAggressiveMaxPermil"),
            conc_lesson1: g("examConcentrationLessonValueMultiplePermil1"),
            conc_lesson2: g("examConcentrationLessonValueMultiplePermil2"),
            pres_lesson1: g("examPreservationLessonValueMultiplePermil1"),
            pres_lesson2: g("examPreservationLessonValueMultiplePermil2"),
            full_lesson: g("examFullPowerLessonValueMultiplePermil"),
            over_pres_lesson: g("examOverPreservationLessonValueMultiplePermil"),
            block_add_down: g("examBlockAddDownPermil"),
            conc_pen1: g("examConcentrationStaminaPenetrateReduce1"),
            conc_pen2: g("examConcentrationStaminaPenetrateReduce2"),
            full_power_pva: g("fullPowerPlayableValueAdd"),
            over_rel_pva: g("overPreservationReleasePlayableValueAdd"),
            over_rel_block: g("overPreservationReleaseBlockAdd"),
            over_rel_enth: g("overPreservationReleaseEnthusiastic"),
            over_rel_full_lesson: g("overPreservationReleaseToFullPowerGrowEffectLessonAdd"),
            rel_pva1: g("preservationReleasePlayableValueAdd1"),
            rel_pva2: g("preservationReleasePlayableValueAdd2"),
            rel_block1: g("preservationReleaseBlockAdd1"),
            rel_block2: g("preservationReleaseBlockAdd2"),
            rel_enth1: g("preservationReleaseEnthusiastic1"),
            rel_enth2: g("preservationReleaseEnthusiastic2"),
        }
    }
}

pub struct Master {
    pub commit: String,
    /// 卡：id → 各强化等级（下标 = upgradeCount）
    pub cards: FxHashMap<Sym, Vec<Option<Arc<CardRow>>>>,
    pub effects: FxHashMap<Sym, Arc<EffectRow>>,
    pub triggers: FxHashMap<Sym, Arc<TriggerRow>>,
    pub searches: FxHashMap<Sym, Arc<SearchRow>>,
    pub exam_enchants: FxHashMap<Sym, ExamEnchantRow>,
    pub card_enchants: FxHashMap<Sym, CardEnchantRow>,
    pub grows: FxHashMap<Sym, GrowRow>,
    pub customize: FxHashMap<(Sym, i64), Vec<Sym>>,
    pub drinks: FxHashMap<Sym, Vec<Sym>>,
    pub drink_effects: FxHashMap<Sym, Sym>,
    pub settings: FxHashMap<Sym, (Arc<Setting>, Arc<SettingK>)>,
    /// 低下（效果目标类型 2）的效果类型（IsBlockAddStatus 用）
    pub debuff_effect_types: Vec<i64>,
    /// 判 DebuffCountUp / DebuffRecover 的状态类
    pub debuff_status_classes: Vec<u16>,
}

fn rows<'a>(v: &'a Value, t: &str) -> impl Iterator<Item = &'a Value> {
    v.get("tables").and_then(|x| x.get(t)).and_then(|x| x.as_array()).into_iter().flatten()
}

impl Master {
    pub fn from_json(v: &Value) -> Master {
        let mut cards: FxHashMap<Sym, Vec<Option<Arc<CardRow>>>> = FxHashMap::default();
        for r in rows(v, "ProduceCard") {
            let row = CardRow::from_json(r);
            let e = cards.entry(row.id).or_default();
            let u = row.upgrade.max(0) as usize;
            if e.len() <= u {
                e.resize(u + 1, None);
            }
            e[u] = Some(Arc::new(row));
        }
        let effects = rows(v, "ProduceExamEffect").map(|r| (gs(r, "id"), Arc::new(EffectRow::from_json(r)))).collect();
        let triggers = rows(v, "ProduceExamTrigger").map(|r| (gs(r, "id"), Arc::new(TriggerRow::from_json(r)))).collect();
        let searches = rows(v, "ProduceCardSearch").map(|r| (gs(r, "id"), Arc::new(SearchRow::from_json(r)))).collect();
        let exam_enchants = rows(v, "ProduceExamStatusEnchant")
            .map(|r| (gs(r, "id"), ExamEnchantRow { id: gs(r, "id"), trigger: gs(r, "produceExamTriggerId"), effects: gvs(r, "produceExamEffectIds") }))
            .collect();
        let card_enchants = rows(v, "ProduceCardStatusEnchant")
            .map(|r| {
                (
                    gs(r, "id"),
                    CardEnchantRow {
                        id: gs(r, "id"),
                        trigger: gs(r, "produceExamTriggerId"),
                        grow_ids: gvs(r, "produceCardGrowEffectIds"),
                        group_ids: gvs(r, "effectGroupIds"),
                        trigger_count: gi(r, "triggerCount"),
                    },
                )
            })
            .collect();
        let grows = rows(v, "ProduceCardGrowEffect").map(|r| (gs(r, "id"), GrowRow::from_json(r))).collect();
        let customize = rows(v, "ProduceCardCustomize").map(|r| ((gs(r, "id"), gi(r, "customizeCount")), gvs(r, "produceCardGrowEffectIds"))).collect();
        let drinks = rows(v, "ProduceDrink").map(|r| (gs(r, "id"), gvs(r, "produceDrinkEffectIds"))).collect();
        let drink_effects = rows(v, "ProduceDrinkEffect").map(|r| (gs(r, "id"), gs(r, "produceExamEffectId"))).collect();
        let settings = rows(v, "ExamSetting")
            .map(|r| {
                let s = Setting::from_json(r);
                let k = SettingK::of(&s);
                (gs(r, "id"), (Arc::new(s), Arc::new(k)))
            })
            .collect();
        let rules = v.get("rules").cloned().unwrap_or(Value::Null);
        let debuff_effect_types = gvi(&rules, "debuff_effect_types");
        let debuff_status_classes = match rules.get("debuff_status_classes") {
            Some(Value::Array(a)) => a.iter().filter_map(|x| x.as_str()).map(crate::status::class_id).collect(),
            _ => vec![],
        };
        Master {
            commit: v.get("commit").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            cards,
            effects,
            triggers,
            searches,
            exam_enchants,
            card_enchants,
            grows,
            customize,
            drinks,
            drink_effects,
            settings,
            debuff_effect_types,
            debuff_status_classes,
        }
    }

    pub fn card(&self, id: Sym, upgrade: i64) -> Option<&Arc<CardRow>> {
        if upgrade < 0 {
            return None;
        }
        self.cards.get(&id).and_then(|v| v.get(upgrade as usize)).and_then(|x| x.as_ref())
    }
    pub fn effect(&self, id: Sym) -> Arc<EffectRow> {
        self.effects.get(&id).cloned().unwrap_or_else(|| panic!("ProduceExamEffect {} 不存在", crate::sym::name(id)))
    }
    pub fn trigger(&self, id: Sym) -> Option<Arc<TriggerRow>> {
        if id == NONE {
            None
        } else {
            self.triggers.get(&id).cloned()
        }
    }
    pub fn search(&self, id: Sym) -> Option<&Arc<SearchRow>> {
        if id == NONE {
            None
        } else {
            self.searches.get(&id)
        }
    }
    pub fn grow(&self, id: Sym) -> &GrowRow {
        self.grows.get(&id).unwrap_or_else(|| panic!("ProduceCardGrowEffect {} 不存在", crate::sym::name(id)))
    }
}

/// 进程内按提交号共享
pub fn shared(json_path: &str) -> Arc<Master> {
    use std::sync::{Mutex, OnceLock};
    static CACHE: OnceLock<Mutex<FxHashMap<String, Arc<Master>>>> = OnceLock::new();
    let c = CACHE.get_or_init(|| Mutex::new(FxHashMap::default()));
    if let Some(m) = c.lock().unwrap().get(json_path) {
        return m.clone();
    }
    let text = std::fs::read_to_string(json_path).unwrap_or_else(|e| panic!("读不到主数据 {json_path}: {e}"));
    let v: Value = serde_json::from_str(&text).expect("主数据 JSON 格式错误");
    let m = Arc::new(Master::from_json(&v));
    c.lock().unwrap().insert(json_path.to_string(), m.clone());
    m
}
