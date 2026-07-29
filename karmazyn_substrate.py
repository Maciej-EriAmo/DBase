#!/usr/bin/env python3
"""
karmazyn_substrate.py — kanoniczny rdzeń KarmazynOS (krok 1 konsolidacji)
==========================================================================
Maciej Mazur, Warsaw 2026

Jeden substrat zamiast pięciu. Neutralny językowo, zbudowany na NAJBOGATSZYM
istniejącym atomie — `karmazyn_atom.Atom` (pełny FSM HOT/WARM/COLD/TOMB, wektor).

Dlaczego ten kształt, a nie AtomRegistry:
  bezpiecznego GC atomów NIE DA SIĘ zrobić bez grafu osiągalności (scope'y,
  korzenie, domknięcia). `AtomRegistry` posiada same atomy → kasuje po samej
  temperaturze (udowodniona katastrofa P6: zimne-ale-żywe domknięcie ginie po
  39 tickach). Ten Store POSIADA atomy + bąble + korzenie razem → liczy
  osiągalność → reguła reach: zimny+nieosiągalny → GC; zimny+osiągalny →
  retencja TOMB (retained tomb). Temperatura mówi KIEDY, osiągalność mówi CZY.

Co rdzeń wie o językach: nic. Reachability domknięć idzie przez wstrzykiwany
callback `env_of(value)`. Front-end (Scheme/JS/LOGO) dostarcza go; rdzeń tylko
śledzi zwrócone env. To jest cała wiedza rdzenia o "domknięciu".

KONTRAKT env_of (v1.2, kuracja S1 — deep reach):
    env_of(v) -> Bubble | iterowalne po Bubble | None
  Pojedynczy Bubble = domknięcie (jak dotąd). Iterowalne = wartość ZŁOŻONA
  (lista/krotka/struktura domknięć) — FRONT sam spłaszcza swoje kontenery
  i zwraca wszystkie env; rdzeń nadal nie zna języka. Elementy nie-Bubble
  w iterowanym wyniku są ignorowane. Wartości bogatsze niż to obejmuje
  kontrakt (np. dict domknięć, którego env_of nie spłaszcza) — front MUSI
  je spłaszczyć w env_of albo użyć extra_reach; inaczej cichy reap
  (dawna sonda 22).

Powierzchnia zgodna z evaluatorem referencji (drop-in):
  atom_new(S,E,T,value) · get_atom · delete_atom · heat ·
  bubble_new(label,parent) · Bubble.bind/lookup/unbind ·
  set_root/unset_root · tick · settle · stats
  wartość: atom.metadata["v"]   (jak w referencji)

Dodatkowo adapter AtomStore (kontrakt aplikacji):
  create_atom · has_atom · create_bubble · import_to_bubble · get_bubble
DBase/KAFD: sync_id_counter() po wczytaniu jawnych id aN.

UWAGA GC dla AtomStore (S3): create_bubble("box") + import_to_bubble(...)
NIE chroni atomów przed GC. Bąbel-worek nie jest korzeniem — atomy w nim
stygną i giną po settle. Ochrona = create_bubble(label, root=True) albo
set_root(get_bubble(label)) albo extra_reach.

FIX v1.1 (audyt 2026-07):
  1. BEZPIECZEŃSTWO WĄTKOWE: publiczny `Store.lock` (RLock). Wszystkie
     mutatory i tick() biorą go; boot uruchamia tick() w wątku tła.
     Front-end może wziąć `store.lock` na sekcję złożoną (montaż ramki
     wywołania — patrz karmazyn_exec._apply).
  2. create_atom: sentinel dla `value` — jeśli caller NIE podał value,
     a `metadata` zawiera już "v", klucz NIE jest nadpisywany na None.
  3. stats() liczy retencję ŚWIEŻO (filtr is_dead), nie surowym len().

FIX v1.2 (runda S1→S13, audyt powtórzony 2026-07-22):
  S1  env_of może zwrócić iterowalne po Bubble (deep reach po stronie frontu).
  S3  create_bubble(label, atom_ids=None, root=False) — root=True czyni
      bąbel korzeniem (ochrona GC); docstring ostrzega o worku-bez-roota.
  S2  `reg` schowany: wewnętrznie `_reg`; publiczne property `reg` działa
      wstecznie, ale emituje jednorazowy UserWarning (użyj atoms()/
      delete_atom/has_atom/heat). roots/bubbles pozostają widoczne
      (diagnostyka), ale mutacja z zewnątrz jest POZA kontraktem —
      używaj set_root/unset_root/bubble_new.
  S8  atom_vector: invalidacja cache przy zmianie E (wektor liczony przez
      Store pamięta źródłowe E w metadata["_vec_E"]). Wektor ZAREJESTROWANY
      ręcznie (atom.vector bez _vec_E) pozostaje nietknięty — jak dotąd.
  S4/S14  Nazwa kanoniczna: `_retained_tomb` + stats["retained_tomb"].
      Aliasy wsteczne zachowane: property `_archived` (ten sam set) i klucz
      stats["archived"] (ta sama wartość). Zero fizycznego archiwum — to
      retencja id w RAM, bez thaw/kompresji (uczciwie, jak w kodzie).
  S12/S13  Jeden walk na tick (dowód: GC usuwa wyłącznie atomy spoza reach,
      więc ich krawędzie nie były częścią walka — `seen` pozostaje poprawne
      dla prune). Strażnik: bąble utworzone PODCZAS ticka (przez handlery
      zdarzeń) są zachowane do następnego ticka. Eventy tick:
        "batch"    — tylko tick_batch (1/tick),
        "per_atom" — tylko emit("tick", atom) per atom,
        "both"     — dual-emit (okres przejściowy; DOMYŚLNE) — batch + per-atom.
  S15  Zagnieżdżony tick() (np. z vacuum_decay) jest no-op; reaped rośnie
      tylko gdy delete faktycznie usunął atom.
"""

