//! gakumas_exam 的原生后端。规则参照实现是 Python 版 gakumas_exam（逐函数移植，对拍验证）；
//! 这里只负责快：局面导入后，克隆 / 合法动作 / 推进 / 推演都在 Rust 里做。

// 搜索每次推演都克隆整个 Exam（大量小块分配）。系统分配器（Windows HeapAlloc）多线程下抢锁：
// 8 线程时每核推演数掉到单线程的 1/4，4 线程以后总量不涨（2026-11-24 实测）。换 mimalloc（每线程自己的堆）。
#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

pub mod card;
pub mod effects;
pub mod enums;
pub mod exam;
pub mod io;
pub mod master;
pub mod num;
pub mod search;
pub mod sig;
pub mod status;
pub mod sym;

use exam::{Action, Exam};
use num::NativeRandom;

pub fn load(master_path: &str, state_json: &str) -> Exam {
    let m = master::shared(master_path);
    let v: serde_json::Value = serde_json::from_str(state_json).expect("局面 JSON 格式错误");
    io::import(m, &v)
}

/// 基准负载：每个决策点每个合法动作各克隆 worlds 份、执行、随机推演到结束，取总分最高的动作。
/// 返回全部推演终局分（顺序与 Python 版一致，用来比对指纹）与终局分。
pub fn bench_game(ex0: &Exam, seed: u64, worlds: usize) -> (Vec<i64>, i64, usize, u64) {
    let mut ex = ex0.clone();
    ex.advance();
    let mut out = vec![];
    let mut k: u64 = 0;
    let mut steps: u64 = 0;
    while !ex.ended && k < 200 {
        let acts = ex.legal_actions();
        let mut best: Option<i64> = None;
        let mut best_a = acts[0];
        for (i, &a) in acts.iter().enumerate() {
            let mut tot = 0;
            for w in 0..worlds {
                let mut c = ex.clone();
                c.step(a);
                c.advance();
                steps += 1;
                let mut rng = NativeRandom::new((seed * 7919 + k * 131 + i as u64 * 17 + w as u64) & 0xffff_ffff);
                let mut n = 0;
                c.advance();
                while !c.ended && n < 500 {
                    let la = c.legal_actions();
                    let j = rng.integer(0, la.len() as i64) as usize;
                    c.step(la[j]);
                    c.advance();
                    steps += 1;
                    n += 1;
                }
                out.push(c.score);
                tot += c.score;
            }
            if best.map(|b| tot > b).unwrap_or(true) {
                best = Some(tot);
                best_a = a;
            }
        }
        ex.step(best_a);
        ex.advance();
        steps += 1;
        k += 1;
    }
    (out, ex.score, k as usize, steps)
}

pub fn action_from_tuple(kind: &str, i: usize) -> Action {
    match kind {
        "play" => Action::Play(i),
        "drink" => Action::Drink(i),
        _ => Action::End,
    }
}

pub fn action_to_tuple(a: Action) -> (&'static str, usize) {
    match a {
        Action::Play(i) => ("play", i),
        Action::Drink(i) => ("drink", i),
        Action::End => ("end", 0),
    }
}

#[cfg(feature = "python")]
mod py {
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::PyTuple;

    #[pyclass(name = "Exam", module = "gakumas_exam_rs")]
    pub struct PyExam {
        pub ex: Exam,
    }

    fn to_action(a: &Bound<'_, PyAny>) -> PyResult<Action> {
        let t = a.cast::<PyTuple>()?;
        let kind: String = t.get_item(0)?.extract()?;
        let i: usize = if t.len() > 1 { t.get_item(1)?.extract()? } else { 0 };
        Ok(action_from_tuple(&kind, i))
    }

    impl PyExam {
        /// guid → 卡（先在牌区里按 Python 版 _all_cards 顺序找，再找全部见过的卡）
        fn find(&self, g: &str) -> Option<usize> {
            self.ex.all_cards().into_iter().find(|&c| &*self.ex.cards[c].guid == g).or_else(|| self.ex.cards.iter().position(|c| &*c.guid == g))
        }
    }

