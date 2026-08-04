"""
karmazyn_kernel.py — PUBLICZNA POWIERZCHNIA JADRA KarmazynOS
=============================================================
Maciej Mazur, Warsaw 2026

To jest JEDYNE wejscie, przez ktore oprogramowanie (JS, DOM, Luneta, KAFD…)
ma dotykac jadra. Cel: zatrzymac balagan, w ktorym aplikacje siegaly do
wnetrza rdzenia (np. `Store.reg`, konkretny modul) i mieszaly sie z systemem.

Po wydzieleniu jadra jako osobnej jednostki:
  - jadro to zbior: karmazyn_atom + karmazyn_hrr + karmazyn_substrate
    + karmazyn_atomstore (domkniety importowo — nie importuje oprogramowania),
  - oprogramowanie importuje TYLKO z `karmazyn_kernel`,
  - `kernel_boundary.py` pilnuje tej reguly maszynowo (w CI).

ZERO-ZALEZNOSC (najnizsza warstwa): fasada MUSI dac sie zaimportowac na samym
CPythonie, bez numpy. Twarz HRR (wektory) jest OPCJONALNA i degraduje lagodnie
— dokladnie jak w substracie. Bez numpy: HAS_HRR=False, funkcje wektora = None,
ale prawo reach-GC (atomy, bable, korzenie, tick) dziala w pelni. To jest
dowod, ze system wstaje samodzielnie, bez ani jednej biblioteki z pip.

Co tu JEST, a czego NIE MA (uczciwie, bo to stan rzeczy, nie marketing):
  - Sa DWIE powierzchnie silnika i to jest swiadomie nierozstrzygniete (szew D2):
      * natywna `Store`     — atom_new / bubble_new / bind / lookup / set_root
                              (uzywa jej front-end leksykalny, np. Scheme),
      * kontrakt `AtomStore`— create_atom / create_bubble / import_to_bubble
                              (spelnia go ADAPTER aplikacji, np. LunetaRuntime).
    Fasada udostepnia obie z jednego miejsca, ale ich NIE scala. Wybor
    kanonicznej powierzchni to decyzja D2, nie zadanie tej fasady.
  - Klasyfikacja stanu `state_for_T` jest tu z karmazyn_atom jako JEDYNE
    zrodlo prawa (szew D3). Kopie w dom/js_phi/kafd/live_dom maja z czasem
    importowac stad — to porzadkuje sie za zgoda autora, bez usuwania na sile.

Prawo jadra (z substratu): temperatura mowi KIEDY, osiagalnosc mowi CZY.
  zimny+nieosiagalny -> GC ; zimny+osiagalny -> retencja TOMB (retained tomb);
  cieply -> zostaje.
Niesmiertelnosc = przypadek szczegolny (atom trzymany przez korzen nie ginie).

v1.1.0 (runda S1→S13, 2026-07-22) — zmiany substratu widoczne przez fasade:
  - env_of(v) moze zwrocic Bubble ALBO iterowalne po Bubble (deep reach
    po stronie frontu — kuracja S1); rdzen dalej nie zna jezyka.
  - create_bubble(label, atom_ids=None, root=False) — root=True chroni
    zawartosc przed GC (S3); worek bez roota NIE chroni.
  - Store.reg wewnetrzny (S2): property wsteczne z jednorazowym warning.
  - Retencja: kanon `retained_tomb` w stats; klucz `archived` = alias (S4/S14).
  - tick: jeden walk; eventy: batch | per_atom | both (dual-emit domyslnie,
    okres przejsciowy S12/S15). Zagniezdzony tick = no-op; reaped tylko po
    skutecznym delete.
"""

# ── Podloga: model atomu + rejestr + klasyfikacja stanu (KANON prawa T) ───────
# Czysty stdlib — zero zaleznosci zewnetrznych.
from karmazyn_atom import (
    Atom,
    AtomRegistry,
    AtomsWrapper,
    state_for_T,            # JEDYNE kanoniczne zrodlo klasyfikacji stanu (D3)
    T_INIT, T_MAX, T_HOT, T_WARM, T_TOMB,
    DECAY_DEFAULT, HEAT_READ, HEAT_WRITE,
)

