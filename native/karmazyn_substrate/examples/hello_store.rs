//! Minimal pure-Rust path for the substrate core.
//!
//!   cd native/karmazyn_substrate
//!   cargo run --example hello_store
//!   cargo run --example hello_store --release

use karmazyn_substrate::Store;

fn main() {
    println!("karmazyn_substrate hello_store");
    let s = Store::new(true);

    let orphan = s.atom_new("var", "orphan", f64::NAN);
    s.settle(80);
    assert!(
        !s.has_atom(orphan),
        "orphan should be vacuumed after settle(80)"
    );
    println!("  [ OK ] orphan vacuum");

    let root = s.bubble_new("root", None);
    s.set_root(root);
    let keep = s.atom_new("var", "keep", 50.0);
    s.atom_set_value_token(keep, 7);
    s.bind(root, "keep", keep).expect("bind");
    s.settle(80);
    assert!(s.has_atom(keep), "rooted atom must survive");
    assert_eq!(s.atom_is_dead(keep), Some(true), "should be TOMB");
    println!("  [ OK ] root retains TOMB");

    let snap = vec![(keep, "var".into(), "keep".into(), 12.0, 7u64)];
    let extra = s.atom_new("var", "extra", 50.0);
    s.restore_atoms(&snap);
    assert!(s.has_atom(keep));
    assert!(!s.has_atom(extra));
    assert!((s.get_atom_t(keep).unwrap() - 12.0).abs() < 1e-9);
    println!("  [ OK ] restore_atoms");

    let st = s.stats();
    println!(
        "  stats: total={} reaped={} retained_tomb={} bubbles={}",
        st.total, st.reaped, st.retained_tomb, st.bubbles
    );
    println!("OK hello_store");
}
