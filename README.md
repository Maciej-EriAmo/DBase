# Cynober DB / KarmazynOS

Relacyjno-grafowa baza danych na termodynamicznym rdzeniu **KarmazynOS**, z transportem **Cynober-Secure-1.2** i warstwą **HSL** (Holographic Session Links). Telefon w Termux może gadać z serwerem na PC; zapytania **KarminQL** lecą po tunelu chronionym **Ring-LWE (HSS)** i **HSL** — splątanie jako certyfikat sesji, nie tylko szyfrowanie payloadu.

> *Ruch TCP widać, ale payload to szum. HSL nie zastępuje TLS — wiąże kontekst sesji (Φ², epoka, PrismMask). Slot `KARM_QKD_SEED` to dziś symulacja hybrydy z siecią kwantową; jutro ten sam punkt w KDF, inne źródło. **N=15** w HSS to świadomy prototyp (lekkość, Termux) — KEM skaluje się bez ruszania RPC.*

## Trzy filary

| Filament | Rola |
|----------|------|
| **KarmazynOS** | Termodynamiczny silnik pamięci — atomy, bąble, reach-GC |
| **Cynober / KarminQL** | Baza i język zapytań (relacyjno-grafowy model) |
| **Protokół Karmazyn** | HSS (Ring-LWE KEM) → HSL (sesja, AAD, opcjonalnie QKD) → Cynober-RPC |

## Co widać w demo, a co nie

**Na filmiku / screencascie:**
- `cynober_konfigurator.py` — profil klienta i serwera, firewall, rate limit
- Połączenie Termux → PC, komunikat `Tunel zabezpieczony (HSS + HSL + QKD)`
- KarminQL na żywo: `INSERT`, `SELECT`, zapis `.kafd`
- Odrzucenie floodu (rate limit połączeń i zapytań)

**Poza kadrem (warto przeczytać opis / manual):**
- Rezonans HSL — ramka bez właściwego stanu sesji kończy się błędem GCM (kolaps do szumu)
- Weryfikacja `qkd_fp` przy rozjazdzie seeda QKD
- AAD na ramkach RPC, anty-replay (`session_id`, `ts`)
- Φ² — trwała tożsamość węzła (`~/.karmazyn_phi2`), nie sekret w handshake

## Szybki start

Wymagania: **Python 3.10+**. Rdzeń i KarminQL działają na samym stdlib.

```bash
# opcjonalnie — pełny stos kryptograficzny
pip install -r requirements.txt

# serwer
python cynober_server.py

# klient (drugi terminal)
python Cynober_db.py

# konfigurator profili (Termux / PC)
python cynober_konfigurator.py
```

Opcjonalne zmienne (obie strony muszą się zgadzać):

```powershell
$env:KARM_PSK = "haslo-sieci"
$env:KARM_QKD_SEED = "seed-hybrydowy-qkd"
```

## Architektura (skrót)

```
Cynober_db.py  ◄── TCP :8080, Cynober-Secure-1.2 ──►  cynober_server.py
       │                                                    │
       └── HSS/ECDH → PSK? → HSL link → RPC+AAD ────────────┘
                              │
                              ▼
                    KarminQL → karmazyn_kernel
```

Szczegóły: [`cynober_manual.md`](cynober_manual.md) · specyfikacja HSL: [`HSL_Paper_v1_1_0_EN.md`](HSL_Paper_v1_1_0_EN.md)

## Testy

```bash
python -m pytest tests/ -q
```

## Status i ograniczenia (prototyp)

- Współdzielony `facade` na serwerze — jedna baza dla wszystkich klientów
- Brak uwierzytelnienia per użytkownik (PSK/QKD = hasło **sieci**)
- Metadane TCP widoczne; treść zapytań chroniona
- HSS domyślnie **N=15, Q=256** — podnoszenie parametrów w `karmazyn_hss.py` bez zmiany HSL/RPC
- Częściowa ochrona DoS (rate limit; flood z wielu IP nadal możliwy)

## Szukam współpracy

Stack jest warstwowy — można wnieść kawałek bez znajomości całości. Przydatne obszary:

- **NTT / N=256** w `karmazyn_hss.py` (Kyber-class KEM)
- **Adapter QKD** zamiast `KARM_QKD_SEED` (ten sam KDF, inne źródło)
- **Izolacja per sesja** na serwerze
- **Hardening** — TLS overlay, capability tokens, rotacja epoki w locie

Jeśli chcesz dołączyć — issue, PR albo kontakt przez profil GitHub.

## Licencja

Kod w tym repozytorium (Cynober DB, KarminQL, warstwa HSS/HSL, pliki `karmazyn_*` i `cynober_*`) jest na licencji **[MIT](LICENSE)**.

Dokumentacja specyfikacji HSL: [`HSL_Paper_v1_1_0_EN.md`](HSL_Paper_v1_1_0_EN.md) — **CC BY 4.0** (osobno od licencji kodu).

Rdzeń KarmazynOS pochodzi z ekosystemu [KarmazynOs](https://github.com/Maciej-EriAmo/KarmazynOs); docelowo oba repozytoria mają wspólną licencję MIT.