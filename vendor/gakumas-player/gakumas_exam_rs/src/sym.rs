//! 字符串驻留：主数据 id、卡 id、检索 id 等都换成 u32，热路径里只比较整数。0 = 空串。

use rustc_hash::FxHashMap;
use std::sync::{OnceLock, RwLock};

pub type Sym = u32;
pub const NONE: Sym = 0;

struct Interner {
    map: FxHashMap<String, Sym>,
    strs: Vec<String>,
}

fn table() -> &'static RwLock<Interner> {
    static T: OnceLock<RwLock<Interner>> = OnceLock::new();
    T.get_or_init(|| {
        let mut map = FxHashMap::default();
        map.insert(String::new(), 0);
        RwLock::new(Interner { map, strs: vec![String::new()] })
    })
}

pub fn intern(s: &str) -> Sym {
    if s.is_empty() {
        return NONE;
    }
    if let Some(&v) = table().read().unwrap().map.get(s) {
        return v;
    }
    let mut t = table().write().unwrap();
    if let Some(&v) = t.map.get(s) {
        return v;
    }
    let v = t.strs.len() as Sym;
    t.strs.push(s.to_string());
    t.map.insert(s.to_string(), v);
    v
}

pub fn lookup(s: &str) -> Sym {
    if s.is_empty() {
        return NONE;
    }
    table().read().unwrap().map.get(s).copied().unwrap_or(NONE)
}

pub fn name(v: Sym) -> String {
    table().read().unwrap().strs[v as usize].clone()
}