import threading
import warnings

import karmazyn_atom as ka

# Twarz wektora (HRR) — opcjonalna. Degradacja łagodna bez numpy/hrr:
# atom_vector zwraca None, resonance zwraca []. Wykonanie/GC niezależne od tego.
try:
    import karmazyn_hrr as _hrr
    _HAS_HRR = True
except Exception:
    _hrr = None
    _HAS_HRR = False

VEC_DIM = 2048   # zamrożone D

# Stałe termiczne — JEDNO źródło (karmazyn_atom); bez lokalnego dryfu
T_INIT = ka.T_INIT
T_MAX = ka.T_MAX
T_TOMB = ka.T_TOMB
DECAY = ka.DECAY_DEFAULT
HEAT_READ = ka.HEAT_READ

# Sentinel: odróżnia "value nie podane" od "value=None" (FIX v1.1 pkt 2)
_MISSING = object()


class EventBus:
    """Kanoniczny bus zdarzeń silnika: on/emit. Atomy ogłaszają stan, słuchacze
    reagują — nikt nie odpytuje (odpytanie zimnego atomu by go ogrzało).
    Dublety EventBus w runtime/phi idą potem do scalenia (herezja)."""
    __slots__ = ("_handlers",)

    def __init__(self):
        self._handlers = {}

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event, *args):
        for h in list(self._handlers.get(event, ())):
            try:
                h(*args)
            except Exception:
                pass


class Bubble:
    """Scope/grupa: rodzic + nazwane wiązania (§4.4 schemat A). lookup grzeje.

    FIX v1.1: bind/unbind/lookup biorą store.lock — bezpieczne przy
    schedulerze tick() w wątku tła (RLock: reentrancja w tym samym wątku)."""
    __slots__ = ("store", "label", "parent", "bindings")

    def __init__(self, store, label="", parent=None):
        self.store = store
        self.label = label
        self.parent = parent
        self.bindings = {}              # name -> atom id

    def bind(self, name, atom):
        """Wiąże nazwę z atomem. Atom musi należeć do tego samego Store."""
        aid = getattr(atom, "id", None)
        if aid is None:
            raise TypeError("bind wymaga Atom z polem id")
        with self.store.lock:
            if not self.store.has_atom(aid):
                raise ValueError(f"bind: atom {aid!r} nie należy do tego Store")
            self.bindings[name] = aid

    def unbind(self, name):
        """Usuwa lokalne wiązanie. Zwraca id atomu albo None."""
        with self.store.lock:
            return self.bindings.pop(name, None)

    def lookup(self, name):
        with self.store.lock:
            b = self
            while b is not None:
                aid = b.bindings.get(name)
                if aid is not None:
                    atom = self.store.get_atom(aid)
                    if atom is None:
                        # dangling: id w mapie, atom usunięty — posprzątaj i szukaj wyżej
                        b.bindings.pop(name, None)
                        b = b.parent
                        continue
                    if self.store.thermal:
                        self.store.heat(atom)     # twarz termiczna: odczyt grzeje
                    return atom                   # wartość NIEZALEŻNA od termiki
                b = b.parent
            return None


