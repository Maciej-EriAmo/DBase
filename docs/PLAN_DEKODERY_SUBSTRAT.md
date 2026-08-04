# Plan: dekodery mediów oparte o substrat Karmazyn

**Status:** plan po spike `IncrementalVideoDecoder` (2026-08)  
**Cel:** film/obraz jako **życie atomów** (T × reach), nie „załaduj codec i listę klatek”.

---

## 1. Problem

| Dziś (po spike) | Docelowo |
|-----------------|----------|
| Incremental: reader imageio, 1 PNG w RAM | Klatka = atom `media_frame` w Store |
| T clipu tylko w Python `FrameClip.T` | T / state na atomie substratu + reach-GC |
| Decode w wątku UI | Decode w worker / native; UI tylko blit dirty |
| Codec = imageio-ffmpeg | Codec plug-in; native YUV opcjonalnie |

Spike **udowodnił**: nie trzeba 180 PNG; wystarczy `next` gdy hot.

---

## 2. Model danych (substrat)

```text
Bubble (kanał / postać / „ekran”)
  bind "stream" → head (S=media, kind=media_stream | media_video)
  bind "blit"   → current frame atom id (opcjonalnie)

Head atom (S=media):
  metadata.mime, fps, path|cas|fold_src
  metadata.v = { decoder: "ffmpeg_incr"|"native", gop_index? }
  T — ciepło z widoczności viewport

Frame atom (S=media_frame):  # nowy kind w DOC_KINDS
  parent → head id
  index, pts
  metadata.data = PNG lub raw RGBA/YUV
  T — krótkożyjący; zimny → GC

Segment atom (S=media_seg):  # już jest
  surowe chunki A_STREAM / opcjonalnie NAL packs
```

**Prawo (jak Luneta GIF):**

- `note_visible(head)` przy blit → `touch` head  
- `T < FREEZE` → decoder.sleep / brak `next`  
- poza reach → usuń `media_frame` z RAM/store  

---

## 3. Protokół dekodera (stabilny)

```python
class StreamDecoder(Protocol):
    def open(self, source: Path | bytes | AtomRef) -> None: ...
    def next_frame(self) -> FrameRef | None:
        """FrameRef = atom_id | (png_bytes, pts, delay)."""
    def seek(self, t_sec: float) -> None: ...
    def close(self) -> None: ...
    @property
    def size(self) -> tuple[int, int]: ...
    @property
    def src_fps(self) -> float: ...
```

Implementacje:

| ID | Opis | Faza |
|----|------|------|
| `ffmpeg_incr` | imageio imiter / path — **spike DONE** | 0 |
| `ffmpeg_cli` | subprocess ffmpeg pipe rawrgba | 1 |
| `gif_preload` | obecny GIF | 0 |
| `png_static` | jedna klatka | 0 |
| `native_yuv` | Rust substrate buffer + blit | 2 |
| `research_codec` | własny bitstream | ∞ |

---

## 4. Fazy wdrożenia

### Faza D0 — Spike (DONE)

- [x] `IncrementalVideoDecoder`  
- [x] `ThermalFramePump` kind=`video_incr`  
- [x] play path bez preload 180  
- [x] testy MP4 incremental  
- [x] audyt pętli/deps  

### Faza D1 — Integracja Store

1. `DOC_KINDS += media_frame`  
2. `PlaybackSession(store, head_id)`:
   - hot → `decoder.next_frame()` → `atom_new(S=media_frame)`  
   - head.v.current_frame = id  
   - cool GC: usuń frame atoms z T < FREEZE i age > N  
3. Canvas czyta `current_frame` z head, nie z Python list  
4. Test: max RAM bound przy 30 s play  

### Faza D2 — Async decode

1. Worker thread / `queue.Queue` (max 2–3 FrameRef)  
2. UI tick tylko `dirty` blit  
3. Backpressure: nie dekoduj gdy queue pełna lub cold  
4. Opcja: decode co N-ty frame (scrub timeline)  

### Faza D3 — Native substrate

1. Crate `karmazyn_media_native` (obok substrate):  
   - alokacja bufora klatki  
   - opcjonalnie link do ffmpeg-sys **lub** tylko blit RGBA z Py  
2. FFI: `frame_upload(aid, rgba, w, h)`  
3. Zero base64 PhotoImage — canvas native / shared memory (później)  

### Faza D4 — Replicate + fold

1. `media_index` już ma head; dodać `frame_policy: lazy`  
2. Sync: head + index GOP; klatki lokalnie z decode  
3. Fold COLD: wyrzuć `media_frame`, zostaw head + cas źródła  

### Faza D5 — Research (opcjonalnie)

- Własny demux (nie codec) + zewnętrzny decode plugin  
- Paper: thermodynamics of media atoms  

---

## 5. Mapowanie na istniejący kod

| Moduł | Rola w planie |
|-------|----------------|
| `karmazyn_media_incremental.py` | D0 decoder protocol impl |
| `karmazyn_media_canvas.py` | viewport + dirty paint |
| `karmazyn_media.py` A_STREAM | chunk storage |
| `cynober_replicate` media_index | D4 sync heads |
| Luneta `ThermalGifPump` | wzorzec T/visibility |
| Native `karmazyn_substrate` | D3 buffers / GC |

---

## 6. Kryteria sukcesu

1. Play 30 s 480p: peak RAM ≪ preload 180 PNG.  
2. Cold head: **0** wywołań `next_frame` / s.  
3. Dirty blit ≤ liczba hot streamów.  
4. Bez hard dep ffmpeg w core wheel (extras OK).  
5. Testy unit bez GUI; 1 integration z `test.mp4`.  

---

## 7. Kolejność PR

1. **D1a** — `media_frame` kind + PlaybackSession (Python only)  
2. **D1b** — canvas na `current_frame` atom  
3. **D2** — async queue  
4. **D3** — native buffer (gdy potrzeba perf)  

---

## 8. Czego nie robić

- Nie pisać H.264 od zera.  
- Nie trzymać pełnego filmu w `metadata["data"]` head.  
- Nie grzać atomów w `pump()` (tylko `note_visible`).  
- Nie wciągać imageio do hard dependencies cynober-db.

---

*Plan spójny z PLAN_MULTIMEDIA_WDROZENIE.md Faza 4–6 oraz modelem Luneta GIF.*
