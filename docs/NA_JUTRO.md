# Na jutro (2026-07-23)

## Kontynuacja: multimedia w Cynober

**Pełny plan:** [`PLAN_MULTIMEDIA_WDROZENIE.md`](PLAN_MULTIMEDIA_WDROZENIE.md)

### Start od Fazy 0 (pierwszy PR)

`feat(media): local attach API and KAFD roundtrip`

1. Nowy moduł `karmazyn_media.py`  
   - `attach_bytes` / `attach_file` / `get_bytes` / `export_to_path`  
2. Export w `karmazyn_kernel`  
3. Testy `tests/test_media_local.py` (roundtrip SHA256 plik→atom→`.kafd`→plik)  
4. Gossip SOUL: domyślnie bez dużych `data_b64` (próg ~64 KiB)  
5. Krótka wzmianka w `cynober_manual.md`

### Kontekst (żeby nie wracać do całej rozmowy)

- Lore-pisarz: **bez zmian** na teraz; AI = osobny klient tej samej DB  
- Media = atomy w bąblach, nie HTTP  
- Przepływ: plik → atom + bind → KAFD → z powrotem  
- Sieć docelowo: RPC = sterowanie, **KAFS** = bajty (Faza 3+)  
- SOUL/JSON **nie** na duże pliki  

### Stan repo (ostatnio)

- Kernel S15, dual-emit tick, public API Store — na `master`  
- Gossip SOUL v8.1 slice — lokalnie / do commit jeśli jeszcze nie  
- PyPI: latest **8.0.1**, repo **8.0.2** (do publikacji osobno)  
- lore ↔ serwer: RPC + PUSH/PULL/SYNC świata **OK**  

### Nie robić jutro (później)

- Faza 3 KAFS w tunelu  
- UI lore attach (Faza 1 — po API z Fazy 0)  
- REST/HTTP media server  

---

*Zapisano 2026-07-22 wieczorem.*
