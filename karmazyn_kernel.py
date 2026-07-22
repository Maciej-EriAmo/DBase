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
# Substrate sam degraduje lagodnie bez hrr/numpy — patrz karmazyn_substrate.
from karmazyn_substrate import (
    Store,                  # natywna powierzchnia silnika (D2)
    Bubble,
    EventBus,
    VEC_DIM,                # zamrozone D = 2048
)

# ── Kontrakt granicy: jaka powierzchnie musi miec magazyn dla aplikacji ───────
# Czysty stdlib (Protocol) — zero zaleznosci.
from karmazyn_atomstore import (
    AtomStore,              # kontrakt adaptera aplikacji (D2)
    capabilities,
    conforms,
    assert_conforms,
    CORE_METHODS,
)

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
    # silnik
    "Store", "Bubble", "EventBus", "VEC_DIM",
    # kontrakt
    "AtomStore", "capabilities", "conforms", "assert_conforms", "CORE_METHODS",
    # meta
    "kernel_info", "__version__",
]


def kernel_info() -> dict:
    """Samoopis jadra — do diagnostyki i potwierdzenia, ze fasada zaladowala
    czesci. Raportuje tez, czy twarz HRR jest aktywna (zalezy od numpy)."""
    return {
        "version": __version__,
        "modules": ["karmazyn_atom", "karmazyn_hrr",
                    "karmazyn_substrate", "karmazyn_atomstore"],
        "vec_dim": VEC_DIM,
        "hrr_active": HAS_HRR,          # False => praca w trybie zero-zaleznosci
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