    #[pymethods]
    impl PyExam {
        #[new]
        fn new(master_path: &str, state_json: &str) -> Self {
            PyExam { ex: load(master_path, state_json) }
        }
        fn clone(&self) -> Self {
            PyExam { ex: self.ex.clone() }
        }
        fn advance(&mut self) {
            self.ex.advance();
        }
        fn legal_actions<'py>(&mut self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyTuple>>> {
            self.ex
                .legal_actions()
                .into_iter()
                .map(|a| match a {
                    Action::Play(i) => PyTuple::new(py, ["play".into_pyobject(py)?.into_any(), i.into_pyobject(py)?.into_any()]),
                    Action::Drink(i) => PyTuple::new(py, ["drink".into_pyobject(py)?.into_any(), i.into_pyobject(py)?.into_any()]),
                    Action::End => PyTuple::new(py, ["end".into_pyobject(py)?.into_any()]),
                })
                .collect()
        }
        fn step(&mut self, action: &Bound<'_, PyAny>) -> PyResult<()> {
            let a = to_action(action)?;
            self.ex.step(a);
            Ok(())
        }
        fn export_json(&self) -> String {
            io::export(&self.ex).to_string()
        }
        /// 随机推演到结束（NativeRandom(seed)），返回终局分
        fn random_rollout(&mut self, seed: u64) -> i64 {
            let mut rng = NativeRandom::new(seed);
            self.ex.advance();
            let mut n = 0;
            while !self.ex.ended && n < 500 {
                let la = self.ex.legal_actions();
                let j = rng.integer(0, la.len() as i64) as usize;
                self.ex.step(la[j]);
                self.ex.advance();
                n += 1;
            }
            self.ex.score
        }
        #[getter]
        fn score(&self) -> i64 {
            self.ex.score
        }
        #[getter]
        fn ended(&self) -> bool {
            self.ex.ended
        }
        #[getter]
        fn stamina(&self) -> i64 {
            self.ex.stamina
        }
        #[getter]
        fn current_turn(&self) -> i64 {
            self.ex.current_turn
        }
        #[getter]
        fn unimplemented(&self) -> u64 {
            self.ex.unimplemented
        }

