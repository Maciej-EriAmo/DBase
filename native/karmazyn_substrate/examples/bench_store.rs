//! Pure-Rust micro-benchmarks for the substrate core (no Python).
//!
//!   cargo run --example bench_store --release

use karmazyn_substrate::Store;
use std::time::Instant;

fn median(mut v: Vec<f64>) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = v.len();
    if n == 0 {
        return f64::NAN;
    }
    if n % 2 == 1 {
        v[n / 2]
    } else {
        (v[n / 2 - 1] + v[n / 2]) / 2.0
    }
}

fn bench(name: &str, rounds: usize, ops: u64, mut f: impl FnMut()) {
    // warmup
    f();
    let mut samples = Vec::with_capacity(rounds);
    for _ in 0..rounds {
        let t0 = Instant::now();
        f();
        samples.push(t0.elapsed().as_secs_f64());
    }
    let med = median(samples);
    let ops_s = if med > 0.0 {
        ops as f64 / med
    } else {
        f64::INFINITY
    };
    println!(
        "  {name:<28} med={:>8.2} ms   {:>12.0} ops/s   (ops={ops})",
        med * 1000.0,
        ops_s
    );
}

fn main() {
    println!("karmazyn_substrate pure-Rust bench (release)");
    let rounds = 5;

    bench("atom_new x50k", rounds, 50_000, || {
        let s = Store::new(false);
        for i in 0..50_000u32 {
            s.atom_new("var", "x", 50.0);
            let _ = i;
        }
    });

    bench("bind_lookup x10k", rounds, 20_000, || {
        let s = Store::new(true);
        let root = s.bubble_new("r", None);
        s.set_root(root);
        let mut ids = Vec::with_capacity(10_000);
        for i in 0..10_000u32 {
            let a = s.atom_new("var", "b", 50.0);
            ids.push(a);
            s.bind(root, &format!("k{i}"), a).unwrap();
        }
        for i in 0..10_000u32 {
            let _ = s.lookup(root, &format!("k{i}"));
        }
    });

    bench("tick_hot 5k x20", rounds, 20, || {
        let s = Store::new(true);
        let root = s.bubble_new("r", None);
        s.set_root(root);
        for i in 0..5_000u32 {
            let a = s.atom_new("var", "h", 50.0);
            s.bind(root, &format!("h{i}"), a).unwrap();
        }
        for _ in 0..20 {
            s.tick();
        }
    });

    bench("gc_orphans 10k settle80", rounds, 10_000, || {
        let s = Store::new(true);
        for _ in 0..10_000u32 {
            s.atom_new("var", "o", 50.0);
        }
        s.settle(80);
    });

    bench("restore_atoms 5k", rounds, 5_000, || {
        let s = Store::new(false);
        let mut entries = Vec::with_capacity(5_000);
        for i in 0..5_000u32 {
            let a = s.atom_new("var", "s", 50.0);
            entries.push((a, "var".into(), "s".into(), 40.0, i as u64));
        }
        // add noise
        for _ in 0..1_000 {
            s.atom_new("var", "n", 50.0);
        }
        s.restore_atoms(&entries);
    });

    println!("OK bench_store");
}
