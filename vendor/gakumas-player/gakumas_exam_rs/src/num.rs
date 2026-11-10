//! 单精度运算（与 gakumas_exam/f32.py 同义）与客户端随机数（gakumas_exam/rng.py 的 xorshift32）。

pub const INT_MIN: i64 = -(1i64 << 31);
pub const INT_MAX: i64 = (1i64 << 31) - 1;
/// f32.EPS32 = f(-0.0001)
pub const EPS32: f32 = -0.0001f32;
/// f32.NEG_EPS32 = f(0.0001)（名字沿用 Python 版）
pub const NEG_EPS32: f32 = 0.0001f32;

#[inline]
pub fn i2f(i: i64) -> f32 {
    i as f64 as f32
}

#[inline]
pub fn ceil_i(x: f32) -> i64 {
    if x.is_infinite() || x.is_nan() {
        INT_MIN
    } else {
        (x as f64).ceil() as i64
    }
}

#[inline]
pub fn floor_i(x: f32) -> i64 {
    if x.is_infinite() || x.is_nan() {
        INT_MIN
    } else {
        (x as f64).floor() as i64
    }
}

#[inline]
pub fn trunc_i(x: f32) -> i64 {
    if x.is_infinite() || x.is_nan() {
        INT_MIN
    } else {
        (x as f64).trunc() as i64
    }
}

#[inline]
pub fn permil(p: i64) -> f32 {
    i2f(p) / 1000.0f32
}

/// ExamEffectUtility.GetRatioEffectIntValue
pub fn ratio_int(value: i64, ratio: i64, is_ceil: bool) -> i64 {
    if value < 1 {
        return 0;
    }
    let r = permil(ratio);
    let x = r * (value as f64 as f32);
    if is_ceil {
        ceil_i(x + EPS32)
    } else {
        floor_i(x + NEG_EPS32)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NativeRandom {
    pub state: u32,
}

impl NativeRandom {
    pub fn new(state: u64) -> Self {
        NativeRandom { state: (state & 0xffff_ffff) as u32 }
    }

    #[inline]
    fn advance(&mut self) -> u32 {
        let old = self.state;
        let mut x = old ^ (old << 13);
        x ^= x >> 17;
        self.state = x ^ (x << 5);
        old
    }

    /// [low, high)
    #[inline]
    pub fn integer(&mut self, low: i64, high: i64) -> i64 {
        assert!(low <= high, "Invalid RNG interval");
        let old = self.advance() as u128;
        low + ((old * (high - low) as u128) >> 32) as i64
    }

    #[inline]
    pub fn signed(&mut self) -> i64 {
        self.advance() as i64 - (1i64 << 31)
    }

    pub fn shuffle<T>(&mut self, v: &mut [T]) {
        let n = v.len();
        let mut k = n;
        while k > 1 {
            let j = self.integer(0, k as i64) as usize;
            v.swap(j, k - 1);
            k -= 1;
        }
    }
}
