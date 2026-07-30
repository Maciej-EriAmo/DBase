//! Store: atoms + bubbles + roots + reach-GC.
//! Mirrors the law of karmazyn_substrate.py (without Python EventBus / HRR).

use crate::atom::{Atom, AtomId, DECAY_DEFAULT, T_INIT};
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

pub type BubbleId = u32;

/// env_of hook: value_token → optional bubble id (0 = none).
pub type EnvOfFn = Arc<dyn Fn(u64) -> Option<BubbleId> + Send + Sync>;
/// extra_reach hook: returns additional reachable atom ids.
pub type ExtraReachFn = Arc<dyn Fn() -> Vec<AtomId> + Send + Sync>;

#[derive(Debug, Clone)]
pub struct Bubble {
    #[allow(dead_code)]
    pub id: BubbleId,
    #[allow(dead_code)]
    pub label: String,
    pub parent: Option<BubbleId>,
    pub bindings: HashMap<String, AtomId>,
}

#[derive(Debug, Clone, Default)]
pub struct Stats {
    pub total: usize,
    pub hot: usize,
    pub warm: usize,
    pub cold: usize,
    pub tomb: usize,
    pub alive: usize,
    pub dead: usize,
    pub reaped: u64,
    pub retained_tomb: usize,
    pub bubbles: usize,
}

struct Inner {
    atoms: HashMap<AtomId, Atom>,
    bubbles: HashMap<BubbleId, Bubble>,
    roots: Vec<BubbleId>,
    next_atom: AtomId,
    next_bubble: BubbleId,
    thermal: bool,
    decay: f64,
    reaped: u64,
    retained_tomb: HashSet<AtomId>,
    ticking: bool,
    env_of_hooks: Vec<(String, EnvOfFn)>,
    extra_reach_hooks: Vec<(String, ExtraReachFn)>,
}

impl Inner {
    fn new(thermal: bool, decay: f64) -> Self {
        Self {
            atoms: HashMap::new(),
            bubbles: HashMap::new(),
            roots: Vec::new(),
            next_atom: 0,
            next_bubble: 0,
            thermal,
            decay,
            reaped: 0,
            retained_tomb: HashSet::new(),
            ticking: false,
            env_of_hooks: Vec::new(),
            extra_reach_hooks: Vec::new(),
        }
    }

    fn dispatch_env_of(&self, token: u64) -> Option<BubbleId> {
        for (_name, f) in &self.env_of_hooks {
            if let Some(b) = f(token) {
                if b != 0 {
                    return Some(b);
                }
            }
        }
        None
    }

    fn dispatch_extra_reach(&self) -> Vec<AtomId> {
        let mut ids = Vec::new();
        for (_name, f) in &self.extra_reach_hooks {
            ids.extend(f());
        }
        ids
    }

    fn walk_reach(&self) -> (HashSet<AtomId>, HashSet<BubbleId>) {
        let mut reach = HashSet::new();
        let mut seen = HashSet::new();
        let mut stack: Vec<BubbleId> = self.roots.clone();

        while let Some(bid) = stack.pop() {
            if !seen.insert(bid) {
                continue;
            }
            let Some(b) = self.bubbles.get(&bid) else {
                continue;
            };
            for (_name, aid) in &b.bindings {
                if self.atoms.contains_key(aid) {
                    reach.insert(*aid);
                    if let Some(atom) = self.atoms.get(aid) {
                        if let Some(env_b) = self.dispatch_env_of(atom.value_token) {
                            stack.push(env_b);
                        }
                    }
                }
            }
            if let Some(p) = b.parent {
                stack.push(p);
            }
        }
        for aid in self.dispatch_extra_reach() {
            reach.insert(aid);
        }
        (reach, seen)
    }

    fn purge_bindings(&mut self, aid: AtomId) {
        for b in self.bubbles.values_mut() {
            b.bindings.retain(|_k, v| *v != aid);
        }
    }

    fn tick_body(&mut self) {
        let (reach, _seen) = self.walk_reach();
        let aids: Vec<AtomId> = self.atoms.keys().copied().collect();
        for aid in &aids {
            if let Some(a) = self.atoms.get_mut(aid) {
                a.decay(self.decay);
            }
        }
        for aid in aids {
            let dead = self.atoms.get(&aid).map(|a| a.is_dead()).unwrap_or(false);
            if !dead {
                self.retained_tomb.remove(&aid);
                continue;
            }
            if reach.contains(&aid) {
                self.retained_tomb.insert(aid);
            } else {
                self.purge_bindings(aid);
                if self.atoms.remove(&aid).is_some() {
                    self.retained_tomb.remove(&aid);
                    self.reaped += 1;
                }
            }
        }
    }
}

