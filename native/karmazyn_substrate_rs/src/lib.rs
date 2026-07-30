//! PyO3 bindings for karmazyn_substrate core.
//!
//! Exposes `CoreStore` — pure GC law (atoms/bubbles/roots/tick/hooks).
//! Language surface (metadata, EventBus, HRR, Bubble isinstance) stays
//! in Python (`karmazyn_substrate_native.NativeStore`).

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyList, PyModule};
use std::sync::Arc;

use karmazyn_substrate::{Store, ABI_VERSION};

/// Opaque Rust store handle used by the Python facade.
#[pyclass(name = "CoreStore", module = "karmazyn_substrate_rs")]
pub struct CoreStore {
    inner: Store,
}

#[pymethods]
impl CoreStore {
    #[new]
    #[pyo3(signature = (thermal=true))]
    fn new(thermal: bool) -> Self {
        Self {
            inner: Store::new(thermal),
        }
    }

    /// ABI / crate version string (without trailing NUL).
    #[staticmethod]
    fn version() -> String {
        ABI_VERSION.trim_end_matches('\0').to_string()
    }

    fn atom_new(&self, s: &str, e: &str, t: f64) -> u32 {
        self.inner.atom_new(s, e, t)
    }

    fn atom_set_value(&self, aid: u32, token: u64) -> bool {
        self.inner.atom_set_value_token(aid, token)
    }

    fn atom_value(&self, aid: u32) -> u64 {
        self.inner.atom_value_token(aid).unwrap_or(0)
    }

    fn has_atom(&self, aid: u32) -> bool {
        self.inner.has_atom(aid)
    }

    fn delete_atom(&self, aid: u32) -> bool {
        self.inner.delete_atom(aid)
    }

    fn heat(&self, aid: u32) -> bool {
        self.inner.heat(aid)
    }

    fn atom_t(&self, aid: u32) -> f64 {
        self.inner.get_atom_t(aid).unwrap_or(-1.0)
    }

    fn atom_set_t(&self, aid: u32, t: f64) -> bool {
        self.inner.atom_set_t(aid, t)
    }

    fn atom_is_dead(&self, aid: u32) -> i32 {
        match self.inner.atom_is_dead(aid) {
            Some(true) => 1,
            Some(false) => 0,
            None => -1,
        }
    }

    /// Live atom ids (unordered).
    fn atom_ids(&self) -> Vec<u32> {
        self.inner.atom_ids()
    }

    /// Upsert atom with fixed id (S, E, T, value token).
    fn atom_upsert(&self, aid: u32, s: &str, e: &str, t: f64, token: u64) -> bool {
        self.inner.atom_upsert(aid, s, e, t, token)
    }

    /// Replace registry with snapshot entries: list of (id, S, E, T, token).
    fn restore_atoms(&self, entries: Vec<(u32, String, String, f64, u64)>) {
        self.inner.restore_atoms(&entries);
    }

    #[pyo3(signature = (label, parent=None))]
    fn bubble_new(&self, label: &str, parent: Option<i64>) -> u32 {
        let p = match parent {
            None => None,
            Some(x) if x < 0 => None,
            Some(x) => Some(x as u32),
        };
        self.inner.bubble_new(label, p)
    }

    fn bind(&self, bid: u32, name: &str, aid: u32) -> PyResult<()> {
        self.inner
            .bind(bid, name, aid)
            .map_err(PyValueError::new_err)
    }

    fn unbind(&self, bid: u32, name: &str) -> Option<u32> {
        self.inner.unbind(bid, name)
    }

    fn lookup(&self, bid: u32, name: &str) -> Option<u32> {
        self.inner.lookup(bid, name)
    }

    fn set_root(&self, bid: u32) {
        self.inner.set_root(bid);
    }

    fn unset_root(&self, bid: u32) {
        self.inner.unset_root(bid);
    }

    fn tick(&self) {
        self.inner.tick();
    }

    fn settle(&self, n: u32) {
        self.inner.settle(n);
    }

    /// Stats as a plain dict (keys match the Python Store.stats() surface).
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
        let s = self.inner.stats();
        let d = pyo3::types::PyDict::new(py);
        d.set_item("total", s.total)?;
        d.set_item("HOT", s.hot)?;
        d.set_item("WARM", s.warm)?;
        d.set_item("COLD", s.cold)?;
        d.set_item("TOMB", s.tomb)?;
        d.set_item("alive", s.alive)?;
        d.set_item("dead", s.dead)?;
        d.set_item("hot", s.alive)?;
        d.set_item("cold", s.dead)?;
        d.set_item("reaped", s.reaped)?;
        d.set_item("retained_tomb", s.retained_tomb)?;
        d.set_item("archived", s.retained_tomb)?;
        d.set_item("bubbles", s.bubbles)?;
        Ok(d)
    }

    /// Register env_of: `callback(token: int) -> int | None` (bubble id).
    fn register_env_of(&self, py: Python<'_>, callback: PyObject) -> PyResult<()> {
        let cb = Arc::new(callback);
        self.inner.unregister_env_of("pyo3");
        self.inner.register_env_of("pyo3", move |tok| {
            Python::with_gil(|py| {
                let result = cb.call1(py, (tok,));
                match result {
                    Ok(obj) => {
                        if obj.is_none(py) {
                            None
                        } else {
                            obj.extract::<u32>(py).ok().filter(|&b| b != 0)
                        }
                    }
                    Err(e) => {
                        let _ = e.print(py);
                        None
                    }
                }
            })
        });
        let _ = py; // keep signature stable; GIL acquired in callback
        Ok(())
    }

    fn unregister_env_of(&self) {
        self.inner.unregister_env_of("pyo3");
    }

    /// Register extra_reach: `callback() -> list[int]` of atom ids.
    fn register_extra_reach(&self, py: Python<'_>, callback: PyObject) -> PyResult<()> {
        let cb = Arc::new(callback);
        self.inner.unregister_extra_reach("pyo3");
        self.inner.register_extra_reach("pyo3", move || {
            Python::with_gil(|py| {
                let result = cb.call0(py);
                match result {
                    Ok(obj) => {
                        if let Ok(list) = obj.downcast_bound::<PyList>(py) {
                            list.iter()
                                .filter_map(|item| item.extract::<u32>().ok())
                                .collect()
                        } else if let Ok(v) = obj.extract::<Vec<u32>>(py) {
                            v
                        } else {
                            Vec::new()
                        }
                    }
                    Err(e) => {
                        let _ = e.print(py);
                        Vec::new()
                    }
                }
            })
        });
        let _ = py;
        Ok(())
    }

    fn unregister_extra_reach(&self) {
        self.inner.unregister_extra_reach("pyo3");
    }

    fn atom_count(&self) -> usize {
        self.inner.atom_count()
    }
}

#[pymodule]
fn karmazyn_substrate_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CoreStore>()?;
    m.add("__version__", ABI_VERSION.trim_end_matches('\0'))?;
    m.add("backend", "pyo3")?;
    Ok(())
}

// silence unused import warning on some pyo3 versions
#[allow(dead_code)]
fn _keep() {
    let _ = PyRuntimeError::new_err("x");
}
