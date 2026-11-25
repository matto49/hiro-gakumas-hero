//! 实战出牌搜索（任务 ②，设计稿 docs/ryugi/specs/2026-11-12-rust-exam-search-design.md 第 2、3 部分）。
//!
//! - `sample_world`：只重排画面看不到的部分——对局随机数、山札顺序（已公开的顶部几张保留）、未见回合的属性（在给定的回合之间
//!   互换，所以属性的总配额不变）；手牌、弃牌、除外、状态、附魔、分数等可见部分不动。
//! - `gnrpa`：Generalized Nested Rollout Policy Adaptation（Cazenave 2020，在 NRPA（Rosin 2011）上加了偏置 β），按论文自己写，
//!   不来自任何现成实现。策略是「动作编码 → 权重」表，续打时按 softmax(w/τ + β) 抽动作；每层迭代后按本层最好路线调整权重。
//! - `search`：W 个世界并行各跑 GNRPA（线程池，到时间就停），每个世界取最好路线的第一手投票，平票比平均终局分。
//!
//! 搜索只在随机补全出来的世界里进行，不读种子、不读存档的隐藏信息（项目规矩）。

use crate::exam::{Action, Exam};
use rustc_hash::FxHashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

/// 画面看不到、要重排的部分（Python 侧建起点时给）。
#[derive(Clone, Debug, Default)]
pub struct Hidden {
    /// 山札顶部已公开的张数（检索 / 预览看到的），这几张顺序保留
    pub known_deck_prefix: usize,
    /// 属性没看到的回合（turn_types 的下标，0 起）：它们之间互换，保持本场配额
    pub free_turns: Vec<usize>,
}

/// 搜索用的随机数（splitmix64），和对局随机数分开，不影响引擎规则。
#[derive(Clone, Copy, Debug)]
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Rng(seed ^ 0x9E37_79B9_7F4A_7C15)
    }
    #[inline]
    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    /// [0, n)
    #[inline]
    pub fn below(&mut self, n: usize) -> usize {
        ((self.next_u64() as u128 * n as u128) >> 64) as usize
    }
    /// [0, 1)
    #[inline]
    pub fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
    pub fn shuffle<T>(&mut self, v: &mut [T]) {
        for k in (1..v.len()).rev() {
            let j = self.below(k + 1);
            v.swap(j, k);
        }
    }
}

/// 一个世界：克隆局面，重排看不到的部分。
pub fn sample_world(ex: &Exam, hidden: &Hidden, seed: u64) -> Exam {
    let mut w = ex.clone();
    let mut r = Rng::new(seed);
    let mut state = (r.next_u64() & 0xffff_ffff) as u32;
    if state == 0 {
        state = 0x6d2b_79f5; // xorshift 的 0 状态会卡死
    }
    w.rng.state = state;
    let k = hidden.known_deck_prefix.min(w.deck.len());
    r.shuffle(&mut w.deck[k..]);
    let idx: Vec<usize> = hidden.free_turns.iter().copied().filter(|&i| i < w.turn_types.len()).collect();
    let mut vals: Vec<i64> = idx.iter().map(|&i| w.turn_types[i]).collect();
    r.shuffle(&mut vals);
    for (&i, v) in idx.iter().zip(vals) {
        w.turn_types[i] = v;
    }
    w
}

/// 终局目标（偶像之路：用户 11-14「追求三星」）。rivals 为空时就是分数（H.I.F）；否则按名次给星：
/// 名次 = 1 + 终局分 ≤ 对手分的对手数（同分算输，保守），★3 = 第 1、★2 = 第 2、★1 = 第 3；
/// 有通关条件（26 层低下状態 ≥ n、27 层除外 SSR ≤ n）时，第 1 但条件不满足给 ★2（GKMS 实测）。评估值 = 星 × 10¹⁰ + 分。
#[derive(Clone, Debug, Default)]
pub struct Objective {
    pub rivals: Vec<i64>,
    pub lost_ssr_max: Option<i64>,
    pub debuffs_min: Option<i64>,
    /// 过关名次（初 = 3、N.I.A = 1）：名次在线外一律 0 档。None = 不限（偶像之路按名次给星）
    pub pass_rank: Option<i64>,
    /// 分数封顶（初 REGULAR 中間的 forceEndScore、课程的满分线）：目标值里的分数按它截断，终局原始分照实
    pub score_cap: Option<i64>,
    /// 课程的合格线（没有对手时）：到线 1 档、没到 0 档
    pub clear_score: Option<i64>,
}

