# Audyt: media canvas / dekodery (2026-08)

## 1. Co działa

| Ścieżka | Status |
|---------|--------|
| PNG static | OK, 1 klatka |
| GIF preload + thermal pump | OK |
| MP4 **preload** 180 PNG | legacy (wolny start, duży RAM) |
| MP4 **incremental** (`video_incr`) | **spike OK** — `next_png` gdy hot |
| dry-run CLI | OK |
| test.mp4 480×270 | imageio-ffmpeg |

## 2. Zbędne pętle / koszty

| Miejsce | Problem | Status po spike |
|---------|---------|-----------------|
| `decode_video_frames` pełna pętla `imiter` → 180 PNG | preload z góry | **ominięte** gdy `load_from_path` + incremental |
| `load_from_path` `read_bytes()` całego MP4 | I/O + RAM | **video:** tylko path, bez read całego pliku |
| GIF `while seek` wszystkie klatki | OK dla krótkich GIF | zostaje preload (małe) |
| `pump()` po wszystkich clipach | O(n_clips) | OK; zimne = continue bez decode |
| Tk `tick` 33 ms + full PhotoImage recreate | alokacja PhotoImage co dirty | **do optymalizacji** (reuse buffer) |
| `cool()` po wszystkich clipach | O(n) float | tanio |

## 3. Zależności

| Pakiet | Wymagany do | Uwagi |
|--------|-------------|--------|
| **Pillow** | PNG/GIF/resize | core canvas |
| **imageio** + **imageio-ffmpeg** | MP4 incremental | opcjonalne `[media]`; **nie** w hard deps |
| pygame/SDL | — | **nie** używamy |
| numpy | imageio frames | transitive z imageio |

**Ryzyko:** imageio-ffmpeg ściąga binarkę ffmpeg — OK dev, cięższe w Nuitka/lore pack.

**Zbędne:** pełny preload path można oznaczyć deprecated w docs (zostaje fallback).

## 4. Bugi / ostre krawędzie

1. **EOF loop** — `next_png` reopen na StopIteration (OK demo; seek UI brak).  
2. **Brak pause** — hot zawsze leci gdy visible.  
3. **Audio** — brak (tylko obraz klatek).  
4. **Wątki** — decode w tick UI może jankować (duży frame); docelowo worker + kolejka.  
5. **Store atoms** — incremental klatki **nie** są atomami `media_frame` w Store (tylko RAM clip) — plan substratu.  
6. **Double place** w starszym play_file — usunięte.  
7. **PhotoImage base64** co klatkę — GC pressure na długim play.

## 5. Rekomendacje krótkie

1. Domyślnie **zawsze** incremental dla video path (zrobione).  
2. Ring buffer 2–3 PNG (poprzednia + current) — anti-tearing.  
3. Opcja `preload_max_frames=0` w CLI.  
4. Nie dodawać hard dep imageio do cynober-db core — tylko extras.

---

*Audyt po spike IncrementalVideoDecoder.*