# ── Podloga: matematyka wektora (HRR) — OPCJONALNA, degradacja lagodna ────────
# Twarz wektora wymaga numpy. Najnizsza warstwa systemu nie moze od niej zalezec,
# wiec import jest zabezpieczony — tak jak w karmazyn_substrate. Bez numpy te
# nazwy sa None, a HAS_HRR=False; reach-GC dziala bez nich.
try:
    from karmazyn_hrr import (
        bind, unbind, bundle, similarity, normalize,
        random_unit_vector,
        name_to_vector,     # sha256 -> seed -> randn(D) -> norm ; D=2048
        HRROperations,
    )
    HAS_HRR = True
except Exception:
    bind = unbind = bundle = similarity = normalize = None
    random_unit_vector = name_to_vector = HRROperations = None
    HAS_HRR = False

# ── Rdzen wlasciwy: silnik z reach-GC (atomy + bable + korzenie + rezonans) ───
# DB_karmin: DOMYŚLNIE NativeStore (Rust) gdy most dostępny; Python = referencja.
# KARMAZYN_SUBSTRATE=python|native  (patrz karmazyn_backend).
from karmazyn_substrate import (
    Store as PythonStore,   # referencja pure-Python
    Bubble,
    EventBus,
    VEC_DIM,                # zamrozone D = 2048
)

try:
    from karmazyn_backend import (
        open_store,
        store_class,
        substrate_backend,
        native_available as native_substrate_available,
        backend_info as substrate_backend_info,
        apply_cli_substrate_flags,
    )
    # Produkcyjny default DB_karmin: Rust gdy most zbudowany
    Store = store_class()
except Exception:  # pragma: no cover
    Store = PythonStore

    def open_store(thermal=True, backend=None, **kwargs):
        return PythonStore(thermal=thermal, **kwargs)

    def store_class(backend=None):
        return PythonStore

    def substrate_backend(explicit=None):
        return "python"

    def native_substrate_available():
        return False

    def substrate_backend_info():
        return {"backend": "python", "native_available": False, "store_class": "Store"}

    def apply_cli_substrate_flags(argv=None):
        return None

# ── Kontrakt granicy: jaka powierzchnie musi miec magazyn dla aplikacji ───────
# Czysty stdlib (Protocol) — zero zaleznosci.
from karmazyn_atomstore import (
    AtomStore,              # kontrakt adaptera aplikacji (D2)
    capabilities,
    conforms,
    assert_conforms,
    CORE_METHODS,
)

# Media lokalne (Faza 0/2/4) — plik/bajty ↔ atom ↔ KAFD / A_STREAM
from karmazyn_media import (  # noqa: E402
    MEDIA_S,
    MEDIA_SEG_S,
    MediaError,
    MediaRef,
    attach_bytes,
    attach_file,
    export_to_path,
    get_bytes,
    is_stream_atom,
    iter_bytes,
    list_bindings,
    load_store as load_media_store,
    materialize_temp,
    open_media,
    open_with_system,
    pipe_to,
    restore_bubbles,
    save_store as save_media_store,
    sync_bubble_record,
    try_external_player,
    build_media_index,
    open_preview,
)
try:
    from karmazyn_media_preview import (  # noqa: E402
        MediaPreviewCache,
        load_preview_image,
        show_image_window,
    )
except Exception:  # pragma: no cover
    MediaPreviewCache = None  # type: ignore
    load_preview_image = None  # type: ignore
    show_image_window = None  # type: ignore
try:
    from karmazyn_media_canvas import (  # noqa: E402
        MediaAtomCanvas,
        ThermalFramePump,
        open_atom_canvas_window,
    )
except Exception:  # pragma: no cover
    MediaAtomCanvas = None  # type: ignore
    ThermalFramePump = None  # type: ignore
    open_atom_canvas_window = None  # type: ignore

__version__ = "1.1.0"

