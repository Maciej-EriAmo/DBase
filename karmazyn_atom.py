"""
karmazyn_atom.py — Unified Atom Model KarmazynOS v1.2
======================================================
KarmazynOS — Maciej Mazur, Warsaw 2026

Jeden model atomu zamiast czterech rozproszonych implementacji.
Zastępuje:
  PhiAtom   (karmazyn_js_phi.py)    — Python variable jako atom JS
  HRRAtom   (karmazyn_hrr.py)       — atom z wektorem HRR
  ręczne T  (karmazyn_dom.py)       — atom.T = ...; atom.state = ...
  ręczne T  (karmazyn_browser.py)   — atom.T = temp; atom.state = ...

Interfejs kompatybilny z istniejącym runtime.py:
  atom.id, atom.S, atom.E, atom.T, atom.state
  atom.touch(), atom.decay(), atom.is_dead(), atom.age()

Nowe możliwości:
  atom.heat(amount)     — bezpośrednie ogrzanie o kwotę
  atom.vector           — opcjonalny wektor HRR (numpy)
  atom.metadata         — dict dla dodatkowych danych
  Atom.from_state(T)    — alias state_for_T (klasyfikacja stanu z T)
  atom.on_state_change  — rejestracja callbacków (lista, nie jeden slot)

Użycie:
    from karmazyn_atom import Atom, T_HOT, T_WARM, T_TOMB, state_for_T

    a = Atom("my_atom", S="text:p", E="treść", T=60.0)
    a.touch()          # ogrzej przy dostępie
    a.decay()          # ostudź (wywołuj przez scheduler)
    a.heat(20.0)       # ogrzej o konkretną kwotę
    print(a.state)     # "HOT" / "WARM" / "COLD" / "TOMB"
    # COLD to NAZWA STANU (T_TOMB <= T < T_WARM), nie osobna stała T_COLD.

UWAGA — AtomRegistry.tick/gc:
  decay + lista martwych po samej temperaturze, BEZ grafu osiągalności.
  Bezpieczny GC z reach: Store.tick (karmazyn_substrate). Samo reg.gc(dead)
  po tick() to znana katastrofa P6 (zimne-ale-żywe domknięcie ginie).

FIX v1.2 (audyt 2026-07):
  INWARIANT TEMPERATURY: 0.0 <= T <= T_MAX, egzekwowany we WSZYSTKICH
  mutatorach. Wcześniej touch(weight<0) i heat(amount<0) mogły zejść
  poniżej zera (potwierdzone sondą: touch(weight=-20) → T=-150).
  decay() dodatkowo zabezpieczony przed ujemnym rate (max z 0.0).
  Ścieżka nominalna (dodatnie wagi/kwoty, rate w [0,1]) — bez zmian.
"""

import time
import warnings
from typing import Any, Callable, Dict, List, Optional


# ─── Progi termodynamiczne (jeden zestaw dla całego systemu) ──────────────────

T_INIT    = 50.0    # temperatura startowa (WARM)
T_MAX     = 100.0   # temperatura maksymalna
T_HOT     = 70.0    # próg HOT:     T >= T_HOT  → HOT
T_WARM    = 30.0    # próg WARM:    T_WARM <= T < T_HOT → WARM; poniżej = COLD
T_TOMB    = 2.0     # próg TOMB/GC: T < T_TOMB → TOMB + is_dead; T == T_TOMB jeszcze COLD

DECAY_DEFAULT = 0.92   # mnożnik przy tick (stygnięcie)
HEAT_READ     = 10.0   # przyrost przy odczycie
HEAT_WRITE    = 5.0    # przyrost przy zapisie (mniejszy niż read)


# ─── Klasyfikacja stanu ───────────────────────────────────────────────────────

def state_for_T(T: float) -> str:
    """Jeden punkt prawdy dla klasyfikacji stanu termodynamicznego.

    Granice domknięte od góry przedziału: T == T_TOMB jest jeszcze COLD
    (żywy termicznie, is_dead=False). TOMB / GC dopiero przy T < T_TOMB.
    """
    if   T >= T_HOT:  return "HOT"
    elif T >= T_WARM: return "WARM"
    elif T >= T_TOMB: return "COLD"
    return "TOMB"


def _clamp_T(T: float) -> float:
    """Inwariant temperatury: 0.0 <= T <= T_MAX (FIX v1.2)."""
    if T < 0.0:
        return 0.0
    if T > T_MAX:
        return T_MAX
    return T


# ─── Atom ─────────────────────────────────────────────────────────────────────