pub const STAR_WEIGHT: i64 = 10_000_000_000;
const RARITY_SSR: i64 = 4;

impl Objective {
    pub fn place(&self, ex: &Exam) -> i64 {
        1 + self.rivals.iter().filter(|&&r| r >= ex.score).count() as i64
    }
    pub fn conditions_ok(&self, ex: &Exam) -> bool {
        if let Some(n) = self.lost_ssr_max {
            let lost = ex.lost.iter().filter(|&&c| ex.row(c).base.rarity == RARITY_SSR).count() as i64;
            if lost > n {
                return false;
            }
        }
        if let Some(n) = self.debuffs_min {
            let d = ex.status.effects.iter().filter(|s| ex.m.debuff_status_classes.contains(&s.cls)).count() as i64;
            if d < n {
                return false;
            }
        }
        true
    }
    fn capped(&self, ex: &Exam) -> i64 {
        match self.score_cap {
            Some(c) if c > 0 => ex.score.min(c),
            _ => ex.score,
        }
    }
    pub fn stars(&self, ex: &Exam) -> i64 {
        if self.rivals.is_empty() {
            return match self.clear_score {
                Some(c) => (ex.score >= c) as i64,
                None => 0,
            };
        }
        let place = self.place(ex);
        if matches!(self.pass_rank, Some(r) if place > r) {
            return 0;
        }
        match place {
            1 => if self.conditions_ok(ex) { 3 } else { 2 },
            2 => 2,
            3 => 1,
            _ => 0,
        }
    }
    pub fn value(&self, ex: &Exam) -> i64 {
        if self.rivals.is_empty() && self.clear_score.is_none() { self.capped(ex) } else { self.stars(ex) * STAR_WEIGHT + self.capped(ex) }
    }
}

/// GNRPA 参数（缺省按论文：α = 1、τ = 1；层数 / 迭代数按 15 秒预算粗定，bench 冒烟时再调）。
#[derive(Clone, Debug)]
pub struct Params {
    pub level: u32,
    pub iterations: u32,
    pub alpha: f64,
    pub temperature: f64,
    /// 「结束回合」的偏置 β（负数 = 还有牌能打时少结束；0 = 不偏）
    pub end_bias: f64,
    /// 一次续打最多走几步（防死循环）
    pub max_steps: usize,
    /// 眼前得分先验的权重（0 = 关）：每个候选加 greedy × 这一手的即时得分增量 / 本步候选里最大的增量。
    /// 增量按动作编码在每个世界里缓存（第一次遇到时 clone 走一步算），续打不会慢几倍。
    pub greedy: f64,
    /// 初始策略（跨步热启动：上一步各世界最好路线学到的权重，按动作编码；空 = 从零开始）。每个世界第一轮从它起，之后的轮从零起。
    pub prior: std::sync::Arc<Policy>,
    /// 终局目标（缺省 = 分数）
    pub obj: Objective,
}

impl Default for Params {
    fn default() -> Self {
        Params { level: 2, iterations: 60, alpha: 1.0, temperature: 1.0, end_bias: -1.0, max_steps: 600, greedy: 0.0, prior: Default::default(), obj: Objective::default() }
    }
}

/// 动作编码：(回合, 本回合第几手, 动作种类, 卡 / 饮料 id, 强化)。同一个编码在不同续打里代表「同一个决定」，权重共享。
fn code(ex: &Exam, a: Action) -> u64 {
    let (kind, obj, up) = match a {
        Action::Play(i) => {
            let c = &ex.cards[ex.hand[i]];
            (1u64, c.card_id as u64, c.upgrade() as u64)
        }
        Action::Drink(i) => (2u64, ex.drinks[i] as u64, 0),
        Action::End => (3u64, 0, 0),
    };
    let t = ex.current_turn.max(0) as u64;
    let n = ex.turn_card_play_count.max(0) as u64;
    (t << 52) ^ (n << 46) ^ (kind << 44) ^ (up << 40) ^ obj
}

fn bias(a: Action, p: &Params, n_legal: usize) -> f64 {
    match a {
        Action::End if n_legal > 1 => p.end_bias,
        _ => 0.0,
    }
}