/// Thread-safe Store handle (mirrors Python Store.lock / RLock).
#[derive(Clone)]
pub struct Store {
    inner: Arc<Mutex<Inner>>,
}

impl Store {
    pub fn new(thermal: bool) -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner::new(thermal, DECAY_DEFAULT))),
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn atom_new(&self, s: &str, e: &str, t: f64) -> AtomId {
        let mut g = self.lock();
        let id = g.next_atom;
        g.next_atom = g.next_atom.saturating_add(1);
        g.atoms.insert(id, Atom::new(id, s, e, if t.is_nan() { T_INIT } else { t }));
        id
    }

    pub fn atom_set_value_token(&self, aid: AtomId, token: u64) -> bool {
        let mut g = self.lock();
        if let Some(a) = g.atoms.get_mut(&aid) {
            a.value_token = token;
            true
        } else {
            false
        }
    }

    pub fn atom_value_token(&self, aid: AtomId) -> Option<u64> {
        let g = self.lock();
        g.atoms.get(&aid).map(|a| a.value_token)
    }

    pub fn has_atom(&self, aid: AtomId) -> bool {
        self.lock().atoms.contains_key(&aid)
    }

    pub fn get_atom_t(&self, aid: AtomId) -> Option<f64> {
        self.lock().atoms.get(&aid).map(|a| a.t)
    }

    pub fn get_atom_state(&self, aid: AtomId) -> Option<String> {
        self.lock().atoms.get(&aid).map(|a| a.state().to_string())
    }

    pub fn atom_is_dead(&self, aid: AtomId) -> Option<bool> {
        self.lock().atoms.get(&aid).map(|a| a.is_dead())
    }

    /// All live atom ids (order not guaranteed).
    pub fn atom_ids(&self) -> Vec<AtomId> {
        self.lock().atoms.keys().copied().collect()
    }

    /// Set absolute temperature (no heat/touch counters). Returns false if missing.
    pub fn atom_set_t(&self, aid: AtomId, t: f64) -> bool {
        use crate::atom::clamp_t;
        let mut g = self.lock();
        if let Some(a) = g.atoms.get_mut(&aid) {
            a.t = clamp_t(if t.is_nan() { T_INIT } else { t });
            true
        } else {
            false
        }
    }

    /// Upsert atom with fixed id (snapshot restore / host import).
    pub fn atom_upsert(
        &self,
        aid: AtomId,
        s: &str,
        e: &str,
        t: f64,
        value_token: u64,
    ) -> bool {
        use crate::atom::clamp_t;
        let mut g = self.lock();
        let tt = clamp_t(if t.is_nan() { T_INIT } else { t });
        if let Some(a) = g.atoms.get_mut(&aid) {
            a.s = s.to_string();
            a.e = e.to_string();
            a.t = tt;
            a.value_token = value_token;
        } else {
            let mut atom = Atom::new(aid, s, e, tt);
            atom.value_token = value_token;
            g.atoms.insert(aid, atom);
            g.retained_tomb.remove(&aid);
        }
        if aid >= g.next_atom {
            g.next_atom = aid.saturating_add(1);
        }
        true
    }

    /// Replace atom registry with snapshot (transaction rollback).
    /// Entries: (id, S, E, T, value_token). Bubbles/roots unchanged;
    /// dangling bindings to removed atoms are purged.
    pub fn restore_atoms(&self, entries: &[(AtomId, String, String, f64, u64)]) {
        use crate::atom::clamp_t;
        let mut g = self.lock();
        let keep: HashSet<AtomId> = entries.iter().map(|e| e.0).collect();
        let remove: Vec<AtomId> = g
            .atoms
            .keys()
            .copied()
            .filter(|id| !keep.contains(id))
            .collect();
        for aid in remove {
            g.purge_bindings(aid);
            g.retained_tomb.remove(&aid);
            g.atoms.remove(&aid);
        }
        let mut max_id = 0u32;
        for (id, s, e, t, token) in entries {
            max_id = max_id.max(*id);
            let tt = clamp_t(if t.is_nan() { T_INIT } else { *t });
            if let Some(a) = g.atoms.get_mut(id) {
                a.s = s.clone();
                a.e = e.clone();
                a.t = tt;
                a.value_token = *token;
            } else {
                let mut atom = Atom::new(*id, s.clone(), e.clone(), tt);
                atom.value_token = *token;
                g.atoms.insert(*id, atom);
            }
            g.retained_tomb.remove(id);
        }
        g.next_atom = max_id.saturating_add(1).max(g.next_atom);
    }

    pub fn heat(&self, aid: AtomId) -> bool {
        let mut g = self.lock();
        if let Some(a) = g.atoms.get_mut(&aid) {
            a.touch();
            true
        } else {
            false
        }
    }

    pub fn delete_atom(&self, aid: AtomId) -> bool {
        let mut g = self.lock();
        if !g.atoms.contains_key(&aid) {
            return false;
        }
        g.purge_bindings(aid);
        g.retained_tomb.remove(&aid);
        g.atoms.remove(&aid).is_some()
    }

    pub fn bubble_new(&self, label: &str, parent: Option<BubbleId>) -> BubbleId {
        let mut g = self.lock();
        let id = g.next_bubble;
        g.next_bubble = g.next_bubble.saturating_add(1);
        g.bubbles.insert(
            id,
            Bubble {
                id,
                label: label.to_string(),
                parent,
                bindings: HashMap::new(),
            },
        );
        id
    }

    pub fn bind(&self, bid: BubbleId, name: &str, aid: AtomId) -> Result<(), &'static str> {
        let mut g = self.lock();
        if !g.atoms.contains_key(&aid) {
            return Err("bind: atom missing");
        }
        let b = g.bubbles.get_mut(&bid).ok_or("bind: bubble missing")?;
        b.bindings.insert(name.to_string(), aid);
        Ok(())
    }

    pub fn unbind(&self, bid: BubbleId, name: &str) -> Option<AtomId> {
        let mut g = self.lock();
        g.bubbles.get_mut(&bid)?.bindings.remove(name)
    }

    pub fn lookup(&self, bid: BubbleId, name: &str) -> Option<AtomId> {
        let mut g = self.lock();
        let thermal = g.thermal;
        let mut cur = Some(bid);
        while let Some(id) = cur {
            let parent = g.bubbles.get(&id).map(|b| b.parent);
            let aid = g.bubbles.get(&id).and_then(|b| b.bindings.get(name).copied());
            if let Some(aid) = aid {
                if g.atoms.contains_key(&aid) {
                    if thermal {
                        if let Some(a) = g.atoms.get_mut(&aid) {
                            a.touch();
                        }
                    }
                    return Some(aid);
                }
            }
            cur = parent.flatten();
        }
        None
    }

    pub fn set_root(&self, bid: BubbleId) {
        let mut g = self.lock();
        if g.bubbles.contains_key(&bid) && !g.roots.contains(&bid) {
            g.roots.push(bid);
        }
    }

    pub fn unset_root(&self, bid: BubbleId) {
        let mut g = self.lock();
        g.roots.retain(|x| *x != bid);
    }

    pub fn register_env_of<F>(&self, name: &str, f: F)
    where
        F: Fn(u64) -> Option<BubbleId> + Send + Sync + 'static,
    {
        let mut g = self.lock();
        g.env_of_hooks.retain(|(n, _)| n != name);
        g.env_of_hooks.insert(0, (name.to_string(), Arc::new(f)));
    }

    pub fn unregister_env_of(&self, name: &str) {
        self.lock().env_of_hooks.retain(|(n, _)| n != name);
    }

    pub fn register_extra_reach<F>(&self, name: &str, f: F)
    where
        F: Fn() -> Vec<AtomId> + Send + Sync + 'static,
    {
        let mut g = self.lock();
        g.extra_reach_hooks.retain(|(n, _)| n != name);
        g.extra_reach_hooks.insert(0, (name.to_string(), Arc::new(f)));
    }

    pub fn unregister_extra_reach(&self, name: &str) {
        self.lock()
            .extra_reach_hooks
            .retain(|(n, _)| n != name);
    }

    pub fn tick(&self) {
        let mut g = self.lock();
        if !g.thermal || g.ticking {
            return;
        }
        g.ticking = true;
        g.tick_body();
        g.ticking = false;
    }

    pub fn settle(&self, n: u32) {
        for _ in 0..n {
            self.tick();
        }
    }

    pub fn stats(&self) -> Stats {
        let g = self.lock();
        let mut st = Stats {
            reaped: g.reaped,
            bubbles: g.bubbles.len(),
            ..Default::default()
        };
        for a in g.atoms.values() {
            st.total += 1;
            match a.state() {
                "HOT" => st.hot += 1,
                "WARM" => st.warm += 1,
                "COLD" => st.cold += 1,
                _ => st.tomb += 1,
            }
            if a.is_dead() {
                st.dead += 1;
            } else {
                st.alive += 1;
            }
        }
        st.retained_tomb = g
            .retained_tomb
            .iter()
            .filter(|id| g.atoms.get(id).map(|a| a.is_dead()).unwrap_or(false))
            .count();
        st
    }

    pub fn atom_count(&self) -> usize {
        self.lock().atoms.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::atom::T_TOMB;

    /// Enough ticks to cool T_INIT * 0.92^n below T_TOMB.
    const COOL: u32 = 80;

    #[test]
    fn orphan_dies() {
        let s = Store::new(true);
        let a = s.atom_new("var", "orphan", f64::NAN);
        s.settle(COOL);
        assert!(!s.has_atom(a));
        assert_eq!(s.stats().reaped, 1);
    }

    #[test]
    fn restore_atoms_rollback() {
        let s = Store::new(false);
        let a0 = s.atom_new("var", "keep", 50.0);
        s.atom_set_value_token(a0, 42);
        let snap = vec![(a0, "var".into(), "keep".into(), 50.0, 42u64)];
        let a1 = s.atom_new("var", "gone", 50.0);
        assert!(s.has_atom(a1));
        s.restore_atoms(&snap);
        assert!(s.has_atom(a0));
        assert!(!s.has_atom(a1));
        assert_eq!(s.atom_value_token(a0), Some(42));
        // re-insert with fixed id after delete
        s.delete_atom(a0);
        s.restore_atoms(&[(7, "S".into(), "E".into(), 12.5, 0)]);
        assert!(s.has_atom(7));
        assert!((s.get_atom_t(7).unwrap() - 12.5).abs() < 1e-9);
        assert!(!s.has_atom(a0));
    }

    #[test]
    fn root_retains_tomb() {
        let s = Store::new(true);
        let root = s.bubble_new("root", None);
        s.set_root(root);
        let a = s.atom_new("var", "keep", f64::NAN);
        s.bind(root, "keep", a).unwrap();
        s.settle(COOL);
        assert!(s.has_atom(a));
        assert!(s.atom_is_dead(a).unwrap());
        assert_eq!(s.stats().retained_tomb, 1);
    }

    #[test]
    fn unset_root_then_reap() {
        let s = Store::new(true);
        let root = s.bubble_new("root", None);
        s.set_root(root);
        let a = s.atom_new("var", "x", f64::NAN);
        s.bind(root, "x", a).unwrap();
        s.settle(COOL);
        assert!(s.has_atom(a));
        s.unset_root(root);
        s.settle(2);
        assert!(!s.has_atom(a));
    }

    #[test]
    fn env_of_closure_survives() {
        let s = Store::new(true);
        let root = s.bubble_new("root", None);
        s.set_root(root);
        let inner = s.bubble_new("inner", None);
        let n = s.atom_new("var", "n", f64::NAN);
        s.bind(inner, "n", n).unwrap();
        // value_token encodes inner bubble id for env_of
        let clo = s.atom_new("var", "clo", f64::NAN);
        s.atom_set_value_token(clo, inner as u64);
        s.bind(root, "clo", clo).unwrap();
        s.register_env_of("guest", |tok| {
            if tok == 0 {
                None
            } else {
                Some(tok as BubbleId)
            }
        });
        s.settle(100);
        assert!(s.has_atom(n));
        assert_eq!(s.lookup(inner, "n"), Some(n));
    }

    #[test]
    fn extra_reach_protects() {
        use std::sync::atomic::{AtomicU32, Ordering};
        static HELD: AtomicU32 = AtomicU32::new(u32::MAX);
        let s = Store::new(true);
        let a = s.atom_new("var", "flat", f64::NAN);
        HELD.store(a, Ordering::SeqCst);
        s.register_extra_reach("guest", || {
            let id = HELD.load(Ordering::SeqCst);
            if id == u32::MAX {
                vec![]
            } else {
                vec![id]
            }
        });
        s.settle(COOL);
        assert!(s.has_atom(a));
        HELD.store(u32::MAX, Ordering::SeqCst);
        s.settle(2);
        assert!(!s.has_atom(a));
    }

    #[test]
    fn state_thresholds() {
        assert!(T_TOMB > 0.0);
        let s = Store::new(false);
        let a = s.atom_new("x", "e", 80.0);
        assert_eq!(s.get_atom_state(a).as_deref(), Some("HOT"));
    }
}
