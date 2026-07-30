//! C ABI — stable seam for Python (ctypes/cffi) and future hosts.
//!
//! Handles are opaque u64 store pointers. Bubble/atom ids are u32.
//! Language payloads are opaque u64 tokens (e.g. Python id(obj)).

use crate::store::Store;
use crate::ABI_VERSION;
use std::ffi::{c_char, CStr, CString};
use std::os::raw::c_int;
use std::ptr;
use std::sync::Mutex;

/// Global table: store_handle → Store (handle = non-zero index).
static STORES: Mutex<Vec<Option<Store>>> = Mutex::new(Vec::new());

fn with_store<R>(handle: u64, f: impl FnOnce(&Store) -> R) -> Option<R> {
    let g = STORES.lock().ok()?;
    let idx = handle.checked_sub(1)? as usize;
    g.get(idx)?.as_ref().map(f)
}

fn with_store_mut<R>(handle: u64, f: impl FnOnce(&Store) -> R) -> Option<R> {
    with_store(handle, f)
}

// ── version / lifecycle ─────────────────────────────────────────────────────

#[no_mangle]
pub extern "C" fn ksub_version() -> *const c_char {
    ABI_VERSION.as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn ksub_store_new(thermal: c_int) -> u64 {
    let Ok(mut g) = STORES.lock() else {
        return 0;
    };
    g.push(Some(Store::new(thermal != 0)));
    g.len() as u64 // 1-based handle
}

#[no_mangle]
pub extern "C" fn ksub_store_free(handle: u64) {
    if handle == 0 {
        return;
    }
    if let Ok(mut g) = STORES.lock() {
        let idx = (handle - 1) as usize;
        if idx < g.len() {
            g[idx] = None;
        }
    }
}

// ── atoms ───────────────────────────────────────────────────────────────────

#[no_mangle]
pub extern "C" fn ksub_atom_new(
    handle: u64,
    s: *const c_char,
    e: *const c_char,
    t: f64,
) -> u32 {
    let s = unsafe { cstr(s) };
    let e = unsafe { cstr(e) };
    with_store(handle, |st| st.atom_new(&s, &e, t)).unwrap_or(u32::MAX)
}

#[no_mangle]
pub extern "C" fn ksub_atom_set_value(handle: u64, aid: u32, token: u64) -> c_int {
    with_store(handle, |st| st.atom_set_value_token(aid, token) as c_int).unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn ksub_atom_value(handle: u64, aid: u32) -> u64 {
    with_store(handle, |st| st.atom_value_token(aid).unwrap_or(0)).unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn ksub_has_atom(handle: u64, aid: u32) -> c_int {
    with_store(handle, |st| st.has_atom(aid) as c_int).unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn ksub_delete_atom(handle: u64, aid: u32) -> c_int {
    with_store(handle, |st| st.delete_atom(aid) as c_int).unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn ksub_heat(handle: u64, aid: u32) -> c_int {
    with_store(handle, |st| st.heat(aid) as c_int).unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn ksub_atom_t(handle: u64, aid: u32) -> f64 {
    with_store(handle, |st| st.get_atom_t(aid).unwrap_or(-1.0)).unwrap_or(-1.0)
}

#[no_mangle]
pub extern "C" fn ksub_atom_set_t(handle: u64, aid: u32, t: f64) -> c_int {
    with_store(handle, |st| st.atom_set_t(aid, t) as c_int).unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn ksub_atom_is_dead(handle: u64, aid: u32) -> c_int {
    with_store(handle, |st| {
        st.atom_is_dead(aid).map(|d| d as c_int).unwrap_or(-1)
    })
    .unwrap_or(-1)
}

/// Upsert atom with fixed id. Returns 1 on success.
#[no_mangle]
pub extern "C" fn ksub_atom_upsert(
    handle: u64,
    aid: u32,
    s: *const c_char,
    e: *const c_char,
    t: f64,
    token: u64,
) -> c_int {
    let s = unsafe { cstr(s) };
    let e = unsafe { cstr(e) };
    with_store(handle, |st| st.atom_upsert(aid, &s, &e, t, token) as c_int).unwrap_or(0)
}

/// Fill `out` with up to `max_out` atom ids. Returns count written (or -1 on error).
#[no_mangle]
pub extern "C" fn ksub_atom_ids(handle: u64, out: *mut u32, max_out: u32) -> i32 {
    if out.is_null() && max_out > 0 {
        return -1;
    }
    with_store(handle, |st| {
        let ids = st.atom_ids();
        let n = ids.len().min(max_out as usize);
        if n > 0 && !out.is_null() {
            unsafe {
                std::ptr::copy_nonoverlapping(ids.as_ptr(), out, n);
            }
        }
        n as i32
    })
    .unwrap_or(-1)
}

// ── bubbles ─────────────────────────────────────────────────────────────────

#[no_mangle]
pub extern "C" fn ksub_bubble_new(handle: u64, label: *const c_char, parent: i64) -> u32 {
    let label = unsafe { cstr(label) };
    let parent = if parent < 0 {
        None
    } else {
        Some(parent as u32)
    };
    with_store(handle, |st| st.bubble_new(&label, parent)).unwrap_or(u32::MAX)
}

#[no_mangle]
pub extern "C" fn ksub_bind(handle: u64, bid: u32, name: *const c_char, aid: u32) -> c_int {
    let name = unsafe { cstr(name) };
    with_store(handle, |st| match st.bind(bid, &name, aid) {
        Ok(()) => 1,
        Err(_) => 0,
    })
    .unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn ksub_lookup(handle: u64, bid: u32, name: *const c_char) -> i64 {
    let name = unsafe { cstr(name) };
    with_store(handle, |st| {
        st.lookup(bid, &name).map(|a| a as i64).unwrap_or(-1)
    })
    .unwrap_or(-1)
}

#[no_mangle]
pub extern "C" fn ksub_unbind(handle: u64, bid: u32, name: *const c_char) -> i64 {
    let name = unsafe { cstr(name) };
    with_store(handle, |st| {
        st.unbind(bid, &name)
            .map(|a| a as i64)
            .unwrap_or(-1)
    })
    .unwrap_or(-1)
}

#[no_mangle]
pub extern "C" fn ksub_set_root(handle: u64, bid: u32) {
    let _ = with_store(handle, |st| st.set_root(bid));
}

#[no_mangle]
pub extern "C" fn ksub_unset_root(handle: u64, bid: u32) {
    let _ = with_store(handle, |st| st.unset_root(bid));
}

// ── tick / stats ────────────────────────────────────────────────────────────

#[no_mangle]
pub extern "C" fn ksub_tick(handle: u64) {
    let _ = with_store(handle, |st| st.tick());
}

#[no_mangle]
pub extern "C" fn ksub_settle(handle: u64, n: u32) {
    let _ = with_store(handle, |st| st.settle(n));
}

#[repr(C)]
pub struct KSubStats {
    pub total: u64,
    pub hot: u64,
    pub warm: u64,
    pub cold: u64,
    pub tomb: u64,
    pub alive: u64,
    pub dead: u64,
    pub reaped: u64,
    pub retained_tomb: u64,
    pub bubbles: u64,
}

#[no_mangle]
pub extern "C" fn ksub_stats(handle: u64, out: *mut KSubStats) -> c_int {
    if out.is_null() {
        return 0;
    }
    match with_store(handle, |st| st.stats()) {
        Some(s) => {
            unsafe {
                *out = KSubStats {
                    total: s.total as u64,
                    hot: s.hot as u64,
                    warm: s.warm as u64,
                    cold: s.cold as u64,
                    tomb: s.tomb as u64,
                    alive: s.alive as u64,
                    dead: s.dead as u64,
                    reaped: s.reaped,
                    retained_tomb: s.retained_tomb as u64,
                    bubbles: s.bubbles as u64,
                };
            }
            1
        }
        None => 0,
    }
}

// ── hooks (function pointers; host keeps data in token space) ───────────────

/// env_of callback: (value_token, userdata) -> bubble_id (0 = none)
pub type EnvOfC = Option<extern "C" fn(u64, *mut std::ffi::c_void) -> u32>;
/// extra_reach: write up to max_out ids into out; return count
pub type ExtraReachC =
    Option<extern "C" fn(out: *mut u32, max_out: u32, userdata: *mut std::ffi::c_void) -> u32>;

struct HookCtx {
    env_of: EnvOfC,
    env_ud: usize,
    extra: ExtraReachC,
    extra_ud: usize,
}

// Per-store hook context (parallel to STORES indices).
static HOOKS: Mutex<Vec<Option<HookCtx>>> = Mutex::new(Vec::new());

#[no_mangle]
pub extern "C" fn ksub_register_env_of(
    handle: u64,
    cb: EnvOfC,
    userdata: *mut std::ffi::c_void,
) -> c_int {
    if handle == 0 {
        return 0;
    }
    let ud = userdata as usize;
    let Ok(mut hooks) = HOOKS.lock() else {
        return 0;
    };
    let idx = (handle - 1) as usize;
    while hooks.len() <= idx {
        hooks.push(None);
    }
    let entry = hooks[idx].get_or_insert(HookCtx {
        env_of: None,
        env_ud: 0,
        extra: None,
        extra_ud: 0,
    });
    entry.env_of = cb;
    entry.env_ud = ud;

    let env_of = cb;
    let env_ud = ud;
    with_store_mut(handle, |st| {
        st.unregister_env_of("c_abi");
        if let Some(f) = env_of {
            st.register_env_of("c_abi", move |tok| {
                let b = f(tok, env_ud as *mut std::ffi::c_void);
                if b == 0 {
                    None
                } else {
                    Some(b)
                }
            });
        }
    });
    1
}

#[no_mangle]
pub extern "C" fn ksub_register_extra_reach(
    handle: u64,
    cb: ExtraReachC,
    userdata: *mut std::ffi::c_void,
) -> c_int {
    if handle == 0 {
        return 0;
    }
    let ud = userdata as usize;
    let Ok(mut hooks) = HOOKS.lock() else {
        return 0;
    };
    let idx = (handle - 1) as usize;
    while hooks.len() <= idx {
        hooks.push(None);
    }
    let entry = hooks[idx].get_or_insert(HookCtx {
        env_of: None,
        env_ud: 0,
        extra: None,
        extra_ud: 0,
    });
    entry.extra = cb;
    entry.extra_ud = ud;

    let extra = cb;
    let extra_ud = ud;
    with_store_mut(handle, |st| {
        st.unregister_extra_reach("c_abi");
        if let Some(f) = extra {
            st.register_extra_reach("c_abi", move || {
                let mut buf = vec![0u32; 256];
                let n = f(
                    buf.as_mut_ptr(),
                    buf.len() as u32,
                    extra_ud as *mut std::ffi::c_void,
                ) as usize;
                buf.truncate(n.min(256));
                buf
            });
        }
    });
    1
}

// ── helpers ─────────────────────────────────────────────────────────────────

unsafe fn cstr(p: *const c_char) -> String {
    if p.is_null() {
        return String::new();
    }
    CStr::from_ptr(p).to_string_lossy().into_owned()
}

/// Leak-free helper for hosts that need owned C strings (tests).
#[no_mangle]
pub extern "C" fn ksub_string_free(p: *mut c_char) {
    if !p.is_null() {
        unsafe {
            let _ = CString::from_raw(p);
        }
    }
}

#[allow(dead_code)]
fn _keep_ptr() {
    let _ = ptr::null::<u8>();
}