# Publiczna powierzchnia. Import spoza tej listy = siegniecie do wnetrza jadra.
__all__ = [
    # atom
    "Atom", "AtomRegistry", "AtomsWrapper", "state_for_T",
    "T_INIT", "T_MAX", "T_HOT", "T_WARM", "T_TOMB",
    "DECAY_DEFAULT", "HEAT_READ", "HEAT_WRITE",
    # hrr (opcjonalne — None bez numpy; sprawdzaj HAS_HRR)
    "bind", "unbind", "bundle", "similarity", "normalize",
    "random_unit_vector", "name_to_vector", "HRROperations", "HAS_HRR",
    # silnik (Store = native default w DB_karmin; PythonStore = referencja)
    "Store", "PythonStore", "Bubble", "EventBus", "VEC_DIM",
    # przełącznik substratu
    "open_store", "store_class", "substrate_backend", "native_substrate_available",
    "substrate_backend_info", "apply_cli_substrate_flags",
    # kontrakt
    "AtomStore", "capabilities", "conforms", "assert_conforms", "CORE_METHODS",
    # media (Faza 0 + 2 + 4 A_STREAM)
    "MEDIA_S", "MEDIA_SEG_S", "MediaError", "MediaRef",
    "attach_bytes", "attach_file", "get_bytes", "iter_bytes", "is_stream_atom",
    "export_to_path",
    "list_bindings", "sync_bubble_record", "restore_bubbles",
    "save_media_store", "load_media_store",
    "pipe_to", "materialize_temp", "open_with_system",
    "try_external_player", "open_media",
    "build_media_index", "open_preview",
    "MediaPreviewCache", "load_preview_image", "show_image_window",
    "MediaAtomCanvas", "ThermalFramePump", "open_atom_canvas_window",
    # meta
    "kernel_info", "__version__",
]


def kernel_info() -> dict:
    """Samoopis jadra — do diagnostyki i potwierdzenia, ze fasada zaladowala
    czesci. Raportuje tez, czy twarz HRR jest aktywna (zalezy od numpy)."""
    try:
        _sub = substrate_backend_info()
    except Exception:
        _sub = {"backend": "python", "native_available": False}
    return {
        "version": __version__,
        "modules": ["karmazyn_atom", "karmazyn_hrr",
                    "karmazyn_substrate", "karmazyn_atomstore", "karmazyn_backend",
                    "karmazyn_media"],
        "vec_dim": VEC_DIM,
        "hrr_active": HAS_HRR,          # False => praca w trybie zero-zaleznosci
        "substrate": _sub,              # native (Rust) | python — DB_karmin
        "store_class": getattr(Store, "__name__", str(Store)),
        "db_karmin": True,
        "law": "temperatura mowi KIEDY, osiagalnosc mowi CZY",
        "surfaces": {
            # Kanoniczna powierzchnia natywna silnika (Store). Jawna lista —
            # introspekcja Store.__dict__ pomijala metody i byla mylaca.
            # bind/lookup sa na Bubble (wynik bubble_new), NIE na Store.
            "engine_native": [
                "atom_new", "get_atom", "has_atom", "delete_atom", "atoms", "heat",
                "snapshot_atoms", "restore_atoms",
                "bubble_new", "set_root", "unset_root",
                "tick", "settle", "resonance", "stats",
                # adapter AtomStore (te same instancje Store)
                "create_atom", "create_bubble", "import_to_bubble", "get_bubble",
            ],
            "bubble_methods": ["bind", "unbind", "lookup"],
            "adapter_contract": list(CORE_METHODS),
        },
        "contracts": {
            # S1 (v1.1.0): deep reach po stronie frontu
            "env_of": "env_of(v) -> Bubble | iterowalne[Bubble] | None; "
                      "wartosci zlozone SPLASZCZA FRONT (albo extra_reach)",
            # S3: ochrona GC w adapterze AtomStore
            "atomstore_gc": "create_bubble(label, root=True) albo set_root/"
                            "extra_reach — worek bez roota NIE chroni przed GC",
            # S12/S15: eventy ticka (dual-emit w okresie przejsciowym)
            "tick_events": "domyslnie 'both' (tick per atom + tick_batch); "
                           "'batch' | 'per_atom' | 'both'; zagniezdzony tick=no-op",
        },
        "open_seams": {
            "D2_two_surfaces": "engine_native ma adapter AtomStore; API natywne (atom_new) i kontrakt (create_atom) wspolistnieja",
            "D3_state_for_T": "kanon tutaj; kopie w dom/js_phi/kafd/live_dom do zimportu stad",
            "retained_tomb": "kanon: _retained_tomb + stats['retained_tomb'] (alias 'archived'); retencja id w RAM — brak thaw/kompresji",
        },
    }