/// 一条路线：每一步的 (候选编码, 偏置, 选了第几个)，调权重时不用重放局面。
#[derive(Clone, Default)]
pub struct Line {
    /// 目标值（Objective::value；没有对手时就是分数）
    pub score: i64,
    /// 原始终局分 / 星
    pub raw: i64,
    pub stars: i64,
    pub first: Option<Action>,
    steps: Vec<(Vec<u64>, Vec<f64>, usize)>,
}

pub type Policy = FxHashMap<u64, f64>;

/// 一个世界里跨续打共用的东西：续打次数、眼前得分增量的缓存（按动作编码）。
#[derive(Default)]
pub struct Run {
    pub playouts: u64,
    gain: FxHashMap<u64, f64>,
}

fn gain(ex: &Exam, a: Action, c: u64, run: &mut Run) -> f64 {
    if let Some(&g) = run.gain.get(&c) {
        return g;
    }
    let g = match a {
        Action::End => 0.0,
        _ => {
            let mut x = ex.clone();
            x.step(a);
            (x.score - ex.score).max(0) as f64
        }
    };
    run.gain.insert(c, g);
    g
}

fn playout(root: &Exam, pol: &Policy, p: &Params, rng: &mut Rng, run: &mut Run) -> Line {
    let mut ex = root.clone();
    let mut line = Line::default();
    let mut probs: Vec<f64> = vec![];
    for _ in 0..p.max_steps {
        ex.advance();
        if ex.ended {
            break;
        }
        let acts = ex.legal_actions();
        if acts.is_empty() {
            break;
        }
        let codes: Vec<u64> = acts.iter().map(|&a| code(&ex, a)).collect();
        let mut bs: Vec<f64> = acts.iter().map(|&a| bias(a, p, acts.len())).collect();
        if p.greedy != 0.0 && acts.len() > 1 {
            let gs: Vec<f64> = acts.iter().zip(&codes).map(|(&a, &c)| gain(&ex, a, c, run)).collect();
            let mx = gs.iter().cloned().fold(0.0, f64::max);
            if mx > 0.0 {
                for (b, g) in bs.iter_mut().zip(&gs) {
                    *b += p.greedy * g / mx;
                }
            }
        }
        probs.clear();
        let mut mx = f64::NEG_INFINITY;
        for (c, b) in codes.iter().zip(&bs) {
            let v = pol.get(c).copied().unwrap_or(0.0) / p.temperature + b;
            probs.push(v);
            mx = mx.max(v);
        }
        let mut tot = 0.0;
        for v in probs.iter_mut() {
            *v = (*v - mx).exp();
            tot += *v;
        }
        let mut x = rng.unit() * tot;
        let mut k = probs.len() - 1;
        for (i, v) in probs.iter().enumerate() {
            if x < *v {
                k = i;
                break;
            }
            x -= v;
        }
        if line.first.is_none() {
            line.first = Some(acts[k]);
        }
        ex.step(acts[k]);
        line.steps.push((codes, bs, k));
    }
    ex.advance();
    line.raw = ex.score;
    line.stars = p.obj.stars(&ex);
    line.score = p.obj.value(&ex);
    line
}

/// 按最好路线调权重：选中的 +α，每步所有候选按当前概率 −α·π（论文的 Adapt，含偏置）。
fn adapt(pol: &Policy, best: &Line, p: &Params) -> Policy {
    let mut out = pol.clone();
    for (codes, bs, k) in &best.steps {
        let mut ws: Vec<f64> = codes.iter().zip(bs).map(|(c, b)| pol.get(c).copied().unwrap_or(0.0) / p.temperature + b).collect();
        let mx = ws.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let mut tot = 0.0;
        for w in ws.iter_mut() {
            *w = (*w - mx).exp();
            tot += *w;
        }
        for (i, c) in codes.iter().enumerate() {
            let d = ws[i] / tot;
            *out.entry(*c).or_insert(0.0) -= p.alpha / p.temperature * d;
        }
        *out.entry(codes[*k]).or_insert(0.0) += p.alpha / p.temperature;
    }
    out
}