class Store:
    """Posiadający magazyn: atomy (karmazyn_atom.Atom) + bąble + korzenie + reach-GC.

    Współbieżność (FIX v1.1): `self.lock` (RLock) chroni cały stan Store.
    Wszystkie publiczne mutatory i tick() biorą go same. Front-end może
    dodatkowo objąć nim sekcję złożoną (kilka operacji atomowo).

    Enkapsulacja (S2, v1.2): rejestr atomów jest wewnętrzny (`_reg`).
    Publiczny kontrakt: atoms(), get_atom, has_atom, delete_atom, heat.
    Property `reg` działa wstecznie z jednorazowym ostrzeżeniem."""

    _reg_warned = False     # jednorazowe ostrzeżenie dostępu do reg (S2)

    def __init__(self, thermal=True, env_of=None, decay=DECAY, extra_reach=None,
                 tick_event_mode="both"):
        """tick_event_mode (S12/S15, v1.2):
          "batch"    — JEDEN emit("tick_batch", {...}) na tick,
          "per_atom" — emit("tick", atom) dla każdego atomu (THRESHOLD),
          "both"     — dual-emit: per-atom + batch (okres przejściowy, domyślne).
        Zagnieżdżony tick (handler pod RLock) jest no-op (S15)."""
        if tick_event_mode not in ("batch", "per_atom", "both"):
            raise ValueError("tick_event_mode: 'batch', 'per_atom' albo 'both'")
        self.lock = threading.RLock()        # FIX v1.1: jedna brama do stanu
        self._reg = ka.AtomRegistry()        # kanoniczny rejestr + FSM atomu (S2: wewnętrzny)
        self.bubbles = []
        self.roots = []
        self.thermal = thermal
        self._env_of = env_of or (lambda v: None)   # rdzeń nie zna języka
        # extra_reach: dostawca dodatkowych osiągalnych id atomów dla front-endów
        # o płaskim członkostwie (np. Luneta: atomy trzymane przez bąble-kontenery).
        # Scheme go nie używa (osiągalność idzie przez korzenie leksykalne + env_of).
        self._extra_reach = extra_reach or (lambda: ())
        self._decay = decay
        self._n = 0
        self.reaped = 0
        self._retained_tomb = set()          # S4/S14: kanon — retencja TOMB pod reach (RAM, bez thaw)
        self._tick_event_mode = tick_event_mode
        self._ticking = False                # S15: strażnik reentrancy tick()
        self.events = EventBus()             # silnik = źródło zdarzeń termicznych

    # ── enkapsulacja rejestru (S2) ────────────────────────────────────────────
    @property
    def reg(self):
        """Alias wsteczny do wewnętrznego rejestru. NOWY kod: atoms(),
        get_atom, has_atom, delete_atom, heat. Bezpośrednie reg.tick+gc
        omija prawo reach (P6) — stąd jednorazowe ostrzeżenie."""
        if not Store._reg_warned:
            warnings.warn(
                "Store.reg jest wewnętrzny — użyj atoms()/get_atom/has_atom/"
                "delete_atom/heat. Bezpośrednie reg.tick+gc omija reach-GC (P6).",
                UserWarning,
                stacklevel=2,
            )
            Store._reg_warned = True
        return self._reg

    # ── alias wsteczny retencji (S4/S14) ──────────────────────────────────────
    @property
    def _archived(self):
        """Alias wsteczny: dawna nazwa `_archived` wskazuje TEN SAM set co
        `_retained_tomb` (mutacje przez alias działają identycznie)."""
        return self._retained_tomb

    # ── atomy (§4.1) ──────────────────────────────────────────────────────────
    def atom_new(self, S, E="", T=T_INIT, value=None):
        with self.lock:
            aid = f"a{self._n}"
            self._n += 1
            atom = self._reg.create(aid, S=S, E=str(E), T=T)
            atom.metadata["v"] = value           # payload językowy (jak w referencji)
            atom.on_state_change(self._announce) # atom sam ogłosi przejście stanu
        self.events.emit("atom_created", atom)
        return atom

    def sync_id_counter(self) -> int:
        """Po wczytaniu .kafd (reg.create z jawnym id) podnieś _n ponad max aN."""
        with self.lock:
            max_n = -1
            for atom in self._reg.atoms():
                aid = atom.id
                if len(aid) > 1 and aid[0] == "a" and aid[1:].isdigit():
                    max_n = max(max_n, int(aid[1:]))
            if max_n >= 0:
                self._n = max(self._n, max_n + 1)
            return self._n

    def _announce(self, atom):
        # Przekroczenie progu FSM — zawsze state_changed.
        # vacuum_decay emituje TYLKO tick() przy realnym GC (nie przy retencji).
        self.events.emit("state_changed", atom)

    def get_atom(self, aid):
        return self._reg.get(aid)

    def has_atom(self, aid):
        """AtomStore / publiczny test obecności — bez grzania."""
        return self._reg.has(aid)

    def create_atom(self, id, S, E, T=T_INIT, **kwargs):
        """
        Adapter AtomStore: create_atom(id, S, E, T, **kw) -> id.
        value (payload językowy) opcjonalnie w kwargs.

        FIX v1.1: gdy value NIE podane, a metadata zawiera "v" — klucz "v"
        zostaje (wcześniej nadpisywany na None: cicha utrata danych).
        """
        with self.lock:
            if self._reg.has(id):
                raise ValueError(f"atom o id {id!r} już istnieje")
            value = kwargs.pop("value", _MISSING)
            vector = kwargs.pop("vector", None)
            metadata = kwargs.pop("metadata", None)
            atom = self._reg.create(id, S=S, E=str(E), T=float(T),
                                    vector=vector, metadata=metadata)
            if kwargs:
                atom.metadata.update(kwargs)
            if value is not _MISSING:
                atom.metadata["v"] = value
            elif "v" not in atom.metadata:
                atom.metadata["v"] = None
            atom.on_state_change(self._announce)
            # utrzymaj generator id powyzej recznych id numerycznych aN
            if isinstance(id, str) and id.startswith("a"):
                try:
                    n = int(id[1:])
                    if n >= self._n:
                        self._n = n + 1
                except ValueError:
                    pass
        self.events.emit("atom_created", atom)
        return id

    def atoms(self, state=None):
        """Publiczny, tylko-do-odczytu widok atomów — zamiast sięgania do rejestru.
        state: None = wszystkie; 'HOT'/'WARM'/'COLD'/'TOMB' = filtr po stanie FSM."""
        with self.lock:
            xs = self._reg.atoms()
        if state is None:
            return xs
        s = state.upper()
        return [a for a in xs if a.state == s]

    def heat(self, atom):
        """Ogrzej atom jak przy odczycie — spójne z touch() (licznik _reads)."""
        with self.lock:
            atom.touch()

    def delete_atom(self, aid):
        """
        Jawne usunięcie atomu: czyści wiązania we wszystkich bąblach,
        retencję i rejestr. Zwraca True jeśli atom istniał.
        """
        with self.lock:
            atom = self.get_atom(aid)
            if atom is None:
                return False
            self._purge_bindings(aid)
            self._retained_tomb.discard(aid)
            self._reg.delete(aid)
        self.events.emit("atom_deleted", atom)
        return True

    def snapshot_atoms(self):
        """Płytka kopia mapy id→Atom (transakcje / rollback). Atomy współdzielone."""
        with self.lock:
            return dict(self._reg._atoms)

    def restore_atoms(self, atoms, temperatures=None):
        """Przywróć rejestr z snapshotu (rollback transakcji).

        atoms: dict id→Atom (jak z snapshot_atoms).
        temperatures: opcjonalnie {id: T} — przywraca T i FSM po restarcie stanu.
        """
        with self.lock:
            self._reg._atoms = dict(atoms)
            self._reg._generation += 1
            if temperatures:
                for aid, T in temperatures.items():
                    a = self._reg.get(aid)
                    if a is not None:
                        a.T = float(T)
                        a._update_state()

    def _purge_bindings(self, aid):
        """Usuń wszystkie name→aid w znanym inwentarzu bąbli (anty-dangling)."""
        for b in self.bubbles:
            if not b.bindings:
                continue
            dead = [n for n, i in b.bindings.items() if i == aid]
            for n in dead:
                del b.bindings[n]

    # ── scope (§4.4) ──────────────────────────────────────────────────────────
    def bubble_new(self, label="", parent=None):
        """Nowy bąbel (call frames, env). Duplikaty etykiet dozwolone (ramki)."""
        b = Bubble(self, label, parent)
        with self.lock:
            self.bubbles.append(b)
        return b

    def create_bubble(self, label, atom_ids=None, root=False):
        """
        Adapter AtomStore: create_bubble(label, atom_ids=None, root=False) -> label.
        Etykieta unikalna: istniejący bąbel jest ponownie użyty (nie klonowany).
        atom_ids: opcjonalna lista id do zaimportowania (klucz = E lub id).

        OCHRONA GC (S3, v1.2): bąbel-worek SAM Z SIEBIE nie chroni atomów —
        nie jest korzeniem, więc atomy w nim stygną i giną po settle.
        root=True robi z bąbla korzeń (set_root) — wtedy zawartość podlega
        regule: zimny+osiągalny → retained tomb, nie vacuum. Alternatywy:
        ręczny set_root(get_bubble(label)) albo extra_reach.
        """
        with self.lock:
            b = self.get_bubble(label)
            if b is None:
                b = self.bubble_new(label)
            if root:
                self.set_root(b)
            if atom_ids:
                for aid in atom_ids:
                    self.import_to_bubble(label, aid)
        return label

    def get_bubble(self, label):
        """Adapter AtomStore: bąbel o etykiecie (lub None). Pierwszy match."""
        with self.lock:
            for b in self.bubbles:
                if b.label == label:
                    return b
        return None

    def import_to_bubble(self, bubble_label, atom_id):
        """Adapter AtomStore: import_to_bubble(label, atom_id) -> bool.
        Klucz wiązania = atom.E (gdy niepuste) albo atom_id; kolizja E
        nadpisuje poprzednie wiązanie (S5 — znane, świadome)."""
        with self.lock:
            b = self.get_bubble(bubble_label)
            atom = self.get_atom(atom_id)
            if b is None or atom is None:
                return False
            name = atom.E if atom.E else atom_id
            b.bind(name, atom)
            return True

    def set_root(self, b):
        with self.lock:
            if b not in self.roots:
                self.roots.append(b)

    def unset_root(self, b):
        with self.lock:
            try:
                self.roots.remove(b)
            except ValueError:
                pass

    # ── osiągalność: korzenie + parent chain + env domknięć (przez env_of) ─────
    def _push_envs(self, env, stack):
        """S1 (v1.2): env_of może zwrócić Bubble ALBO iterowalne po Bubble.
        Elementy nie-Bubble w iterowanym wyniku są ignorowane; string/bytes
        nie jest traktowany jak kontener."""
        if env is None:
            return
        if isinstance(env, Bubble):
            stack.append(env)
            return
        if isinstance(env, (str, bytes)):
            return
        try:
            it = iter(env)
        except TypeError:
            return
        for e in it:
            if isinstance(e, Bubble):
                stack.append(e)

    def _walk_bubbles(self):
        """BFS/DFS po korzeniach: parent chain + env_of z wartości atomów.
        Zwraca (reach_atom_ids: set[str], live_bubble_ids: set[int])."""
        reach = set()
        seen = set()
        stack = list(self.roots)
        while stack:
            b = stack.pop()
            bid = id(b)
            if bid in seen:
                continue
            seen.add(bid)
            for name, aid in list(b.bindings.items()):
                a = self.get_atom(aid)
                if a is None:
                    b.bindings.pop(name, None)   # dangling → usuń przy walku
                    continue
                reach.add(aid)
                env = self._env_of(a.metadata.get("v"))
                self._push_envs(env, stack)      # env domknięcia / lista env (S1)
            if b.parent is not None:
                stack.append(b.parent)           # łańcuch leksykalny
        reach.update(self._extra_reach())
        return reach, seen

    def _reachable(self):
        with self.lock:
            reach, _ = self._walk_bubbles()
            return reach

    def _prune_bubbles(self, live_bubble_ids):
        """Usuń z inwentarza bąble nieosiągalne (wyciek call frames).

        Wyjątek: bąbel nieosiągalny, ale z wiązaniami do wciąż istniejących
        atomów, zostaje do GC tych atomów — inaczej _purge_bindings ich nie
        widzi i powstają dangling name→id na odłączonych obiektach.
        """
        if not live_bubble_ids:
            live_bubble_ids = {id(b) for b in self.roots}
        kept = []
        for b in self.bubbles:
            if id(b) in live_bubble_ids:
                kept.append(b)
                continue
            # posprzątaj dangling w odłączonym bąblu
            for name, aid in list(b.bindings.items()):
                if self.get_atom(aid) is None:
                    b.bindings.pop(name, None)
            if b.bindings:
                # wciąż trzyma żywe atomy → zostaw do reapa / delete_atom
                kept.append(b)
            # else: pusty i nieosiągalny → drop (Python GC gdy brak innych ref)
        self.bubbles = kept

    # ── upływ czasu: stygnięcie + reguła reach. No-op poza thermal. ───────────
    def tick(self):
        if not self.thermal:
            return
        # FIX v1.1: cały tick pod lockiem — walk→decay→GC→prune atomowo
        # względem mutacji z innych wątków (boot: scheduler w tle).
        # S15: zagnieżdżony tick (handler vacuum_decay/state_changed) → no-op.
        with self.lock:
            if self._ticking:
                return
            self._ticking = True
            try:
                self._tick_body()
            finally:
                self._ticking = False

    def _tick_body(self):
        """Ciało ticka — wołane wyłącznie pod lockiem z _ticking=True."""
        reach, seen = self._walk_bubbles()
        # S13 (v1.2): JEDEN walk na tick. Dowód redundancji drugiego:
        # GC usuwa wyłącznie atomy SPOZA reach — ich krawędzie env_of
        # nigdy nie były częścią walka, a purge zdejmuje tylko bindy
        # wskazujące na martwe atomy, co nie zmienia osiągalności bąbli.
        # Strażnik: bąble utworzone PODCZAS ticka (przez handlery
        # state_changed/vacuum_decay) zachowujemy do następnego ticka.
        n_bubbles_before = len(self.bubbles)
        mode = self._tick_event_mode
        emit_per_atom = mode in ("per_atom", "both")
        emit_batch = mode in ("batch", "both")
        for atom in self._reg.atoms():
            atom.decay(self._decay)          # hak atomu → state_changed
            if emit_per_atom:
                self.events.emit("tick", atom)   # THRESHOLD / dual-emit
        reaped_now = 0
        retained_now = 0
        # atoms() już zwraca list() — snapshot na czas delete; bez podwójnego list().
        for atom in self._reg.atoms():
            if atom.is_dead():                   # zimny (T < T_TOMB)
                if atom.id in reach:
                    # osiągalny → retencja TOMB pod korzeniem (bez
                    # kompresji/thaw — retained tomb ids, S4/S14)
                    self._retained_tomb.add(atom.id)
                    retained_now += 1
                else:
                    self.events.emit("vacuum_decay", atom)  # realne GC
                    aid = atom.id
                    self._purge_bindings(aid)    # zero dangling name→id
                    # S15: reaped tylko gdy delete faktycznie usunął
                    if self._reg.delete(aid):
                        self._retained_tomb.discard(aid)
                        self.reaped += 1
                        reaped_now += 1
            else:
                self._retained_tomb.discard(atom.id)  # ożywiony (T≥TOMB) → poza retencją
        # prune na `seen` z jedynego walka + strażnik nowych bąbli
        live = seen | {id(b) for b in self.bubbles[n_bubbles_before:]}
        self._prune_bubbles(live)
        if emit_batch:
            # S12 (v1.2): zbiorczy event (sam albo dual z per-atom)
            self.events.emit("tick_batch", {
                "atoms": len(self._reg),
                "reaped": reaped_now,
                "retained": retained_now,
            })

    def settle(self, n):
        # Bez locka na całość: każdy tick bierze go sam — między tickami
        # inne wątki mogą wejść (świadomie; brak głodzenia REPL-a).
        for _ in range(n):
            self.tick()

    # ── twarz wektora (HRR) — leniwa, odtwarzana z nazwy ───────────────────────
    def atom_vector(self, atom):
        """Trzecia twarz: wektor HRR z kanonicznej nazwy (E). Liczony NA ŻĄDANIE
        i cache'owany w slocie atomu — zgodnie z 'wektory nie są przechowywane,
        odtwarzane z nazwy'. Tylko dla nazwanych (E niepuste). None bez HRR.

        S8 (v1.2): wektor policzony przez Store pamięta źródłowe E w
        metadata["_vec_E"]; zmiana E → przeliczenie (koniec stale cache).
        Wektor ZAREJESTROWANY z zewnątrz (atom.vector bez _vec_E) pozostaje
        nietknięty — jak w dotychczasowym zachowaniu.

        Hot path wykonania go nie woła — koszt płaci dopiero rezonans."""
        if not _HAS_HRR or not atom.E:
            return None
        with self.lock:
            if atom.vector is None:
                atom.vector = _hrr.name_to_vector(atom.E, VEC_DIM)
                atom.metadata["_vec_E"] = atom.E
            elif "_vec_E" in atom.metadata and atom.metadata["_vec_E"] != atom.E:
                atom.vector = _hrr.name_to_vector(atom.E, VEC_DIM)
                atom.metadata["_vec_E"] = atom.E
            return atom.vector

    def resonance(self, query, k=5, threshold=0.1):
        """Adresowanie przez falę, nie przez id: zwraca k nazwanych atomów
        najbliższych zapytaniu (nazwa lub wektor) w sensie podobieństwa HRR.
        Lista [(sim, atom_id), ...] malejąco. [] bez HRR."""
        if not _HAS_HRR:
            return []
        qv = query if hasattr(query, "shape") else _hrr.name_to_vector(query, VEC_DIM)
        with self.lock:
            atoms = self._reg.atoms()
        hits = []
        for atom in atoms:
            if not atom.E:
                continue
            v = self.atom_vector(atom)
            if v is None:
                continue
            s = _hrr.similarity(qv, v)
            if s >= threshold:
                hits.append((s, atom.id))
        hits.sort(key=lambda x: -x[0])
        return hits[:k]

    def stats(self):
        """
        Statystyki magazynu.
        HOT/WARM/COLD/TOMB — liczniki FSM (kanoniczne).
        alive/dead — czy T ≥ T_TOMB (żywy termicznie) vs is_dead.
        hot/cold — aliasy wsteczne: hot=alive, cold=dead (NIE mylić z HOT/COLD).
        retained_tomb — ŚWIEŻY licznik retencji (S4/S14, kanon): id z
        _retained_tomb, które wciąż istnieją i wciąż są martwe. Klucz
        "archived" = alias wsteczny (ta sama wartość). Zbiór pozostaje
        leniwy (discard robi tick) — raport nie kłamie między tickami.
        """
        with self.lock:
            live = self._reg.atoms()
            by = {"HOT": 0, "WARM": 0, "COLD": 0, "TOMB": 0}
            alive = 0
            dead = 0
            for a in live:
                by[a.state] = by.get(a.state, 0) + 1
                if a.is_dead():
                    dead += 1
                else:
                    alive += 1
            retained_now = 0
            for aid in self._retained_tomb:
                a = self._reg.get(aid)
                if a is not None and a.is_dead():
                    retained_now += 1
            return {
                "total": len(live),
                "HOT": by["HOT"],
                "WARM": by["WARM"],
                "COLD": by["COLD"],
                "TOMB": by["TOMB"],
                "alive": alive,
                "dead": dead,
                "hot": alive,               # alias wsteczny (dawniej: nie-is_dead)
                "cold": dead,               # alias wsteczny (dawniej: is_dead)
                "reaped": self.reaped,
                "retained_tomb": retained_now,   # kanon (S4/S14)
                "archived": retained_now,        # alias wsteczny
                "bubbles": len(self.bubbles),
            }