class Atom:
    """
    Universalny atom KarmazynOS.

    Pola zgodne z istniejącym runtime.py (kompatybilność wsteczna):
      id, S, E, T, state

    Dodatkowe:
      vector   — opcjonalny wektor HRR (numpy ndarray)
      metadata — dict dla specyficznych danych warstwy
      age      — wiek atomu w sekundach
    """

    __slots__ = (
        "id", "S", "E", "T", "state",
        "vector", "metadata",
        "_born", "_reads", "_writes",
        "_on_state_change",
    )

    def __init__(self,
                 id:      str,
                 S:       str   = "",
                 E:       str   = "",
                 T:       float = T_INIT,
                 vector         = None,
                 metadata: Dict[str, Any] = None):
        self.id       = id
        self.S        = S
        self.E        = E
        self.T        = _clamp_T(float(T))
        self.state    = state_for_T(self.T)
        self.vector   = vector        # numpy array lub None
        self.metadata = metadata if metadata is not None else {}
        self._born    = time.monotonic()
        self._reads   = 0
        self._writes  = 0
        self._on_state_change: List[Callable] = []

    # ── Interfejs termodynamiczny ──────────────────────────────────────────────

    def touch(self, weight: float = 1.0) -> None:
        """Ogrzej przy dostępie (odczyt). weight > 1 = intensywniejszy dostęp.
        FIX v1.2: wynik zawsze w [0, T_MAX] — ujemna waga nie zejdzie poniżej 0."""
        self._reads += 1
        self.T = _clamp_T(self.T + HEAT_READ * weight)
        self._update_state()

    def touch_write(self) -> None:
        """Ogrzej przy zapisie (mniejszy przyrost niż read)."""
        self._writes += 1
        self.T = _clamp_T(self.T + HEAT_WRITE)
        self._update_state()

    def heat(self, amount: float) -> None:
        """Bezpośrednie ogrzanie o konkretną kwotę.
        FIX v1.2: wynik zawsze w [0, T_MAX]."""
        self.T = _clamp_T(self.T + amount)
        self._update_state()

    def cool(self, amount: float) -> None:
        """Bezpośrednie ochłodzenie o kwotę."""
        self.T = _clamp_T(self.T - amount)
        self._update_state()

    def decay(self, rate: float = DECAY_DEFAULT) -> None:
        """Stygnięcie — wywoływane przez scheduler co tick.
        FIX v1.2: wynik nie zejdzie poniżej 0 (ochrona przed ujemnym rate)."""
        self.T = _clamp_T(self.T * rate)
        self._update_state()

    def kill(self) -> None:
        """Natychmiastowe przejście do TOMB (usunięcie węzła DOM itp.)."""
        self.T = T_TOMB * 0.5
        self._update_state()

    def is_dead(self) -> bool:
        return self.T < T_TOMB

    def age(self) -> float:
        """Wiek atomu w sekundach."""
        return time.monotonic() - self._born

    def _update_state(self) -> None:
        new_state = state_for_T(self.T)
        if new_state != self.state:
            self.state = new_state
            for cb in list(self._on_state_change):
                try:
                    cb(self)
                except Exception:
                    pass

    def on_state_change(self, callback: Callable) -> None:
        """Dokłada callback wywoływany przy zmianie stanu (wiele listenerów)."""
        if callback is not None and callback not in self._on_state_change:
            self._on_state_change.append(callback)

    def clear_state_listeners(self) -> None:
        """Usuń wszystkie callbacki zmiany stanu."""
        self._on_state_change.clear()

    @staticmethod
    def from_state(T: float) -> str:
        """Klasyfikacja stanu z T — alias state_for_T (kompatybilność z docs)."""
        return state_for_T(T)

    # ── Właściwości ───────────────────────────────────────────────────────────

    @property
    def is_hot(self)  -> bool: return self.T >= T_HOT
    @property
    def is_warm(self) -> bool: return T_WARM <= self.T < T_HOT
    @property
    def is_cold(self) -> bool: return T_TOMB <= self.T < T_WARM
    @property
    def is_tomb(self) -> bool: return self.T < T_TOMB

    @property
    def has_vector(self) -> bool:
        return self.vector is not None

    # ── Reprezentacja ─────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":    self.id,
            "S":     self.S,
            "E":     self.E,
            "T":     round(self.T, 2),
            "state": self.state,
            "age":   round(self.age(), 1),
        }

    def __repr__(self) -> str:
        return f"Atom({self.id!r}, T={self.T:.1f}, {self.state})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Atom) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


# ─── AtomRegistry — prosty rejestr ───────────────────────────────────────────