/// 嵌套搜索；到 deadline 就返回目前最好的路线。
pub fn gnrpa(root: &Exam, level: u32, pol: &Policy, p: &Params, rng: &mut Rng, deadline: Instant, run: &mut Run) -> Line {
    if level == 0 {
        run.playouts += 1;
        return playout(root, pol, p, rng, run);
    }
    let mut best = Line { score: i64::MIN, ..Default::default() };
    let mut pol = pol.clone();
    for _ in 0..p.iterations {
        let r = gnrpa(root, level - 1, &pol, p, rng, deadline, run);
        if r.score >= best.score {
            best = r;
        }
        if Instant::now() >= deadline {
            break;
        }
        pol = adapt(&pol, &best, p);
    }
    best
}

/// 一个世界的结果。
#[derive(Clone, Debug)]
pub struct WorldResult {
    pub first: Option<Action>,
    /// 目标值（同 Line.score）
    pub score: i64,
    pub raw: i64,
    pub stars: i64,
    pub playouts: u64,
    /// 这个世界最好路线调出来的权重（从零 adapt 一次；search 平均成下一步的 prior）
    pub policy: Policy,
    /// 顶层至少跑完了一整轮（false = 第一轮就被时间截断，路线没充分搜）
    pub complete: bool,
}

/// 搜索总结果：各第一手的票数和平均终局分，按（票数，平均分）从高到低。
#[derive(Clone, Debug)]
pub struct SearchResult {
    pub best: Option<Action>,
    pub votes: Vec<(Action, usize, f64)>,
    pub worlds: Vec<WorldResult>,
    pub elapsed: Duration,
    /// 各世界 policy 的平均（下一步热启动用）
    pub policy: Policy,
}

fn run_world(w: Exam, p: &Params, seed: u64, deadline: Instant) -> WorldResult {
    let mut rng = Rng::new(seed.wrapping_mul(0x2545_F491_4F6C_DD1D));
    let mut n = Run::default();
    // 顶层跑完一轮还有时间：从空策略重新跑一轮（换随机数），留最好的路线（预算用满，不提前交卷）
    let mut best = gnrpa(&w, p.level, &p.prior, p, &mut rng, deadline, &mut n);
    let mut rounds = 1;
    while Instant::now() < deadline {
        let r = gnrpa(&w, p.level, &Policy::default(), p, &mut rng, deadline, &mut n);
        if r.score > best.score {
            best = r;
        }
        rounds += 1;
    }
    let policy = adapt(&Policy::default(), &best, p);
    WorldResult { first: best.first, score: best.score, raw: best.raw, stars: best.stars, playouts: n.playouts, policy, complete: rounds > 1 }
}

/// 把一个线程要跑的世界换成只属于这个线程的副本：主数据深拷一份，局面导出后用这份主数据导入（往返无损，2026-11-24 对拍
/// 6 个编成、各种中途局面 1020 次推演结果全同）。线程之间不再共享任何 Arc，引用计数不抢缓存行：8 线程推演总数约是原来的 3 倍。
pub fn thread_local_worlds(chunk: Vec<(usize, Exam)>) -> Vec<(usize, Exam)> {
    let Some((_, first)) = chunk.first() else { return chunk };
    let m = Arc::new(first.m.deep_copy());
    chunk.into_iter().map(|(i, w)| (i, crate::io::import(m.clone(), &crate::io::export(&w)))).collect()
}

