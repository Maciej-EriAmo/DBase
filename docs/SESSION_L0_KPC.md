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

## 2. Dwa reżimy L0 — opis połączenia

### 2.1 Dziś (to, co łączy `cynober-server` / lore `--rpc`)

```text
L0 = TCP :8080
  → powitanie (capabilities, session_id)
  → HSS KEM (główne shared_key na klasycznym kanale)
  → opc. PSK / KARM_QKD_SEED (ten sam slot KDF co przyszły QKD)
  → HSL (Φ², epoch, PrismMask, KPC bootstrap)
  → RPC + opc. KAFS
```

`ZDROWIE` / `session_info()`: `l0_carrier: "tcp"`.

### 2.2 Docelowo (L0 = sieć kwantowa / QKD)

Gdy Carrier pod spodem przestanie być „tylko TCP”, **zmienia się urodzenie seeda**, nie język zapytań:

```text
L0 ≈ QKD link  →  k_QKD (IT-secure)
  → hybrid_link_seed / KDF (ten sam slot co dziś KARM_QKD_SEED)
  → HSL + KPC  →  RPC   (te same query, put_media, WYBIERZ ŚWIAT)
```

| Zmiana przy L0→QKD | Bez zmian dla app / lore |
|--------------------|---------------------------|
| Seed z łącza kwantowego, nie (tylko) z HSS-KEM po TCP | `connect` / `query` / KarminQL |
| HSS KEM = fallback klasyczny lub hybrydowy | HSL link, epoch, capability |
| Diagnostyka `l0_carrier` ≠ `"tcp"` | Media KAFS, światy, auth |
| `qkd_fp` obowiązkowy przy produkcji | KPC na establish/epoch |

Adapter już: `karmazyn_qkd.py` (env / file / pipe). Hardware metropolitalny = future work; **slot KDF jest ten sam**, więc app i lore-editor nie dostają drugiego protokołu.

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
| 2026-08-10 | Pakiet **8.2.2** na PyPI; docs/manual/README wyrównane; `python -m cynober_server` |