        // ---- FidelityBattle（旧 Battle 接口）用的读写
        #[getter]
        fn max_stamina(&self) -> i64 {
            self.ex.max_stamina
        }
        #[setter]
        fn set_stamina(&mut self, v: i64) {
            self.ex.stamina = v;
        }
        #[getter]
        fn block(&self) -> i64 {
            self.ex.block
        }
        #[setter]
        fn set_block(&mut self, v: i64) {
            self.ex.block = v;
        }
        #[getter]
        fn remaining_actions(&self) -> i64 {
            self.ex.remaining_actions
        }
        #[setter]
        fn set_remaining_actions(&mut self, v: i64) {
            self.ex.remaining_actions = v;
        }
        #[getter]
        fn exam_card_play_count(&self) -> i64 {
            self.ex.exam_card_play_count
        }
        #[getter]
        fn turn_card_play_count(&self) -> i64 {
            self.ex.turn_card_play_count
        }
        #[getter]
        fn aggressive(&self) -> i64 {
            self.ex.status.aggressive()
        }
        #[getter]
        fn review(&self) -> i64 {
            self.ex.status.review()
        }
        #[setter]
        fn set_aggressive(&mut self, v: i64) {
            self.ex.status.override_aggressive(v);
        }
        #[setter]
        fn set_review(&mut self, v: i64) {
            self.ex.status.override_review(v);
        }
        /// 画面读数覆盖一种状态：cls 是状态类名（如 LessonBuffStatusEffect），by_turn 见 StatusCollection::override_single。
        /// 不认识的类名报错（不往引擎里塞新类）。
        fn override_status(&mut self, cls: &str, by_turn: bool, n: i64) -> PyResult<()> {
            if !crate::enums::CLASS_NAMES.iter().any(|c| *c == cls) {
                return Err(pyo3::exceptions::PyKeyError::new_err(format!("不认识的状态类 {cls}")));
            }
            let c = crate::status::class_id(cls);
            self.ex.status.override_single(c, by_turn, n);
            Ok(())
        }
        #[setter]
        fn set_score(&mut self, v: i64) {
            self.ex.score = v;
        }
        #[getter]
        fn lesson_buff(&self) -> i64 {
            self.ex.status.lesson_buff()
        }
        #[getter]
        fn extra_turn(&self) -> i64 {
            self.ex.extra_turn
        }
        #[getter]
        fn remain_turn(&self) -> i64 {
            self.ex.remain_turn
        }
        #[getter]
        fn limit_turn(&self) -> i64 {
            self.ex.limit_turn
        }
        /// bonus_permil.get(current_parameter_type())（没有时 None）
        #[getter]
        fn bonus(&self) -> Option<i64> {
            let t = self.ex.current_parameter_type();
            if (1..=3).contains(&t) {
                self.ex.bonus_permil[t as usize]
            } else {
                None
            }
        }
        #[getter]
        fn rng_state(&self) -> u32 {
            self.ex.rng.state
        }
        #[setter]
        fn set_rng_state(&mut self, v: u64) {
            self.ex.rng = NativeRandom::new(v);
        }
        #[getter]
        fn turn_types(&self) -> Vec<i64> {
            self.ex.turn_types.clone()
        }
        #[setter]
        fn set_turn_types(&mut self, v: Vec<i64>) {
            self.ex.turn_types = v;
        }
        #[getter]
        fn drinks(&self) -> Vec<String> {
            self.ex.drinks.iter().map(|d| sym::name(*d)).collect()
        }
        /// 饮料栏按画面设（模拟器跟踪按画面重建时用：任务 ② 验证 2，喝掉的饮料重建后还留在引擎里）
        #[setter]
        fn set_drinks(&mut self, v: Vec<String>) {
            self.ex.drinks = v.iter().map(|d| sym::intern(d)).collect();
        }
        #[getter]
        fn playing(&self) -> Option<String> {
            self.ex.playing.map(|c| self.ex.cards[c].guid.to_string())
        }
        /// 牌区的 guid 列表（hand / deck / grave / lost / hold）
        fn zone(&self, z: &str) -> Vec<String> {
            let v = match z {
                "hand" => &self.ex.hand,
                "deck" => &self.ex.deck,
                "grave" => &self.ex.grave,
                "lost" => &self.ex.lost,
                "hold" => &self.ex.hold,
                _ => return vec![],
            };
            v.iter().map(|&c| self.ex.cards[c].guid.to_string()).collect()
        }
        /// 按 guid 写回一个牌区（卡从全部见过的卡里找：同 guid 取最先出现的）
        fn set_zone(&mut self, z: &str, guids: Vec<String>) -> PyResult<()> {
            let mut ids = Vec::with_capacity(guids.len());
            for g in &guids {
                match self.find(g) {
                    Some(c) => ids.push(c),
                    None => return Err(pyo3::exceptions::PyKeyError::new_err(format!("unknown card instance {g}"))),
                }
            }
            match z {
                "hand" => self.ex.hand = ids,
                "deck" => self.ex.deck = ids,
                "grave" => self.ex.grave = ids,
                "lost" => self.ex.lost = ids,
                "hold" => self.ex.hold = ids,
                _ => {}
            }
            Ok(())
        }
        /// 全部在牌区 / 出牌中的卡：[(guid, card_id, 强化, customize_counts)]（Python 版 _all_cards 的顺序）
        fn cards_info(&self) -> Vec<(String, String, i64, Vec<i64>)> {
            self.ex
                .all_cards()
                .into_iter()
                .map(|c| {
                    let k = &self.ex.cards[c];
                    (k.guid.to_string(), sym::name(k.card_id), k.upgrade(), k.customize_counts.clone())
                })
                .collect()
        }
        /// 卡的总强化次数按画面设（模拟器跟踪对齐用：考试中的支援强化是随机的，引擎掷的和实机不同，画面的「+」数才是真的）。
        /// 只调支援强化那一块（多了去掉、少了补上）；支援强化减到 0 还多时再减考试中强化，不动基础强化。返回改没改。
        fn set_card_upgrade(&mut self, guid: &str, total: i64) -> bool {
            let Some(c) = self.find(guid) else { return false };
            let k = &mut self.ex.cards[c];
            let mut delta = total - k.upgrade();
            if delta == 0 {
                return false;
            }
            while delta > 0 {
                k.support_upgrades.push(sym::NONE);
                delta -= 1;
            }
            while delta < 0 && !k.support_upgrades.is_empty() {
                k.support_upgrades.pop();
                delta += 1;
            }
            if delta < 0 {
                k.tmp_upgrade = (k.tmp_upgrade + delta).max(0);
            }
            k.invalidate();
            true
        }
        /// 新建一张卡（不放进任何区，调用方用 set_zone 放）→ guid。画面上出现引擎牌组里没有的卡（眠気这类机关 / 效果生成的卡、
        /// 编成里没读到的牌）时，对齐用它接住。主数据里没有这张卡时报错。
        fn new_card(&mut self, card_id: &str, upgrade: i64) -> PyResult<String> {
            let cid = sym::intern(card_id);
            if self.ex.m.card(cid, upgrade).is_none() {
                return Err(pyo3::exceptions::PyKeyError::new_err(format!("主数据里没有 {card_id}+{upgrade}")));
            }
            let g = self.ex.new_guid();
            self.ex.add_card_obj(crate::card::Card::new(cid, upgrade, vec![], g.clone()));
            Ok(g.to_string())
        }
        fn card_info(&self, guid: &str) -> Option<(String, i64, Vec<i64>)> {
            self.find(guid).map(|c| {
                let k = &self.ex.cards[c];
                (sym::name(k.card_id), k.upgrade(), k.customize_counts.clone())
            })
        }
        fn signature(&self) -> u64 {
            sig::signature(&self.ex)
        }
    }

    /// bench_game 的 Python 入口：返回 (推演终局分列表, 终局分, 决策数, 推进步数)
    #[pyfunction(name = "bench_game")]
    fn bench_game_py(ex: &PyExam, seed: u64, worlds: usize) -> (Vec<i64>, i64, usize, u64) {
        super::bench_game(&ex.ex, seed, worlds)
    }

    /// 世界采样（任务 ②）：重排对局随机数、山札（顶部 known_deck_prefix 张保留）、free_turns 这几个回合的属性（互换）
    #[pyfunction(name = "sample_world")]
    fn sample_world_py(ex: &PyExam, known_deck_prefix: usize, free_turns: Vec<usize>, seed: u64) -> PyExam {
        let h = crate::search::Hidden { known_deck_prefix, free_turns };
        PyExam { ex: crate::search::sample_world(&ex.ex, &h, seed) }
    }

    /// 实战出牌搜索（任务 ②）：W 个世界并行 GNRPA，第一手投票。返回 dict：
    /// best（动作元组或 None）、votes [(动作, 票数, 平均终局分)]、worlds [(第一手, 终局分, 续打数, 是否跑完)]、seconds
    #[pyfunction(name = "search")]
    #[pyo3(signature = (ex, known_deck_prefix, free_turns, worlds=8, seconds=15.0, threads=0, seed=0, level=2, iterations=60, alpha=1.0, temperature=1.0, end_bias=-1.0,
                        rivals=vec![], lost_ssr_max=None, debuffs_min=None, greedy=0.0, prior=None,
                        pass_rank=None, score_cap=None, clear_score=None))]
    #[allow(clippy::too_many_arguments)]
    fn search_py<'py>(py: Python<'py>, ex: &PyExam, known_deck_prefix: usize, free_turns: Vec<usize>, worlds: usize, seconds: f64, threads: usize,
                      seed: u64, level: u32, iterations: u32, alpha: f64, temperature: f64, end_bias: f64, rivals: Vec<i64>,
                      lost_ssr_max: Option<i64>, debuffs_min: Option<i64>, greedy: f64,
                      prior: Option<std::collections::HashMap<u64, f64>>, pass_rank: Option<i64>, score_cap: Option<i64>,
                      clear_score: Option<i64>) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
        use pyo3::types::PyDict;
        let h = crate::search::Hidden { known_deck_prefix, free_turns };
        let obj = crate::search::Objective { rivals, lost_ssr_max, debuffs_min, pass_rank, score_cap, clear_score };
        let prior: crate::search::Policy = prior.unwrap_or_default().into_iter().collect();
        let p = crate::search::Params { level, iterations, alpha, temperature, end_bias, greedy, prior: std::sync::Arc::new(prior), obj, ..Default::default() };
        let ex0 = ex.ex.clone();
        let r = py.detach(move || crate::search::search(&ex0, &h, worlds, seconds, threads, seed, &p));
        let tup = |a: Action| -> PyResult<Bound<'py, PyTuple>> {
            let (k, i) = action_to_tuple(a);
            if k == "end" { PyTuple::new(py, [k.into_pyobject(py)?.into_any()]) } else { PyTuple::new(py, [k.into_pyobject(py)?.into_any(), i.into_pyobject(py)?.into_any()]) }
        };
        let d = PyDict::new(py);
        d.set_item("best", match r.best { Some(a) => Some(tup(a)?), None => None })?;
        let votes: PyResult<Vec<_>> = r.votes.iter().map(|&(a, n, m)| Ok((tup(a)?, n, m))).collect();
        d.set_item("votes", votes?)?;
        let ws: PyResult<Vec<_>> = r.worlds.iter().map(|w| Ok((match w.first { Some(a) => Some(tup(a)?), None => None }, w.score, w.playouts, w.complete, w.raw, w.stars))).collect();
        d.set_item("worlds", ws?)?;
        d.set_item("seconds", r.elapsed.as_secs_f64())?;
        let pol: std::collections::HashMap<u64, f64> = r.policy.into_iter().collect();
        d.set_item("policy", pol)?;
        Ok(d)
    }

    /// 一批局面各自从当前起搜一整局（不重排隐藏信息），返回 [(目标值, 终局分, 星)]。偶像之路编成评估用。
    #[pyfunction(name = "plan_many")]
    #[pyo3(signature = (exams, seconds=1.0, threads=0, seed=0, level=2, iterations=60, rivals=vec![], lost_ssr_max=None, debuffs_min=None, greedy=0.0,
                        pass_rank=None, score_cap=None, clear_score=None))]
    #[allow(clippy::too_many_arguments)]
    fn plan_many_py(py: Python<'_>, exams: Vec<PyRef<'_, PyExam>>, seconds: f64, threads: usize, seed: u64, level: u32, iterations: u32,
                    rivals: Vec<i64>, lost_ssr_max: Option<i64>, debuffs_min: Option<i64>, greedy: f64, pass_rank: Option<i64>,
                    score_cap: Option<i64>, clear_score: Option<i64>) -> Vec<(i64, i64, i64)> {
        let xs: Vec<Exam> = exams.iter().map(|e| e.ex.clone()).collect();
        let obj = crate::search::Objective { rivals, lost_ssr_max, debuffs_min, pass_rank, score_cap, clear_score };
        let p = crate::search::Params { level, iterations, greedy, obj, ..Default::default() };
        py.detach(move || crate::search::plan_many(xs, seconds, threads, seed, &p))
    }

    #[pymodule]
    fn gakumas_exam_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_class::<PyExam>()?;
        m.add_function(wrap_pyfunction!(bench_game_py, m)?)?;
        m.add_function(wrap_pyfunction!(sample_world_py, m)?)?;
        m.add_function(wrap_pyfunction!(search_py, m)?)?;
        m.add_function(wrap_pyfunction!(plan_many_py, m)?)?;
        Ok(())
    }
}