/// W 个世界并行各跑 GNRPA，第一手投票。threads = 0 时用「CPU 核数 − 1」（至少 1）。
/// 局面已经结束 / 没有决定可做时 best = None；一个世界都没出第一手时 best = None（Python 退回 UINet）。
pub fn search(ex: &Exam, hidden: &Hidden, worlds: usize, seconds: f64, threads: usize, seed: u64, p: &Params) -> SearchResult {
    let t0 = Instant::now();
    let deadline = t0 + Duration::from_secs_f64(seconds.max(0.0));
    let mut root = ex.clone();
    root.advance();
    if root.ended || root.legal_actions().len() <= 1 {
        let only = if root.ended { None } else { root.legal_actions().first().copied() };
        return SearchResult { best: only, votes: only.map(|a| vec![(a, 0, 0.0)]).unwrap_or_default(), worlds: vec![], elapsed: t0.elapsed(), policy: Policy::default() };
    }
    let threads = if threads == 0 {
        std::thread::available_parallelism().map(|n| n.get().saturating_sub(1)).unwrap_or(1).max(1)
    } else {
        threads
    };
    let jobs: Vec<(usize, Exam)> = (0..worlds.max(1)).map(|i| (i, sample_world(&root, hidden, seed.wrapping_add(i as u64 * 7_919)))).collect();
    let mut results: Vec<Option<WorldResult>> = vec![None; jobs.len()];
    let chunks: Vec<Vec<(usize, Exam)>> = {
        let mut c: Vec<Vec<(usize, Exam)>> = (0..threads.min(jobs.len())).map(|_| vec![]).collect();
        let n = c.len();
        for (k, j) in jobs.into_iter().enumerate() {
            c[k % n].push(j);
        }
        c
    };
    std::thread::scope(|s| {
        let hs: Vec<_> = chunks
            .into_iter()
            .map(|chunk| {
                let p = p.clone();
                s.spawn(move || {
                    // 一个线程负责几个世界：剩余时间平分给还没跑的世界
                    let n = chunk.len();
                    let mut out = vec![];
                    let chunk = thread_local_worlds(chunk);
                    for (k, (i, w)) in chunk.into_iter().enumerate() {
                        let now = Instant::now();
                        let left = deadline.saturating_duration_since(now);
                        let dl = now + left / (n - k) as u32;
                        out.push((i, run_world(w, &p, seed ^ (i as u64) << 20, dl)));
                    }
                    out
                })
            })
            .collect();
        for h in hs {
            for (i, r) in h.join().expect("搜索线程 panic") {
                results[i] = Some(r);
            }
        }
    });
    let worlds: Vec<WorldResult> = results.into_iter().flatten().collect();
    let mut tally: Vec<(Action, usize, f64)> = vec![];
    for w in &worlds {
        if let Some(a) = w.first {
            match tally.iter_mut().find(|(b, _, _)| *b == a) {
                Some(t) => {
                    t.2 = (t.2 * t.1 as f64 + w.score as f64) / (t.1 + 1) as f64;
                    t.1 += 1;
                }
                None => tally.push((a, 1, w.score as f64)),
            }
        }
    }
    tally.sort_by(|a, b| b.1.cmp(&a.1).then(b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal)));
    let mut policy = Policy::default();
    let nw = worlds.len().max(1) as f64;
    for w in &worlds {
        for (c, v) in &w.policy {
            *policy.entry(*c).or_insert(0.0) += v / nw;
        }
    }
    SearchResult { best: tally.first().map(|t| t.0), votes: tally, worlds, elapsed: t0.elapsed(), policy }
}


/// 一批局面各自从当前起搜一整局（不重排隐藏信息：局面里的山札顺序就当真），返回每个局面最好路线的 (目标值, 终局分, 星)。
/// 偶像之路编成评估用：每个编成按种子开 N 局，各搜一遍——世界内牌序固定，相当于「看得到牌序」的上限估计，分数偏乐观，
/// 但对所有编成一视同仁，拿来排序公平。threads = 0 时用 CPU 核数 − 1。
pub fn plan_many(exams: Vec<Exam>, seconds: f64, threads: usize, seed: u64, p: &Params) -> Vec<(i64, i64, i64)> {
    let threads = if threads == 0 {
        std::thread::available_parallelism().map(|n| n.get().saturating_sub(1)).unwrap_or(1).max(1)
    } else {
        threads
    };
    let n = exams.len();
    let mut out = vec![(0i64, 0i64, 0i64); n];
    let mut chunks: Vec<Vec<(usize, Exam)>> = (0..threads.min(n.max(1))).map(|_| vec![]).collect();
    let k = chunks.len();
    for (i, mut ex) in exams.into_iter().enumerate() {
        ex.advance();
        chunks[i % k].push((i, ex));
    }
    std::thread::scope(|s| {
        let hs: Vec<_> = chunks
            .into_iter()
            .map(|chunk| {
                let p = p.clone();
                s.spawn(move || {
                    thread_local_worlds(chunk)
                        .into_iter()
                        .map(|(i, ex)| {
                            let w = if ex.ended {
                                WorldResult { first: None, score: p.obj.value(&ex), raw: ex.score, stars: p.obj.stars(&ex), playouts: 0, policy: Policy::default(), complete: true }
                            } else {
                                run_world(ex, &p, seed ^ ((i as u64) << 24), Instant::now() + Duration::from_secs_f64(seconds.max(0.0)))
                            };
                            (i, (w.score, w.raw, w.stars))
                        })
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        for h in hs {
            for (i, r) in h.join().expect("评估线程 panic") {
                out[i] = r;
            }
        }
    });
    out
}