class AtomRegistry:
    """
    Minimalny rejestr atomów.
    Używany przez PhiSpace, JSPhi, DOMMapper.
    Nie duplikuje logiki — tylko przechowuje i udostępnia.

    GC: tick()/gc() operują WYŁĄCZNIE na temperaturze (bez reach).
    Bezpieczny silnik z osiągalnością: karmazyn_substrate.Store.tick.
    """

    _p6_warned = False

    def __init__(self):
        self._atoms: Dict[str, Atom] = {}
        self._generation: int = 0
        self._atoms_wrapper: Optional["AtomsWrapper"] = None  # cache — reużycie AtomsWrapper

    def create(self, id: str, S: str = "", E: str = "",
               T: float = T_INIT, **kwargs) -> Atom:
        """Tworzy atom. Konflikt id → ValueError (bez cichego nadpisania)."""
        if id in self._atoms:
            raise ValueError(f"atom o id {id!r} już istnieje w rejestrze")
        a = Atom(id, S, E, T, **kwargs)
        self._atoms[id] = a
        self._generation += 1
        return a

    def get(self, id: str) -> Optional[Atom]:
        return self._atoms.get(id)

    def has(self, id: str) -> bool:
        return id in self._atoms

    def delete(self, id: str) -> bool:
        if id in self._atoms:
            del self._atoms[id]
            self._generation += 1
            return True
        return False

    @property
    def atoms_wrapper(self) -> "AtomsWrapper":
        """Zwraca AtomsWrapper — wspiera atoms() i iterację.

        Jeden współdzielony wrapper: każdy odczyt property nie tworzy nowego
        obiektu (inaczej cache generacyjny AtomsWrapper nigdy nie jest reużywany).
        """
        if self._atoms_wrapper is None:
            self._atoms_wrapper = AtomsWrapper(self)
        return self._atoms_wrapper

    def atoms(self) -> List[Atom]:
        """Zwraca listę wszystkich żywych atomów."""
        return list(self._atoms.values())

    def tick(self, rate: float = DECAY_DEFAULT) -> List[str]:
        """
        Decay wszystkich atomów. Zwraca id z T < T_TOMB (kandydaci do GC).

        UWAGA (P6): to NIE jest bezpieczny GC. Brak grafu osiągalności —
        zimny-ale-osiągalny atom też trafia na listę. Po tick() NIE wołaj
        ślepo gc(dead) na silniku ze scope'ami — użyj Store.tick.
        """
        if not AtomRegistry._p6_warned:
            warnings.warn(
                "AtomRegistry.tick() jest temp-only (bez reach-GC). "
                "Bezpieczny GC: Store.tick(). Samo reg.gc(dead) po tick() "
                "usuwa zimne-ale-żywe domknięcia (P6).",
                UserWarning,
                stacklevel=2,
            )
            AtomRegistry._p6_warned = True
        dead = []
        # Brak mutacji _atoms w tick — GC osobno (gc()). items() bez list().
        for id, atom in self._atoms.items():
            atom.decay(rate)
            if atom.is_dead():
                dead.append(id)
        return dead

    def gc(self, dead_ids: List[str]) -> int:
        """
        Usuń podane id. Zwraca liczbę usuniętych.

        UWAGA: nie sprawdza osiągalności. Preferuj Store.tick / Store.delete_atom.
        """
        removed = 0
        for id in dead_ids:
            if self._atoms.pop(id, None) is not None:
                removed += 1
        if removed > 0:
            self._generation += 1
        return removed

    def hot_atoms(self) -> List[Atom]:
        return [a for a in self._atoms.values() if a.is_hot]

    def by_state(self, state: str) -> List[Atom]:
        return [a for a in self._atoms.values() if a.state == state]

    def stats(self) -> Dict[str, int]:
        # Jedna pętla O(N): progi T są rozłączne (is_hot/warm/cold/tomb).
        # Liczymy z T (nie z a.state), bo T może być ustawione ręcznie.
        out = {"total": 0, "HOT": 0, "WARM": 0, "COLD": 0, "TOMB": 0}
        for a in self._atoms.values():
            out["total"] += 1
            if a.is_hot:
                out["HOT"] += 1
            elif a.is_warm:
                out["WARM"] += 1
            elif a.is_cold:
                out["COLD"] += 1
            else:
                out["TOMB"] += 1
        return out

    def __len__(self) -> int:
        return len(self._atoms)

    def __contains__(self, id: str) -> bool:
        return id in self._atoms

    # Kompatybilność z runtime.py: matrix.has_atom(), matrix.atoms()
    def has_atom(self, id: str) -> bool:
        return self.has(id)


class AtomsWrapper:
    """
    Wrapper AtomRegistry wspierający dwa style dostępu:
      matrix.atoms()  — wywołanie jako funkcja (oryginalny API)
      matrix.atoms    — użycie jako property/iterowalny obiekt

    Rozwiązuje niekompatybilność między modułami które używają
    różnych konwencji dostępu do listy atomów.

    Używany przez PhiBuffer, draw_phi_map, zewnętrzne narzędzia.

    FIX v1.1: cache invalidacja przez generation counter zamiast len().
    len() nie wykrywa replace (delete+create przy tym samym rozmiarze).
    """

    def __init__(self, registry: "AtomRegistry"):
        self._reg   = registry
        self._cache: Optional[List["Atom"]] = None
        self._cache_gen = -1

    def _get_list(self) -> List["Atom"]:
        """Zwraca listę atomów — z cache jeśli generacja się nie zmieniła."""
        current_gen = self._reg._generation
        if self._cache is None or self._cache_gen != current_gen:
            self._cache     = list(self._reg._atoms.values())
            self._cache_gen = current_gen
        return self._cache

    def __call__(self) -> List["Atom"]:
        """Pozwala wywołać atoms() jako funkcję."""
        return self._get_list()

    def __iter__(self):
        """Pozwala iterować bezpośrednio: for atom in matrix.atoms."""
        return iter(self._reg._atoms.values())

    def __len__(self) -> int:
        return len(self._reg._atoms)

    def __getitem__(self, index: int) -> "Atom":
        return self._get_list()[index]