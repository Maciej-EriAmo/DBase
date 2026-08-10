# Sesja Karmazyn: L0 Carrier, HSL, KPC

**Status:** implementacja w DBase (Cynober-Secure-1.2)  
**Powiązania:** HSL Paper §1.5 / §6.4 · HSS Paper · `bubble_network_assumptions` (A6, §15) · kod: `karmazyn_hsl.py`, `karmazyn_key_predict.py`, `karmazyn_qpredict.py`, `karmazyn_qkd.py`

---

## 1. Co jest czym

| Symbol | Rola |
|--------|------|
| **L0 / Carrier** | Hydraulika bitów — **dziś TCP**. Wymienna (ETH, Wi‑Fi, IPC, plik, mesh, przyszły QKD). |
| **KSH / HSS** | Uścisk / KEM — urodzenie shared seed na klasycznym kanale (lub lżej gdy seed z QKD). |
| **HSL** | Ontologia sesji: Φ², epoch, PrismMask, AAD, capability. |
| **KPC** | Ciągłość klucza przy **bootstrap i rotacji epoki** (nie na każdej ramce RPC). |
| **\|Ψ⟩ / qpredict** | Interferencja EriAmo — fidelity \(F=\lvert\langle\hat\Psi\mid\Psi\rangle\rvert^2\); brama opcjonalna, **nie KDF**. |
| **Proca** | Dedup COLD payloadów — **nie** kompresja kluczy sesji. |

**Kanoniczne:** łącze znaczenia = HSL + Surface/bąbel, nie „mamy socket TCP”.

---

## 2. Dwa reżimy L0

```text
Dziś (klasyczny):
  TCP  →  HSS KEM  →  HSL  →  RPC
          (+ opc. KARM_QKD_SEED w hybrid_link_seed)

Sieć kwantowa (paper §6.4):
  QKD → k_QKD  →  ten sam slot KDF  →  HSL  →  RPC
  (HSS na TCP może zejść na drugi plan; app bez zmian)
```

Adapter QKD: `karmazyn_qkd.py` (env / file / pipe). Hardware metropolitalny = future work; slot KDF już jest.

---

## 3. KPC — reguły

**Exact (materiał klucza wire):**

\[
K_{t+1} = \mathrm{HKDF}(K_t \,\|\, \mathrm{chain}(H) \,\|\, \mathrm{ctx})
\]

- brak historii / wadliwy łańcuch / offer ≠ evolve → **REJECT**  
- thermal soft **nie** w exact KDF (dziurawy klucz przy szumie T)

**Tor \|Ψ⟩ (ciągłość predykcyjna):**

\[
\hat\Psi = U_J^{\Delta t}(\Psi_t),\quad F=\lvert\langle\hat\Psi\mid\Psi_{\mathrm{obs}}\rangle\rvert^2,\quad \varepsilon=1-F
\]

- domyślnie soft_gate **off**; diagnostyka: `HSLLink.kpc_last_soft_residual`  
- `KARM_KPC_SOFT_GATE=1`, `KARM_KPC_SOFT_THETA=0.08` (F≥0.92) gdy włączacie bramę  
- root hosta **zna** sekrety (trusted endpoint, jak HTTPS)

Hook: `HSLLink._kpc_bootstrap()` po establish; `_kpc_rotate()` w `ensure_epoch()`.

---

## 4. Czym to nie jest

| Nie | Tak |
|-----|-----|
| Uniwersalny drop-in TLS w całym internecie | HTTPS-class sesja **w ekosystemie** Karmazyn/Cynober |
| Matrix / E2EE chat | Wire DB/runtime (executor widzi treść) |
| Proca na kluczach | Proca na COLD blobach |
| Soft residual w KDF | Soft = fidelity / reject trajektorii |

---

## 5. Testy

```bash
python -m unittest tests.test_key_predict tests.test_qpredict tests.test_hsl_session -v
```

---

## 6. Historia

| Data | Uwagi |
|------|--------|
| 2026-08-10 | KPC + qpredict + spięcie L0/QKD/docs; HSL hook bootstrap/epoch |
