//! Atom model + temperature FSM (mirrors karmazyn_atom.py).

pub const T_INIT: f64 = 50.0;
pub const T_MAX: f64 = 100.0;
pub const T_HOT: f64 = 70.0;
pub const T_WARM: f64 = 30.0;
pub const T_TOMB: f64 = 2.0;
pub const DECAY_DEFAULT: f64 = 0.92;
pub const HEAT_READ: f64 = 10.0;
#[allow(dead_code)]
pub const HEAT_WRITE: f64 = 5.0;

pub type AtomId = u32;

#[inline]
pub fn clamp_t(t: f64) -> f64 {
    if t < 0.0 {
        0.0
    } else if t > T_MAX {
        T_MAX
    } else {
        t
    }
}

/// HOT / WARM / COLD / TOMB — single source of truth for T classification.
#[inline]
pub fn state_for_t(t: f64) -> &'static str {
    if t >= T_HOT {
        "HOT"
    } else if t >= T_WARM {
        "WARM"
    } else if t >= T_TOMB {
        "COLD"
    } else {
        "TOMB"
    }
}

#[derive(Debug, Clone)]
pub struct Atom {
    pub id: AtomId,
    pub s: String,
    pub e: String,
    pub t: f64,
    /// Opaque language payload handle (Python object id, or 0 = none).
    /// Native core never inspects it — only passes to env_of callback.
    pub value_token: u64,
    pub reads: u64,
    pub writes: u64,
}

impl Atom {
    pub fn new(id: AtomId, s: impl Into<String>, e: impl Into<String>, t: f64) -> Self {
        Self {
            id,
            s: s.into(),
            e: e.into(),
            t: clamp_t(t),
            value_token: 0,
            reads: 0,
            writes: 0,
        }
    }

    #[inline]
    pub fn state(&self) -> &'static str {
        state_for_t(self.t)
    }

    #[inline]
    pub fn is_dead(&self) -> bool {
        self.t < T_TOMB
    }

    pub fn decay(&mut self, rate: f64) {
        let r = if rate < 0.0 { 0.0 } else { rate };
        self.t = clamp_t(self.t * r);
    }

    pub fn heat(&mut self, amount: f64) {
        let a = if amount < 0.0 { 0.0 } else { amount };
        self.t = clamp_t(self.t + a);
    }

    pub fn touch(&mut self) {
        self.reads += 1;
        self.heat(HEAT_READ);
    }
}
