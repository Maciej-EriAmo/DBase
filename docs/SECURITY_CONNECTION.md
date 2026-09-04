# Bezpieczeństwo połączenia Cynober (8.2.4+)

**Pakiet:** cynober-db ≥ **8.2.5** (8.2.4 na PyPI było zepsute packagingiem — brak `cynober_paths`).  
**Wire:** Cynober-Secure-1.2 · HSL-1.1 · testy: `python -m unittest tests.test_security_audit`

---

## Postawa produkcyjna (zalecane)

```powershell
$env:CYNOBER_MIN_CRYPTO = "hss"
$env:CYNOBER_ALLOW_LEGACY = "0"
$env:CYNOBER_ALLOW_SIMPLE = "0"
$env:KARM_PSK = "…sekret sieci…"
$env:KARM_HSS_PROFILE = "standard"   # lub production; unikaj proto na public bind
$env:CYNOBER_SECURE_BOOT = "1"       # odmawia startu przy złej posture na 0.0.0.0
# + worlds/auth.json z enabled=true, użytkownicy, ACL
```

Przy `CYNOBER_SECURE_BOOT=1` i publicznym bindzie serwer **nie wstanie**, jeśli: auth OFF, brak PSK, HSS=proto, legacy/simple dozwolone.

---

## Zmienne bramkujące crypto

| Env | Domyślnie | Skutek |
|-----|-----------|--------|
| `CYNOBER_MIN_CRYPTO` | ∅ | Minimalny tryb: `hss` / `ecdh` / `simple` |
| `CYNOBER_ALLOW_LEGACY` | ON (dev) | OFF gdy MIN=`hss`\|`ecdh`; `0` wymusza odrzut Cynober-Secure-1.0 |
| `CYNOBER_ALLOW_SIMPLE` | ON (dev) | `0` / MIN silniejszy → brak XOR/`simple` w caps |
| `CYNOBER_FORCE_CRYPTO` | ∅ | Wymusza jeden tryb w caps lokalnych |
| `KARM_PSK` | ∅ | Mieszany w klucz sesji po KEM (obie strony muszą mieć ten sam) |
| `KARM_QKD_SEED` | ∅ | Hybrid QKD fingerprint w HSL link |

**Legacy 1.0** omija HSL i anty-replay — nie używać na LAN poza świadomym labem.

---

## HSL Faza 2 — nie cleartext

Po ustaleniu klucza sesji ramki `hsl_link` / `hsl_cap` idą jako:

```text
HSL1 ‖ AEAD(payload)
AAD: cynober-hsl-link-v1  |  cynober-hsl-cap-v1
```

`node_id`, epoch, commit Φ², `qkd_fp` **nie** lecą jako goły JSON.  
Test: `tests.test_hsl_encrypted_link`.

---

## Auth (`auth.json`)

- Hash tokenu: **scrypt** (stdlib). Stary SHA256 przy loginie → upgrade zapisu.
- Lockout: **5** nieudanych w **5 min** → blokada **60 s** (`LOGIN_LOCKOUT`).
- Audit: `LOGIN` / `LOGIN_FAIL` / `LOGIN_LOCKOUT` — query zmaskowane (`TOKEN "…"`), bez surowego sekretu.

### Gossip ACL (gdy auth ON)

| Komenda | Sesja efemeryczna (bez świata) | Świat dołączony |
|---------|--------------------------------|-----------------|
| `GOSSIP EKSPORT …` | global **reader** | reader na świecie |
| `GOSSIP IMPORT …` | global **writer** | writer na świecie |
| `GOSSIP SYNC/FETCH …` | global **admin** (peers.json) | writer na świecie |

Bez logowania → błąd. Sesja efemeryczna **nie** jest już „open proxy” na tokeny `peers.json`.

`LISTA WĘZŁÓW` / `LISTA WĘZŁÓW REZONANS` / `METRYKI SERWERA` → global reader.  
`ZDROWIE` → liveness **bez** logowania.

---

## Suite audytu

```powershell
cd C:\Users\drwis\DBase
python -m unittest tests.test_security_audit -v
```

Sekcje A–G: crypto/legacy, gossip deputy, PSK/QKD, METRYKI/LOGIN audit, replay prune, REZONANS ACL, `SECURE_BOOT`.

---

## Powiązania

- Manual § sieć: [`../cynober_manual.md`](../cynober_manual.md)
- L0/KPC: [`SESSION_L0_KPC.md`](SESSION_L0_KPC.md)
- Mazur / peer \(R\): [`MAZUR_CRYSTAL.md`](MAZUR_CRYSTAL.md)
- Changelog: [`../CHANGELOG.md`](../CHANGELOG.md)
